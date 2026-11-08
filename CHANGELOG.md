# Changelog

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
