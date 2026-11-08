"""Listing-quality checks over published products, computed in the server so a whole-catalog
audit costs one tool call instead of the model paging through every product."""

import re
from collections import Counter
from collections.abc import Callable
from decimal import Decimal
from typing import Any

Product = dict[str, Any]


def _prices(product: Product) -> list[Decimal]:
    return [Decimal(n) for n in re.findall(r"\d+(?:\.\d+)?", product["price"])]


def _wide_range(product: Product) -> bool:
    prices = _prices(product)
    return len(prices) == 2 and prices[0] > 0 and prices[1] > 3 * prices[0]


def _all_caps(product: Product) -> bool:
    letters = [c for c in product["title"] if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters)


CHECKS: dict[str, tuple[str, Callable[[Product], bool]]] = {
    "short_description": (
        "Description missing or under 100 characters",
        lambda p: len(p["description"]) < 100,
    ),
    "no_image": ("No product image", lambda p: not p["image"]),
    "no_product_type": ("No product type", lambda p: not p["type"]),
    "no_vendor": ("No vendor", lambda p: not p["vendor"]),
    "no_tags": ("No tags", lambda p: not p["tags"]),
    "not_on_online_store": (
        "Not published to the online store (no product URL)",
        lambda p: not p["url"],
    ),
    "sold_out": ("Sold out (no variant available)", lambda p: not p["available"]),
    "wide_price_range": (
        "Highest variant price is over 3x the lowest (check for a typo)",
        _wide_range,
    ),
    "title_all_caps": ("Title in ALL CAPS", _all_caps),
    "title_too_long": ("Title longer than 70 characters", lambda p: len(p["title"]) > 70),
}


def audit(products: list[Product], truncated: bool, examples: int) -> dict[str, Any]:
    titles = Counter(p["title"].strip().lower() for p in products)
    checks = {
        **CHECKS,
        "duplicate_title": (
            "Title shared with another product",
            lambda p: titles[p["title"].strip().lower()] > 1,
        ),
    }
    total = len(products)
    report: dict[str, Any] = {}
    for name, (label, failing) in checks.items():
        hits = [p for p in products if failing(p)]
        report[name] = {
            "label": label,
            "count": len(hits),
            "percent": round(100 * len(hits) / total, 1) if total else 0.0,
            "examples": [
                {"title": p["title"], "handle": p["handle"], "url": p["url"]}
                for p in hits[:examples]
            ],
        }
    notes = []
    if total and report["not_on_online_store"]["count"] == total:
        # One store-wide cause, not a per-listing problem: keep it out of the ranking.
        report["not_on_online_store"]["store_wide"] = True
        notes.append(
            "No product has an online store URL: the Online Store sales channel is not set up, "
            "or products are not published to it. Check Sales channels > Online Store."
        )
    worst = sorted(
        (n for n in report if report[n]["count"] and not report[n].get("store_wide")),
        key=lambda n: report[n]["count"],
        reverse=True,
    )
    return {
        "products_checked": total,
        "truncated": truncated,
        "top_issues": worst[:3],
        "checks": report,
        "notes": notes,
        "scope": "Published storefront data only: drafts, inventory counts, SEO fields, and "
        "sales are not visible to this audit.",
    }
