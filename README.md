# mcp-store-tools

<!-- mcp-name: io.github.thealirazadev/mcp-store-tools -->

[![ci](https://github.com/thealirazadev/mcp-store-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/thealirazadev/mcp-store-tools/actions/workflows/ci.yml)
[![live](https://github.com/thealirazadev/mcp-store-tools/actions/workflows/live.yml/badge.svg)](https://github.com/thealirazadev/mcp-store-tools/actions/workflows/live.yml)
![coverage](https://img.shields.io/badge/coverage-%E2%89%A595%25-brightgreen)
![python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants such as Claude answer
questions about a Shopify store from its live catalog and policies. It is read-only by
construction: it uses a Storefront API token, which can only see what the public storefront
already shows.

```
Claude Desktop / any MCP client ──stdio──► mcp-store-tools ──HTTPS──► Shopify Storefront API
                                              6 read-only tools          (public token)
```

Ask your agent "what's your cheapest rain jacket in medium?", "can I return sale items?", or
"audit our catalog for listing problems" and it answers from the live store, with links.

## Try it in 30 seconds

No Shopify account needed: `--demo` uses Shopify's public sample store.

```bash
claude mcp add store-demo -- uvx --from git+https://github.com/thealirazadev/mcp-store-tools mcp-store-tools --demo
```

Then ask Claude Code: "What's the cheapest thing in the store, and what's the refund policy?"

## Tools

| Tool | Input | Returns |
| --- | --- | --- |
| `search_products` | `query`, `limit` (1 to 20, default 5) | title, handle, price range, availability, URL |
| `get_product` | `handle` | the product plus each variant's price and availability |
| `list_collections` | none | the store's collections (categories) with handles |
| `browse_products` | `collection`, `sort`, `limit` (1 to 50), `cursor` | one sorted page of products and `next_cursor` |
| `list_policies` | none | which policies the store publishes, with URLs |
| `get_policy` | `kind`: refund, shipping, privacy, terms, subscription | the policy as plain text |

Every tool carries the MCP `readOnlyHint`, and failures come back as tool errors the model can
relay ("the store is busy, try again shortly"), never stack traces or the token.

## Setup

1. In Shopify admin, open **Settings > Apps and sales channels > Develop apps**, create an app,
   and enable the Storefront API with `unauthenticated_read_product_listings` and
   `unauthenticated_read_content`. Install it and copy the **Storefront API access token**.
2. Install the server: `uv tool install git+https://github.com/thealirazadev/mcp-store-tools`
   (or run it without installing via `uvx --from git+https://github.com/thealirazadev/mcp-store-tools mcp-store-tools`).

### Claude Desktop

Add this to `claude_desktop_config.json` (Settings > Developer > Edit Config), then restart:

```json
{
  "mcpServers": {
    "my-store": {
      "command": "mcp-store-tools",
      "env": {
        "SHOPIFY_STORE_DOMAIN": "your-store.myshopify.com",
        "SHOPIFY_STOREFRONT_TOKEN": "your-storefront-token"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add my-store --env SHOPIFY_STORE_DOMAIN=your-store.myshopify.com \
  --env SHOPIFY_STOREFRONT_TOKEN=your-storefront-token -- mcp-store-tools
```

### Configuration

| Variable | Required | Meaning |
| --- | --- | --- |
| `SHOPIFY_STORE_DOMAIN` | yes | `your-store.myshopify.com` (a pasted URL is fine) |
| `SHOPIFY_STOREFRONT_TOKEN` | yes | Storefront API access token |
| `SHOPIFY_COUNTRY` | no | ISO country code (`GB`): prices and currency for that market |
| `SHOPIFY_LANGUAGE` | no | Language code (`FR`, `PT_BR`): translated titles and descriptions |
| `SHOPIFY_API_VERSION` | no | Storefront API version, default `2026-10` |

Policies and collections are cached for five minutes; products, prices, and stock are always
fetched live. Shopify throttling is retried with backoff, honouring `Retry-After`.

### Cursor, VS Code, Windsurf, Codex, Gemini CLI

Config snippets for each are in
[skill/shopify-store-assistant/reference/setup.md](skill/shopify-store-assistant/reference/setup.md).

### Hosting over HTTP

`--transport http` serves MCP Streamable HTTP at `/mcp` (default `127.0.0.1:8000`; `--host`,
`--port`, or `PORT`). The Docker image runs in this mode:

```bash
docker build -t mcp-store-tools .
docker run -p 8000:8000 -e SHOPIFY_STORE_DOMAIN=your-store.myshopify.com \
  -e SHOPIFY_STOREFRONT_TOKEN=your-storefront-token mcp-store-tools
claude mcp add --transport http my-store http://localhost:8000/mcp
```

HTTP mode has no authentication of its own; read [SECURITY.md](SECURITY.md) before exposing it.

## Agent skill

[skill/shopify-store-assistant](skill/shopify-store-assistant/SKILL.md) is an agent skill that
teaches the model how to use the tools well: which tool answers which question, to quote only
prices and policies the store returned, to link its sources, and how to run a full catalog audit
(missing descriptions, sold-out products, suspicious price ranges, duplicate titles) as a report
for the merchant.

- Claude Code: copy the folder to `~/.claude/skills/` (or `.claude/skills/` in a project).
- Other agents that read `SKILL.md`: copy it to the agent's skills folder.
- Each [release](https://github.com/thealirazadev/mcp-store-tools/releases) attaches the folder
  as `shopify-store-assistant-skill.zip`.

## Safety

Product descriptions and policies are written by merchants and suppliers, so the server treats
them as untrusted:

- HTML is stripped to plain text (script and style contents are dropped), so markup cannot hide
  instructions.
- Every text field is length-capped, so one product cannot flood the model's context.
- Tool descriptions and the server instructions tell the model the content is data to quote,
  not instructions to follow.

The design reasoning is in [docs/decisions.md](docs/decisions.md); the security model and how to
report a vulnerability are in [SECURITY.md](SECURITY.md).

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

The default tests never call Shopify: the GraphQL client runs against `httpx.MockTransport`, the
server tests connect a real MCP client in-process to check the tool list, schemas, and error
results a client actually sees, and a skill test checks `SKILL.md` names exactly the served tools.

`MCP_STORE_TOOLS_LIVE=1 uv run pytest tests/test_live.py` starts the server in demo mode over
stdio and over HTTP and calls the tools against the real Storefront API. CI runs it weekly, and
builds the Docker image on every push. See [CONTRIBUTING.md](CONTRIBUTING.md).

Releases: bump `version` in `pyproject.toml`, add a `CHANGELOG.md` entry, and push a `v*` tag.

## License

MIT
