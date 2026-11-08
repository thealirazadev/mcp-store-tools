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
