from decimal import Decimal

import pytest

from emporio.money import Money


def test_money_rejects_float() -> None:
    with pytest.raises(ValueError):
        Money(0.1)  # type: ignore[arg-type]


def test_money_quantizes_to_cents() -> None:
    assert Money("599.9").amount == Decimal("599.90")


def test_money_addition_is_exact() -> None:
    assert Money("0.1") + Money("0.2") == Money("0.3")


def test_format_brl_with_thousands_separator() -> None:
    assert Money("2199").format_brl() == "R$ 2.199,00"


def test_format_brl_below_one_thousand() -> None:
    assert Money("159.9").format_brl() == "R$ 159,90"


def test_format_brl_five_digits() -> None:
    assert str(Money("13285")) == "R$ 13.285,00"


def test_rounding_is_half_up() -> None:
    assert Money("87.475") == Money("87.48")


def test_apply_discount_ohana_ck20() -> None:
    # Policy 6.2: promotional price = list price x (1 - discount)
    assert Money("549").apply_discount(20) == Money("439.20")


def test_apply_discount_pix_takamine() -> None:
    # Policy 3: PIX gives 5% off the list price
    assert Money("2199").apply_discount(5) == Money("2089.05")


def test_split_last_installment_absorbs_cents() -> None:
    parts = Money("349.90").split(4)
    assert parts == [Money("87.48"), Money("87.48"), Money("87.48"), Money("87.46")]


def test_split_exact_division() -> None:
    assert Money("2199").split(12) == [Money("183.25")] * 12


@pytest.mark.parametrize(
    ("total", "parts"),
    [("349.90", 4), ("799.90", 7), ("159.90", 3), ("13285", 12), ("0.10", 3)],
)
def test_split_sum_equals_total(total: str, parts: int) -> None:
    pieces = Money(total).split(parts)
    assert len(pieces) == parts
    assert sum(pieces, Money.zero()) == Money(total)


def test_split_rejects_zero_parts() -> None:
    with pytest.raises(ValueError):
        Money("10").split(0)


def test_apply_discount_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        Money("10").apply_discount(101)


def test_money_ordering() -> None:
    assert Money("500") <= Money("500.00")
    assert Money("499.99") < Money("500")
    assert not Money("500.01") <= Money("500")
