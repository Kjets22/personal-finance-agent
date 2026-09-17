"""Core data types.

Amounts are ``Decimal`` and are only ever set by the CSV parser. Transactions
are frozen: categorization produces a *new* record with only ``category``
changed (``dataclasses.replace``), so no model-facing code path can alter an
amount.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class Kind(str, Enum):
    """What a row *is*. Decided by code (see classify.py), never by the model."""

    INCOME = "income"
    EXPENSE = "expense"
    REFUND = "refund"  # money back from a merchant; reduces expenses
    TRANSFER = "transfer"  # between own accounts; excluded from totals
    PENDING = "pending"  # not settled; excluded entirely


class Category(str, Enum):
    INCOME = "Income"
    FOOD = "Food"
    TRANSPORTATION = "Transportation"
    SHOPPING = "Shopping"
    ENTERTAINMENT = "Entertainment"
    HEALTHCARE = "Healthcare"
    HOME = "Home"
    HOUSING = "Housing"
    INSURANCE = "Insurance"
    UTILITIES = "Utilities"
    CASH = "Cash"
    TRANSFER = "Transfer"
    OTHER = "Other"  # the model looked and could not tell
    UNCATEGORIZED = "Uncategorized"  # the system failed to get a valid answer


# The only labels the model may assign to expenses and refunds.
SPENDING_CATEGORIES: tuple[Category, ...] = (
    Category.FOOD,
    Category.TRANSPORTATION,
    Category.SHOPPING,
    Category.ENTERTAINMENT,
    Category.HEALTHCARE,
    Category.HOME,
    Category.HOUSING,
    Category.INSURANCE,
    Category.UTILITIES,
    Category.CASH,
    Category.OTHER,
)

CATEGORY_HINTS: dict[Category, str] = {
    Category.FOOD: "groceries, supermarkets, warehouse clubs, restaurants, coffee, food delivery",
    Category.TRANSPORTATION: "fuel, rideshare, taxis, transit, parking",
    Category.SHOPPING: "general retail, online marketplaces, clothing, electronics",
    Category.ENTERTAINMENT: "streaming, music, apps, games, movies",
    Category.HEALTHCARE: "pharmacy, doctors, medical, gym and fitness",
    Category.HOME: "hardware stores, home improvement, furnishings",
    Category.HOUSING: "rent or mortgage payments",
    Category.INSURANCE: "auto, home, health or life insurance premiums",
    Category.UTILITIES: "internet, cable, electricity, water, phone",
    Category.CASH: "ATM withdrawals",
    Category.OTHER: "cannot be determined from the text",
}

# Deterministic near-miss mapping, used for CSV labels and for model output.
ALIASES: dict[str, Category] = {
    "health": Category.HEALTHCARE,
    "medical": Category.HEALTHCARE,
    "pharmacy": Category.HEALTHCARE,
    "fitness": Category.HEALTHCARE,
    "groceries": Category.FOOD,
    "grocery": Category.FOOD,
    "dining": Category.FOOD,
    "restaurant": Category.FOOD,
    "restaurants": Category.FOOD,
    "food & dining": Category.FOOD,
    "rent": Category.HOUSING,
    "mortgage": Category.HOUSING,
    "gas": Category.TRANSPORTATION,
    "fuel": Category.TRANSPORTATION,
    "transport": Category.TRANSPORTATION,
    "travel": Category.TRANSPORTATION,
    "retail": Category.SHOPPING,
    "streaming": Category.ENTERTAINMENT,
    "subscriptions": Category.ENTERTAINMENT,
    "home improvement": Category.HOME,
    "internet": Category.UTILITIES,
    "atm": Category.CASH,
    "unknown": Category.OTHER,
    "misc": Category.OTHER,
    "miscellaneous": Category.OTHER,
}


def resolve_category(
    label: object, allowed: tuple[Category, ...]
) -> tuple[Category | None, bool]:
    """Map untrusted text to an allowed Category.

    Returns ``(category, via_alias)``; ``(None, False)`` when nothing matches.
    Anything that is not a string is rejected outright.
    """
    if not isinstance(label, str):
        return None, False
    key = " ".join(label.strip().casefold().split())
    if not key:
        return None, False
    for cat in allowed:
        if cat.value.casefold() == key:
            return cat, False
    cat = ALIASES.get(key)
    if cat is not None and cat in allowed:
        return cat, True
    return None, False


@dataclass(frozen=True)
class RawRow:
    """One parsed CSV row, before reconciliation."""

    source_file: str
    line: int
    date: date
    description: str
    amount: Decimal
    label: str | None

    @property
    def ref(self) -> str:
        return f"{self.source_file}:{self.line}"


@dataclass(frozen=True)
class Transaction:
    id: str
    date: date
    description: str
    merchant: str
    amount: Decimal
    kind: Kind
    category: Category | None
    category_source: str  # csv | alias | rule | llm | fallback | none
    sources: tuple[str, ...]

    @property
    def source_files(self) -> frozenset[str]:
        return frozenset(s.rsplit(":", 1)[0] for s in self.sources)

    @property
    def needs_category(self) -> bool:
        return self.kind in (Kind.EXPENSE, Kind.REFUND) and self.category is None
