# Setup

The skill needs the open-source `mcp-store-tools` MCP server
(https://github.com/thealirazadev/mcp-store-tools) connected to your agent. It runs locally over
stdio and needs [uv](https://docs.astral.sh/uv/getting-started/installation/).

## Try it first, no store needed

`--demo` connects to Shopify's public sample store (mock.shop), so you can test the skill before
creating any credentials. Use these arguments in any config below and leave out `env`:

```
uvx --from git+https://github.com/thealirazadev/mcp-store-tools mcp-store-tools --demo
```

## Get a Storefront token for your store

1. Shopify admin: **Settings > Apps and sales channels > Develop apps > Create an app**.
2. **Configure Storefront API scopes**: enable `unauthenticated_read_product_listings` and
   `unauthenticated_read_content`. Save, then **Install app**.
3. Copy the **Storefront API access token**. It can only read what your public storefront
   already shows: no orders, no customers, no writes.

In the configs below, replace `your-store.myshopify.com` and `your-storefront-token`.

## Claude Code

```bash
claude mcp add my-store \
  --env SHOPIFY_STORE_DOMAIN=your-store.myshopify.com \
  --env SHOPIFY_STOREFRONT_TOKEN=your-storefront-token \
  -- uvx --from git+https://github.com/thealirazadev/mcp-store-tools mcp-store-tools
```

## Claude Desktop, Cursor, Windsurf, Gemini CLI

Same JSON shape, different file: Claude Desktop `claude_desktop_config.json` (Settings >
Developer > Edit Config), Cursor `.cursor/mcp.json`, Windsurf
`~/.codeium/windsurf/mcp_config.json`, Gemini CLI `~/.gemini/settings.json`.

```json
{
  "mcpServers": {
    "my-store": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/thealirazadev/mcp-store-tools", "mcp-store-tools"],
      "env": {
        "SHOPIFY_STORE_DOMAIN": "your-store.myshopify.com",
        "SHOPIFY_STOREFRONT_TOKEN": "your-storefront-token"
      }
    }
  }
}
```

## VS Code (GitHub Copilot)

`.vscode/mcp.json` uses `servers` instead of `mcpServers`, with the same entry plus
`"type": "stdio"`.

## Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.my-store]
command = "uvx"
args = ["--from", "git+https://github.com/thealirazadev/mcp-store-tools", "mcp-store-tools"]
env = { SHOPIFY_STORE_DOMAIN = "your-store.myshopify.com", SHOPIFY_STOREFRONT_TOKEN = "your-storefront-token" }
```

## Check it works

Restart the agent and ask: "List the store's policies." You should see `list_policies` called.
If the agent reports "rejected the access token", re-copy the Storefront token (not the Admin
API token) and check both scopes are enabled.
