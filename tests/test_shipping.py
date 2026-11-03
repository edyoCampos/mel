"""ShippingRules (R-05, A-19, A-24, A-25)."""

import pytest

from emporio.money import Money
from emporio.services import ShippingRules
from tests.factories import make_product

shipping = ShippingRules()


def test_local_fee_exactly_500_pays_35() -> None:
    # A-19: "acima de R$ 500" means strictly above
    assert shipping.local_delivery_fee(Money("500")) == Money("35")


def test_local_fee_above_500_is_free() -> None:
    assert shipping.local_delivery_fee(Money("500.01")) == Money.zero()


@pytest.mark.parametrize(
    ("product_id", "name"),
    [
        (139, "Bateria Acústica Yamaha Kit 1 Studio"),
        (140, "Bateria Acústica Pearl Kit 2 Studio"),
        (141, "Bateria Acústica Yamaha Kit 3 Studio"),
    ],
)
def test_acoustic_drums_require_special_quote(product_id: int, name: str) -> None:
    drum = make_product(id=product_id, name=name, category_id=3, price="13285")
    assert shipping.requires_special_quote(drum)


def test_electric_bass_does_not_require_special_quote() -> None:
    # A-25: electric basses are not "contrabaixos"
    bass = make_product(id=135, name="Music Man Bass 1X Electric Bass", category_id=2)
    assert not shipping.requires_special_quote(bass)
