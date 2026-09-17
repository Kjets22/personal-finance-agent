from decimal import Decimal

import pytest

from finance_agent.classify import classify_kind
from finance_agent.ingest import load_all
from finance_agent.models import Category, Kind
from finance_agent.reconcile import build_ledger


@pytest.fixture
def ledger(data_dir):
    return build_ledger(*load_all(data_dir))


def by_desc(ledger, text):
    return [t for t in ledger.transactions if text in t.description]


@pytest.mark.parametrize("desc,amount,kind", [
    ("TRANSFER TO SAVINGS", "-500.00", Kind.TRANSFER),
    ("Refund AMAZON.COM", "34.99", Kind.REFUND),
    ("ACH CREDIT PAYROLL ACME CORP", "3200.00", Kind.INCOME),
    ("INTEREST CREDIT", "1.25", Kind.INCOME),
    ("PENDING AUTH TARGET T-8821", "0.00", Kind.PENDING),
    ("PENDING AUTH SOMETHING", "-5.00", Kind.PENDING),
    ("ZELLE PAYMENT TO LANDLORD", "-1800.00", Kind.EXPENSE),
])
def test_kind_rules(desc, amount, kind):
    assert classify_kind(desc, Decimal(amount)) is kind


def test_ledger_size_and_exclusions(ledger):
    assert len(ledger.transactions) == 47
    assert len(ledger.excluded) == 1
    assert "PENDING" in ledger.excluded[0]["description"]
    kinds = [t.kind for t in ledger.transactions]
    assert (kinds.count(Kind.INCOME), kinds.count(Kind.EXPENSE),
            kinds.count(Kind.REFUND), kinds.count(Kind.TRANSFER)) == (8, 37, 1, 1)


def test_bank_and_expenses_merge_except_restaurant_42(ledger):
    merged = [t for t in ledger.transactions
              if {"bank_statement.csv", "expenses.csv"} <= t.source_files]
    assert len(merged) == 14
    lone = [t for t in ledger.transactions if t.source_files == {"expenses.csv"}]
    assert [(t.description, t.amount) for t in lone] == [("Restaurant", Decimal("-42.00"))]


def test_costco_does_not_merge_with_home_depot(ledger):
    costco, = by_desc(ledger, "COSTCO")
    assert costco.sources == ("transactions_uncategorized.csv:29",)
    home_depot = [t for t in ledger.transactions if t.amount == Decimal("-156.78")]
    assert len(home_depot) == 2


def test_whole_foods_twins_both_kept(ledger):
    assert len(by_desc(ledger, "WHOLEFDS")) == 2


def test_netflix_three_way_merge_keeps_bank_date_and_csv_category(ledger):
    netflix, = [t for t in ledger.transactions if "netflix" in t.merchant]
    assert len(netflix.sources) == 3
    assert netflix.date.isoformat() == "2024-01-06"
    assert netflix.category is Category.ENTERTAINMENT
    assert netflix.description == "Netflix Subscription"


def test_geico_merges_into_car_insurance(ledger):
    assert not by_desc(ledger, "GEICO")
    car, = by_desc(ledger, "Car Insurance")
    assert "transactions_uncategorized.csv:14" in car.sources


def test_income_merges(ledger):
    salaries = by_desc(ledger, "Salary")
    assert len(salaries) == 2
    assert {len(s.sources) for s in salaries} == {1, 2}  # 01-15 salary is not in the bank


def test_health_label_is_aliased(ledger):
    gym, = by_desc(ledger, "Gym")
    assert (gym.category, gym.category_source) == (Category.HEALTHCARE, "alias")


def test_sixteen_rows_need_the_model(ledger):
    needing = [t for t in ledger.transactions if t.needs_category]
    assert len(needing) == 16
    assert not any("Spotify" in t.description for t in needing)  # inherited from expenses.csv
