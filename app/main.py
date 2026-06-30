from dotenv import load_dotenv

load_dotenv()

import chainlit as cl
from app.agent import OktaGroupAgent
from app.auth import get_owned_groups


@cl.oauth_callback
def oauth_callback(
    provider_id: str,
    token: str,
    raw_user_data: dict,
    default_user: cl.User,
) -> cl.User | None:
    if provider_id != "okta":
        return None
    email = raw_user_data.get("email", default_user.identifier)
    name = raw_user_data.get("name", email)
    return cl.User(
        identifier=email,
        display_name=name,
        metadata={"provider": "okta"},
    )


@cl.on_chat_start
async def on_chat_start():
    user = cl.user_session.get("user")
    email = user.identifier
    owned_groups = get_owned_groups(email)

    agent = OktaGroupAgent(user_email=email, owned_groups=owned_groups)
    cl.user_session.set("agent", agent)

    if not owned_groups:
        await cl.Message(
            content=(
                f"Welcome, **{email}**!\n\n"
                "Your account has no groups configured for management. "
                "Please ask your Okta administrator to add your email to `config/group_owners.yaml`."
            )
        ).send()
        return

    groups_md = "\n".join(f"- **{g}**" for g in owned_groups)
    await cl.Message(
        content=(
            f"Welcome, **{email}**!\n\n"
            f"You're authorized to manage the following groups:\n{groups_md}\n\n"
            "How can I help? Try:\n"
            "- *Add jane.doe@company.com to the Engineering group*\n"
            "- *Who is in the Sales group?*\n"
            "- *Look up john.smith@company.com*"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    agent: OktaGroupAgent | None = cl.user_session.get("agent")
    if not agent:
        await cl.Message(content="Session expired. Please refresh the page.").send()
        return

    response_msg = cl.Message(content="")
    await response_msg.send()

    try:
        result = await agent.run(message.content)
        response_msg.content = result
        await response_msg.update()
    except Exception as e:
        response_msg.content = f"An error occurred: {e}\n\nPlease check your configuration and try again."
        await response_msg.update()
        raise
