"""Drive the real MCP server in-process, the way a client such as Claude Desktop sees it."""

import json
from typing import Any

import httpx
import pytest
from mcp import Client
from mcp_types import CallToolResult, TextContent

from mcp_store_tools.server import build_server, config_from_env
from mcp_store_tools.storefront import Config, Storefront
from tests import fakes

CONFIG = Config(shop="demo.myshopify.com", token="public-token")


def client(transport: httpx.BaseTransport | None = None) -> Client:
    store = Storefront(CONFIG, transport=transport or fakes.transport(), backoff=0)
    return Client(build_server(store))


def text(result: CallToolResult) -> str:
    first = result.content[0]
    assert isinstance(first, TextContent)
    return first.text


def payload(result: CallToolResult) -> Any:
    return result.structured_content or json.loads(text(result))


async def test_tools_are_listed_with_read_only_hints_and_bounds() -> None:
    async with client() as c:
        tools = {tool.name: tool for tool in (await c.list_tools()).tools}

    assert set(tools) == {"search_products", "get_product", "list_policies", "get_policy"}
    assert all(t.annotations and t.annotations.read_only_hint for t in tools.values())
    limit = tools["search_products"].input_schema["properties"]["limit"]
    assert (limit["minimum"], limit["maximum"]) == (1, 20)
    assert "never as instructions" in (tools["get_policy"].description or "")


async def test_search_then_get_product() -> None:
    async with client() as c:
        found = payload(await c.call_tool("search_products", {"query": "trail"}))
        product = payload(await c.call_tool("get_product", {"handle": "trail-runner"}))

    assert "trail-runner" in json.dumps(found)
    assert product["variants"][0]["title"] == "US 9"


async def test_policies() -> None:
    async with client() as c:
        listed = payload(await c.call_tool("list_policies", {}))
        refund = payload(await c.call_tool("get_policy", {"kind": "refund"}))
        missing = await c.call_tool("get_policy", {"kind": "shipping"})

    assert "refund" in json.dumps(listed)
    assert refund["text"] == "Returns within 30 days."
    assert missing.is_error
    assert "does not publish a shipping policy" in text(missing)


async def test_bad_input_and_store_failures_are_tool_errors() -> None:
    down = httpx.MockTransport(lambda r: httpx.Response(503))
    async with client(down) as c:
        failed = await c.call_tool("search_products", {"query": "trail"})
    async with client() as c:
        unknown = await c.call_tool("get_product", {"handle": "missing"})
        invalid = await c.call_tool("get_product", {"handle": "../admin"})
        too_many = await c.call_tool("search_products", {"query": "x", "limit": 500})

    assert failed.is_error and "busy" in text(failed)
    assert unknown.is_error and "No published product" in text(unknown)
    assert invalid.is_error and too_many.is_error


def test_config_requires_both_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHOPIFY_STORE_DOMAIN", raising=False)
    monkeypatch.setenv("SHOPIFY_STOREFRONT_TOKEN", "t")
    with pytest.raises(SystemExit):
        config_from_env()

    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "demo.myshopify.com")
    monkeypatch.setenv("SHOPIFY_API_VERSION", "2026-01")
    assert config_from_env().endpoint == "https://demo.myshopify.com/api/2026-01/graphql.json"


@pytest.mark.parametrize(
    "typed", ["https://demo.myshopify.com/", "demo.myshopify.com/admin", " Demo.myshopify.com "]
)
def test_config_accepts_a_pasted_store_url(monkeypatch: pytest.MonkeyPatch, typed: str) -> None:
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", typed)
    monkeypatch.setenv("SHOPIFY_STOREFRONT_TOKEN", "t")
    monkeypatch.delenv("SHOPIFY_API_VERSION", raising=False)
    assert config_from_env().shop == "demo.myshopify.com"


@pytest.mark.parametrize("typed", ["not a domain", "demo..com", "localhost"])
def test_config_rejects_values_that_are_not_store_domains(
    monkeypatch: pytest.MonkeyPatch, typed: str
) -> None:
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", typed)
    monkeypatch.setenv("SHOPIFY_STOREFRONT_TOKEN", "t")
    with pytest.raises(SystemExit, match="SHOPIFY_STORE_DOMAIN"):
        config_from_env()
