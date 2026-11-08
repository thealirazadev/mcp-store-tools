"""Drive the real MCP server in-process, the way a client such as Claude Desktop sees it."""

import json
import re
from typing import Any

import httpx
import pytest
from mcp import Client
from mcp_types import CallToolResult, TextContent

from mcp_store_tools.server import build_server, config_from_env, main
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
        assert c.server_info and c.server_info.version[0].isdigit()
        tools = {tool.name: tool for tool in (await c.list_tools()).tools}

    assert set(tools) == {
        "search_products",
        "get_product",
        "list_collections",
        "browse_products",
        "list_policies",
        "get_policy",
        "get_recommendations",
        "get_store_info",
        "search_pages",
        "build_cart_link",
        "audit_catalog",
        "find_deals",
        "compare_products",
    }
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


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.strip()[0].isdigit()


def test_demo_store_uses_the_public_sample_endpoint() -> None:
    assert Config("mock.shop", token="").endpoint == "https://mock.shop/api"


async def test_collections_then_browse_pages() -> None:
    async with client() as c:
        collections = payload(await c.call_tool("list_collections", {}))["result"]
        first = payload(await c.call_tool("browse_products", {"sort": "price_low_to_high"}))
        second = payload(await c.call_tool("browse_products", {"cursor": first["next_cursor"]}))
        missing = await c.call_tool("browse_products", {"collection": "hats"})
        bad_sort = await c.call_tool("browse_products", {"sort": "random"})

    assert collections == [
        {
            "handle": "shoes",
            "title": "Shoes",
            "description": "Road and trail shoes.",
            "url": "https://demo.example/collections/shoes",
        }
    ]
    assert first["next_cursor"] == "cursor-1" and second["next_cursor"] is None
    assert first["products"][0]["handle"] == "trail-runner"
    assert missing.is_error and "hats" in text(missing)
    assert bad_sort.is_error


def test_config_reads_country_and_language(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "demo.myshopify.com")
    monkeypatch.setenv("SHOPIFY_STOREFRONT_TOKEN", "t")
    monkeypatch.setenv("SHOPIFY_COUNTRY", "gb")
    monkeypatch.setenv("SHOPIFY_LANGUAGE", "pt-br")
    config = config_from_env()
    assert (config.country, config.language) == ("GB", "PT_BR")

    monkeypatch.setenv("SHOPIFY_COUNTRY", "Britain")
    with pytest.raises(SystemExit, match="SHOPIFY_COUNTRY"):
        config_from_env()


async def test_search_rejects_an_inverted_price_range() -> None:
    async with client() as c:
        result = await c.call_tool(
            "search_products", {"query": "shoe", "min_price": 50, "max_price": 10}
        )
    assert result.is_error and "min_price" in text(result)


async def test_prompts_are_listed_and_name_only_real_tools() -> None:
    async with client() as c:
        prompts = {p.name for p in (await c.list_prompts()).prompts}
        tools = {t.name for t in (await c.list_tools()).tools}
        audit = await c.get_prompt("audit_catalog")
        compare = await c.get_prompt("compare_products", {"products": "Trail Runner, Summit Boot"})
        policy = await c.get_prompt("policy_question", {"question": "Can I return boots?"})

    assert prompts == {"audit_catalog", "compare_products", "policy_question"}
    texts = []
    for result in (audit, compare, policy):
        body = result.messages[0].content
        assert isinstance(body, TextContent)
        texts.append(body.text)
        # Every snake_case word followed by "(" or matching a tool must be a served tool.
        named = set(re.findall(r"\b([a-z]+_[a-z_]+)\(", body.text))
        named |= {w for w in re.findall(r"\b[a-z]+_[a-z]+\b", body.text) if w in tools}
        assert named and named <= tools
    assert "Summit Boot" in texts[1]


def test_ctrl_c_stops_the_server_once_without_a_traceback() -> None:
    import signal
    import subprocess
    import sys
    import time

    server = subprocess.Popen(
        [sys.executable, "-m", "mcp_store_tools.server", "--demo"],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(1.5)
    server.send_signal(signal.SIGINT)
    _, stderr = server.communicate(timeout=5)

    assert server.returncode == 130
    assert "Traceback" not in stderr


def test_main_reports_ctrl_c_and_exits_130(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import mcp_store_tools.server as srv

    def interrupted(*args: object) -> None:
        raise KeyboardInterrupt

    exits: list[int] = []
    monkeypatch.setattr(srv, "run", interrupted)
    monkeypatch.setattr("os._exit", exits.append)
    srv.main(["--demo"])

    assert exits == [130]
    assert "stopped" in capsys.readouterr().err


def test_run_serves_the_chosen_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    import mcp_store_tools.server as srv

    served: list[tuple[Any, ...]] = []

    class FakeServer:
        def run(self, transport: str) -> None:
            served.append(("stdio", transport))

    monkeypatch.setattr(srv, "serve", lambda *args: served.append(("http", *args[1:])))
    monkeypatch.setenv("MCP_AUTH_TOKEN", " s3cret ")
    stdio = argparse.Namespace(transport="stdio")
    http = argparse.Namespace(transport="http", host="127.0.0.1", port=9000)

    srv.run(FakeServer(), CONFIG, stdio)  # type: ignore[arg-type]
    srv.run(FakeServer(), CONFIG, http)  # type: ignore[arg-type]

    assert served == [("stdio", "stdio"), ("http", "127.0.0.1", 9000, "s3cret")]


async def test_recommendations_store_info_and_pages() -> None:
    async with client() as c:
        similar = payload(await c.call_tool("get_recommendations", {"handle": "trail-runner"}))
        missing = await c.call_tool("get_recommendations", {"handle": "nope"})
        bad_kind = await c.call_tool(
            "get_recommendations", {"handle": "trail-runner", "kind": "random"}
        )
        info = payload(await c.call_tool("get_store_info", {}))
        pages = payload(await c.call_tool("search_pages", {"query": "size guide"}))

    assert [p["handle"] for p in similar["result"]] == ["summit-boot"]
    assert missing.is_error and "nope" in text(missing)
    assert bad_kind.is_error
    assert info["name"] == "Northpeak" and info["ships_to"] == ["US", "CA"]
    assert pages["result"][0]["url"] == "https://demo.example/pages/size-guide"


async def test_build_cart_link_merges_lines_and_rejects_bad_items() -> None:
    async with client() as c:
        link = payload(
            await c.call_tool(
                "build_cart_link",
                {
                    "items": [
                        {"handle": "trail-runner", "variant_id": "1001", "quantity": 1},
                        {"handle": "trail-runner", "variant_id": "1001"},
                    ]
                },
            )
        )
        sold_out = await c.call_tool(
            "build_cart_link", {"items": [{"handle": "trail-runner", "variant_id": "1002"}]}
        )
        guessed = await c.call_tool(
            "build_cart_link", {"items": [{"handle": "trail-runner", "variant_id": "abc"}]}
        )
        empty = await c.call_tool("build_cart_link", {"items": []})
        too_many = await c.call_tool(
            "build_cart_link",
            {
                "items": [
                    {"handle": "trail-runner", "variant_id": "1001", "quantity": 99},
                    {"handle": "trail-runner", "variant_id": "1001"},
                ]
            },
        )

    assert link["url"] == "https://demo.example/cart/1001:2"
    assert link["subtotal"] == "178.0 USD"
    assert sold_out.is_error and "Sold out" in text(sold_out)
    assert guessed.is_error and empty.is_error
    assert too_many.is_error and "99" in text(too_many)


async def test_audit_catalog_tool_reports_the_whole_catalog() -> None:
    async with client() as c:
        report = payload(await c.call_tool("audit_catalog", {"examples_per_check": 1}))

    assert report["products_checked"] == 2 and report["truncated"] is False
    # The fake catalog serves the trail runner, then the sold-out summit boot.
    assert report["checks"]["sold_out"]["count"] == 1
    assert report["checks"]["sold_out"]["examples"][0]["handle"] == "summit-boot"
    assert report["checks"]["duplicate_title"]["count"] == 0
    assert report["checks"]["short_description"]["count"] == 2
    assert report["top_issues"] == ["short_description", "sold_out"]


async def test_deals_compare_options_and_country_through_mcp() -> None:
    async with client() as c:
        deals = payload(await c.call_tool("find_deals", {"min_percent_off": 10}))
        missing = await c.call_tool("find_deals", {"collection": "hats"})
        compared = payload(
            await c.call_tool("compare_products", {"handles": ["trail-runner", "summit-boot"]})
        )
        one = await c.call_tool("compare_products", {"handles": ["trail-runner"]})
        sized = payload(
            await c.call_tool("search_products", {"query": "shoe", "options": {"Size": "US 9"}})
        )
        bad_country = await c.call_tool(
            "get_product", {"handle": "trail-runner", "country": "Britain"}
        )

    assert [p["handle"] for p in deals["deals"]] == ["summit-boot"]
    assert missing.is_error and "hats" in text(missing)
    assert len(compared["products"]) == 2 and "price" in compared["differs_in"]
    assert one.is_error
    assert sized["result"][0]["matching_variants"][0]["options"] == {"Size": "US 9"}
    assert bad_country.is_error
