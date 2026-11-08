"""Streamable HTTP serving, with an optional bearer token for hosted deployments."""

import hmac
import json
from collections.abc import MutableMapping
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
                body = json.dumps({"error": "unauthorized"}).encode()
                headers = [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                ]
                await send({"type": "http.response.start", "status": 401, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def serve(server: MCPServer, host: str, port: int, token: str | None) -> None:
    app: Any = server.streamable_http_app(host=host)
    if token:
        app = BearerAuth(app, token)
    uvicorn.run(app, host=host, port=port, log_level="info")
