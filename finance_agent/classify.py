"""Deterministic kind rules. First match wins.

Kind decides whether money counts as income, spending, or neither, so it
directly affects totals. That makes it code's job, not the model's.
"""

from __future__ import annotations

import re
from decimal import Decimal

from .models import Kind

_TRANSFER = re.compile(r"\btransfer\s+(to|from)\b", re.IGNORECASE)
# Merchants send money back under several names. Matching only "refund" counts a
# return or a reversal as INCOME, which inflates both income and savings_rate.
_REFUND = re.compile(r"\b(refund|return|returned|reversal|chargeback)\b", re.IGNORECASE)
# "PENDING" is not always the first word: "AUTH PENDING TARGET 99" is a pending
# authorization too, and a startswith() check would settle it as real spending.
_PENDING = re.compile(r"\bpending\b", re.IGNORECASE)


def classify_kind(description: str, amount: Decimal) -> Kind:
    if amount == 0 or _PENDING.search(description):
        return Kind.PENDING
    if _TRANSFER.search(description):
        return Kind.TRANSFER
    if amount > 0 and _REFUND.search(description):
        return Kind.REFUND
    if amount > 0:
        return Kind.INCOME
    return Kind.EXPENSE
