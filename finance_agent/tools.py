"""Tools the controller model can choose. Each has a strict contract.

Contract violations raise ToolError; the loop turns that into an observation
the model can react to.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from .categorize import BATCH_SIZE, categorize_batch
from .models import Category
from .report import APPROVED_SUMMARIES, finalize_report
from .state import AgentState
from .totals import to_money, to_rate


class ToolError(Exception):
    """The model called a tool incorrectly."""


TOOL_SPECS = [
    {
        "name": "list_uncategorized",
        "args": {"limit": f"optional integer 1-{BATCH_SIZE}"},
        "returns": "count and ids/merchants of transactions that still need a category",
    },
    {
        "name": "categorize_batch",
        "args": {"ids": f"list of 1-{BATCH_SIZE} ids taken from list_uncategorized"},
        "returns": "how many got a category, how many fell back, how many remain",
    },
    {
        "name": "compute_totals",
        "args": {},
        "returns": "income, expenses, net, savings_rate and top categories, computed by code",
    },
    {
        "name": "write_report",
        "args": {"summary": "Choose exactly one of these factual templates (copy it unchanged): "
                            + " OR ".join(APPROVED_SUMMARIES)},
        "returns": "confirmation; fails if transactions still need a category "
                   "or totals are not computed",
    },
]


def _check_args(args: object, allowed: set[str]) -> dict:
    if not isinstance(args, dict):
        raise ToolError("args must be a JSON object")
    extra = sorted(set(args) - allowed)
    if extra:
        raise ToolError(f"unexpected args {extra}; allowed: {sorted(allowed)}")
    return args


def list_uncategorized(state: AgentState, args: dict) -> dict:
    args = _check_args(args, {"limit"})
    limit = args.get("limit", BATCH_SIZE)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= BATCH_SIZE:
        raise ToolError(f"limit must be an integer from 1 to {BATCH_SIZE}")
    pending = state.needing()
    return {
        "remaining": len(pending),
        "items": [{"id": t.id, "merchant": t.merchant, "raw": t.description} for t in pending[:limit]],
    }


def categorize(state: AgentState, args: dict) -> dict:
    args = _check_args(args, {"ids"})
    ids = args.get("ids")
    if (not isinstance(ids, list) or not ids or len(ids) > BATCH_SIZE
            or not all(isinstance(i, str) for i in ids)):
        raise ToolError(f"ids must be a list of 1-{BATCH_SIZE} id strings")
    if len(set(ids)) != len(ids):
        raise ToolError("ids contains duplicates")
    unknown = [i for i in ids if state.get(i) is None]
    if unknown:
        raise ToolError(f"unknown ids {unknown}; use ids from list_uncategorized")
    done = [i for i in ids if not state.get(i).needs_category]
    if done:
        raise ToolError(f"these ids do not need a category: {done}")

    txns = [state.get(i) for i in ids]
    outcome = categorize_batch(state.llm, txns)
    for t in txns:
        if t.id in outcome.assigned:
            state.update(replace(t, category=outcome.assigned[t.id], category_source="llm"))
        else:
            state.update(replace(t, category=Category.UNCATEGORIZED, category_source="fallback"))
    state.warnings.extend(outcome.warnings)
    state.totals = None  # categories changed; totals must be recomputed
    return {
        "categorized": len(outcome.assigned),
        "fell_back_to_uncategorized": len(outcome.fallback),
        "remaining": len(state.needing()),
    }


def compute(state: AgentState, args: dict) -> dict:
    from .totals import compute_totals

    _check_args(args, set())
    totals = compute_totals(state.transactions)
    state.totals = totals
    return {
        "income": to_money(totals.income),
        "expenses": to_money(totals.expenses),
        "net": to_money(totals.net),
        "savings_rate": to_rate(totals.savings_rate),
        "top_categories": [[k, to_money(v)] for k, v in list(totals.by_category.items())[:3]],
    }


def write_report(state: AgentState, args: dict) -> dict:
    args = _check_args(args, {"summary"})
    remaining = state.needing()
    if remaining:
        raise ToolError(f"{len(remaining)} transactions still need a category "
                        f"(e.g. {[t.id for t in remaining[:5]]}); call categorize_batch first")
    if state.totals is None:
        raise ToolError("totals are not computed for the current categories; call compute_totals first")
    finalize_report(state, draft=args.get("summary"), agent_finished=True)
    # The loop writes the file after recording this step, so the saved trace is complete.
    return {"report": "finalized", "summary_source": state.summary_source}


TOOLS: dict[str, Callable[[AgentState, dict], dict]] = {
    "list_uncategorized": list_uncategorized,
    "categorize_batch": categorize,
    "compute_totals": compute,
    "write_report": write_report,
}
