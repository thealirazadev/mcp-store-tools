"""Read-only Shopify Storefront GraphQL client.

The Storefront access token can only read what the public storefront shows, so nothing here can
change the store or see orders and customers (docs/decisions.md, decision 1).
"""

import html
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any

import httpx

MAX_TEXT = 2000
# Shopify's public sample store: real Storefront API responses, no token needed.
DEMO_SHOP = "mock.shop"
RETRY_STATUS = frozenset({429, 430, 500, 502, 503, 504})
MAX_RETRY_WAIT = 10.0
# Enough for every size/colour grid; beyond this the answer is flagged, not silently cut.
MAX_VARIANTS = 100
# Policies and collections rarely change; products are never cached so stock stays live.
CACHE_SECONDS = 300.0

POLICY_FIELDS = {
    "refund": "refundPolicy",
    "shipping": "shippingPolicy",
    "privacy": "privacyPolicy",
    "terms": "termsOfService",
    "subscription": "subscriptionPolicy",
}

_PRODUCT_FIELDS = """
  handle title description vendor productType tags onlineStoreUrl availableForSale
  priceRange { minVariantPrice { amount currencyCode } maxVariantPrice { amount currencyCode } }
"""

# Every query runs in the configured country and language: local currency, prices, and
# translations for stores that sell internationally (Shopify Markets).
_CTX_VARS = "$country: CountryCode, $language: LanguageCode"
_CTX = "@inContext(country: $country, language: $language)"

SEARCH_QUERY = f"""
query Search($query: String!, $first: Int!, $filters: [ProductFilter!], {_CTX_VARS}) {_CTX} {{
  search(query: $query, first: $first, types: [PRODUCT], productFilters: $filters,
         sortKey: RELEVANCE) {{
    nodes {{ ... on Product {{ {_PRODUCT_FIELDS} }} }}
  }}
}}
"""

PRODUCT_QUERY = f"""
query Product($handle: String!, {_CTX_VARS}) {_CTX} {{
  product(handle: $handle) {{
    {_PRODUCT_FIELDS}
    variants(first: {MAX_VARIANTS}) {{
      nodes {{ title availableForSale price {{ amount currencyCode }} }}
      pageInfo {{ hasNextPage }}
    }}
  }}
}}
"""

_PAGE = f"nodes {{ {_PRODUCT_FIELDS} }} pageInfo {{ hasNextPage endCursor }}"
_PAGE_ARGS = "first: $first, after: $after, sortKey: $sortKey, reverse: $reverse"

BROWSE_QUERY = f"""
query Browse(
  $first: Int!, $after: String, $sortKey: ProductSortKeys!, $reverse: Boolean!, {_CTX_VARS}
) {_CTX} {{
  products({_PAGE_ARGS}) {{ {_PAGE} }}
}}
"""

COLLECTION_BROWSE_QUERY = f"""
query CollectionBrowse(
  $handle: String!, $first: Int!, $after: String,
  $sortKey: ProductCollectionSortKeys!, $reverse: Boolean!, {_CTX_VARS}
) {_CTX} {{
  collection(handle: $handle) {{ products({_PAGE_ARGS}) {{ {_PAGE} }} }}
}}
"""

COLLECTIONS_QUERY = (
    f"query Collections({_CTX_VARS}) {_CTX} {{ collections(first: 100) "
    "{ nodes { handle title description onlineStoreUrl } } }"
)

# sort name -> (ProductSortKeys, ProductCollectionSortKeys, reverse)
SORTS = {
    "best_selling": ("BEST_SELLING", "BEST_SELLING", False),
    "price_low_to_high": ("PRICE", "PRICE", False),
    "price_high_to_low": ("PRICE", "PRICE", True),
    "newest": ("CREATED_AT", "CREATED", True),
    "title": ("TITLE", "TITLE", False),
}

POLICIES_QUERY = (
    f"query Policies({_CTX_VARS}) {_CTX} {{ shop {{ "
    + " ".join(f"{field} {{ title body url }}" for field in POLICY_FIELDS.values())
    + " } }"
)


class StorefrontError(Exception):
    """A failure safe to show the model: no tokens, URLs with secrets, or stack traces."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("p", "br", "li", "div", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def plain_text(value: str | None, limit: int = MAX_TEXT) -> str:
    """HTML to bounded plain text: hidden markup cannot carry instructions to the model."""
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    text = html.unescape("".join(parser.parts))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class Config:
    shop: str
    token: str
    api_version: str = "2026-10"
    country: str | None = None
    language: str | None = None

    @property
    def endpoint(self) -> str:
        if self.shop == DEMO_SHOP:
            return "https://mock.shop/api"
        return f"https://{self.shop}/api/{self.api_version}/graphql.json"


class Storefront:
    def __init__(
        self,
        config: Config,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 3,
        backoff: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._cache: dict[str, tuple[float, Any]] = {}
        self._config = config
        self._max_retries = max_retries
        self._backoff = backoff
        self._sleep = sleep
        self._client = httpx.Client(
            transport=transport,
            timeout=15,
            headers={
                "X-Shopify-Storefront-Access-Token": config.token,
                "Content-Type": "application/json",
            },
        )

    def query(self, document: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        variables = dict(variables or {})
        if "$country" in document:
            variables |= {"country": self._config.country, "language": self._config.language}
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.post(
                    self._config.endpoint, json={"query": document, "variables": variables}
                )
            except httpx.HTTPError as exc:
                if attempt < self._max_retries:
                    self._wait(attempt, None)
                    continue
                raise StorefrontError("The store did not respond. Try again shortly.") from exc
            if response.status_code in RETRY_STATUS:
                if attempt < self._max_retries:
                    self._wait(attempt, response.headers.get("Retry-After"))
                    continue
                break
            if response.status_code in (401, 403):
                raise StorefrontError("The store rejected the access token.")
            if response.status_code >= 400:
                raise StorefrontError(f"The store returned an error ({response.status_code}).")
            body: dict[str, Any] = response.json()
            if body.get("errors"):
                first = body["errors"][0].get("message", "unknown error")
                raise StorefrontError(f"The store could not answer the query: {first}")
            data: dict[str, Any] = body.get("data") or {}
            return data
        raise StorefrontError("The store is busy. Try again shortly.")

    def _cached(self, key: str, load: Callable[[], Any]) -> Any:
        hit = self._cache.get(key)
        if hit and self._clock() - hit[0] < CACHE_SECONDS:
            return hit[1]
        value = load()
        self._cache[key] = (self._clock(), value)
        return value

    def _wait(self, attempt: int, retry_after: str | None) -> None:
        """Honour the server's Retry-After seconds when given, else exponential backoff."""
        try:
            delay = float(retry_after) if retry_after else self._backoff * 2**attempt
        except ValueError:
            delay = self._backoff * 2**attempt
        self._sleep(min(max(delay, 0.0), MAX_RETRY_WAIT))

    def search_products(
        self,
        query: str,
        limit: int,
        min_price: Decimal | None = None,
        max_price: Decimal | None = None,
        in_stock_only: bool = False,
    ) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = []
        if min_price is not None or max_price is not None:
            price = {"min": min_price, "max": max_price}
            filters.append({"price": {k: float(v) for k, v in price.items() if v is not None}})
        if in_stock_only:
            filters.append({"available": True})
        variables = {"query": query, "first": limit, "filters": filters or None}
        nodes = self.query(SEARCH_QUERY, variables)["search"]["nodes"]

        # Apply the filters again here: a store whose search ignores a filter must not make
        # the model quote a product outside the range the shopper asked for.
        def wanted(node: dict[str, Any]) -> bool:
            low = Decimal(node["priceRange"]["minVariantPrice"]["amount"])
            high = Decimal(node["priceRange"]["maxVariantPrice"]["amount"])
            return (
                (max_price is None or low <= max_price)
                and (min_price is None or high >= min_price)
                and (node["availableForSale"] or not in_stock_only)
            )

        return [_product(node) for node in nodes if node and wanted(node)]

    def get_product(self, handle: str) -> dict[str, Any] | None:
        node = self.query(PRODUCT_QUERY, {"handle": handle}).get("product")
        if node is None:
            return None
        product = _product(node)
        product["variants"] = [
            {
                "title": v["title"],
                "available": v["availableForSale"],
                "price": _money(v["price"]),
            }
            for v in node["variants"]["nodes"]
        ]
        product["more_variants"] = bool(node["variants"].get("pageInfo", {}).get("hasNextPage"))
        return product

    def collections(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = self._cached("collections", self._load_collections)
        return result

    def _load_collections(self) -> list[dict[str, str]]:
        nodes = self.query(COLLECTIONS_QUERY)["collections"]["nodes"]
        return [
            {
                "handle": c["handle"],
                "title": c["title"],
                "description": plain_text(c.get("description"), limit=500),
                "url": c.get("onlineStoreUrl") or "",
            }
            for c in nodes
        ]

    def browse(
        self, collection: str | None, sort: str, limit: int, cursor: str | None
    ) -> dict[str, Any] | None:
        """One page of products; None when the collection does not exist."""
        product_key, collection_key, reverse = SORTS[sort]
        variables: dict[str, Any] = {"first": limit, "after": cursor, "reverse": reverse}
        if collection is None:
            page = self.query(BROWSE_QUERY, {**variables, "sortKey": product_key})["products"]
        else:
            variables |= {"handle": collection, "sortKey": collection_key}
            found = self.query(COLLECTION_BROWSE_QUERY, variables).get("collection")
            if found is None:
                return None
            page = found["products"]
        info = page["pageInfo"]
        return {
            "products": [_product(node) for node in page["nodes"]],
            "next_cursor": info["endCursor"] if info["hasNextPage"] else None,
        }

    def policies(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = self._cached("policies", self._load_policies)
        return result

    def _load_policies(self) -> dict[str, dict[str, str]]:
        shop = self.query(POLICIES_QUERY)["shop"]
        found = {}
        for kind, field in POLICY_FIELDS.items():
            policy = shop.get(field)
            if policy:
                found[kind] = {
                    "title": policy["title"],
                    "url": policy.get("url") or "",
                    "text": plain_text(policy.get("body"), limit=8000),
                }
        return found


def _money(value: dict[str, str]) -> str:
    return f"{value['amount']} {value['currencyCode']}"


def _product(node: dict[str, Any]) -> dict[str, Any]:
    low, high = node["priceRange"]["minVariantPrice"], node["priceRange"]["maxVariantPrice"]
    price = _money(low) if low["amount"] == high["amount"] else f"{_money(low)} to {_money(high)}"
    return {
        "handle": node["handle"],
        "title": node["title"],
        "description": plain_text(node.get("description")),
        "vendor": node.get("vendor") or "",
        "type": node.get("productType") or "",
        "tags": node.get("tags") or [],
        "price": price,
        "available": node["availableForSale"],
        "url": node.get("onlineStoreUrl") or "",
    }
