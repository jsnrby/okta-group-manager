import base64
import json
import os
import time
import uuid

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class OktaClient:
    """
    Async Okta Management API client (private_key_jwt client_credentials).

    Ownership is determined by Okta's native group owners feature:
      Admin Console → Groups → <group> → Owners tab

    MANAGED_GROUPS env var lists the groups this app controls (comma-separated).
    Group IDs are cached for 5 minutes; the token is cached until 60s before expiry.
    """

    def __init__(self):
        self._org_url   = os.environ["OKTA_ORG_URL"].rstrip("/")
        self._client_id = os.environ["OKTA_MCP_CLIENT_ID"]
        self._key_id    = os.environ["OKTA_KEY_ID"]
        self._private_key = serialization.load_pem_private_key(
            os.environ["OKTA_PRIVATE_KEY"].replace("\\n", "\n").encode(),
            password=None,
        )
        self._access_token: str | None = None
        self._token_expires: float = 0.0
        self._group_id_cache: dict[str, str] = {}   # group name → Okta group id
        self._group_cache_expires: float = 0.0

    # ── JWT & token exchange ────────────────────────────────────────────────

    @staticmethod
    def _b64url(data: bytes | dict) -> str:
        if isinstance(data, dict):
            data = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    def _make_jwt(self) -> str:
        now = int(time.time())
        header  = {"alg": "RS256", "kid": self._key_id}
        payload = {
            "iss": self._client_id,
            "sub": self._client_id,
            "aud": f"{self._org_url}/oauth2/v1/token",
            "iat": now,
            "exp": now + 300,
            "jti": str(uuid.uuid4()),
        }
        signing_input = f"{self._b64url(header)}.{self._b64url(payload)}".encode()
        sig = self._private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{signing_input.decode()}.{self._b64url(sig)}"

    async def _ensure_token(self) -> None:
        if self._access_token and time.time() < self._token_expires - 60:
            return
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self._org_url}/oauth2/v1/token",
                data={
                    "grant_type": "client_credentials",
                    "client_assertion_type":
                        "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                    "client_assertion": self._make_jwt(),
                    "scope": "okta.users.read okta.groups.read",
                },
            )
            r.raise_for_status()
            td = r.json()
        self._access_token  = td["access_token"]
        self._token_expires = time.time() + td.get("expires_in", 3600)

    # ── Managed group helpers ───────────────────────────────────────────────

    @staticmethod
    def managed_groups() -> list[str]:
        """Returns the list of group names this app manages, from MANAGED_GROUPS env var."""
        raw = os.environ.get("MANAGED_GROUPS", "")
        return [g.strip() for g in raw.split(",") if g.strip()]

    async def _refresh_group_id_cache(self, client: httpx.AsyncClient) -> None:
        """Resolves each managed group name to its Okta group ID and caches it."""
        headers = {"Authorization": f"Bearer {self._access_token}"}
        for name in self.managed_groups():
            if name in self._group_id_cache:
                continue
            r = await client.get(
                f"{self._org_url}/api/v1/groups",
                headers=headers,
                params={"q": name, "limit": 10},
            )
            r.raise_for_status()
            match = next((g for g in r.json() if g["profile"]["name"] == name), None)
            if match:
                self._group_id_cache[name] = match["id"]
        self._group_cache_expires = time.time() + 300

    # ── Public API ──────────────────────────────────────────────────────────

    async def get_owned_groups(self, email: str) -> list[str]:
        """
        Returns the group names from MANAGED_GROUPS where the user is a
        registered owner (Okta Admin Console → group → Owners tab).
        """
        await self._ensure_token()
        managed = self.managed_groups()
        if not managed:
            return []

        headers = {"Authorization": f"Bearer {self._access_token}"}

        async with httpx.AsyncClient() as client:
            # Refresh group ID cache if stale
            if time.time() > self._group_cache_expires:
                await self._refresh_group_id_cache(client)

            # Resolve the user's Okta ID
            r = await client.get(
                f"{self._org_url}/api/v1/users/{email}",
                headers=headers,
            )
            if r.status_code != 200:
                return []
            user_id = r.json()["id"]

            # Check each managed group's native owners list
            owned = []
            for name in managed:
                gid = self._group_id_cache.get(name)
                if not gid:
                    continue
                r = await client.get(
                    f"{self._org_url}/api/v1/groups/{gid}/owners",
                    headers=headers,
                )
                if r.is_success and any(o.get("id") == user_id for o in r.json()):
                    owned.append(name)

        return owned


# Module-level singleton — shared across requests; caches bearer token + group IDs
_client = OktaClient()


async def get_owned_groups(email: str) -> list[str]:
    return await _client.get_owned_groups(email)
