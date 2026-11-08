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

1. Install Shopify's free **Headless** sales channel from the App Store
   (apps.shopify.com/headless) on your store.
2. In the store admin: **Headless > Create storefront**, then **Storefront API**.
3. Copy the **public access token**. It can only read what your storefront shows: no orders,
   no customers, no writes. (A trial store's storefront stays locked until a plan is chosen.)

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

## Optional settings

Add these to `env` next to the two required values:

- `SHOPIFY_COUNTRY`: ISO country code (`GB`, `DE`) for that market's currency and prices.
- `SHOPIFY_LANGUAGE`: language code (`FR`, `PT_BR`) for translated product text.

## Hosted (HTTP) instead of local

If someone runs the server for you with `--transport http` or the Docker image, connect to its URL
instead of starting it locally, for example in Claude Code:
`claude mcp add --transport http my-store https://your-host/mcp`. If the host set
`MCP_AUTH_TOKEN`, add `--header "Authorization: Bearer <token>"`.

## Check it works

Restart the agent and ask: "List the store's policies." You should see `list_policies` called.
If the agent reports "rejected the access token", re-copy the Storefront token (not the Admin
API token) and check both scopes are enabled.
