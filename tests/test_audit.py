"""Each listing check flags exactly the products it describes."""

from typing import Any

from mcp_store_tools.audit import audit


def product(**overrides: Any) -> dict[str, Any]:
    good = {
        "handle": "good",
        "title": "Trail Runner",
        "description": "x" * 120,
        "vendor": "Northpeak",
        "type": "Shoes",
        "tags": ["trail"],
        "price": "89.0 USD to 99.0 USD",
        "available": True,
        "url": "https://demo.example/products/good",
        "image": "https://cdn.example/good.jpg",
    }
    return good | overrides


def test_a_clean_catalog_has_no_issues() -> None:
    report = audit([product(), product(handle="other", title="Summit Boot")], False, 5)
    assert report["products_checked"] == 2 and report["truncated"] is False
    assert report["top_issues"] == []
    assert all(check["count"] == 0 for check in report["checks"].values())


def test_every_check_flags_its_own_problem() -> None:
    cases = {
        "short_description": product(handle="a", title="A1", description="Short."),
        "no_image": product(handle="b", title="B1", image=""),
        "no_product_type": product(handle="c", title="C1", type=""),
        "no_vendor": product(handle="d", title="D1", vendor=""),
        "no_tags": product(handle="e", title="E1", tags=[]),
        "not_on_online_store": product(handle="f", title="F1", url=""),
        "sold_out": product(handle="g", title="G1", available=False),
        "wide_price_range": product(handle="h", title="H1", price="10.0 USD to 99.0 USD"),
        "title_all_caps": product(handle="i", title="BIG SALE BOOT"),
        "title_too_long": product(handle="j", title="Boot " * 15),
    }
    report = audit(list(cases.values()), False, 5)
    for name, flagged in cases.items():
        check = report["checks"][name]
        assert [e["handle"] for e in check["examples"]] == [flagged["handle"]], name
        assert check["count"] == 1 and check["percent"] == 10.0


def test_duplicates_examples_cap_and_top_issues() -> None:
    products = [product(handle=f"p{i}", title="Same Name", tags=[]) for i in range(4)]
    report = audit(products, True, 2)

    assert report["truncated"] is True
    assert report["checks"]["duplicate_title"]["count"] == 4
    assert len(report["checks"]["no_tags"]["examples"]) == 2
    assert set(report["top_issues"]) == {"duplicate_title", "no_tags"}


def test_an_empty_catalog_reports_zero_percent() -> None:
    report = audit([], False, 5)
    assert report["products_checked"] == 0
    assert report["checks"]["sold_out"]["percent"] == 0.0


def test_no_urls_anywhere_is_reported_once_as_a_store_wide_note() -> None:
    products = [product(handle=f"p{i}", title=f"P{i}", url="") for i in range(3)]
    report = audit(products, False, 5)

    assert report["checks"]["not_on_online_store"]["count"] == 3
    assert report["checks"]["not_on_online_store"]["store_wide"] is True
    assert "not_on_online_store" not in report["top_issues"]
    assert "Online Store" in report["notes"][0]
