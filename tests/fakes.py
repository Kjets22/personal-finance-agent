"""Scripted fake LLMs ("test agents"). No network, fully deterministic.

Each persona is a handler routed by the system prompt tag:
[controller] -> tool choice, [categorizer] -> category mapping.
"""

from __future__ import annotations

import json

from finance_agent.llm import LLMError, ScriptedClient

from finance_agent.report import TEMPLATE

GOOD_SUMMARY = TEMPLATE

_KEYWORDS = [
    ("landlord", "Housing"), ("atm", "Cash"), ("venmo", "Other"), ("unknown", "Other"),
    ("home depot", "Home"), ("homedepot", "Home"), ("wholefds", "Food"), ("safeway", "Food"),
    ("costco", "Food"), ("pizza", "Food"), ("doordash", "Food"), ("shell", "Transportation"),
    ("uber", "Transportation"), ("lyft", "Transportation"), ("amzn", "Shopping"),
    ("amazon", "Shopping"),
]

EXPECTED_BY_CATEGORY = {
    "Housing": 1800.00, "Food": 787.87, "Home": 449.23, "Shopping": 229.99,
    "Transportation": 154.28, "Insurance": 125.00, "Cash": 100.00, "Healthcare": 93.97,
    "Utilities": 89.99, "Other": 52.00, "Entertainment": 28.97,
}


def expected_category(merchant: str) -> str:
    for keyword, category in _KEYWORDS:
        if keyword in merchant:
            return category
    return "Other"


def transactions_in(prompt: str) -> list[dict]:
    return json.loads(prompt.split("Transactions:\n", 1)[1])


def state_in(prompt: str) -> dict:
    line = next(l for l in prompt.splitlines() if l.startswith("STATE: "))
    return json.loads(line[len("STATE: "):])


def last_observation(prompt: str) -> tuple[str, dict] | None:
    lines = [l for l in prompt.splitlines() if l.startswith("OBSERVATION ")]
    if not lines:
        return None
    head, body = lines[-1].split(": ", 1)
    return head.split()[-1], json.loads(body)


def action(tool: str, **args) -> str:
    return json.dumps({"tool": tool, "args": args, "reason": "scripted"})


def perfect_controller(prompt: str, summary: str = GOOD_SUMMARY) -> str:
    state = state_in(prompt)
    last = last_observation(prompt)
    if state["needing_category"] > 0:
        if last and last[0] == "list_uncategorized" and last[1].get("items"):
            return action("categorize_batch", ids=[i["id"] for i in last[1]["items"]])
        return action("list_uncategorized", limit=8)
    if not state["totals_computed"]:
        return action("compute_totals")
    return action("write_report", summary=summary)


def perfect_categorizer(prompt: str) -> str:
    return json.dumps({t["id"]: expected_category(t["merchant"]) for t in transactions_in(prompt)})


def routed(controller=perfect_controller, categorizer=perfect_categorizer):
    def handler(prompt: str, system: str) -> str:
        if system.startswith("[controller]"):
            return controller(prompt)
        if system.startswith("[categorizer]"):
            return categorizer(prompt)
        raise AssertionError(f"unexpected system prompt: {system[:40]}")
    return handler


def perfect_agent() -> ScriptedClient:
    return ScriptedClient(routed(), model="scripted-perfect")


def fenced_chatty_agent() -> ScriptedClient:
    def wrap(fn):
        return lambda p: f"Sure! Here you go:\n```json\n{fn(p)}\n```\nHope that helps."
    return ScriptedClient(routed(wrap(perfect_controller), wrap(perfect_categorizer)), model="chatty")


def wrong_category_agent() -> ScriptedClient:
    return ScriptedClient(routed(categorizer=lambda p: json.dumps(
        {t["id"]: "Groceries & Dining" for t in transactions_in(p)})), model="wrong-category")


def hallucinating_id_agent() -> ScriptedClient:
    def categorizer(prompt: str) -> str:
        if prompt.startswith("Your previous answer had problems"):
            return perfect_categorizer(prompt)
        return json.dumps({"t999": "Food"})
    return ScriptedClient(routed(categorizer=categorizer), model="hallucinating")


def impatient_agent() -> ScriptedClient:
    def controller(prompt: str) -> str:
        if last_observation(prompt) is None:
            return action("write_report", summary=GOOD_SUMMARY)
        return perfect_controller(prompt)
    return ScriptedClient(routed(controller), model="impatient")


def looping_agent() -> ScriptedClient:
    return ScriptedClient(routed(lambda p: action("compute_totals")), model="looping")


def unknown_tool_agent() -> ScriptedClient:
    return ScriptedClient(routed(lambda p: action("delete_everything")), model="unknown-tool")


def number_injector_agent() -> ScriptedClient:
    return ScriptedClient(routed(lambda p: perfect_controller(
        p, summary="You earned $99,999 this month, amazing!")), model="injector")


def offline_model_agent() -> ScriptedClient:
    def down(prompt: str, system: str) -> str:
        raise LLMError("connection refused")
    return ScriptedClient(down, model="offline")
