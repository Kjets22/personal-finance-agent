import re

import pytest

from finance_agent.pipeline import run
from finance_agent.report import APPROVED_SUMMARIES, render_summary, summary_path_for, validate_summary
from tests import fakes

VALUES = {"period": "January 2024", "income": "$10.00", "expenses": "$5.00", "net": "$5.00",
          "savings_rate": "50.00%", "top_category": "Food", "top_category_amount": "$5.00",
          "transaction_count": "2", "transfers": "$0.00", "flag_count": "0"}


def test_contract_shape(data_dir, out_path):
    report = run(data_dir, out_path, fakes.perfect_agent())
    assert set(report["totals"]) == {"income", "expenses", "net", "savings_rate"}
    assert all(isinstance(v, float) for v in report["totals"].values())
    assert all(isinstance(v, float) for v in report["by_category"].values())
    assert isinstance(report["flagged"], list)
    for f in report["flagged"]:
        assert isinstance(f["description"], str) and isinstance(f["reason"], str)
    for t in report["transactions"]:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", t["date"])
        assert isinstance(t["description"], str) and isinstance(t["amount"], float)
        assert isinstance(t["category"], str)
    assert isinstance(report["summary"], str) and report["summary"]
    assert summary_path_for(out_path).exists()


def test_llm_summary_numbers_come_from_code(data_dir, out_path):
    report = run(data_dir, out_path, fakes.perfect_agent())
    assert report["llm"]["summary_source"] == "llm_selected_template"
    assert "$11,001.25" in report["summary"] and "64.45%" in report["summary"]


def test_number_injection_is_rejected(data_dir, out_path):
    report = run(data_dir, out_path, fakes.number_injector_agent())
    assert "99,999" not in report["summary"]
    assert report["llm"]["summary_source"] == "template"
    assert any("digits" in w for w in report["warnings"])
    assert report["totals"]["income"] == 11001.25


@pytest.mark.parametrize("draft,problem", [
    ("You saved $5.", "digits"),
    ("You saved {net} out of ٥ things.", "digits"),  # unicode digit
    ("{income} and {bonus}", "unknown placeholders"),
    ("{income} {", "stray braces"),
    ("", "empty"),
    (None, "empty"),
    (["list"], "empty"),
    ("x" * 2000, "longer"),
])
def test_summary_validation(draft, problem):
    assert problem in validate_summary(draft)


def test_valid_summary_is_filled():
    text, source, problem = render_summary(APPROVED_SUMMARIES[1], VALUES)
    assert "Income was $10.00; net expenses were $5.00" in text
    assert source == "llm_selected_template" and problem is None


def test_expected_flags(data_dir, out_path):
    report = run(data_dir, out_path, fakes.perfect_agent())
    reasons = {(f["description"], f["reason"].split(";")[0].split(" (")[0]) for f in report["flagged"]}
    descriptions = sorted(f["description"] for f in report["flagged"])
    assert descriptions == sorted([
        "WHOLEFDS MKT #10452", "Salary", "Restaurant", "Freelance Work",
        "ACH CREDIT PAYROLL ACME CORP", "Refund AMAZON.COM", "VENMO *JOHN SMITH",
        "ZELLE PAYMENT TO LANDLORD", "ATM WITHDRAWAL 001234",
    ])
    assert any("46%" in f["reason"] for f in report["flagged"])
    assert reasons  # readable reasons exist


def test_excluded_and_transfers(data_dir, out_path):
    report = run(data_dir, out_path, fakes.perfect_agent())
    assert report["transfers"]["total"] == 500.0
    assert report["excluded"][0]["amount"] == 0.0
