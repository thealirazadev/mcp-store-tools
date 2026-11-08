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

Four tools: `search_products`, `get_product`, `list_policies`, `get_policy`. Each has a narrow
input schema with bounds (`limit` is 1 to 20, handles match `[a-z0-9-]+`). Fewer, well-described
tools are chosen more reliably by models than many overlapping ones.

## 4. Errors are tool results, not crashes

Shopify throttling and 5xx responses are retried with backoff. Anything still failing comes back
as a short error message the model can relay ("the store did not respond"), never a stack trace
or the access token.

## 5. Tests never call Shopify

The GraphQL client takes an `httpx` transport, so tests run against `httpx.MockTransport` with
recorded-shape responses. A separate test drives the real MCP server over an in-memory session
to check the tool list and schemas a client actually sees.
