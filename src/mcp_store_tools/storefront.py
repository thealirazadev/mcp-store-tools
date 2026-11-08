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
  featuredImage { url altText }
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

# Fallback when the storefront search index returns nothing (seen on a new development store,
# where `search` returned 0 results while these products were published and listed).
KEYWORD_QUERY = f"""
query ProductsByKeyword($query: String!, $first: Int!, {_CTX_VARS}) {_CTX} {{
  products(first: $first, query: $query, sortKey: RELEVANCE) {{ nodes {{ {_PRODUCT_FIELDS} }} }}
}}
"""

PRODUCT_QUERY = f"""
query Product($handle: String!, {_CTX_VARS}) {_CTX} {{
  product(handle: $handle) {{
    {_PRODUCT_FIELDS}
    variants(first: {MAX_VARIANTS}) {{
      nodes {{
        id title availableForSale price {{ amount currencyCode }} selectedOptions {{ name value }}
      }}
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

RECOMMENDATIONS_QUERY = f"""
query Recommendations($handle: String!, $intent: ProductRecommendationIntent!, {_CTX_VARS})
{_CTX} {{
  product(handle: $handle) {{ handle }}
  productRecommendations(productHandle: $handle, intent: $intent) {{ {_PRODUCT_FIELDS} }}
}}
"""

STORE_INFO_QUERY = f"""
query StoreInfo({_CTX_VARS}) {_CTX} {{
  shop {{
    name description primaryDomain {{ url }} shipsToCountries
    paymentSettings {{ currencyCode acceptedCardBrands supportedDigitalWallets }}
  }}
  localization {{
    availableCountries {{ isoCode name currency {{ isoCode }} }}
    availableLanguages {{ isoCode endonymName }}
  }}
}}
"""

_PAGE_FIELDS = "handle title body onlineStoreUrl"

PAGE_SEARCH_QUERY = f"""
query PageSearch($query: String!, $first: Int!, {_CTX_VARS}) {_CTX} {{
  search(query: $query, first: $first, types: [PAGE]) {{
    nodes {{ ... on Page {{ {_PAGE_FIELDS} }} }}
  }}
}}
"""

PAGE_LIST_QUERY = (
    f"query PageList({_CTX_VARS}) {_CTX} {{ pages(first: 100) {{ nodes {{ {_PAGE_FIELDS} }} }} }}"
)

RECOMMENDATION_INTENTS = {"similar": "RELATED", "goes_with": "COMPLEMENTARY"}

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
                # Shopify explains most 4xx responses ("Online Store channel is locked" for a
                # store that has not opened yet); that text is safe to relay and actionable.
                reason = _graphql_error(response)
                detail = f": {reason}" if reason else "."
                raise StorefrontError(
                    f"The store returned an error ({response.status_code}){detail}"
                )
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
        nodes = [n for n in self.query(SEARCH_QUERY, variables)["search"]["nodes"] if n]
        if not nodes:
            fallback = {"query": query, "first": limit}
            nodes = self.query(KEYWORD_QUERY, fallback)["products"]["nodes"]

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

        return [_product(node) for node in nodes if wanted(node)]

    def get_product(self, handle: str) -> dict[str, Any] | None:
        node = self.query(PRODUCT_QUERY, {"handle": handle}).get("product")
        if node is None:
            return None
        product = _product(node)
        product["variants"] = [
            {
                "id": _numeric_id(v["id"]),
                "title": v["title"],
                "available": v["availableForSale"],
                "price": _money(v["price"]),
                "options": {o["name"]: o["value"] for o in v.get("selectedOptions") or []},
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

    def recommendations(self, handle: str, kind: str) -> list[dict[str, Any]] | None:
        """Shopify's own recommendations; None when the product does not exist."""
        variables = {"handle": handle, "intent": RECOMMENDATION_INTENTS[kind]}
        data = self.query(RECOMMENDATIONS_QUERY, variables)
        if data.get("product") is None:
            return None
        return [_product(node) for node in data.get("productRecommendations") or []]

    def store_info(self) -> dict[str, Any]:
        result: dict[str, Any] = self._cached("store_info", self._load_store_info)
        return result

    def _load_store_info(self) -> dict[str, Any]:
        data = self.query(STORE_INFO_QUERY)
        shop, local = data["shop"], data.get("localization") or {}
        payments = shop.get("paymentSettings") or {}
        return {
            "name": shop["name"],
            "description": plain_text(shop.get("description"), limit=500),
            "url": (shop.get("primaryDomain") or {}).get("url") or "",
            "currency": payments.get("currencyCode") or "",
            "ships_to": shop.get("shipsToCountries") or [],
            "card_brands": payments.get("acceptedCardBrands") or [],
            "wallets": payments.get("supportedDigitalWallets") or [],
            "markets": [
                {"country": c["isoCode"], "name": c["name"], "currency": c["currency"]["isoCode"]}
                for c in local.get("availableCountries") or []
            ],
            "languages": [lang["isoCode"] for lang in local.get("availableLanguages") or []],
        }

    def search_pages(self, query: str, limit: int) -> list[dict[str, str]]:
        """Content pages (size guides, FAQs, about); falls back to matching words when the
        search index returns nothing, as with product search."""
        found = self.query(PAGE_SEARCH_QUERY, {"query": query, "first": limit})["search"]
        nodes = [n for n in found["nodes"] if n]
        if not nodes:
            words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2] or [query.lower()]
            every = self._cached("pages", lambda: self.query(PAGE_LIST_QUERY)["pages"]["nodes"])

            def score(page: dict[str, Any]) -> int:
                title, body = page["title"].lower(), plain_text(page.get("body"), 20000).lower()
                return sum(3 * (w in title) + (w in body) for w in words)

            nodes = sorted((p for p in every if score(p)), key=score, reverse=True)[:limit]
        return [
            {
                "handle": page["handle"],
                "title": page["title"],
                "url": page.get("onlineStoreUrl") or "",
                "text": plain_text(page.get("body"), limit=3000),
            }
            for page in nodes
        ]

    def cart_link(self, items: list[tuple[str, str, int]]) -> dict[str, Any]:
        """A cart permalink for (product handle, variant id, quantity) items, after checking
        each variant belongs to that published product and is in stock, so the model cannot
        hand a shopper a link to a guessed or sold-out item. Nothing is written to the store;
        the cart is created when the shopper opens the link."""
        products: dict[str, dict[str, Any] | None] = {}
        lines, problems, sold_out = [], [], []
        for handle, variant_id, quantity in items:
            if handle not in products:
                products[handle] = self.get_product(handle)
            product = products[handle]
            if product is None:
                problems.append(f"no published product '{handle}'")
                continue
            variant = next((v for v in product["variants"] if v["id"] == variant_id), None)
            if variant is None:
                problems.append(f"variant {variant_id} is not a variant of '{handle}'")
                continue
            if not variant["available"]:
                sold_out.append(f"{product['title']} ({variant['title']})")
                continue
            amount, currency = variant["price"].split()
            lines.append(
                {
                    "product": product["title"],
                    "variant": variant["title"],
                    "variant_id": variant_id,
                    "quantity": quantity,
                    "unit_price": variant["price"],
                    "line_total": f"{Decimal(amount) * quantity} {currency}",
                    "_amount": Decimal(amount) * quantity,
                    "_currency": currency,
                }
            )
        if problems:
            raise StorefrontError(
                f"Cannot build the link: {'; '.join(problems)}. "
                "Use a handle and variant id returned by get_product."
            )
        if sold_out:
            raise StorefrontError(f"Sold out, so not added: {', '.join(sold_out)}.")
        base = self.store_info()["url"].rstrip("/")
        if not base:
            raise StorefrontError("The store has no online store address for a cart link.")
        currencies = {line["_currency"] for line in lines}
        total = sum((line.pop("_amount") for line in lines), Decimal(0))
        subtotal = f"{total} {currencies.pop()}" if len(currencies) == 1 else ""
        for line in lines:
            line.pop("_currency")
        path = ",".join(f"{line['variant_id']}:{line['quantity']}" for line in lines)
        return {
            "url": f"{base}/cart/{path}",
            "lines": lines,
            "subtotal": subtotal,
            "note": "Opens the store's cart with these items. Discounts, shipping, and taxes "
            "are applied at checkout.",
        }

    def all_products(self, max_products: int) -> tuple[list[dict[str, Any]], bool]:
        """Walk the catalog in title order; returns (products, truncated)."""
        products: list[dict[str, Any]] = []
        cursor = None
        while len(products) < max_products:
            page = self.browse(None, "title", min(100, max_products - len(products)), cursor)
            assert page is not None
            products += page["products"]
            cursor = page["next_cursor"]
            if cursor is None:
                return products, False
        return products, True


def _numeric_id(gid: str) -> str:
    return gid.rsplit("/", 1)[-1]


def _graphql_error(response: httpx.Response) -> str:
    try:
        errors = response.json().get("errors")
    except ValueError:
        return ""
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("message", ""))[:200]
    return ""


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
        "image": (node.get("featuredImage") or {}).get("url") or "",
    }
