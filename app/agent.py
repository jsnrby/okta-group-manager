import os
import anthropic
import chainlit as cl
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Tools the agent is allowed to use — all others are filtered out even if the
# MCP server exposes them.
ALLOWED_TOOLS = {
    "list_users",
    "get_user",
    "list_groups",
    "get_group",
    "list_group_users",
    "add_user_to_group",
    "remove_user_from_group",
}

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096


class OktaGroupAgent:
    def __init__(self, user_email: str, owned_groups: list[str]):
        self.user_email = user_email
        self.owned_groups = owned_groups
        self.anthropic_client = anthropic.Anthropic()
        self.conversation_history: list[dict] = []

    def _system_prompt(self) -> str:
        groups = "\n".join(f"- {g}" for g in self.owned_groups) or "(none)"
        return f"""You are an Okta Group Manager assistant.

Authenticated user: {self.user_email}

AUTHORIZED GROUPS (you may ONLY operate on these):
{groups}

RULES:
1. Never add, remove, or inspect members of groups not listed above.
2. If asked about an unauthorized group, decline and list the groups you can manage.
3. Before executing an add/remove, state clearly what you are about to do and ask for confirmation if the user hasn't already confirmed.
4. Be concise and helpful. Use the user's first name if available.

CAPABILITIES:
- Look up users by name or email
- List members of authorized groups
- Add users to authorized groups
- Remove users from authorized groups"""

    def _mcp_server_params(self) -> StdioServerParameters:
        mcp_path = os.path.abspath(
            os.environ.get("MCP_SERVER_PATH", "./vendor/okta-mcp-server")
        )
        return StdioServerParameters(
            command="uv",
            args=["run", "--directory", mcp_path, "okta-mcp-server"],
            env={
                "OKTA_ORG_URL": os.environ["OKTA_ORG_URL"],
                "OKTA_CLIENT_ID": os.environ["OKTA_MCP_CLIENT_ID"],
                "OKTA_SCOPES": os.environ.get(
                    "OKTA_SCOPES", "okta.users.read okta.groups.read okta.groups.manage"
                ),
                "OKTA_PRIVATE_KEY": os.environ["OKTA_PRIVATE_KEY"],
                "OKTA_KEY_ID": os.environ["OKTA_KEY_ID"],
                "PYTHON_KEYRING_BACKEND": "keyrings.alt.file.PlaintextKeyring",
            },
        )

    @staticmethod
    def _extract_text(content_items) -> str:
        return "\n".join(
            item.text for item in content_items if hasattr(item, "text")
        )

    @staticmethod
    def _mcp_tools_to_anthropic(mcp_tools) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.inputSchema,
            }
            for t in mcp_tools
            if t.name in ALLOWED_TOOLS
        ]

    async def run(self, user_message: str) -> str:
        self.conversation_history.append({"role": "user", "content": user_message})
        messages = list(self.conversation_history)

        async with stdio_client(self._mcp_server_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools_result = await session.list_tools()
                tools = self._mcp_tools_to_anthropic(tools_result.tools)

                while True:
                    response = self.anthropic_client.messages.create(
                        model=MODEL,
                        max_tokens=MAX_TOKENS,
                        system=self._system_prompt(),
                        messages=messages,
                        tools=tools,
                    )

                    if response.stop_reason == "end_turn":
                        text = self._extract_text(response.content)
                        self.conversation_history.append(
                            {"role": "assistant", "content": response.content}
                        )
                        return text

                    if response.stop_reason == "tool_use":
                        tool_results = []
                        for block in response.content:
                            if block.type != "tool_use":
                                continue
                            async with cl.Step(name=block.name, type="tool") as step:
                                step.input = block.input
                                result = await session.call_tool(block.name, block.input)
                                result_text = self._extract_text(result.content)
                                step.output = result_text
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result_text,
                                }
                            )
                        messages.append({"role": "assistant", "content": response.content})
                        messages.append({"role": "user", "content": tool_results})
                        continue

                    # Unexpected stop reason
                    return "Unexpected response from the model. Please try again."
