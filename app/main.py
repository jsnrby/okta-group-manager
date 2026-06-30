import os
import uuid
import urllib.parse

import truststore
truststore.inject_into_ssl()

from dotenv import load_dotenv
load_dotenv(override=True)

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth, OAuthError
from pydantic import BaseModel

from app.agent import OktaGroupAgent
from app.auth import get_owned_groups

# Per-session conversation history and token store
_histories:    dict[str, list] = {}
_token_store:  dict[str, dict] = {}   # session_id → {id_token, access_token}

app = FastAPI()
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("CHAINLIT_AUTH_SECRET", "dev-secret"),
    max_age=86400,
)

oauth = OAuth()
oauth.register(
    name="okta",
    client_id=os.environ.get("OAUTH_OKTA_CLIENT_ID", ""),
    client_secret=os.environ.get("OAUTH_OKTA_CLIENT_SECRET", ""),
    server_metadata_url=(
        f"https://{os.environ.get('OAUTH_OKTA_DOMAIN', '')}"
        "/oauth2/default/.well-known/openid-configuration"
    ),
    client_kwargs={"scope": "openid email profile"},
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


def require_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/login")
    with open("app/static/index.html") as f:
        return HTMLResponse(f.read())


@app.get("/login")
async def login(request: Request):
    redirect_uri = str(request.url_for("auth_callback"))
    return await oauth.okta.authorize_redirect(request, redirect_uri)


@app.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.okta.authorize_access_token(request)
    except OAuthError as e:
        return HTMLResponse(f"Authentication error: {e}", status_code=400)
    user_info = token.get("userinfo") or {}
    session_id = str(uuid.uuid4())
    request.session["user"] = {
        "email": user_info.get("email", ""),
        "name": user_info.get("name", user_info.get("email", "")),
        "session_id": session_id,
    }
    _histories[session_id] = []
    _token_store[session_id] = {
        "id_token":     token.get("id_token", ""),
        "access_token": token.get("access_token", ""),
    }
    return RedirectResponse("/?fresh=1")


@app.get("/logout")
async def logout(request: Request):
    user = request.session.get("user") or {}
    sid  = user.get("session_id")

    # Grab ID token before wiping the store (needed for id_token_hint)
    id_token = (_token_store.get(sid) or {}).get("id_token", "")

    if sid:
        _histories.pop(sid, None)
        _token_store.pop(sid, None)
    request.session.clear()

    # End the Okta SSO session so the user isn't silently re-authenticated
    domain = os.environ.get("OAUTH_OKTA_DOMAIN", "")
    params: dict = {"post_logout_redirect_uri": "http://localhost:8000/signed-out"}
    if id_token:
        params["id_token_hint"] = id_token
    okta_logout = (
        f"https://{domain}/oauth2/default/v1/logout"
        f"?{urllib.parse.urlencode(params)}"
    )
    return RedirectResponse(okta_logout)


@app.get("/signed-out", response_class=HTMLResponse)
async def signed_out():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Signed out — Okta Group Manager</title>
  <style>
    body{font-family:'Jost','Futura PT','Helvetica Neue',Arial,sans-serif;
         background:#F4F6F8;display:flex;align-items:center;justify-content:center;
         height:100vh;margin:0;}
    .card{background:#fff;border-radius:10px;border:1px solid #c9c9c9;
          box-shadow:0 2px 8px rgba(0,0,0,.08);padding:40px 48px;text-align:center;max-width:360px;}
    h2{color:#001967;font-size:20px;margin-bottom:8px;}
    p{color:#6B7280;font-size:14px;margin-bottom:28px;}
    a{display:inline-block;background:#00249c;color:#fff;padding:10px 28px;
      border-radius:6px;text-decoration:none;font-weight:600;font-size:14px;}
    a:hover{background:#001967;}
  </style>
</head>
<body>
  <div class="card">
    <div style="background:#00071d;border-radius:6px;padding:12px 20px;margin-bottom:24px;display:inline-block;">
      <img src="https://www.northropgrumman.com/images/NGC-logo-white-on-clear.webp" alt="Northrop Grumman" style="height:26px;display:block;">
    </div>
    <h2>You've been signed out</h2>
    <p>Your session has ended. Sign back in when you're ready.</p>
    <a href="/login">Sign in with Okta</a>
  </div>
</body>
</html>""")


@app.get("/api/auth-info")
async def auth_info(user: dict = Depends(require_user)):
    sid = user.get("session_id", "")
    tokens = _token_store.get(sid, {})
    return {
        "id_token":     tokens.get("id_token", ""),
        "access_token": tokens.get("access_token", ""),
    }


@app.get("/api/me")
async def me(user: dict = Depends(require_user)):
    return {
        "email": user["email"],
        "name": user["name"],
        "groups": await get_owned_groups(user["email"]),
    }


class ActionRequest(BaseModel):
    user: str


@app.post("/api/groups/{group_name}/add")
async def add_user(
    group_name: str, body: ActionRequest, user: dict = Depends(require_user)
):
    owned = await get_owned_groups(user["email"])
    if group_name not in owned:
        raise HTTPException(
            403,
            f"You are not authorized to manage members of the {group_name} Group.",
        )
    agent = OktaGroupAgent(user["email"], owned)
    result = await agent.run(f"Add {body.user} to the {group_name} group. Do it directly without asking for confirmation.")
    return {"result": result, "tools_called": agent.tools_called}


@app.post("/api/groups/{group_name}/remove")
async def remove_user(
    group_name: str, body: ActionRequest, user: dict = Depends(require_user)
):
    owned = await get_owned_groups(user["email"])
    if group_name not in owned:
        raise HTTPException(
            403,
            f"You are not authorized to manage members of the {group_name} Group.",
        )
    agent = OktaGroupAgent(user["email"], owned)
    result = await agent.run(f"Remove {body.user} from the {group_name} group. Do it directly without asking for confirmation.")
    return {"result": result, "tools_called": agent.tools_called}


class ChatRequest(BaseModel):
    message: str


@app.post("/api/chat")
async def chat(body: ChatRequest, request: Request, user: dict = Depends(require_user)):
    sid = user.get("session_id", "")
    history = _histories.get(sid, [])
    owned = await get_owned_groups(user["email"])
    agent = OktaGroupAgent(user["email"], owned, history)
    result = await agent.run(body.message)
    _histories[sid] = agent.conversation_history
    return {"result": result, "tools_called": agent.tools_called}
