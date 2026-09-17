"""THE arithmetic. Every total in the report is computed here and nowhere else.

This module imports nothing that touches the model. It reads only ``amount``
(set by the CSV parser), ``kind`` (set by classify.py) and ``category``. The
model can influence which bucket a fixed amount lands in -- nothing more.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from .models import Category, Kind

CENT = Decimal("0.01")
RATE_PLACES = Decimal("0.0001")
ZERO = Decimal("0")


@dataclass(frozen=True)
class Totals:
    income: Decimal
    expenses: Decimal  # positive: money out, net of refunds
    net: Decimal
    savings_rate: Decimal  # fraction, e.g. 0.6445
    by_category: dict[str, Decimal]  # sorted, largest first; sums to expenses
    transfers: Decimal  # positive magnitude, excluded from income/expenses
    gross_spending: Decimal
    refunds: Decimal


def compute_totals(transactions: Sequence) -> Totals:
    income = ZERO
    spent = ZERO
    refunded = ZERO
    transfers = ZERO
    buckets: dict[str, Decimal] = defaultdict(lambda: ZERO)

    for t in transactions:
        if t.kind is Kind.INCOME:
            income += t.amount
        elif t.kind is Kind.EXPENSE:
            spent += -t.amount
            buckets[(t.category or Category.UNCATEGORIZED).value] += -t.amount
        elif t.kind is Kind.REFUND:
            refunded += t.amount
            buckets[(t.category or Category.UNCATEGORIZED).value] -= t.amount
        elif t.kind is Kind.TRANSFER:
            transfers += abs(t.amount)

    expenses = spent - refunded
    net = income - expenses
    rate = (net / income).quantize(RATE_PLACES) if income > 0 else ZERO
    by_category = dict(sorted(buckets.items(), key=lambda kv: (-kv[1], kv[0])))
    return Totals(income, expenses, net, rate, by_category, transfers, spent, refunded)


def to_money(value: Decimal) -> float:
    """The only place money leaves Decimal: at the JSON boundary."""
    return float(value.quantize(CENT)) + 0.0


def to_rate(value: Decimal) -> float:
    return float(value.quantize(RATE_PLACES)) + 0.0
