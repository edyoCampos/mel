"""Exact BRL amounts (NFR-04, D-10).

Money never uses float: a one-cent error in a price is misleading advertising,
as the store manual warns (policy 7.1).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import total_ordering
from typing import Self

CENT = Decimal("0.01")


def _to_decimal(value: Decimal | int | str) -> Decimal:
    if isinstance(value, bool | float):
        raise ValueError("Money does not accept float or bool; use str, int or Decimal")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, str):
        try:
            number = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"Invalid money amount: {value!r}") from exc
    else:
        raise ValueError(f"Unsupported money type: {type(value).__name__}")
    if not number.is_finite():
        raise ValueError(f"Money must be finite: {value!r}")
    return number


@total_ordering
class Money:
    """Immutable BRL amount, always quantized to cents with ROUND_HALF_UP."""

    __slots__ = ("_amount",)

    def __init__(self, amount: Decimal | int | str) -> None:
        self._amount = _to_decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)

    @classmethod
    def zero(cls) -> Self:
        return cls(0)

    @property
    def amount(self) -> Decimal:
        return self._amount

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self._amount + other._amount)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self._amount - other._amount)

    def __mul__(self, factor: Decimal | int) -> Money:
        return Money(self._amount * _to_decimal(factor))

    __rmul__ = __mul__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._amount == other._amount

    def __lt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._amount < other._amount

    def __hash__(self) -> int:
        return hash(self._amount)

    def __repr__(self) -> str:
        return f"Money('{self._amount}')"

    def apply_discount(self, percent: Decimal | int) -> Money:
        """Return self x (1 - percent/100), rounded ROUND_HALF_UP."""
        pct = _to_decimal(percent)
        if not Decimal(0) <= pct <= Decimal(100):
            raise ValueError(f"Discount must be between 0 and 100: {percent}")
        return Money(self._amount * (Decimal(100) - pct) / Decimal(100))

    def split(self, parts: int) -> list[Money]:
        """Split into installments; the last one absorbs the cent difference (R-03)."""
        if parts < 1:
            raise ValueError("parts must be at least 1")
        regular = Money(self._amount / Decimal(parts))
        last = self - regular * (parts - 1)
        return [regular] * (parts - 1) + [last]

    def format_brl(self) -> str:
        """Format as 'R$ 2.199,00'."""
        sign = "-" if self._amount < 0 else ""
        text = f"{abs(self._amount):,.2f}"  # 2,199.00
        text = text.replace(",", "_").replace(".", ",").replace("_", ".")
        return f"{sign}R$ {text}"

    def __str__(self) -> str:
        return self.format_brl()
