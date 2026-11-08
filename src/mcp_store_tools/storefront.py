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
  compareAtPriceRange { maxVariantPrice { amount currencyCode } }
"""

# Added to search results only when the shopper asks for specific options (size, colour).
_VARIANT_OPTION_FIELDS = """
  variants(first: 100) {
    nodes { id title availableForSale price { amount currencyCode } selectedOptions { name value } }
  }
"""

# Every query runs in the configured country and language: local currency, prices, and
# translations for stores that sell internationally (Shopify Markets).
_CTX_VARS = "$country: CountryCode, $language: LanguageCode"
_CTX = "@inContext(country: $country, language: $language)"


def _search_queries(fields: str) -> tuple[str, str]:
    search = f"""
query Search($query: String!, $first: Int!, $filters: [ProductFilter!], {_CTX_VARS}) {_CTX} {{
  search(query: $query, first: $first, types: [PRODUCT], productFilters: $filters,
         sortKey: RELEVANCE) {{
    nodes {{ ... on Product {{ {fields} }} }}
  }}
}}
"""
    # Fallback when the storefront search index returns nothing (seen on a new development
    # store, where `search` returned 0 results while these products were published and listed).
    keyword = f"""
query ProductsByKeyword($query: String!, $first: Int!, {_CTX_VARS}) {_CTX} {{
  products(first: $first, query: $query, sortKey: RELEVANCE) {{ nodes {{ {fields} }} }}
}}
"""
    return search, keyword


SEARCH_QUERY, KEYWORD_QUERY = _search_queries(_PRODUCT_FIELDS)
OPTION_SEARCH_QUERY, OPTION_KEYWORD_QUERY = _search_queries(
    _PRODUCT_FIELDS + _VARIANT_OPTION_FIELDS
)

PRODUCT_QUERY = f"""
query Product($handle: String!, {_CTX_VARS}) {_CTX} {{
  product(handle: $handle) {{
    {_PRODUCT_FIELDS}
    variants(first: {MAX_VARIANTS}) {{
      nodes {{
        id title availableForSale price {{ amount currencyCode }} selectedOptions {{ name value }}
        compareAtPrice {{ amount currencyCode }}
      }}
      pageInfo {{ hasNextPage }}
    }}
  }}
}}
"""

# Same query plus stock counts, used while the token has unauthenticated_read_product_inventory.
PRODUCT_INVENTORY_QUERY = PRODUCT_QUERY.replace(
    "compareAtPrice { amount currencyCode }",
    "compareAtPrice { amount currencyCode } quantityAvailable",
)
assert PRODUCT_INVENTORY_QUERY != PRODUCT_QUERY

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

_PAGE_FIELDS = "__typename handle title body onlineStoreUrl"
_ARTICLE_FIELDS = "__typename handle title contentHtml onlineStoreUrl"

PAGE_SEARCH_QUERY = f"""
query PageSearch($query: String!, $first: Int!, {_CTX_VARS}) {_CTX} {{
  search(query: $query, first: $first, types: [PAGE, ARTICLE]) {{
    nodes {{ ... on Page {{ {_PAGE_FIELDS} }} ... on Article {{ {_ARTICLE_FIELDS} }} }}
  }}
}}
"""

PAGE_LIST_QUERY = f"""
query PageList({_CTX_VARS}) {_CTX} {{
  pages(first: 100) {{ nodes {{ {_PAGE_FIELDS} }} }}
  articles(first: 100, sortKey: PUBLISHED_AT, reverse: true) {{ nodes {{ {_ARTICLE_FIELDS} }} }}
}}
"""

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
        # None until the first product lookup shows whether the token may read stock counts.
        self._inventory: bool | None = None
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

    def query(
        self, document: str, variables: dict[str, Any] | None = None, country: str | None = None
    ) -> dict[str, Any]:
        """Run a Storefront query; ``country`` overrides the configured market for this call."""
        variables = dict(variables or {})
        if "$country" in document:
            variables |= {
                "country": country or self._config.country,
                "language": self._config.language,
            }
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
        options: dict[str, str] | None = None,
        country: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = []
        for name, value in (options or {}).items():
            filters.append({"variantOption": {"name": name, "value": value}})
        if min_price is not None or max_price is not None:
            price = {"min": min_price, "max": max_price}
            filters.append({"price": {k: float(v) for k, v in price.items() if v is not None}})
        if in_stock_only:
            filters.append({"available": True})
        search, keyword = (
            (OPTION_SEARCH_QUERY, OPTION_KEYWORD_QUERY)
            if options
            else (
                SEARCH_QUERY,
                KEYWORD_QUERY,
            )
        )
        variables = {"query": query, "first": limit, "filters": filters or None}
        nodes = [n for n in self.query(search, variables, country)["search"]["nodes"] if n]
        if not nodes:
            fallback = {"query": query, "first": limit}
            nodes = self.query(keyword, fallback, country)["products"]["nodes"]

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

        results = []
        for node in nodes:
            if not wanted(node):
                continue
            product = _product(node)
            if options:
                # Stores often ignore the option filter, so match real variants here.
                matching = [
                    _variant(v)
                    for v in node.get("variants", {}).get("nodes", [])
                    if _has_options(v, options) and (v["availableForSale"] or not in_stock_only)
                ]
                if not matching:
                    continue
                product["matching_variants"] = matching
            results.append(product)
        return results

    def get_product(self, handle: str, country: str | None = None) -> dict[str, Any] | None:
        variables = {"handle": handle}
        node = None
        if self._inventory is not False:
            try:
                node = self.query(PRODUCT_INVENTORY_QUERY, variables, country).get("product")
                self._inventory = True
            except StorefrontError as exc:
                if "quantityAvailable" not in str(exc):
                    raise
                self._inventory = False  # the token lacks the inventory scope; stop asking
        if self._inventory is False:
            node = self.query(PRODUCT_QUERY, variables, country).get("product")
        if node is None:
            return None
        product = _product(node)
        product["variants"] = [_variant(v) for v in node["variants"]["nodes"]]
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
        self,
        collection: str | None,
        sort: str,
        limit: int,
        cursor: str | None,
        country: str | None = None,
    ) -> dict[str, Any] | None:
        """One page of products; None when the collection does not exist."""
        product_key, collection_key, reverse = SORTS[sort]
        variables: dict[str, Any] = {"first": limit, "after": cursor, "reverse": reverse}
        if collection is None:
            page = self.query(BROWSE_QUERY, {**variables, "sortKey": product_key}, country)[
                "products"
            ]
        else:
            variables |= {"handle": collection, "sortKey": collection_key}
            found = self.query(COLLECTION_BROWSE_QUERY, variables, country).get("collection")
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

    def recommendations(
        self, handle: str, kind: str, country: str | None = None
    ) -> list[dict[str, Any]] | None:
        """Shopify's own recommendations; None when the product does not exist."""
        variables = {"handle": handle, "intent": RECOMMENDATION_INTENTS[kind]}
        data = self.query(RECOMMENDATIONS_QUERY, variables, country)
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
        """Content pages and blog articles (size guides, FAQs, care guides); falls back to
        matching words when the search index returns nothing, as with product search."""
        found = self.query(PAGE_SEARCH_QUERY, {"query": query, "first": limit})["search"]
        nodes = [n for n in found["nodes"] if n and n.get("handle")]
        if not nodes:
            words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2] or [query.lower()]
            every = self._cached("content", self._load_content)

            def score(entry: dict[str, Any]) -> int:
                title, body = entry["title"].lower(), _content_text(entry, 20000).lower()
                return sum(3 * (w in title) + (w in body) for w in words)

            nodes = sorted((e for e in every if score(e)), key=score, reverse=True)[:limit]
        return [
            {
                "kind": "article" if entry.get("__typename") == "Article" else "page",
                "handle": entry["handle"],
                "title": entry["title"],
                "url": entry.get("onlineStoreUrl") or "",
                "text": _content_text(entry, 3000),
            }
            for entry in nodes
        ]

    def _load_content(self) -> list[dict[str, Any]]:
        data = self.query(PAGE_LIST_QUERY)
        pages = data["pages"]["nodes"]
        articles = (data.get("articles") or {}).get("nodes") or []
        return [*pages, *articles]

    def cart_link(
        self, items: list[tuple[str, str, int]], country: str | None = None
    ) -> dict[str, Any]:
        """A cart permalink for (product handle, variant id, quantity) items, after checking
        each variant belongs to that published product and is in stock, so the model cannot
        hand a shopper a link to a guessed or sold-out item. Nothing is written to the store;
        the cart is created when the shopper opens the link."""
        products: dict[str, dict[str, Any] | None] = {}
        lines, problems, sold_out = [], [], []
        for handle, variant_id, quantity in items:
            if handle not in products:
                products[handle] = self.get_product(handle, country)
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

    def all_products(
        self, max_products: int, collection: str | None = None, country: str | None = None
    ) -> tuple[list[dict[str, Any]], bool] | None:
        """Walk the catalog (or one collection) in title order; returns (products, truncated),
        or None when the collection does not exist."""
        products: list[dict[str, Any]] = []
        cursor = None
        while len(products) < max_products:
            size = min(100, max_products - len(products))
            page = self.browse(collection, "title", size, cursor, country)
            if page is None:
                return None
            products += page["products"]
            cursor = page["next_cursor"]
            if cursor is None:
                return products, False
        return products, True

    def deals(
        self,
        collection: str | None,
        min_percent_off: int,
        limit: int,
        max_products: int,
        country: str | None = None,
    ) -> dict[str, Any] | None:
        """Products priced below their compare-at price, biggest discount first."""
        walked = self.all_products(max_products, collection, country)
        if walked is None:
            return None
        products, truncated = walked
        on_sale = [p for p in products if p["on_sale"] and p["percent_off"] >= min_percent_off]
        on_sale.sort(key=lambda p: p["percent_off"], reverse=True)
        return {
            "deals": on_sale[:limit],
            "on_sale_count": len(on_sale),
            "products_checked": len(products),
            "truncated": truncated,
        }

    def compare(self, handles: list[str], country: str | None = None) -> dict[str, Any]:
        """Side-by-side facts for several products, with the fields that differ."""
        found = {h: self.get_product(h, country) for h in handles}
        missing = [h for h, p in found.items() if p is None]
        if missing:
            raise StorefrontError(f"No published product has the handle: {', '.join(missing)}.")
        rows = []
        for product in found.values():
            assert product is not None
            options: dict[str, list[str]] = {}
            for variant in product["variants"]:
                for name, value in variant["options"].items():
                    if value not in options.setdefault(name, []):
                        options[name].append(value)
            rows.append(
                {
                    key: product[key]
                    for key in (
                        "handle",
                        "title",
                        "price",
                        "compare_at_price",
                        "on_sale",
                        "percent_off",
                        "available",
                        "vendor",
                        "type",
                        "url",
                        "image",
                    )
                }
                | {
                    "in_stock_variants": sum(v["available"] for v in product["variants"]),
                    "variant_count": len(product["variants"]),
                    "options": options,
                    "description": product["description"][:500],
                }
            )
        compared = ("price", "on_sale", "available", "vendor", "type", "options")
        differs = [key for key in compared if len({repr(row[key]) for row in rows}) > 1]
        return {"products": rows, "differs_in": differs}


def _has_options(variant: dict[str, Any], wanted: dict[str, str]) -> bool:
    have = {o["name"].lower(): o["value"].lower() for o in variant.get("selectedOptions") or []}
    return all(have.get(name.lower()) == value.lower() for name, value in wanted.items())


def _variant(v: dict[str, Any]) -> dict[str, Any]:
    compare = v.get("compareAtPrice")
    return {
        "id": _numeric_id(v["id"]),
        "title": v["title"],
        "available": v["availableForSale"],
        "price": _money(v["price"]),
        "compare_at_price": _money(compare) if compare else "",
        "options": {o["name"]: o["value"] for o in v.get("selectedOptions") or []},
        "quantity_available": _quantity(v),
    }


def _quantity(v: dict[str, Any]) -> int | None:
    """Units in stock, or None when unknown: the token cannot read stock, or Shopify reports 0
    for an item that is still for sale (inventory not tracked, or overselling allowed)."""
    quantity = v.get("quantityAvailable")
    if quantity is None or (quantity <= 0 and v["availableForSale"]):
        return None
    return max(int(quantity), 0)


def _numeric_id(gid: str) -> str:
    return gid.rsplit("/", 1)[-1]


def _content_text(entry: dict[str, Any], limit: int) -> str:
    return plain_text(entry.get("body") or entry.get("contentHtml"), limit)


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
        **_sale(node),
    }


def _sale(node: dict[str, Any]) -> dict[str, Any]:
    """Compare-at price and the deepest discount ("up to N% off") across variants."""
    compare = ((node.get("compareAtPriceRange") or {}).get("maxVariantPrice")) or {}
    was = Decimal(compare.get("amount") or 0)
    now = Decimal(node["priceRange"]["minVariantPrice"]["amount"])
    on_sale = was > now > 0
    return {
        "compare_at_price": _money(compare) if on_sale else "",
        "on_sale": on_sale,
        "percent_off": int((was - now) * 100 / was) if on_sale else 0,
    }
