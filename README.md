# mcp-store-tools

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants such as Claude answer
questions about a Shopify store from its live catalog and policies. It is read-only by
construction: it uses a Storefront API token, which can only see what the public storefront
already shows.

```
Claude Desktop / any MCP client ──stdio──► mcp-store-tools ──HTTPS──► Shopify Storefront API
                                              4 read-only tools          (public token)
```

## Tools

| Tool | Input | Returns |
| --- | --- | --- |
| `search_products` | `query`, `limit` (1 to 20, default 5) | title, handle, price range, availability, URL |
| `get_product` | `handle` | the product plus each variant's price and availability |
| `list_policies` | none | which policies the store publishes, with URLs |
| `get_policy` | `kind`: refund, shipping, privacy, terms, subscription | the policy as plain text |

Every tool carries the MCP `readOnlyHint`, and failures come back as tool errors the model can
relay ("the store is busy, try again shortly"), never stack traces or the token.

## Setup

1. In Shopify admin, open **Settings > Apps and sales channels > Develop apps**, create an app,
   and enable the Storefront API with `unauthenticated_read_product_listings` and
   `unauthenticated_read_content`. Install it and copy the **Storefront API access token**.
2. Install the server: `uv tool install git+https://github.com/thealirazadev/mcp-store-tools`
   (or clone the repo and run `uv sync`).

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

`SHOPIFY_API_VERSION` is optional (default `2025-10`).

## Safety

Product descriptions and policies are written by merchants and suppliers, so the server treats
them as untrusted:

- HTML is stripped to plain text (script and style contents are dropped), so markup cannot hide
  instructions.
- Every text field is length-capped, so one product cannot flood the model's context.
- Tool descriptions and the server instructions tell the model the content is data to quote,
  not instructions to follow.

The design reasoning is in [docs/decisions.md](docs/decisions.md).

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Tests never call Shopify: the GraphQL client runs against `httpx.MockTransport`, and the server
tests connect a real MCP client to the server in-process to check the tool list, schemas, and
error results a client actually sees.

## License

MIT
