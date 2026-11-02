"""StoreData with the real CSV files (D-05, A-07, A-13, A-21, D-13, NFR-03)."""

import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from emporio.money import Money
from emporio.store_data import CSV_FILES, DataLoadError, ProductFilter, StoreData
from tests.factories import make_product, make_promotion

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"


@pytest.fixture(scope="module")
def data() -> StoreData:
    return StoreData.load(DATA_DIR)


def copy_data(tmp_path: Path) -> Path:
    target = tmp_path / "raw data with spaces"
    shutil.copytree(DATA_DIR, target)
    return target


def test_load_counts_match_csv_files(data: StoreData) -> None:
    assert len(data.categories()) == 9
    assert len(data.products()) == 65
    assert data.customer_count() == 50
    assert data.order_count() == 20
    assert sum(len(data.order_items(o)) for o in range(1, 21)) == 22
    assert sum(len(data.promotions_for(p.id)) for p in data.products()) == 25


def test_load_raises_on_missing_file(tmp_path: Path) -> None:
    target = copy_data(tmp_path)
    (target / CSV_FILES["orders"]).unlink()
    with pytest.raises(DataLoadError, match="orders"):
        StoreData.load(target)


def test_load_raises_on_invalid_row(tmp_path: Path) -> None:
    target = copy_data(tmp_path)
    path = target / CSV_FILES["products"]
    text = path.read_text(encoding="utf-8").replace(",active,", ",foo,", 1)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(DataLoadError, match="row 2"):
        StoreData.load(target)


def test_specs_strings_material_for_acoustic_guitars(data: StoreData) -> None:
    yamaha_c40 = data.get_product(81)
    assert yamaha_c40 is not None
    assert yamaha_c40.string_material == "nylon"
    assert yamaha_c40.string_count is None


def test_specs_seven_string_guitar_count_from_name(data: StoreData) -> None:
    tagima_tw7 = data.get_product(112)
    assert tagima_tw7 is not None
    assert tagima_tw7.string_material == "steel"
    assert tagima_tw7.string_count == 7


def test_specs_strings_count_for_electric_guitars(data: StoreData) -> None:
    guitar = data.products_in_category(1)[0]
    assert guitar.string_material is None
    assert guitar.string_count == 6


def test_specs_without_strings_key(data: StoreData) -> None:
    pearl = data.get_product(140)
    assert pearl is not None
    assert pearl.string_material is None
    assert pearl.string_count is None


@pytest.mark.parametrize(
    ("query", "category_id"),
    [("violoes", 5), ("Violões", 5), ("violão", 5), (5, 5), ("ukulele", 9), ("guitarra", 1)],
)
def test_find_category_is_accent_insensitive(
    data: StoreData, query: str | int, category_id: int
) -> None:
    category = data.find_category(query)
    assert category is not None and category.id == category_id


@pytest.mark.parametrize(
    ("query", "category_id"), [("saxofone", 6), ("trompete", 7), ("violino", 8)]
)
def test_find_category_by_instrument_alias(data: StoreData, query: str, category_id: int) -> None:
    category = data.find_category(query)
    assert category is not None and category.id == category_id


def test_find_category_unknown(data: StoreData) -> None:
    assert data.find_category("pedal") is None


def test_active_promotion_ignores_inactive(data: StoreData) -> None:
    assert data.active_promotion_for(95) is None
    assert [p.discount_percent for p in data.promotions_for(95)] == [12]


def test_active_promotion_for_ohana_ck20(data: StoreData) -> None:
    promotion = data.active_promotion_for(121)
    assert promotion is not None
    assert promotion.id == 14
    assert promotion.discount_percent == 20


def test_active_promotion_picks_largest_when_two_active() -> None:
    product = make_product(id=1)
    data = StoreData(
        categories=[],
        products=[product],
        promotions=[
            make_promotion(product, 10, True, id=1),
            make_promotion(product, 15, True, id=2),
        ],
        customers=[],
        orders=[],
        order_items=[],
    )
    promotion = data.active_promotion_for(1)
    assert promotion is not None and promotion.discount_percent == 15


def test_search_acoustic_guitars_up_to_1000(data: StoreData) -> None:
    results = data.search_products(ProductFilter(category_id=5, max_price=Decimal("1000")))
    ids = [p.id for p in results]
    assert len(ids) == 12
    assert 96 not in ids and 113 not in ids
    assert 85 in ids  # A-15: Rozini RC-104 has stock today


def test_search_include_unavailable(data: StoreData) -> None:
    flt = ProductFilter(category_id=5, max_price=Decimal("1000"), include_unavailable=True)
    assert len(data.search_products(flt)) == 14


def test_search_uses_effective_price(data: StoreData) -> None:
    results = data.search_products(ProductFilter(category_id=9, max_price=Decimal("330")))
    assert 127 in [p.id for p in results]  # Kalani KAL-700T: R$ 359,00 with 10% off


def test_search_excludes_discontinued_with_stock(data: StoreData) -> None:
    results = data.search_products(ProductFilter(category_id=2))
    assert 136 not in [p.id for p in results]


def test_search_sorted_by_effective_price(data: StoreData) -> None:
    results = data.search_products(ProductFilter(category_id=5, max_price=Decimal("1000")))
    assert results[0].name == "Tagima Memphis AC-39 Nylon Natural"
    assert data.current_price(results[0]) == Money("429.90")
    assert results[-1].name == "Tagima TW-7 7 Cordas Aço Natural"


def test_search_text_and_material(data: StoreData) -> None:
    flt = ProductFilter(category_id=5, string_material="nylon", max_price=Decimal("600"))
    results = data.search_products(flt)
    assert results
    assert all(p.string_material == "nylon" for p in results)
    # Data finding: product 139 is named "Yamaha" but its description says "Pearl Export"
    assert {p.id for p in data.search_products(ProductFilter(text="pearl"))} == {139, 140}


def test_search_electric(data: StoreData) -> None:
    results = data.search_products(ProductFilter(category_id=5, electric=True))
    assert 94 in [p.id for p in results]  # Taylor 110e: electronics "yes", no "Elétrico" in name
    assert all(p.specs.get("electronics") == "yes" for p in results)
    assert 81 not in [p.id for p in results]  # Yamaha C40 is acoustic


def test_match_product_name_with_typo_and_space(data: StoreData) -> None:
    matches = data.match_product_name("takamine gd 20")
    assert matches[0].product.id == 95
    assert matches[0].score >= 85
    assert matches[0].score - matches[1].score >= 5


def test_match_product_name_ambiguous(data: StoreData) -> None:
    matches = data.match_product_name("takamine", limit=4)
    assert {m.product.id for m in matches} == {95, 100, 105, 115}
    assert matches[0].score - matches[1].score < 5


def test_model_code_in_one_name_is_the_only_match(data: StoreData) -> None:
    matches = data.match_product_name("Yamaha GF-3D")  # code beats the brand
    assert [m.product.id for m in matches] == [96]


def test_model_code_in_several_names_keeps_fuzzy_matching(data: StoreData) -> None:
    matches = data.match_product_name("gd20 c40", limit=3)  # two codes, two products
    assert len(matches) == 3


def test_query_made_only_of_category_words_is_kept(data: StoreData) -> None:
    assert data.match_product_name("violão")  # nothing left to strip it down to


def test_order_registered_total_is_kept(data: StoreData) -> None:
    order = data.get_order(3)
    assert order is not None
    assert order.total == Money("3450")
    item_sum = sum(
        (data.get_product(i.product_id).price * i.quantity for i in data.order_items(3)),  # type: ignore[union-attr]
        Money.zero(),
    )
    assert item_sum == Money("3498")


def test_get_customer_by_id_only(data: StoreData) -> None:
    public = [name for name in dir(data) if not name.startswith("_")]
    assert not [name for name in public if "customer" in name and name.endswith("s")]
    customer = data.get_customer(2)
    assert customer is not None and customer.first_name == "Ana"


def test_getters_return_none_for_unknown_ids(data: StoreData) -> None:
    assert data.get_product(9999) is None
    assert data.get_order(9999) is None
    assert data.get_customer(9999) is None


# ---------- Text search matches whole words ----------


def test_text_search_kala_does_not_bring_kalani(data: StoreData) -> None:
    names = [p.name for p in data.search_products(ProductFilter(text="Kala"))]
    assert names and all(name.startswith("Kala ") for name in names)
    assert not any("Kalani" in name for name in names)


def test_text_search_kalani_brings_kalani(data: StoreData) -> None:
    names = [p.name for p in data.search_products(ProductFilter(text="kalani"))]
    assert names and all(name.startswith("Kalani ") for name in names)


def test_name_lookup_kala_does_not_bring_kalani(data: StoreData) -> None:
    matches = data.match_product_name("ukulele Kala", limit=5)
    assert matches and all(m.product.name.startswith("Kala ") for m in matches)


def test_name_lookup_kalani_brings_kalani(data: StoreData) -> None:
    matches = data.match_product_name("kalani", limit=3)
    assert all(m.product.name.startswith("Kalani ") for m in matches)
