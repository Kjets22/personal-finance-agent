"""Mutable run state shared by the tools, the loop and the report writer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .llm import LLMClient
from .models import Transaction
from .totals import Totals


@dataclass
class AgentState:
    llm: LLMClient
    out_path: Path
    transactions: list[Transaction]
    excluded: list[dict]
    bank_period: tuple[date, date] | None
    period: tuple[date, date] | None
    warnings: list[str] = field(default_factory=list)
    totals: Totals | None = None  # set by compute_totals; cleared when categories change
    report: dict | None = None
    summary_source: str | None = None
    trace: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._index = {t.id: i for i, t in enumerate(self.transactions)}

    def get(self, tid: str) -> Transaction | None:
        i = self._index.get(tid)
        return None if i is None else self.transactions[i]

    def update(self, txn: Transaction) -> None:
        self.transactions[self._index[txn.id]] = txn

    def needing(self) -> list[Transaction]:
        return [t for t in self.transactions if t.needs_category]
