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
    "featuredImage": {"url": "https://cdn.example/trail-runner.jpg", "altText": None},
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

VARIANTS: dict[str, Any] = {
    "nodes": [
        {
            "id": "gid://shopify/ProductVariant/1001",
            "title": "US 9",
            "availableForSale": True,
            "price": {"amount": "89.0", "currencyCode": "USD"},
            "selectedOptions": [{"name": "Size", "value": "US 9"}],
        },
        {
            "id": "gid://shopify/ProductVariant/1002",
            "title": "US 12",
            "availableForSale": False,
            "price": {"amount": "99.0", "currencyCode": "USD"},
            "selectedOptions": [{"name": "Size", "value": "US 12"}],
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


STORE_INFO = {
    "shop": {
        "name": "Northpeak",
        "description": "Trail gear.",
        "primaryDomain": {"url": "https://demo.example"},
        "shipsToCountries": ["US", "CA"],
        "paymentSettings": {
            "currencyCode": "USD",
            "acceptedCardBrands": ["VISA", "MASTERCARD"],
            "supportedDigitalWallets": ["SHOPIFY_PAY"],
        },
    },
    "localization": {
        "availableCountries": [
            {"isoCode": "US", "name": "United States", "currency": {"isoCode": "USD"}}
        ],
        "availableLanguages": [{"isoCode": "EN", "endonymName": "English"}],
    },
}

PAGES = [
    {
        "handle": "size-guide",
        "title": "Size guide",
        "body": "<p>Our shoes run <b>half a size small</b>.</p>",
        "onlineStoreUrl": "https://demo.example/pages/size-guide",
    },
    {
        "handle": "contact",
        "title": "Contact",
        "body": "<p>Email us any time.</p>",
        "onlineStoreUrl": "https://demo.example/pages/contact",
    },
]


def page(has_next: bool) -> dict[str, Any]:
    info = {"hasNextPage": has_next, "endCursor": "cursor-1" if has_next else None}
    return {"nodes": [PRODUCT], "pageInfo": info}


def respond(payload: dict[str, Any]) -> dict[str, Any]:
    query: str = payload["query"]
    if "query Search" in query:
        # "unindexed" mimics a store whose search index is empty for the query.
        if payload["variables"]["query"] == "unindexed":
            return {"data": {"search": {"nodes": []}}}
        return {"data": {"search": {"nodes": [PRODUCT, SOLD_OUT]}}}
    if "query ProductsByKeyword" in query:
        return {"data": {"products": {"nodes": [PRODUCT, SOLD_OUT]}}}
    if "query Product" in query:
        if payload["variables"]["handle"] != PRODUCT["handle"]:
            return {"data": {"product": None}}
        return {"data": {"product": {**PRODUCT, "variants": VARIANTS}}}
    if "query Recommendations" in query:
        if payload["variables"]["handle"] != PRODUCT["handle"]:
            return {"data": {"product": None, "productRecommendations": None}}
        return {
            "data": {"product": {"handle": PRODUCT["handle"]}, "productRecommendations": [SOLD_OUT]}
        }
    if "query StoreInfo" in query:
        return {"data": STORE_INFO}
    if "query PageSearch" in query:
        hits = [
            p
            for p in PAGES
            if payload["variables"]["query"] == "contact" and p["handle"] == "contact"
        ]
        return {"data": {"search": {"nodes": hits}}}
    if "query PageList" in query:
        return {"data": {"pages": {"nodes": PAGES}}}
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
