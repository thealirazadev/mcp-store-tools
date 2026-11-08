"""The bearer-token middleware in front of the HTTP transport."""

from collections.abc import MutableMapping
from typing import Any

import httpx

from mcp_store_tools.http import BearerAuth, Health, RateLimit, build_app


async def ok(scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def test_requests_need_the_exact_bearer_token() -> None:
    transport = httpx.ASGITransport(app=BearerAuth(ok, "s3cret"))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.post("/mcp")
        wrong = await client.post("/mcp", headers={"Authorization": "Bearer nope"})
        right = await client.post("/mcp", headers={"Authorization": "Bearer s3cret"})

    assert missing.status_code == wrong.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert "s3cret" not in missing.text
    assert right.status_code == 200 and right.text == "ok"


async def test_rate_limit_answers_429_with_retry_after_per_client() -> None:
    now = [120.0]
    limited = RateLimit(ok, per_minute=2, clock=lambda: now[0])
    transport = httpx.ASGITransport(app=limited, client=("10.0.0.1", 1))
    other = httpx.ASGITransport(app=limited, client=("10.0.0.2", 1))
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as first,
        httpx.AsyncClient(transport=other, base_url="http://test") as second,
    ):
        codes = [(await first.post("/mcp")).status_code for _ in range(3)]
        blocked = await first.post("/mcp")
        others = (await second.post("/mcp")).status_code
        now[0] = 185.0  # next minute
        again = (await first.post("/mcp")).status_code

    assert codes == [200, 200, 429]
    assert blocked.headers["retry-after"] == "60"
    assert others == 200 and again == 200


async def test_healthz_skips_auth_and_everything_else_does_not() -> None:
    app = Health(BearerAuth(ok, "s3cret"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/healthz")
        mcp = await client.post("/mcp")

    assert health.status_code == 200 and health.json() == {"status": "ok"}
    assert mcp.status_code == 401


def test_build_app_layers_health_rate_limit_and_auth() -> None:
    from mcp_store_tools.server import build_server
    from mcp_store_tools.storefront import Config, Storefront

    server = build_server(Storefront(Config("demo.myshopify.com", "t")))
    app = build_app(server, "127.0.0.1", "s3cret", 60)
    assert isinstance(app, Health) and isinstance(app.app, RateLimit)
    assert isinstance(app.app.app, BearerAuth)
    assert isinstance(build_app(server, "127.0.0.1", None, 0), Health)
