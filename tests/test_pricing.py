"""PricingService (R-01, R-02, R-04, A-13)."""

from emporio.money import Money
from emporio.services import PricingService
from tests.factories import make_product, make_promotion

pricing = PricingService()


def test_quote_without_promotion_has_pix_price() -> None:
    takamine = make_product()
    quote = pricing.quote(takamine, [])
    assert quote.list_price == Money("2199")
    assert quote.effective_price == Money("2199")
    assert quote.promotional_price is None
    assert quote.pix_price == Money("2089.05")
    assert quote.best_installments.count == 12
    assert quote.best_installments.amount == Money("183.25")


def test_quote_ignores_inactive_promotion() -> None:
    takamine = make_product()
    expired = make_promotion(takamine, discount_percent=12, is_active=False, id=4)
    quote = pricing.quote(takamine, [expired])
    assert quote.active_promotion is None
    assert quote.promotional_price is None
    assert quote.effective_price != Money("1935.12")


def test_pix_discount_is_not_applied_over_promotional_price() -> None:
    # Policy 6.2: promotions do not stack with the PIX discount
    ohana = make_product(id=121, name="Ohana CK-20 Concert Natural", category_id=9, price="549")
    promotion = make_promotion(ohana, discount_percent=20, is_active=True, id=14)
    quote = pricing.quote(ohana, [promotion])
    assert quote.promotional_price == Money("439.20")
    assert quote.effective_price == Money("439.20")
    assert quote.pix_price is None


def test_installments_use_effective_price() -> None:
    ohana = make_product(id=121, price="549")
    promotion = make_promotion(ohana, discount_percent=20, is_active=True)
    quote = pricing.quote(ohana, [promotion])
    assert quote.best_installments.count == 5
    assert quote.best_installments.amount == Money("87.84")


def test_two_active_promotions_use_largest() -> None:
    product = make_product(price="1000")
    small = make_promotion(product, discount_percent=10, is_active=True, id=1)
    large = make_promotion(product, discount_percent=15, is_active=True, id=2)
    quote = pricing.quote(product, [small, large])
    assert quote.active_promotion == large
    assert quote.promotional_price == Money("850")


# ---------- Ready-to-copy display strings ----------


def test_display_without_promotion() -> None:
    quote = pricing.quote(make_product(), [])
    assert quote.price_display == "R$ 2.199,00"
    assert quote.pix_display == "R$ 2.089,05"
    assert quote.installments_display == "até 12x de R$ 183,25 sem juros"


def test_display_with_active_promotion_has_no_pix() -> None:
    ohana = make_product(id=121, name="Ohana CK-20 Concert Natural", category_id=9, price="549")
    promotion = make_promotion(ohana, discount_percent=20, is_active=True, id=14)
    quote = pricing.quote(ohana, [promotion])
    assert quote.price_display == "de R$ 549,00 por R$ 439,20 (20% off)"
    assert quote.pix_display is None  # R-02: no PIX discount over a promotion
    assert quote.installments_display == "até 5x de R$ 87,84 sem juros"


def test_display_ignores_inactive_promotion() -> None:
    takamine = make_product()
    expired = make_promotion(takamine, discount_percent=12, is_active=False, id=4)
    assert pricing.quote(takamine, [expired]).price_display == "R$ 2.199,00"


def test_installments_display_prefix_and_single() -> None:
    many = pricing.quote(make_product(price="1000"), [])
    assert many.installments_display == "até 10x de R$ 100,00 sem juros"
    single = pricing.quote(make_product(price="80"), [])
    assert single.installments_display == single.best_installments.describe_pt()
