"""Merchant cleaning. Used for matching and for prompts; raw text is kept."""

from __future__ import annotations

import re

_PREFIXES = (
    re.compile(r"^sq \*\s*"),
    re.compile(r"^tst\*\s*"),
    re.compile(r"^paypal \*\s*"),
    re.compile(r"^pos debit\s+"),
    re.compile(r"^checkcard \d+\s+"),
    re.compile(r"^ach credit\s+"),
)
_STORE_NUMBER = re.compile(r"#\d+")
_SEPARATORS = re.compile(r"[*/.]")
_DROP_TOKENS = {"com", "usa", "inc"}

# Words too generic to prove two descriptions refer to the same event.
STOPWORDS = frozenset(
    {"payment", "store", "the", "fee", "deposit", "subscription", "to", "from",
     "debit", "credit", "pos", "of", "and", "purchase"}
)


def clean_merchant(description: str) -> str:
    s = description.casefold().strip()
    for pattern in _PREFIXES:
        s = pattern.sub("", s)
    s = _STORE_NUMBER.sub(" ", s)
    s = _SEPARATORS.sub(" ", s)
    tokens = [
        t for t in s.split()
        if t not in _DROP_TOKENS and not any(ch.isdigit() for ch in t)
    ]
    return " ".join(tokens)


def match_tokens(description: str) -> frozenset[str]:
    return frozenset(clean_merchant(description).split()) - STOPWORDS
