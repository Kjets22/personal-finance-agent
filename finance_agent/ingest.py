"""CSV loading with explicit, documented parsing rules.

Rules:
* Dates: exactly three shapes are accepted -- ``YYYY-MM-DD``, unpadded
  ``YYYY-M-D``, and US ``MM/DD/YYYY``. Anything else is rejected, never guessed.
* Amounts: validated USD fixed-point grammar, then parsed as ``Decimal``.
  ``-0.00`` becomes ``0.00``.
* A bad row goes to the rejected list with a reason; the run continues.
* A missing file or missing required column is unrecoverable (DataError).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import RawRow

# Order matters: this is the reconciliation order (bank is the account of record).
SOURCE_FILES: tuple[str, ...] = (
    "bank_statement.csv",
    "expenses.csv",
    "income.csv",
    "transactions_uncategorized.csv",
)
REQUIRED_COLUMNS = ("Date", "Description", "Amount")

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_US = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


class DataError(Exception):
    """Unrecoverable input problem."""


@dataclass(frozen=True)
class RejectedRow:
    ref: str
    raw: dict
    reason: str


def parse_date(text: str) -> date:
    s = (text or "").strip()
    if m := _ISO.match(s):
        year, month, day = (int(g) for g in m.groups())
    elif m := _US.match(s):
        month, day, year = (int(g) for g in m.groups())
    else:
        raise ValueError(f"unrecognized date format: {text!r}")
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"impossible date {text!r}: {exc}") from None


def parse_amount(text: str) -> Decimal:
    s = (text or "").strip()
    # USD-style fixed-point input only. Do not silently repair malformed money.
    if not re.fullmatch(r"[+-]?\$?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{1,2})?", s):
        raise ValueError(f"unparseable amount: {text!r}; expected dollars and at most two decimals")
    s = s.replace("$", "").replace(",", "")
    if len(s.lstrip("+-").split(".")[0]) > 12:
        raise ValueError(f"amount exceeds supported 12-digit dollar precision: {text!r}")
    try:
        value = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"unparseable amount: {text!r}") from None
    if not value.is_finite():
        raise ValueError(f"non-finite amount: {text!r}")
    return value + Decimal("0")  # normalizes -0.00 to 0.00


def _read_source(path: Path) -> tuple[list[RawRow], list[RejectedRow]]:
    if not path.is_file():
        raise DataError(f"missing input file: {path}")
    rows: list[RawRow] = []
    rejected: list[RejectedRow] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh, strict=True)
        header = [(h or "").strip() for h in (reader.fieldnames or [])]
        if len(header) != len(set(header)) or any(not h for h in header):
            raise DataError(f"{path.name}: duplicate or empty column name")
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise DataError(f"{path.name}: missing required column(s) {missing}")
        reader.fieldnames = header
        for rec in reader:
            ref = f"{path.name}:{reader.line_num}"
            raw = {k: v for k, v in rec.items() if isinstance(k, str) and isinstance(v, str)}
            if None in rec or any(v is None for v in rec.values()):
                rejected.append(RejectedRow(ref, raw, "column count does not match header"))
                continue
            try:
                when = parse_date(rec.get("Date") or "")
                amount = parse_amount(rec.get("Amount") or "")
            except ValueError as exc:
                rejected.append(RejectedRow(ref, raw, str(exc)))
                continue
            description = (rec.get("Description") or "").strip()
            if not description:
                rejected.append(RejectedRow(ref, raw, "empty description"))
                continue
            label = (rec.get("Category") or "").strip() or None
            rows.append(RawRow(path.name, reader.line_num, when, description, amount, label))
    return rows, rejected


def read_source(path: Path) -> tuple[list[RawRow], list[RejectedRow]]:
    try:
        return _read_source(Path(path))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise DataError(f"cannot read {path}: {exc}") from exc


def load_all(data_dir: Path) -> tuple[dict[str, list[RawRow]], list[RejectedRow]]:
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise DataError(f"data directory not found: {data_dir}")
    sources: dict[str, list[RawRow]] = {}
    rejected: list[RejectedRow] = []
    for name in SOURCE_FILES:
        rows, bad = read_source(data_dir / name)
        sources[name] = rows
        rejected.extend(bad)
    return sources, rejected
