# Okta Group Manager

An AI-powered chatbot that lets Okta Group Owners manage their groups using natural language. Built with Claude (Anthropic), Chainlit, and the [Okta MCP Server](https://github.com/okta/okta-mcp-server).

## How it works

1. A Group Owner logs in with their Okta account (OIDC).
2. The app looks up which groups they are authorized to manage from `config/group_owners.yaml`.
3. They chat with Claude, which calls the Okta MCP Server to perform operations — scoped strictly to authorized groups.

## Architecture

```
Browser (Chainlit UI)
  ↓ Okta OIDC login
Python App (Chainlit + Claude)
  ↓ MCP stdio
Okta MCP Server subprocess
  ↓ Okta REST APIs
Okta Tenant (ic-demo.okta.com)
```

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- An [Anthropic API key](https://console.anthropic.com/)
- Admin access to your Okta tenant

## Okta Setup

You need **two** app integrations in your Okta tenant.

### 1. API Services App (MCP Server auth)

This app allows the MCP Server to call Okta APIs on behalf of the application.

1. Admin Console → **Applications** → **Create App Integration**
2. Select **API Services** → Create
3. **Client Credentials** tab → disable DPoP → select **Public key / Private key**
4. Click **Add key** → generate a new key pair → save the private key and note the **Key ID (kid)**
5. **Okta API Scopes** tab → grant:
   - `okta.groups.manage`
   - `okta.groups.read`
   - `okta.users.read`
6. **Admin Roles** tab → assign **Group Administrator** (or a custom role with group management permissions)
7. Copy the **Client ID**

### 2. OIDC Web Application (Chainlit user login)

This app authenticates end users to the chatbot.

1. Admin Console → **Applications** → **Create App Integration**
2. Select **OIDC – OpenID Connect** → **Web Application** → Create
3. **Sign-in redirect URIs**: `http://localhost:8000/auth/oauth/okta/callback`
4. **Sign-out redirect URIs**: `http://localhost:8000`
5. **Assignments** → assign to the users or groups who should access the chatbot
6. Copy the **Client ID** and **Client Secret**

## Installation

```bash
git clone https://github.com/jsnrby/okta-group-manager.git
cd okta-group-manager
bash setup.sh
```

The setup script clones the Okta MCP Server into `vendor/okta-mcp-server` and installs all dependencies.

## Configuration

### `.env`

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

For the `OKTA_PRIVATE_KEY`, paste the contents of your private key PEM file. In `.env`, multi-line values should be wrapped in double quotes with literal `\n` between lines:

```
OKTA_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nMIIEvg...\n-----END PRIVATE KEY-----"
```

Or export from your shell:

```bash
OKTA_PRIVATE_KEY=$(cat private.pem) uv run chainlit run app/main.py -w
```

Generate the `CHAINLIT_AUTH_SECRET`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### `config/group_owners.yaml`

Map Okta user email addresses to the groups they can manage:

```yaml
owners:
  jane.doe@company.com:
    - "Engineering"
    - "Product"
  john.smith@company.com:
    - "Sales"
```

Group names must exactly match the group names in your Okta tenant.

## Running

```bash
uv run chainlit run app/main.py -w
```

Open [http://localhost:8000](http://localhost:8000) and sign in with your Okta account.

## Example interactions

- *"Add jane.doe@company.com to the Engineering group"*
- *"Who is currently in the Sales team?"*
- *"Look up the user john.smith@company.com"*
- *"Remove bob@company.com from Engineering"*

## Security notes

- Users can only manage groups listed in `config/group_owners.yaml` for their email address.
- The allowed tool set is restricted at the agent level (`ALLOWED_TOOLS` in `app/agent.py`) — destructive operations (delete group, deactivate user) are never exposed.
- The MCP Server authenticates to Okta using Private Key JWT — no passwords or tokens stored in the keyring.
