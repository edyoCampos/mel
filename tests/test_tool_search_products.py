"""search_products tool (FR-02, FR-16, R-17)."""

import re
from collections.abc import Callable
from typing import Any

import pytest

from emporio.tools import SearchProductsTool, ToolContext, ToolRegistry

MONEY = re.compile(r"^R\$ \d{1,3}(\.\d{3})*,\d{2}$")


@pytest.fixture
def search(make_context: Callable[..., ToolContext]) -> Callable[[dict[str, Any]], Any]:
    registry = ToolRegistry([SearchProductsTool(make_context())])
    return lambda args: registry.dispatch("search_products", args)


def ids(result: Any) -> list[int]:
    return [p["id"] for p in result.payload["products"]]


def test_acoustic_guitars_up_to_1000(search: Any) -> None:
    result = search({"category": "violões", "max_price": 1000})
    assert result.ok
    assert result.payload["category"] == "Violões"
    assert result.payload["total_matches"] == 12
    assert len(ids(result)) == 12
    assert 96 not in ids(result) and 113 not in ids(result)


def test_results_sorted_by_price(search: Any) -> None:
    products = search({"category": "violões", "max_price": 1000}).payload["products"]
    assert products[0]["name"] == "Tagima Memphis AC-39 Nylon Natural"
    assert products[0]["price"] == "R$ 429,90"
    assert products[0]["pix_price"] == "R$ 408,41"
    assert products[0]["best_installments"] == "5x de R$ 85,98 sem juros"
    assert products[0]["price_display"] == "R$ 429,90"
    assert products[0]["pix_display"] == "R$ 408,41 no PIX"
    assert products[0]["installments_display"] == "até 5x de R$ 85,98 sem juros"
    assert products[-1]["name"] == "Tagima TW-7 7 Cordas Aço Natural"
    assert products[-1]["price"] == "R$ 999,00"


def test_include_unavailable_marks_status(search: Any) -> None:
    result = search({"category": "violões", "max_price": 1000, "include_unavailable": True})
    by_id = {p["id"]: p for p in result.payload["products"]}
    assert len(by_id) == 14
    assert by_id[96]["availability"] == "out_of_stock"
    assert by_id[113]["availability"] == "discontinued"


def test_promotion_fields(search: Any) -> None:
    result = search({"category": "ukulele", "on_sale_only": True})
    by_id = {p["id"]: p for p in result.payload["products"]}
    assert set(by_id) == {121, 127}
    ohana = by_id[121]
    assert ohana["price"] == "R$ 549,00"
    assert ohana["current_price"] == "R$ 439,20"
    assert ohana["promotion"] == {
        "discount_percent": 20,
        "promotional_price": "R$ 439,20",
        "description": "Desconto Exclusivo",
    }
    assert ohana["pix_price"] is None
    assert by_id[127]["current_price"] == "R$ 323,10"


def test_max_price_uses_promotional_price(search: Any) -> None:
    assert 127 in ids(search({"category": "ukulele", "max_price": 330}))


def test_discontinued_with_stock_hidden(search: Any) -> None:
    assert 136 not in ids(search({"category": "baixos"}))


def test_empty_category(search: Any) -> None:
    result = search({"category": "saxofone"})
    assert result.ok
    assert result.payload["products"] == []
    assert result.payload["note"] == "No products registered in this category."


def test_unknown_category(search: Any) -> None:
    result = search({"category": "pedal"})
    assert result.payload["products"] == []
    assert result.payload["note"].startswith("Unknown category. Known categories: Guitarras")


def test_limit_and_note(search: Any) -> None:
    result = search({"category": "violões"})
    assert result.payload["total_matches"] == 33
    assert result.payload["returned"] == 15
    assert result.payload["note"] == "Showing the 15 cheapest of 33 matches."


def test_money_is_formatted(search: Any) -> None:
    for product in search({"category": "violões"}).payload["products"]:
        assert MONEY.match(product["price"])
        assert MONEY.match(product["current_price"])


def test_string_material_filter(search: Any) -> None:
    result = search({"category": "violões", "string_material": "nylon", "max_price": 600})
    assert result.payload["products"]
    assert all("Nylon" in p["name"] for p in result.payload["products"])


def test_negative_price_rejected(search: Any) -> None:
    assert search({"max_price": -1}).payload["error"] == "invalid_arguments"
