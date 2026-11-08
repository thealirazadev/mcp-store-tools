"""The bearer-token middleware in front of the HTTP transport."""

from collections.abc import MutableMapping
from typing import Any

import httpx

from mcp_store_tools.http import BearerAuth


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
