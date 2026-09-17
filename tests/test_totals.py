from dataclasses import replace
from datetime import date
from decimal import Decimal

from finance_agent.models import Category, Kind, Transaction
from finance_agent.pipeline import run
from finance_agent.totals import compute_totals
from tests.fakes import EXPECTED_BY_CATEGORY, perfect_agent
from finance_agent.llm import GarbageClient


def txn(tid, amount, kind, category=None):
    return Transaction(tid, date(2024, 1, 1), tid, tid, Decimal(amount), kind, category, "csv", (tid,))


def test_golden_totals(data_dir, out_path):
    report = run(data_dir, out_path, perfect_agent())
    assert report["status"] == "ok"
    assert report["totals"] == {"income": 11001.25, "expenses": 3911.3, "net": 7089.95, "savings_rate": 0.6445}
    assert report["by_category"] == EXPECTED_BY_CATEGORY


def test_by_category_sums_exactly_to_expenses(data_dir, out_path):
    run(data_dir, out_path, perfect_agent())
    from finance_agent.pipeline import prepare_state
    state = prepare_state(data_dir, out_path, perfect_agent())
    totals = compute_totals(state.transactions)
    assert sum(totals.by_category.values(), Decimal(0)) == totals.expenses


def test_refund_reduces_its_category_and_expenses():
    t = compute_totals([
        txn("a", "-100.00", Kind.EXPENSE, Category.SHOPPING),
        txn("b", "30.00", Kind.REFUND, Category.SHOPPING),
        txn("c", "1000.00", Kind.INCOME, Category.INCOME),
    ])
    assert t.expenses == Decimal("70.00")
    assert t.by_category == {"Shopping": Decimal("70.00")}
    assert t.income == Decimal("1000.00")  # refund is not income


def test_transfer_excluded_from_income_and_expenses():
    t = compute_totals([
        txn("a", "-500.00", Kind.TRANSFER, Category.TRANSFER),
        txn("b", "1000.00", Kind.INCOME, Category.INCOME),
    ])
    assert (t.income, t.expenses, t.net, t.transfers) == (
        Decimal("1000.00"), Decimal("0"), Decimal("1000.00"), Decimal("500.00"))
    assert t.by_category == {}


def test_zero_income_gives_zero_rate():
    t = compute_totals([txn("a", "-10.00", Kind.EXPENSE, Category.FOOD)])
    assert t.savings_rate == 0
    assert t.net == Decimal("-10.00")


def test_no_float_drift():
    rows = [txn(str(i), "-0.10", Kind.EXPENSE, Category.FOOD) for i in range(3)]
    assert compute_totals(rows).expenses == Decimal("0.30")


def test_boundary_invariance(data_dir, tmp_path):
    """The model can move amounts between buckets, and nothing else."""
    good = run(data_dir, tmp_path / "good.json", perfect_agent())
    bad = run(data_dir, tmp_path / "bad.json", GarbageClient())
    assert good["status"] == "ok" and bad["status"] == "degraded"
    assert good["totals"] == bad["totals"]
    assert good["by_category"] != bad["by_category"]
    assert round(sum(bad["by_category"].values()), 2) == bad["totals"]["expenses"]
    good_amounts = [(t["id"], t["amount"]) for t in good["transactions"]]
    bad_amounts = [(t["id"], t["amount"]) for t in bad["transactions"]]
    assert good_amounts == bad_amounts


def test_changing_only_categories_never_changes_headline_totals(data_dir, out_path):
    from finance_agent.pipeline import prepare_state
    state = prepare_state(data_dir, out_path, perfect_agent())
    base = compute_totals(state.transactions)
    for cat in Category:
        shuffled = [replace(t, category=cat) if t.needs_category else t for t in state.transactions]
        other = compute_totals(shuffled)
        assert (other.income, other.expenses, other.net, other.savings_rate) == (
            base.income, base.expenses, base.net, base.savings_rate)
