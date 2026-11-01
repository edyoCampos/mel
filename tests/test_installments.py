"""InstallmentCalculator: policy 3.1 bands (R-03, A-03)."""

import pytest

from emporio.money import Money
from emporio.services import InstallmentCalculator, InstallmentPlan

calc = InstallmentCalculator()


@pytest.mark.parametrize(
    ("count", "minimum"),
    [(1, "0"), (2, "50"), (3, "50"), (4, "80"), (6, "80"), (7, "100"), (12, "100")],
)
def test_minimum_installment_bands(count: int, minimum: str) -> None:
    assert calc.minimum_installment(count) == Money(minimum)


def test_max_installments_takamine() -> None:
    assert calc.max_installments(Money("2199")) == 12
    assert calc.best_plan(Money("2199")) == InstallmentPlan(
        count=12, amount=Money("183.25"), last_amount=Money("183.25")
    )


def test_max_installments_349_90_is_four() -> None:
    # 5x would be R$ 69,98, below the R$ 80 minimum of the 4x-6x band
    assert calc.max_installments(Money("349.90")) == 4


def test_plan_last_installment_absorbs_cents() -> None:
    assert calc.plan(Money("349.90"), 4) == InstallmentPlan(
        count=4, amount=Money("87.48"), last_amount=Money("87.46")
    )


def test_max_installments_ohana_promotional_price() -> None:
    assert calc.best_plan(Money("439.20")) == InstallmentPlan(
        count=5, amount=Money("87.84"), last_amount=Money("87.84")
    )


def test_boundary_exactly_100_allows_12x() -> None:
    assert calc.best_plan(Money("1200")).count == 12
    assert calc.best_plan(Money("1200")).amount == Money("100")


def test_boundary_just_below_100_drops_to_11x() -> None:
    assert calc.max_installments(Money("1199.99")) == 11


def test_boundary_exactly_80_allows_6x() -> None:
    assert calc.best_plan(Money("480")) == InstallmentPlan(
        count=6, amount=Money("80"), last_amount=Money("80")
    )


def test_cheapest_product_three_installments() -> None:
    assert calc.best_plan(Money("159.90")) == InstallmentPlan(
        count=3, amount=Money("53.30"), last_amount=Money("53.30")
    )


def test_below_50_per_installment_limits_to_2x() -> None:
    # 3 x R$ 50,00 = R$ 150,00 > R$ 149,99, although R$ 49,9967 rounds to R$ 50,00
    assert calc.max_installments(Money("149.99")) == 2


def test_plan_above_max_raises() -> None:
    with pytest.raises(ValueError):
        calc.plan(Money("349.90"), 6)
