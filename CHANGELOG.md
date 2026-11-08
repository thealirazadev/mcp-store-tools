# Changelog

## 0.4.3 - 2026-11-09

- `search_products` falls back to the keyword product query when Storefront search returns
  nothing, which happens on stores whose search index is empty. Found against a real
  development store.
- Opt-in live tests against a real store (search, variants, collections, policies) and the
  wrong-token error; the weekly live workflow runs them when the store is configured.

## 0.4.2 - 2026-11-09

- Shopify's reason for a 4xx response is passed to the model (for example "Online Store channel
  is locked" for a store that has not opened yet) instead of a bare status code. Found by
  testing against a real trial store.

## 0.4.1 - 2026-11-09

- Ctrl+C stops the server on the first press with exit code 130, without tracebacks.

## 0.4.0 - 2026-11-08

- `search_products` filters by `min_price`, `max_price`, and `in_stock_only`, using Shopify's
  storefront `search` API; results are re-checked against the filters.
- Products include their `image`; variants include their `options` (Size, Color, ...).
- MCP prompts: audit the catalog, compare products, answer a policy question.
- `MCP_AUTH_TOKEN` requires a bearer token on every HTTP request.
- Test coverage is measured in CI and must stay at or above 95%.

## 0.3.0 - 2026-11-08

- Streamable HTTP transport (`--transport http`, `--host`, `--port`) and a Dockerfile for hosting.
- `SHOPIFY_COUNTRY` and `SHOPIFY_LANGUAGE`: local currency, prices, and translations.
- Honour Shopify's `Retry-After` header (capped at 10 seconds).
- `get_product` returns up to 100 variants (was 50) and flags more with `more_variants`.
- Policies and collections are cached for five minutes; products stay live.
- Default Storefront API version is now `2026-10`.
- `serverInfo` reports the package version and website.
- MCP Registry metadata (`server.json`), SECURITY.md, CONTRIBUTING.md, Docker build in CI.

## 0.2.0 - 2026-11-08

- `list_collections` and `browse_products` tools: sorted (price, newest, best selling, title),
  paged browsing of the whole catalog or one collection.
- `--demo` runs against Shopify's public sample store (mock.shop), no credentials needed.
- `--version` flag.
- `SHOPIFY_STORE_DOMAIN` accepts a pasted URL (`https://shop.myshopify.com/admin`) and rejects
  values that are not domains, with a clear message.
- `shopify-store-assistant` agent skill (`skill/`), released as a ZIP with each version.
- CI on Python 3.11 and 3.13, a weekly end-to-end check against the demo store, and tagged
  releases.

## 0.1.0 - 2026-11-07

- Read-only Storefront API client with retries on throttling and server errors.
- `search_products`, `get_product`, `list_policies`, `get_policy` tools over stdio.
