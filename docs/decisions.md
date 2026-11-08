# Design decisions

## 1. Storefront API, not Admin API

The server talks to Shopify's Storefront GraphQL API with a Storefront access token. That token
can only read what the public storefront already shows: published products, prices, availability,
and shop policies. It cannot read orders or customers and cannot write anything, so "read-only"
is a property of the credential, not a promise in the code. An Admin API token with read scopes
would also expose orders and customer data to whatever model calls the tools.

## 2. Store content is untrusted

Product descriptions and policy text are written by merchants, apps, and sometimes copied from
suppliers. A description can contain text aimed at the model ("ignore previous instructions").
The server cannot stop a model from reading it, but it does three things:

- returns plain text, with HTML stripped, so hidden markup cannot carry instructions;
- caps the length of every text field, so one product cannot flood the context;
- says in each tool description that the returned content is store data, not instructions.

## 3. Small, typed tool surface

Six tools: `search_products`, `get_product`, `list_collections`, `browse_products`,
`list_policies`, `get_policy`. Each has a narrow input schema with bounds (`limit` is 1 to 20 for
search and 1 to 50 for browsing, handles match `[a-z0-9-]+`, `sort` is an enum). Fewer,
well-described tools are chosen more reliably by models than many overlapping ones.

`browse_products` was added in 0.2.0 because keyword search cannot answer "cheapest", "newest",
or "everything in this category", and cannot walk the whole catalog for an audit. It exposes
Shopify's cursor as an opaque string. A cursor is only valid for the sort and collection that
produced it, so the tool description says so; otherwise models reuse it with a new sort.

## 4. Errors are tool results, not crashes

Shopify throttling and 5xx responses are retried with backoff. Anything still failing comes back
as a short error message the model can relay ("the store did not respond"), never a stack trace
or the access token.

## 5. Tests never call Shopify

The GraphQL client takes an `httpx` transport, so tests run against `httpx.MockTransport` with
recorded-shape responses. A separate test drives the real MCP server over an in-memory session
to check the tool list and schemas a client actually sees.

## 6. A demo store for trying it without credentials

`--demo` points the client at mock.shop, Shopify's public sample store, which serves real
Storefront API responses without a token. People can try the server and the skill before
creating a Shopify app, and the weekly `live` workflow uses it as an end-to-end check. It runs on
a schedule, not on every push, so an outage of a third-party demo store never blocks a merge.

## 7. The skill ships with the server

The `skill/shopify-store-assistant` folder teaches an agent when to call which tool, how to
answer (cite URLs, never invent prices), and how to run a catalog audit. It lives in this repo,
and a test checks it names exactly the tools the server serves, so the two cannot drift apart.

## 8. Up to 100 variants, flagged beyond that

Shopify allows up to 2,048 variants per product. Returning them all would flood the model's
context for one product, so `get_product` returns the first 100 and sets `more_variants` when
there are more. The skill tells the model not to call an unseen variant "missing".

## 9. Cache what rarely changes, never stock

Policies and collections are cached in memory for five minutes per process: `get_policy` and
`list_policies` share one fetch. Products, prices, and availability are never cached, because an
answer about stock must be true now.

## 10. Local markets through `@inContext`

`SHOPIFY_COUNTRY` and `SHOPIFY_LANGUAGE` are sent with every query through Storefront's
`@inContext` directive, so stores using Shopify Markets return that market's currency, prices,
and translations. They are validated at startup, because Shopify rejects an invalid enum value
with a GraphQL error on every call.

## 11. Streamable HTTP is opt-in and unauthenticated

stdio stays the default: local clients start the server themselves and nothing listens on a
port. `--transport http` exists for hosting one store for several agents. It binds to localhost
unless told otherwise and adds no authentication of its own: the data is public storefront data,
and TLS plus access control belong in the reverse proxy in front of it (SECURITY.md).

## 12. Search filters are enforced twice

`search_products` sends price and stock filters to Shopify's `search` API, and then checks every
result against the same bounds before returning it. A store (or the demo store) that ignores a
filter would otherwise let the model quote a $40 product to a shopper who asked for under $30.
Search moved from `products(query:)` to `search`, which is Shopify's storefront search with typo
tolerance and is the only one that accepts `productFilters`.

## 13. Prompts carry the workflows for clients without skills

Not every client loads `SKILL.md`. The server also exposes three MCP prompts (catalog audit,
product comparison, policy question) with the core of those workflows, and a test checks that
each prompt names only tools the server actually serves.

## 14. Optional bearer token, no TLS in process

`MCP_AUTH_TOKEN` adds a small ASGI middleware that rejects any HTTP request without the exact
bearer token (constant-time comparison, 401 with `WWW-Authenticate`). A full OAuth flow would be
heavy for public catalog data; a shared token is enough to stop a hosted server becoming an open
proxy to the store's API quota. TLS stays the reverse proxy's job.

## 15. Search falls back to the keyword product query

Tested against a real development store, Storefront `search` returned 0 results for
"snowboard" while `products(query: "snowboard")` listed the store's snowboards: the search index
of a new store (or of a channel) can be empty. When `search` returns nothing, the client runs the
`products` keyword query and applies the same price and stock checks to its results, so a
shopper is never told a product does not exist because of indexing.

## 16. Cart links, not carts

Shoppers' last step is "I'll take it". `build_cart_link` returns a Shopify cart permalink
(`/cart/<variant>:<qty>`) instead of creating a cart through the API, which would need a write
scope and make the server more than read-only. Each item carries the product handle, and the
server checks the variant belongs to that product and is in stock before building the link, so a
hallucinated or sold-out variant is an error, not a broken checkout. (Lookup by global id was
tried first; Shopify's sample store does not resolve `nodes(ids:)`, while `product(handle:)`
works everywhere.)

## 17. The catalog audit runs in the server

Walking a catalog through `browse_products` costs the model one call and thousands of tokens per
50 products. `audit_catalog` walks up to 5,000 products server-side and returns only counts,
percentages, and a few examples per check. When no product has an online store URL the cause is
store-wide, so it is reported once as a note and kept out of the top issues.

## 18. More tools, each answering a question shoppers ask

Recommendations, store info (shipping countries, payment methods, markets), and content pages
(size guides, FAQs) were added in 0.5.0 because they are the most common questions an agent
could not answer from products and policies alone. Each has a narrow schema and a description
that says when to use it; the skill maps shopper questions to tools so the larger tool list does
not make selection worse.
