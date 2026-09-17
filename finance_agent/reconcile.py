"""Cross-source reconciliation.

The four files overlap: the bank statement repeats most of expenses.csv and
income.csv (usually one day later), and the card export repeats Netflix,
Spotify and car insurance. Summing all files would double-count January.

Merge rule -- two rows from DIFFERENT files are one event when:
  1. amounts are exactly equal,
  2. kinds are equal,
  3. dates are within MERGE_WINDOW_DAYS of every row already in the group,
  4. their cleaned descriptions share a non-stopword token with every group row.

Clause 4 has one deliberate relaxation. A statement sometimes renames a charge
past recognition -- "Dentist Visit" in expenses.csv against "POS PURCHASE 4471"
on the statement -- and refusing that merge double-counts a real charge. So the
rows are merged anyway, with a review warning that marks the report degraded,
but ONLY when all of these hold:

  * clauses 1-3 hold,
  * the candidate group is a SINGLE row, and one of the two descriptions has no
    merchant word at all -- so token matching is impossible, not just unsuccessful,
  * and it is the only such candidate.

The single-row condition is what separates a renamed charge from the transitive
A->AB->B hazard: a row sharing a token with some but not all members of a bigger
group is exactly the false merge clause 4 exists to stop, and it never merges.
Uniqueness stops the rest: if two groups qualify weakly, nothing merges.

Dates must also agree with every group row. Tied nearest matches stay separate
with a review warning.

A canonical row absorbs at most one row per file, and rows are never merged
within the same file (same-file look-alikes are flagged instead, see flags.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .classify import classify_kind
from .ingest import SOURCE_FILES, RejectedRow
from .models import (
    SPENDING_CATEGORIES,
    Category,
    Kind,
    RawRow,
    Transaction,
    resolve_category,
)
from .normalize import clean_merchant, match_tokens
from .totals import to_money

MERGE_WINDOW_DAYS = 2

# Lower is preferred when choosing the display description.
_DESCRIPTION_PRIORITY = {
    "expenses.csv": 0,
    "income.csv": 0,
    "bank_statement.csv": 1,
    "transactions_uncategorized.csv": 2,
}


@dataclass
class _Group:
    rows: list[RawRow]
    kind: Kind
    tokens: set[str]

    @property
    def date(self) -> date:
        # Rows are appended in SOURCE_FILES order, so a bank row comes first
        # and its posting date wins.
        return self.rows[0].date

    @property
    def amount(self):
        return self.rows[0].amount

    @property
    def files(self) -> set[str]:
        return {r.source_file for r in self.rows}


@dataclass
class Ledger:
    transactions: list[Transaction]
    excluded: list[dict]
    bank_period: tuple[date, date] | None
    period: tuple[date, date] | None
    warnings: list[str] = field(default_factory=list)


def _find_match(
    groups: list[_Group], row: RawRow, kind: Kind, tokens: frozenset[str]
) -> tuple[_Group | None, str | None]:
    """Find the group this row belongs to.

    Returns (group, weak_reason). A non-None weak_reason means the match rests on
    amount, kind and date alone with no shared merchant token, and the caller must
    record a review warning.
    """
    strong: list[tuple[int, _Group]] = []
    weak: list[tuple[int, _Group]] = []
    for g in groups:
        if g.kind is not kind or g.amount != row.amount or row.source_file in g.files:
            continue
        # Every source must agree on the date: a group is one event, not a chain.
        if not all(abs((r.date - row.date).days) <= MERGE_WINDOW_DAYS for r in g.rows):
            continue
        distance = abs((g.date - row.date).days)
        # Every source must agree on a token too: token unions allow A->AB->B
        # false merges, so the shared token is required pairwise, not in aggregate.
        if all(bool(match_tokens(r.description) & tokens) for r in g.rows):
            strong.append((distance, g))
        elif len(g.rows) == 1 and not (tokens and match_tokens(g.rows[0].description)):
            # Eligible for the relaxation only when the group is a SINGLE row AND
            # one of the two descriptions yields no merchant word at all -- a bare
            # reference like "POS PURCHASE 4471". Then token matching is
            # impossible, not merely unsuccessful, so amount+kind+date is the best
            # evidence available.
            #
            # Two descriptions that BOTH carry merchant words which simply differ
            # ("Alpha" vs "Beta") are different merchants and never merge. Sharing
            # a token with some but not all rows of a bigger group is the
            # transitive A->AB->B hazard, and never merges either.
            weak.append((distance, g))

    if strong:
        gap = min(item[0] for item in strong)
        nearest = [g for distance, g in strong if distance == gap]
        if len(nearest) != 1:
            refs = [g.rows[0].ref for g in nearest]
            raise AmbiguousMatch(
                f"{row.ref}: equally plausible matches {refs}; kept separate for review"
            )
        return nearest[0], None

    # No token in common with anything. Merge only if the choice is unambiguous,
    # and never silently: the warning marks the whole report degraded.
    if len(weak) == 1:
        group = weak[0][1]
        return group, (
            f"{row.ref} ({row.description!r}) matched {group.rows[0].ref} "
            f"({group.rows[0].description!r}) on amount, kind and date only - no shared "
            f"merchant word. Merged to avoid double-counting; verify it is one charge"
        )
    return None, None


class AmbiguousMatch(ValueError):
    """A heuristic match is not strong enough to discard a source row."""


def _initial_category(kind: Kind, rows: list[RawRow], warnings: list[str]) -> tuple[Category | None, str]:
    if kind is Kind.INCOME:
        return Category.INCOME, "rule"
    if kind is Kind.TRANSFER:
        return Category.TRANSFER, "rule"
    labels = []
    for r in rows:
        if not r.label:
            continue
        cat, via_alias = resolve_category(r.label, SPENDING_CATEGORIES)
        if cat is not None:
            labels.append((cat, "alias" if via_alias else "csv"))
        else:
            warnings.append(f"{r.ref}: unrecognized CSV category {r.label!r}; sent to the model instead")
    if len({cat for cat, _ in labels}) > 1:
        warnings.append(f"reconciliation: conflicting CSV categories in {[r.ref for r in rows]}; first valid label retained")
    return labels[0] if labels else (None, "none")


def build_ledger(sources: dict[str, list[RawRow]], rejected: list[RejectedRow]) -> Ledger:
    groups: list[_Group] = []
    excluded: list[dict] = []
    warnings: list[str] = []
    all_dates: list[date] = []

    for name in SOURCE_FILES:
        for row in sources.get(name, []):
            all_dates.append(row.date)
            kind = classify_kind(row.description, row.amount)
            if kind is Kind.PENDING:
                excluded.append({
                    "date": row.date.isoformat(),
                    "description": row.description,
                    "amount": to_money(row.amount),
                    "source": row.ref,
                    "reason": "pending authorization / zero amount; not a settled transaction",
                })
                continue
            tokens = match_tokens(row.description)
            try:
                match, weak_reason = _find_match(groups, row, kind, tokens)
            except AmbiguousMatch as exc:
                warnings.append(f"reconciliation: {exc}")
                match, weak_reason = None, None
            if weak_reason:
                warnings.append(f"reconciliation: {weak_reason}")
            if match is None:
                groups.append(_Group([row], kind, set(tokens)))
            else:
                match.rows.append(row)
                match.tokens |= tokens

    for bad in rejected:
        excluded.append({"source": bad.ref, "raw": bad.raw, "reason": f"rejected: {bad.reason}"})

    order = {name: i for i, name in enumerate(SOURCE_FILES)}
    groups.sort(key=lambda g: (g.date, order[g.rows[0].source_file], g.rows[0].line))

    transactions: list[Transaction] = []
    for n, g in enumerate(groups, start=1):
        display = min(g.rows, key=lambda r: _DESCRIPTION_PRIORITY.get(r.source_file, 9))
        category, cat_source = _initial_category(g.kind, g.rows, warnings)
        transactions.append(Transaction(
            id=f"t{n:03d}",
            date=g.date,
            description=display.description,
            merchant=clean_merchant(display.description),
            amount=g.amount,
            kind=g.kind,
            category=category,
            category_source=cat_source,
            sources=tuple(r.ref for r in g.rows),
        ))

    bank_dates = [r.date for r in sources.get("bank_statement.csv", [])]
    bank_period = (min(bank_dates), max(bank_dates)) if bank_dates else None
    period = (min(all_dates), max(all_dates)) if all_dates else None
    return Ledger(transactions, excluded, bank_period, period, warnings)
