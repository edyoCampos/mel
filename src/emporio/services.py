"""Business rules from the store manual, as pure functions and classes.

Nothing here reads files, the network or the clock: "now" is always passed in (D-07).
"""

from __future__ import annotations

from dataclasses import dataclass

from emporio.models import Product, Promotion, normalize_text
from emporio.money import Money

# ---------- Installments (R-03, A-03) ----------


@dataclass(frozen=True)
class InstallmentPlan:
    count: int
    amount: Money  # each installment except the last
    last_amount: Money  # absorbs the cent difference (R-03)

    def describe_pt(self) -> str:
        """'12x de R$ 183,25 sem juros' (or 'à vista' for a single payment)."""
        if self.count == 1:
            return f"1x de {self.amount} (à vista)"
        if self.amount == self.last_amount:
            return f"{self.count}x de {self.amount} sem juros"
        return (
            f"{self.count}x sem juros ({self.count - 1}x de {self.amount} "
            f"e 1x de {self.last_amount})"
        )


class InstallmentCalculator:
    """Credit card installments without interest (policy 3.1)."""

    MAX_INSTALLMENTS = 12

    def minimum_installment(self, count: int) -> Money:
        if not 1 <= count <= self.MAX_INSTALLMENTS:
            raise ValueError(f"Installments must be between 1 and {self.MAX_INSTALLMENTS}")
        if count == 1:
            return Money.zero()
        if count <= 3:
            return Money("50")
        if count <= 6:
            return Money("80")
        return Money("100")

    def max_installments(self, price: Money) -> int:
        """Largest count with price >= count x minimum, compared exactly (AS-08)."""
        for count in range(self.MAX_INSTALLMENTS, 0, -1):
            if price >= self.minimum_installment(count) * count:
                return count
        return 1

    def plan(self, price: Money, count: int) -> InstallmentPlan:
        if count > self.max_installments(price):
            raise ValueError(f"{price} cannot be split into {count} installments")
        parts = price.split(count)
        return InstallmentPlan(count=count, amount=parts[0], last_amount=parts[-1])

    def best_plan(self, price: Money) -> InstallmentPlan:
        return self.plan(price, self.max_installments(price))


# ---------- Pricing (R-01, R-02, R-04, A-13) ----------

PIX_DISCOUNT_PERCENT = 5
COMBINED_PAYMENT_THRESHOLD = Money("2000")


@dataclass(frozen=True)
class PriceQuote:
    list_price: Money
    active_promotion: Promotion | None
    promotional_price: Money | None  # None without an active promotion
    effective_price: Money  # promotional price if any, else list price
    pix_price: Money | None  # list price minus 5%; None when a promotion is active (R-02)
    best_installments: InstallmentPlan


class PricingService:
    def __init__(self, installments: InstallmentCalculator | None = None) -> None:
        self._installments = installments or InstallmentCalculator()

    def quote(self, product: Product, promotions: list[Promotion]) -> PriceQuote:
        active = [p for p in promotions if p.is_active and p.product_id == product.id]
        promotion = max(active, key=lambda p: p.discount_percent, default=None)
        list_price = product.price
        if promotion is not None:
            promotional_price = list_price.apply_discount(promotion.discount_percent)
            effective_price = promotional_price
            pix_price = None  # promotions do not stack with PIX (policy 6.2)
        else:
            promotional_price = None
            effective_price = list_price
            pix_price = list_price.apply_discount(PIX_DISCOUNT_PERCENT)
        return PriceQuote(
            list_price=list_price,
            active_promotion=promotion,
            promotional_price=promotional_price,
            effective_price=effective_price,
            pix_price=pix_price,
            best_installments=self._installments.best_plan(effective_price),
        )

    def can_combine_payment_methods(self, total: Money) -> bool:
        """Combining payment methods is allowed only above R$ 2.000,00 (policy 3.1)."""
        return total > COMBINED_PAYMENT_THRESHOLD


# ---------- Shipping (R-05, A-19, A-24, A-25) ----------

DRUMS_CATEGORY_ID = 3  # "Baterias e Percussão"


class ShippingRules:
    LOCAL_CITY = "Campo Grande"
    FREE_SHIPPING_ABOVE = Money("500")
    LOCAL_FEE = Money("35")

    def is_local(self, city: str) -> bool:
        """Only the municipality of Campo Grande gets the local rule (A-24)."""
        return normalize_text(city) == normalize_text(self.LOCAL_CITY)

    def local_delivery_fee(self, order_amount: Money) -> Money:
        """Free strictly above R$ 500,00, otherwise R$ 35,00 (policy 5.1, A-19)."""
        if order_amount > self.FREE_SHIPPING_ABOVE:
            return Money.zero()
        return self.LOCAL_FEE

    def requires_special_quote(self, product: Product) -> bool:
        """Acoustic drum kits need an individual freight quote (policy 5.2).

        Electric basses are not the large acoustic "contrabaixos" of the manual (A-25).
        """
        return product.category_id == DRUMS_CATEGORY_ID and "bateria acustica" in normalize_text(
            product.name
        )
