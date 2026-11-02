"""Business rules from the store manual, as pure functions and classes.

Nothing here reads files, the network or the clock: "now" is always passed in (D-07).
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import holidays

from emporio.models import Customer, Order, OrderStatus, Product, Promotion, normalize_text
from emporio.money import Money

# ---------- Customer language (D-19, AS-82, AS-84) ----------

Language = Literal["pt", "en"]
# The language of the customer's conversation, set by the Agent for each turn and read by the
# tools that write customer-facing phrases (next opening, response time, order labels).
CUSTOMER_LANGUAGE: ContextVar[Language] = ContextVar("customer_language", default="pt")

# Function words that exist in only one of the two languages ("a", "no", "do" are in both).
_EN_WORDS = frozenset(
    "the is are was my your you i me where what when how why please yes thanks thank hi "  # noqa: SIM905
    "hello and to for with can could would will it this that have has order ticket of".split()
)
_PT_WORDS = frozenset(
    "o os as de da das dos que é não seu sua meu minha para com um uma você está estou "  # noqa: SIM905
    "por em na mas já oi olá obrigado obrigada pedido tudo bem cadê quero posso quanto custa "
    "qual quais tem vocês".split()
)
_WORD = re.compile(r"[^\W\d_]+")


def detect_language(text: str) -> Language | None:
    """'en' or 'pt' when at least 2 function words of one language outnumber the other's;
    None for short or ambiguous text ("ok", a product name)."""
    words = _WORD.findall(text.lower())
    english = sum(w in _EN_WORDS for w in words)
    portuguese = sum(w in _PT_WORDS for w in words)
    if english >= 2 and english > portuguese:
        return "en"
    if portuguese >= 2 and portuguese > english:
        return "pt"
    return None


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

    # Ready-to-copy strings: the model copies them instead of composing prices (AS-78)
    @property
    def price_display(self) -> str:
        """'de R$ 549,00 por R$ 439,20 (20% off)' with an active promotion, else 'R$ 2.199,00'."""
        if self.active_promotion is not None and self.promotional_price is not None:
            return (
                f"de {self.list_price} por {self.promotional_price} "
                f"({self.active_promotion.discount_percent}% off)"
            )
        return str(self.list_price)

    @property
    def pix_display(self) -> str | None:
        """'R$ 2.089,05' (the prompt adds "PIX:"); None with an active promotion (R-02)."""
        return str(self.pix_price) if self.pix_price is not None else None

    @property
    def installments_display(self) -> str:
        """'até 12x de R$ 183,25 sem juros' (single payment keeps describe_pt)."""
        plan = self.best_installments.describe_pt()
        return plan if self.best_installments.count == 1 else f"até {plan}"


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


# ---------- Returns and warranty (R-06, R-07, R-08, A-11, A-18) ----------

REGRET_DAYS = 7  # policy 4.1, from receipt
PREFERENCE_EXCHANGE_DAYS = 7  # policy 4.3, from receipt (A-18)
DEFECT_EXCHANGE_DAYS = 30  # policy 4.2, from purchase
LEGAL_WARRANTY_DAYS = 90  # policy 8.1, from receipt


@dataclass(frozen=True)
class Window:
    deadline: date
    is_open: bool  # today <= deadline (inclusive, AS-16)


@dataclass(frozen=True)
class ReturnWindowsResult:
    receipt_date: date  # estimated_delivery is used as the receipt date (A-11)
    regret: Window
    preference_exchange: Window
    defect_exchange: Window
    legal_warranty: Window


class ReturnWindows:
    def compute(self, order: Order, today: date) -> ReturnWindowsResult | None:
        """Deadlines for a delivered order; None for any other order."""
        if order.status is not OrderStatus.DELIVERED or order.estimated_delivery is None:
            return None
        receipt = order.estimated_delivery

        def window(start: date, days: int) -> Window:
            deadline = start + timedelta(days=days)
            return Window(deadline=deadline, is_open=today <= deadline)

        return ReturnWindowsResult(
            receipt_date=receipt,
            regret=window(receipt, REGRET_DAYS),
            preference_exchange=window(receipt, PREFERENCE_EXCHANGE_DAYS),
            defect_exchange=window(order.order_date, DEFECT_EXCHANGE_DAYS),
            legal_warranty=window(receipt, LEGAL_WARRANTY_DAYS),
        )


# ---------- Delay (A-20) ----------

MAX_DAYS_WITHOUT_SHIPPING = 7


@dataclass(frozen=True)
class DelayAssessment:
    is_delayed: bool
    reason: str | None  # "estimated_delivery_passed" | "not_shipped_after_7_days" | None


class DelayAssessor:
    def assess(self, order: Order, today: date) -> DelayAssessment:
        """The manual has no dispatch deadline, so A-20 defines when an order is late."""
        shipped_late = (
            order.status is OrderStatus.SHIPPED
            and order.estimated_delivery is not None
            and order.estimated_delivery < today
        )
        if shipped_late:
            return DelayAssessment(True, "estimated_delivery_passed")
        not_shipped_late = order.status in (
            OrderStatus.PENDING,
            OrderStatus.CONFIRMED,
        ) and today > order.order_date + timedelta(days=MAX_DAYS_WITHOUT_SHIPPING)
        if not_shipped_late:
            return DelayAssessment(True, "not_shipped_after_7_days")
        return DelayAssessment(False, None)


# ---------- Identity (R-13, A-16, NFR-03) ----------


class VerificationInputError(ValueError):
    """Neither e-mail nor phone was provided."""


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


class CustomerVerifier:
    def verify(self, customer: Customer, email: str | None, phone: str | None) -> bool:
        """True when the e-mail or phone matches the customer who owns the order.

        Always called with the order's owner: verification is by the pair order + contact,
        never by contact alone, because two customers share an e-mail (A-16).
        """
        if not email and not phone:
            raise VerificationInputError("An e-mail or a phone number is required")
        if email and email.strip().lower() == customer.email.strip().lower():
            return True
        return bool(phone) and _digits(phone or "") == _digits(customer.phone)


def mask_email(email: str) -> str:
    """'anacarol.ferreira@coldmail.com' -> 'a***@coldmail.com'."""
    local, _, domain = email.strip().partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


def mask_phone(phone: str) -> str:
    """'(67) 99654-7821' -> '(67) *****-7821': keeps the area code and the last 4 digits."""
    total = len(_digits(phone))
    seen = 0
    masked = []
    for char in phone:
        if char.isdigit():
            seen += 1
            keep = seen <= 2 or seen > total - 4
            masked.append(char if keep else "*")
        else:
            masked.append(char)
    return "".join(masked)


# ---------- Store hours (R-10, R-15, A-17) ----------

STORE_TZ = ZoneInfo("America/Campo_Grande")  # UTC-04:00 (AS-04)

# weekday -> (opening, closing); Sunday is closed (policy 2)
_SCHEDULE: dict[int, tuple[time, time]] = {
    0: (time(9), time(18)),
    1: (time(9), time(18)),
    2: (time(9), time(18)),
    3: (time(9), time(18)),
    4: (time(9), time(18)),
    5: (time(9), time(13)),
}
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKDAYS_PT = [
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
]


def describe_opening(opening: datetime, language: Language = "pt") -> str:
    """'terça-feira, 03/11/2026, às 09:00' or 'Tuesday, 03/11/2026, at 09:00' (AS-80, AS-84)."""
    if language == "en":
        return f"{_WEEKDAYS[opening.weekday()]}, {opening:%d/%m/%Y}, at {opening:%H:%M}"
    return f"{WEEKDAYS_PT[opening.weekday()]}, {opening:%d/%m/%Y}, às {opening:%H:%M}"


@dataclass(frozen=True)
class StoreStatus:
    is_open: bool
    now_local: datetime
    closes_at: datetime | None  # when open
    next_opening: datetime | None  # when closed
    closed_reason: str | None  # "sunday" | "holiday:<name>" | "outside_hours"


class StoreHours:
    """Mon-Fri 09:00-18:00, Sat 09:00-13:00, closed on Sundays and national holidays."""

    def __init__(self) -> None:
        self._holidays = holidays.country_holidays("BR")

    def _to_local(self, now: datetime) -> datetime:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return now.astimezone(STORE_TZ)

    def holiday_name(self, day: date) -> str | None:
        return self._holidays.get(day)

    def _hours_for(self, day: date) -> tuple[time, time] | None:
        if self.holiday_name(day) is not None:
            return None
        return _SCHEDULE.get(day.weekday())

    def status(self, now: datetime) -> StoreStatus:
        local = self._to_local(now)
        today = local.date()
        hours = self._hours_for(today)
        if hours is not None:
            opening, closing = hours
            if opening <= local.time() < closing:
                closes_at = datetime.combine(today, closing, tzinfo=STORE_TZ)
                return StoreStatus(True, local, closes_at, None, None)
        holiday = self.holiday_name(today)
        if holiday is not None:
            reason = f"holiday:{holiday}"
        elif today.weekday() == 6:
            reason = "sunday"
        else:
            reason = "outside_hours"
        return StoreStatus(False, local, None, self.next_opening(local), reason)

    def next_opening(self, now: datetime) -> datetime:
        """First opening time strictly after `now`."""
        local = self._to_local(now)
        day = local.date()
        for _ in range(30):
            hours = self._hours_for(day)
            if hours is not None:
                opening = datetime.combine(day, hours[0], tzinfo=STORE_TZ)
                if opening > local:
                    return opening
            day += timedelta(days=1)
        raise RuntimeError("No opening found in the next 30 days")

    def describe(self, now: datetime, language: Language = "pt") -> str:
        """One English line for the system prompt (D-18); the next opening is written in the
        customer's language, ready to copy."""
        status = self.status(now)
        local = status.now_local
        head = f"{_WEEKDAYS[local.weekday()]}, {local:%d/%m/%Y %H:%M} (America/Campo_Grande)."
        if status.is_open and status.closes_at is not None:
            return f"{head} Store is OPEN until {status.closes_at:%H:%M}."
        assert status.next_opening is not None
        opening = status.next_opening
        line = f"{head} Store is CLOSED. Next opening: {describe_opening(opening, language)}"
        skipped = []
        day = local.date() + timedelta(days=1)
        while day < opening.date():
            name = self.holiday_name(day)
            if name is not None:
                skipped.append(f"{_WEEKDAYS[day.weekday()]} {day:%d/%m} is {name}")
            day += timedelta(days=1)
        if skipped:
            line += f" ({'; '.join(skipped)})"
        return line + "."
