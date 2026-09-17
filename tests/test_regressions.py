"""Regressions for defects found by differential testing against a second,
independently written implementation of the same brief.

Each test below corresponds to a concrete wrong number this code used to
produce. They are grouped here rather than scattered so the walkthrough can
point at one file and say what changed and why.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finance_agent.classify import classify_kind
from finance_agent.ingest import load_all
from finance_agent.models import Kind, RawRow
from finance_agent.reconcile import build_ledger
from finance_agent.totals import compute_totals

ROOT = Path(__file__).resolve().parents[1]
RECORDING = ROOT / "recordings" / "llm_recording.json"


def row(source, line, day, description, amount, label=None):
    return RawRow(source, line, date(2024, 1, day), description, Decimal(amount), label)


# --------------------------------------------------------------------------
# 1. Money back from a merchant is negative spend, whatever the bank calls it.
#    Before: only the literal word "refund" matched, so a return or a reversal
#    was counted as INCOME -- inflating both income and savings_rate.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("description", [
    "Refund AMAZON.COM",
    "RETURN WIDGET CO",
    "RETURNED ITEM CREDIT",
    "REVERSAL GADGET LTD",
    "CHARGEBACK ACME",
])
def test_money_back_is_a_refund_not_income(description):
    assert classify_kind(description, Decimal("34.99")) is Kind.REFUND


def test_a_credit_that_is_not_money_back_is_still_income():
    assert classify_kind("ACH CREDIT PAYROLL ACME CORP", Decimal("3200")) is Kind.INCOME
    assert classify_kind("INTEREST CREDIT", Decimal("1.25")) is Kind.INCOME
    assert classify_kind("Dividend Payment", Decimal("150")) is Kind.INCOME


def test_a_return_offsets_spending_rather_than_adding_income():
    ledger = build_ledger({
        "expenses.csv": [
            row("expenses.csv", 2, 10, "Widget Co", "-60.00", "Shopping"),
            row("expenses.csv", 3, 20, "RETURN WIDGET CO", "60.00", "Shopping"),
        ],
    }, [])
    totals = compute_totals(ledger.transactions)
    assert totals.income == Decimal("0")          # was 60.00
    assert totals.expenses == Decimal("0")        # was 60.00
    assert totals.by_category["Shopping"] == Decimal("0")


# --------------------------------------------------------------------------
# 2. "PENDING" is not always the first word.
#    Before: a startswith() check settled "AUTH PENDING TARGET 99" as real
#    spending, so an unsettled authorization landed in expenses.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("description", [
    "PENDING AUTH TARGET T-8821",
    "AUTH PENDING TARGET 99",
    "CARD AUTH - PENDING",
])
def test_pending_is_detected_anywhere_in_the_description(description):
    assert classify_kind(description, Decimal("-12.00")) is Kind.PENDING


def test_pending_rows_never_reach_expenses():
    ledger = build_ledger({
        "expenses.csv": [row("expenses.csv", 2, 25, "AUTH PENDING TARGET 99", "-12.00")],
    }, [])
    assert ledger.transactions == []
    assert len(ledger.excluded) == 1
    assert compute_totals(ledger.transactions).expenses == Decimal("0")


def test_the_word_pending_does_not_swallow_a_real_merchant():
    # Guard against over-reach: "pendleton" must not read as "pending".
    assert classify_kind("PENDLETON WOOLEN MILLS", Decimal("-88.00")) is Kind.EXPENSE


# --------------------------------------------------------------------------
# 3. A charge renamed past recognition still merges -- but never silently.
#    Before: requiring a shared merchant token meant one real charge seen in
#    two files as "Dentist Visit" and "POS PURCHASE 4471" was double-counted.
# --------------------------------------------------------------------------

def test_renamed_charge_across_two_files_merges_and_warns():
    ledger = build_ledger({
        "bank_statement.csv": [row("bank_statement.csv", 2, 19, "POS PURCHASE 4471", "-210.00")],
        "expenses.csv": [row("expenses.csv", 2, 18, "Dentist Visit", "-210.00", "Healthcare")],
    }, [])
    assert len(ledger.transactions) == 1                      # was 2
    assert compute_totals(ledger.transactions).expenses == Decimal("210.00")   # was 420.00
    assert any("no shared merchant word" in w for w in ledger.warnings)


def test_a_weak_merge_is_never_silent():
    ledger = build_ledger({
        "bank_statement.csv": [row("bank_statement.csv", 2, 19, "POS PURCHASE 4471", "-210.00")],
        "expenses.csv": [row("expenses.csv", 2, 18, "Dentist Visit", "-210.00", "Healthcare")],
    }, [])
    assert ledger.warnings, "a token-less merge must record a warning"


def test_two_different_merchants_at_the_same_price_never_merge():
    # Both descriptions carry real merchant words that simply differ. That is two
    # merchants, not one renamed charge, and the relaxation must not apply.
    ledger = build_ledger({
        "bank_statement.csv": [row("bank_statement.csv", 2, 18, "Alpha Diner", "-50.00")],
        "expenses.csv": [row("expenses.csv", 2, 18, "Beta Garage", "-50.00", "Food")],
    }, [])
    assert len(ledger.transactions) == 2
    assert compute_totals(ledger.transactions).expenses == Decimal("100.00")


def test_relaxation_needs_a_token_less_side_not_merely_a_mismatch():
    from finance_agent.normalize import match_tokens  # noqa: PLC0415
    # This is the distinction the rule turns on.
    assert match_tokens("POS PURCHASE 4471") == frozenset()   # no merchant word
    assert match_tokens("Alpha Diner") == frozenset({"alpha", "diner"})


def test_the_relaxation_does_not_reopen_transitive_false_merges():
    # A shares a token with AB, B shares a token with AB but not with A.
    # B must stay separate: it is not a renamed charge, it is the A->AB->B trap.
    ledger = build_ledger({
        "bank_statement.csv": [row("bank_statement.csv", 2, 2, "Alpha", "-10.00")],
        "expenses.csv": [row("expenses.csv", 2, 2, "Alpha Beta", "-10.00")],
        "transactions_uncategorized.csv": [
            row("transactions_uncategorized.csv", 2, 2, "Beta", "-10.00")],
    }, [])
    assert len(ledger.transactions) == 2
    assert sorted(len(t.sources) for t in ledger.transactions) == [1, 2]


def test_same_file_lookalikes_are_still_never_merged():
    # Two genuine identical charges a day apart in ONE export are two charges.
    ledger = build_ledger({
        "expenses.csv": [
            row("expenses.csv", 2, 5, "STARBUCKS STORE 11234", "-5.45", "Food"),
            row("expenses.csv", 3, 6, "STARBUCKS STORE 11234", "-5.45", "Food"),
        ],
    }, [])
    assert len(ledger.transactions) == 2
    assert compute_totals(ledger.transactions).expenses == Decimal("10.90")


# --------------------------------------------------------------------------
# 4. The committed recording is honest about what it is, and exercises the
#    recovery path on the default replay command.
# --------------------------------------------------------------------------

def test_committed_recording_declares_its_provenance():
    data = json.loads(RECORDING.read_text(encoding="utf-8"))
    assert "NOT A CAPTURE OF A REAL MODEL RUN" in data["provenance"]
    assert data["entries"]


def test_committed_recording_starts_with_an_unparseable_reply():
    from finance_agent.agent import parse_action  # noqa: PLC0415
    data = json.loads(RECORDING.read_text(encoding="utf-8"))
    with pytest.raises(ValueError):
        parse_action(data["entries"][0]["response"])


def test_sample_data_totals_are_unchanged_by_all_of_the_above():
    sources, rejected = load_all(ROOT / "sample_data")
    totals = compute_totals(build_ledger(sources, rejected).transactions)
    assert totals.income == Decimal("11001.25")
    assert totals.expenses == Decimal("3911.30")
    assert totals.net == Decimal("7089.95")
    assert totals.savings_rate == Decimal("0.6445")
