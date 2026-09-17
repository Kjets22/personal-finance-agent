"""Optional extra: deterministic duplicate/anomaly flags. Nothing is dropped."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Sequence

from .models import Category, Kind, Transaction
from .totals import Totals, to_money

LARGE_EXPENSE_SHARE = Decimal("0.30")
UNVERIFIED_DEPOSIT_MIN = Decimal("100")
MANUAL_FILES = frozenset({"expenses.csv", "income.csv"})
STATEMENT_ONLY = frozenset({"transactions_uncategorized.csv"})
_P2P = re.compile(r"\b(venmo|zelle|cash app)\b", re.IGNORECASE)
_KNOWN_PURPOSE = re.compile(r"\b(landlord|rent)\b", re.IGNORECASE)


def detect_flags(
    txns: Sequence[Transaction],
    bank_period: tuple[date, date] | None,
    totals: Totals,
) -> list[dict]:
    flags: list[dict] = []

    def add(t: Transaction, reason: str) -> None:
        flags.append({
            "id": t.id,
            "date": t.date.isoformat(),
            "description": t.description,
            "amount": to_money(t.amount),
            "reason": reason,
        })

    for t in txns:
        files = t.source_files
        if bank_period and files <= MANUAL_FILES:
            start, end = bank_period
            listed_in = ", ".join(sorted(files))
            if start <= t.date <= end:
                add(t, f"Listed in {listed_in} but not found in bank_statement.csv")
            else:
                add(t, f"Listed in {listed_in} but dated outside the bank statement period "
                       f"({start.isoformat()} to {end.isoformat()}); could not be verified")
        if t.kind is Kind.INCOME and files == STATEMENT_ONLY and t.amount >= UNVERIFIED_DEPOSIT_MIN:
            add(t, "Deposit not recorded in income.csv; confirm its source")
        if t.kind is Kind.REFUND and not any(
            o.kind is Kind.EXPENSE and o.amount == -t.amount for o in txns
        ):
            add(t, "Refund with no matching charge in this period")
        if t.kind is Kind.EXPENSE and _P2P.search(t.description) and not _KNOWN_PURPOSE.search(t.description):
            add(t, "Person-to-person payment with unknown purpose")
        if t.kind is Kind.EXPENSE and totals.expenses > 0 and -t.amount > totals.expenses * LARGE_EXPENSE_SHARE:
            share = (-t.amount / totals.expenses * 100).quantize(Decimal("1"))
            add(t, f"Single payment is {share}% of this month's spending")
        if t.category is Category.UNCATEGORIZED:
            add(t, "Category could not be determined (model failure)")

    groups: dict[tuple, list[Transaction]] = defaultdict(list)
    for t in txns:
        if t.kind is Kind.EXPENSE and len(t.source_files) == 1:
            groups[(next(iter(t.source_files)), t.amount, t.merchant)].append(t)
    for (file, _, _), members in groups.items():
        dates = sorted({m.date for m in members})
        if len(members) > 1 and len(dates) > 1:
            add(members[0], "Possible duplicate charge: same merchant and amount on "
                + " and ".join(d.isoformat() for d in dates) + f" in {file}; all kept")
    return sorted(flags, key=lambda f: (f["date"], f["id"]))
