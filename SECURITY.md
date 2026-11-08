# Security

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub:
**Security > Report a vulnerability** on this repository. Do not open a public issue. You will get
a reply within five working days, and a fix or mitigation plan once the report is confirmed.

Supported: the latest release. Older versions do not get fixes.

## Security model

- **Read-only by credential.** The server uses a Shopify Storefront API token, which can only read
  what the public storefront shows. It cannot see orders, customers, or drafts, and cannot write
  anything. Never configure it with an Admin API token.
- **Store content is untrusted.** Product and policy text is stripped of HTML, length-capped, and
  labelled as data in every tool description, to limit prompt injection through listings
  ([docs/decisions.md](docs/decisions.md), decision 2).
- **No secrets in output.** Errors returned to the model never include the token, request URLs, or
  stack traces.
- **HTTP mode has no authentication.** `--transport http` binds to `127.0.0.1` by default. The data
  it serves is already public, but if you expose it beyond localhost, put it behind a reverse
  proxy that handles TLS and authentication. The Docker image binds `0.0.0.0` inside the
  container and relies on your deployment for this.
