"""A 5,000-product catalog with Shopify throttling: the whole-catalog tools must finish,
count correctly, honour Retry-After, and stay fast."""

import json
import time
from typing import Any

import httpx

from mcp_store_tools.audit import audit
from mcp_store_tools.storefront import Config, Storefront

TOTAL = 5000


def product(i: int) -> dict[str, Any]:
    price = "50.0"
    return {
        "handle": f"p-{i}",
        "title": f"Product {i:05d}" if i % 100 else "Duplicate",
        "description": "x" * (150 if i % 10 else 20),  # every 10th is short
        "vendor": "V",
        "productType": "T",
        "tags": ["t"],
        "onlineStoreUrl": f"https://demo.example/products/p-{i}",
        "availableForSale": i % 25 != 0,  # every 25th sold out
        "featuredImage": {"url": "https://cdn.example/i.jpg"},
        "priceRange": {
            "minVariantPrice": {"amount": price, "currencyCode": "USD"},
            "maxVariantPrice": {"amount": price, "currencyCode": "USD"},
        },
        # every 50th on sale at 50% off
        "compareAtPriceRange": {
            "maxVariantPrice": {"amount": "100.0" if i % 50 == 0 else "0", "currencyCode": "USD"}
        },
    }


def catalog_transport(requests: list[int], throttle_every: int) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(1)
        if len(requests) % throttle_every == 0:
            return httpx.Response(429, headers={"Retry-After": "1"})
        variables = json.loads(request.content)["variables"]
        start = int(variables["after"] or 0)
        end = min(start + variables["first"], TOTAL)
        info = {"hasNextPage": end < TOTAL, "endCursor": str(end)}
        nodes = [product(i) for i in range(start, end)]
        return httpx.Response(200, json={"data": {"products": {"nodes": nodes, "pageInfo": info}}})

    return httpx.MockTransport(handle)


def test_audit_and_deals_over_5000_products_with_throttling() -> None:
    requests: list[int] = []
    waits: list[float] = []
    store = Storefront(
        Config("demo.myshopify.com", "t"),
        transport=catalog_transport(requests, throttle_every=7),
        sleep=waits.append,
    )

    started = time.perf_counter()
    walked = store.all_products(TOTAL)
    elapsed = time.perf_counter() - started

    assert walked is not None
    products, truncated = walked
    assert len(products) == TOTAL and truncated is False
    assert len({p["handle"] for p in products}) == TOTAL  # no page lost or repeated
    assert waits and set(waits) == {1.0}  # throttled pages retried after Retry-After
    assert elapsed < 10, f"walking 5,000 products took {elapsed:.1f}s"

    report = audit(products, truncated, 3)
    assert report["checks"]["short_description"]["count"] == TOTAL // 10
    assert report["checks"]["sold_out"]["count"] == TOTAL // 25
    assert report["checks"]["duplicate_title"]["count"] == TOTAL // 100

    deals = store.deals(None, 0, 10, TOTAL)
    assert deals is not None
    assert deals["on_sale_count"] == TOTAL // 50
    assert all(d["percent_off"] == 50 for d in deals["deals"])


def test_the_product_cap_stops_the_walk_early() -> None:
    requests: list[int] = []
    store = Storefront(
        Config("demo.myshopify.com", "t"), transport=catalog_transport(requests, 10**9)
    )
    walked = store.all_products(250)
    assert walked is not None and len(walked[0]) == 250 and walked[1] is True
    assert len(requests) == 3  # 100 + 100 + 50, nothing more
