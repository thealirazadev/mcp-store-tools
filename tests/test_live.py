"""End-to-end against Shopify's public sample store over stdio. Opt in: MCP_STORE_TOOLS_LIVE=1."""

import os
import sys
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.skipif(
    os.environ.get("MCP_STORE_TOOLS_LIVE") != "1", reason="set MCP_STORE_TOOLS_LIVE=1 to run"
)


async def test_demo_store_answers_every_tool_over_stdio() -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_store_tools.server", "--demo"]
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        async def call(name: str, **arguments: Any) -> Any:
            result = await session.call_tool(name, arguments)
            assert not result.is_error, result.content
            return result.structured_content

        found = (await call("search_products", query="shirt", limit=3))["result"]
        assert found and all(p["handle"] and p["price"] for p in found)
        product = await call("get_product", handle=found[0]["handle"])
        assert product["variants"]
        handle = (await call("list_collections"))["result"][0]["handle"]
        cheap = await call("browse_products", collection=handle, sort="price_low_to_high", limit=2)
        assert cheap["products"]
        if cheap["next_cursor"]:
            more = await call(
                "browse_products",
                collection=handle,
                sort="price_low_to_high",
                cursor=cheap["next_cursor"],
            )
            assert more["products"]
        kinds = {p["kind"] for p in (await call("list_policies"))["result"]}
        assert "refund" in kinds
        policy = await call("get_policy", kind="refund")
        assert "<" not in policy["text"]


async def test_demo_store_over_streamable_http() -> None:
    import asyncio
    import socket

    from mcp.client.streamable_http import streamable_http_client

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "mcp_store_tools.server",
        "--demo",
        "--transport",
        "http",
        "--port",
        str(port),
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                break
            except OSError:
                await asyncio.sleep(0.2)
        url = f"http://127.0.0.1:{port}/mcp"
        async with (
            streamable_http_client(url) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool("search_products", {"query": "shirt"})
            assert not result.is_error and result.structured_content
    finally:
        server.terminate()
        await server.wait()
