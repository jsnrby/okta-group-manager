#!/bin/bash
set -e

echo "=== Okta Group Manager Setup ==="

# Clone MCP server into vendor/
if [ ! -d "vendor/okta-mcp-server" ]; then
    echo "Cloning okta-mcp-server..."
    git clone https://github.com/okta/okta-mcp-server.git vendor/okta-mcp-server
else
    echo "okta-mcp-server already present, skipping clone."
fi

# Install MCP server dependencies
echo "Installing okta-mcp-server dependencies..."
(cd vendor/okta-mcp-server && uv sync)

# Install app dependencies
echo "Installing app dependencies..."
uv sync

# Create .env if missing
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "Created .env from .env.example."
    echo "Fill in your credentials before running the app."
else
    echo ".env already exists, skipping."
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your Okta and Anthropic credentials"
echo "  2. Add group owners to config/group_owners.yaml"
echo "  3. Run: uv run chainlit run app/main.py -w"
