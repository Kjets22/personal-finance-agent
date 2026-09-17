"""Report assembly, summary validation, and writing.

finalize_report() always recomputes totals itself. Nothing a model said in an
earlier step is read back into the numbers.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from .flags import detect_flags
from .fileio import atomic_write_texts
from .models import Category, Kind
from .state import AgentState
from .totals import CENT, Totals, compute_totals, to_money, to_rate

MAX_SUMMARY_CHARS = 1200
PLACEHOLDERS = (
    "period", "income", "expenses", "net", "savings_rate", "top_category",
    "top_category_amount", "transaction_count", "transfers", "flag_count",
)
_PLACEHOLDER = re.compile(r"\{([A-Za-z_]+)\}")
_DIGIT = re.compile(r"\d")

TEMPLATE = (
    "In {period} you received {income} and spent {expenses}, leaving {net} "
    "({savings_rate} savings rate). The largest spending category was {top_category} "
    "at {top_category_amount}. {transfers} moved between your own accounts and is not "
    "counted as spending. {flag_count} items are flagged for review."
)


# The model selects wording; it cannot relabel a computed value or add claims.
# Whitespace is flexible, but every accepted sentence has reviewed semantics.
APPROVED_SUMMARIES = (
    TEMPLATE,
    "Income was {income}; net expenses were {expenses}; the remaining amount was {net} "
    "({savings_rate} savings rate). {flag_count} items are flagged for review.",
    "For {period}, recorded income was {income} and net expenses were {expenses}. "
    "The net amount was {net}, with a savings rate of {savings_rate}. "
    "Review the {flag_count} flagged items before relying on the report.",
)


class OutputError(Exception):
    """The report could not be written. Unrecoverable."""


def fmt_money(value: Decimal) -> str:
    q = value.quantize(CENT)
    return f"-${abs(q):,}" if q < 0 else f"${q:,}"


def fmt_rate(value: Decimal) -> str:
    return f"{(value * 100).quantize(CENT)}%"


def period_label(state: AgentState) -> str:
    if not state.period:
        return "this period"
    start, end = state.period
    if (start.year, start.month) == (end.year, end.month):
        return start.strftime("%B %Y")
    return f"{start.isoformat()} to {end.isoformat()}"


def summary_values(state: AgentState, totals: Totals, flags: list[dict]) -> dict[str, str]:
    top_name, top_amount = next(iter(totals.by_category.items()), ("none", Decimal("0")))
    return {
        "period": period_label(state),
        "income": fmt_money(totals.income),
        "expenses": fmt_money(totals.expenses),
        "net": fmt_money(totals.net),
        "savings_rate": fmt_rate(totals.savings_rate),
        "top_category": top_name,
        "top_category_amount": fmt_money(top_amount),
        "transaction_count": str(len(state.transactions)),
        "transfers": fmt_money(totals.transfers),
        "flag_count": str(len(flags)),
    }


def validate_summary(draft: object) -> str | None:
    """Return a problem description, or None if the draft is acceptable."""
    if not isinstance(draft, str) or not draft.strip():
        return "summary is empty or not text"
    if len(draft) > MAX_SUMMARY_CHARS:
        return f"summary longer than {MAX_SUMMARY_CHARS} characters"
    unknown = sorted(set(_PLACEHOLDER.findall(draft)) - set(PLACEHOLDERS))
    if unknown:
        return f"unknown placeholders {unknown}"
    rest = _PLACEHOLDER.sub("", draft)
    if _DIGIT.search(rest):
        return "contains digits outside placeholders"
    if "{" in rest or "}" in rest:
        return "contains stray braces"
    normalized = " ".join(draft.split())
    if normalized not in {" ".join(template.split()) for template in APPROVED_SUMMARIES}:
        return "wording is not an approved factual summary template"
    return None


def render_summary(draft: object, values: dict[str, str]) -> tuple[str, str, str | None]:
    """Return (text, source, problem). source is 'llm' or 'template'."""
    if draft is None:
        return _fill(TEMPLATE, values), "template", None
    problem = validate_summary(draft)
    if problem:
        return _fill(TEMPLATE, values), "template", problem
    # Not "llm": the model only SELECTED one of APPROVED_SUMMARIES and code
    # filled the placeholders. Calling that "llm" overstates what it wrote.
    return _fill(draft.strip(), values), "llm_selected_template", None


def _fill(text: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], text)


def _txn_dict(t) -> dict:
    return {
        "id": t.id,
        "date": t.date.isoformat(),
        "description": t.description,
        "amount": to_money(t.amount),
        "category": (t.category or Category.UNCATEGORIZED).value,
        "kind": t.kind.value,
        "category_source": t.category_source,
        "sources": list(t.sources),
    }


def build_report(state: AgentState, totals: Totals, flags: list[dict],
                 summary: str, summary_source: str, status: str) -> dict:
    transfers = [t for t in state.transactions if t.kind is Kind.TRANSFER]
    return {
        "status": status,
        "period": {
            "start": state.period[0].isoformat() if state.period else None,
            "end": state.period[1].isoformat() if state.period else None,
        },
        "totals": {
            "income": to_money(totals.income),
            "expenses": to_money(totals.expenses),
            "net": to_money(totals.net),
            "savings_rate": to_rate(totals.savings_rate),
        },
        "by_category": {k: to_money(v) for k, v in totals.by_category.items()},
        "flagged": flags,
        "transactions": [_txn_dict(t) for t in state.transactions],
        "transfers": {
            "total": to_money(totals.transfers),
            "items": [{"id": t.id, "date": t.date.isoformat(), "description": t.description,
                       "amount": to_money(t.amount)} for t in transfers],
        },
        "excluded": state.excluded,
        "warnings": list(state.warnings),
        "agent_trace": state.trace,
        "llm": {"model": state.llm.model, "summary_source": summary_source},
        "summary": summary,
    }


def _summary_markdown(report: dict, period: str) -> str:
    t = report["totals"]
    lines = [
        f"# Monthly summary: {period}", "",
        f"Status: **{report['status']}**", "",
        report["summary"], "",
        "| Total | Amount |", "| --- | ---: |",
        f"| Income | {t['income']:,.2f} |",
        f"| Expenses | {t['expenses']:,.2f} |",
        f"| Net | {t['net']:,.2f} |",
        f"| Savings rate | {t['savings_rate'] * 100:.2f}% |", "",
        "## Spending by category", "",
        "| Category | Amount |", "| --- | ---: |",
    ]
    lines += [f"| {k} | {v:,.2f} |" for k, v in report["by_category"].items()]
    lines += ["", f"## Flagged ({len(report['flagged'])})", ""]
    lines += [f"- {f['date']} {f['description']}: {f['reason']}" for f in report["flagged"]] or ["- none"]
    if report["warnings"]:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in report["warnings"]]
    return "\n".join(lines) + "\n"


def summary_path_for(out_path: Path) -> Path:
    return out_path.with_name(out_path.stem + "_summary.md")


def finalize_report(state: AgentState, draft: object, agent_finished: bool) -> dict:
    totals = compute_totals(state.transactions)  # always fresh
    state.totals = totals
    flags = detect_flags(state.transactions, state.bank_period, totals)
    values = summary_values(state, totals, flags)
    summary, source, problem = render_summary(draft, values)
    if problem:
        state.warnings.append(f"model summary rejected ({problem}); template used")
    has_fallback = any(t.category is Category.UNCATEGORIZED for t in state.transactions)
    input_issues = any(str(row.get("reason", "")).startswith("rejected:") for row in state.excluded)
    review_needed = any(w.startswith("reconciliation:") for w in state.warnings)
    status = "ok" if agent_finished and not (has_fallback or input_issues or review_needed or problem) else "degraded"
    report = build_report(state, totals, flags, summary, source, status)
    state.report = report
    state.summary_source = source
    return report


def write_outputs(state: AgentState) -> None:
    """Serialize the finalized report. Called by the loop after the last trace entry."""
    report = state.report
    if report is None:
        raise OutputError("no report was finalized")
    try:
        atomic_write_texts([
            (summary_path_for(state.out_path), _summary_markdown(report, period_label(state))),
            (state.out_path, json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"),
        ])
    except OSError as exc:
        raise OutputError(f"cannot write report to {state.out_path}: {exc}") from exc


def finalize_fallback(state: AgentState, reason: str) -> dict:
    """Guaranteed ending: no model involved."""
    state.warnings.append(f"agent stopped early ({reason}); finished by deterministic fallback")
    remaining = state.needing()
    for t in remaining:
        state.update(replace(t, category=Category.UNCATEGORIZED, category_source="fallback"))
    if remaining:
        state.warnings.append(f"{len(remaining)} transactions set to Uncategorized by fallback")
    finalize_report(state, draft=None, agent_finished=False)
    write_outputs(state)
    return state.report
