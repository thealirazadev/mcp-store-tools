"""Streamable HTTP serving, with an optional bearer token for hosted deployments."""

import hmac
import json
import time
from collections.abc import Callable, MutableMapping
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer

Scope = MutableMapping[str, Any]


class BearerAuth:
    """ASGI middleware: every HTTP request must carry ``Authorization: Bearer <token>``."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            given = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(given, self.expected):
                challenge = [(b"www-authenticate", b"Bearer")]
                await _reply(send, 401, {"error": "unauthorized"}, challenge)
                return
        await self.app(scope, receive, send)


async def _reply(send: Any, status: int, body: dict[str, Any], headers: list[Any]) -> None:
    payload = json.dumps(body).encode()
    start = [(b"content-type", b"application/json"), *headers]
    await send({"type": "http.response.start", "status": status, "headers": start})
    await send({"type": "http.response.body", "body": payload})


class RateLimit:
    """Per-client fixed one-minute window, so one caller cannot spend the store's Storefront
    API quota for everyone. Clients are identified by address (behind a proxy, run uvicorn's
    proxy headers so this is the real client)."""

    def __init__(
        self, app: Any, per_minute: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.app = app
        self.per_minute = per_minute
        self.clock = clock
        self.windows: dict[str, tuple[int, int]] = {}

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            client = (scope.get("client") or ("unknown", 0))[0]
            minute = int(self.clock() // 60)
            start, count = self.windows.get(client, (minute, 0))
            if start != minute:
                start, count = minute, 0
            if count >= self.per_minute:
                wait = str(60 - int(self.clock() % 60)).encode()
                await _reply(send, 429, {"error": "rate limited"}, [(b"retry-after", wait)])
                return
            self.windows[client] = (start, count + 1)
            if len(self.windows) > 10_000:  # forget old windows so memory stays bounded
                self.windows = {k: v for k, v in self.windows.items() if v[0] == minute}
        await self.app(scope, receive, send)


class Health:
    """GET /healthz answers without authentication, for load balancers and Docker."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"] == "/healthz":
            await _reply(send, 200, {"status": "ok"}, [])
            return
        await self.app(scope, receive, send)


def build_app(server: MCPServer, host: str, token: str | None, per_minute: int) -> Any:
    app: Any = server.streamable_http_app(host=host)
    if token:
        app = BearerAuth(app, token)
    if per_minute > 0:
        app = RateLimit(app, per_minute)
    return Health(app)


def serve(server: MCPServer, host: str, port: int, token: str | None, per_minute: int) -> None:
    uvicorn.run(build_app(server, host, token, per_minute), host=host, port=port, log_level="info")
