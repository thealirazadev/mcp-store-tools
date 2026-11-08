"""End-to-end against Shopify's public sample store over stdio. Opt in: MCP_STORE_TOOLS_LIVE=1."""

import os
import sys
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp_types import TextContent

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
        capped = (await call("search_products", query="shirt", max_price=30))["result"]
        assert all(float(p["price"].split()[0]) <= 30 for p in capped)
        product = await call("get_product", handle=found[0]["handle"])
        assert product["variants"] and product["variants"][0]["options"]
        assert product["image"].startswith("https://")
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

        info = await call("get_store_info")
        assert info["name"] and info["currency"] and info["url"].startswith("https://")
        assert (await call("get_recommendations", handle=product["handle"]))["result"]
        assert (await call("search_pages", query="contact"))["result"]
        variant = next(v for v in product["variants"] if v["available"])
        link = await call(
            "build_cart_link",
            items=[{"handle": product["handle"], "variant_id": variant["id"], "quantity": 2}],
        )
        assert link["url"].endswith(f"/cart/{variant['id']}:2") and link["subtotal"]
        report = await call("audit_catalog", max_products=100, examples_per_check=1)
        assert report["products_checked"] > 0 and set(report["checks"]) >= {"sold_out"}


async def test_demo_store_over_streamable_http_with_a_bearer_token() -> None:
    import asyncio
    import socket

    import httpx
    import httpx2
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
        env={**os.environ, "MCP_AUTH_TOKEN": "live-test-token"},
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
        async with httpx.AsyncClient() as anonymous:
            assert (await anonymous.post(url, json={})).status_code == 401
        authorised = httpx2.AsyncClient(
            headers={"Authorization": "Bearer live-test-token"}, timeout=30
        )
        async with (
            authorised,
            streamable_http_client(url, http_client=authorised) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool("search_products", {"query": "shirt"})
            assert not result.is_error and result.structured_content
    finally:
        server.terminate()
        await server.wait()


real_store = pytest.mark.skipif(
    not os.environ.get("SHOPIFY_STORE_DOMAIN") or not os.environ.get("SHOPIFY_STOREFRONT_TOKEN"),
    reason="set SHOPIFY_STORE_DOMAIN and SHOPIFY_STOREFRONT_TOKEN for a real store",
)


def _real_store_params(token: str) -> StdioServerParameters:
    env = {**os.environ, "SHOPIFY_STOREFRONT_TOKEN": token}
    return StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_store_tools.server"], env=env
    )


@real_store
async def test_real_store_with_a_storefront_token() -> None:
    params = _real_store_params(os.environ["SHOPIFY_STOREFRONT_TOKEN"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        async def call(name: str, **arguments: Any) -> Any:
            result = await session.call_tool(name, arguments)
            assert not result.is_error, result.content
            return result.structured_content

        page = await call("browse_products", sort="title", limit=5)
        assert page["products"], "the test store needs published products"
        first = page["products"][0]
        # Search must find a product by a word of its own title, even on a fresh store whose
        # search index is empty (the keyword fallback).
        word = max(first["title"].split(), key=len)
        found = (await call("search_products", query=word, limit=20))["result"]
        assert first["handle"] in {p["handle"] for p in found}
        product = await call("get_product", handle=first["handle"])
        assert product["variants"] and "options" in product["variants"][0]
        await call("list_collections")
        await call("list_policies")
        await call("get_store_info")
        await call("get_recommendations", handle=first["handle"])
        await call("search_pages", query="contact")
        report = await call("audit_catalog", max_products=200)
        assert report["products_checked"] > 0
        in_stock = next((v for v in product["variants"] if v["available"]), None)
        if in_stock:
            item = {"handle": first["handle"], "variant_id": in_stock["id"]}
            link = await call("build_cart_link", items=[item])
            assert f"/cart/{in_stock['id']}:1" in link["url"]


@real_store
async def test_real_store_rejects_a_wrong_token() -> None:
    params = _real_store_params("0" * 32)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("search_products", {"query": "shirt"})
    assert result.is_error
    first = result.content[0]
    assert isinstance(first, TextContent) and "rejected the access token" in first.text
