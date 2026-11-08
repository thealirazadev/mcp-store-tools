"""A fake Storefront API: answers by operation name with responses shaped like Shopify's."""

import json
from collections.abc import Callable
from typing import Any

import httpx

PRODUCT = {
    "handle": "trail-runner",
    "title": "Trail Runner",
    "description": "Light shoe for <b>rough</b> trails.",
    "vendor": "Northpeak",
    "productType": "Shoes",
    "tags": ["running", "trail"],
    "onlineStoreUrl": "https://demo.example/products/trail-runner",
    "availableForSale": True,
    "priceRange": {
        "minVariantPrice": {"amount": "89.0", "currencyCode": "USD"},
        "maxVariantPrice": {"amount": "99.0", "currencyCode": "USD"},
    },
}

SOLD_OUT = {
    **PRODUCT,
    "handle": "summit-boot",
    "title": "Summit Boot",
    "availableForSale": False,
    "priceRange": {
        "minVariantPrice": {"amount": "180.0", "currencyCode": "USD"},
        "maxVariantPrice": {"amount": "180.0", "currencyCode": "USD"},
    },
}

VARIANTS = {
    "nodes": [
        {
            "title": "US 9",
            "availableForSale": True,
            "price": {"amount": "89.0", "currencyCode": "USD"},
        },
        {
            "title": "US 12",
            "availableForSale": False,
            "price": {"amount": "99.0", "currencyCode": "USD"},
        },
    ],
    "pageInfo": {"hasNextPage": False},
}

POLICIES = {
    "refundPolicy": {
        "title": "Refund policy",
        "url": "https://demo.example/policies/refund-policy",
        "body": "<p>Returns within <strong>30 days</strong>.</p><script>steal()</script>",
    },
    "shippingPolicy": None,
    "privacyPolicy": None,
    "termsOfService": None,
    "subscriptionPolicy": None,
}


COLLECTIONS = [
    {
        "handle": "shoes",
        "title": "Shoes",
        "description": "Road and <i>trail</i> shoes.",
        "onlineStoreUrl": "https://demo.example/collections/shoes",
    }
]


def page(has_next: bool) -> dict[str, Any]:
    info = {"hasNextPage": has_next, "endCursor": "cursor-1" if has_next else None}
    return {"nodes": [PRODUCT], "pageInfo": info}


def respond(payload: dict[str, Any]) -> dict[str, Any]:
    query: str = payload["query"]
    if "query Search" in query:
        return {"data": {"search": {"nodes": [PRODUCT, SOLD_OUT]}}}
    if "query Product" in query:
        if payload["variables"]["handle"] != PRODUCT["handle"]:
            return {"data": {"product": None}}
        return {"data": {"product": {**PRODUCT, "variants": VARIANTS}}}
    if "query CollectionBrowse" in query:
        if payload["variables"]["handle"] != "shoes":
            return {"data": {"collection": None}}
        return {"data": {"collection": {"products": page(False)}}}
    if "query Browse" in query:
        return {"data": {"products": page(payload["variables"]["after"] is None)}}
    if "query Collections" in query:
        return {"data": {"collections": {"nodes": COLLECTIONS}}}
    if "query Policies" in query:
        return {"data": {"shop": POLICIES}}
    return {"errors": [{"message": "unknown operation"}]}


def transport(
    handler: Callable[[dict[str, Any]], dict[str, Any]] = respond,
    calls: list[dict[str, Any]] | None = None,
) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if calls is not None:
            calls.append({"headers": dict(request.headers), "url": str(request.url), **payload})
        return httpx.Response(200, json=handler(payload))

    return httpx.MockTransport(handle)
