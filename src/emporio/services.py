"""Business rules from the store manual, as pure functions and classes.

Nothing here reads files, the network or the clock: "now" is always passed in (D-07).
"""

from __future__ import annotations

from dataclasses import dataclass

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
