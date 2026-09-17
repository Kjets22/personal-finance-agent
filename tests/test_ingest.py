from datetime import date
from decimal import Decimal

import pytest

from finance_agent.ingest import DataError, load_all, parse_amount, parse_date, read_source
from finance_agent.normalize import clean_merchant


@pytest.mark.parametrize("text", ["2024-01-06", "2024-1-6", "01/06/2024", " 1/6/2024 "])
def test_three_date_shapes_parse_to_same_date(text):
    assert parse_date(text) == date(2024, 1, 6)


@pytest.mark.parametrize("text", ["13/45/2024", "2024-02-30", "Jan 6 2024", "06.01.2024", ""])
def test_bad_dates_raise(text):
    with pytest.raises(ValueError):
        parse_date(text)


def test_negative_zero_becomes_plain_zero():
    value = parse_amount("-0.00")
    assert value == 0 and not value.is_signed()


@pytest.mark.parametrize("text,expected", [("-87.34", "-87.34"), ("$1,234.50", "1234.50"), (" 3200.00 ", "3200.00")])
def test_amounts(text, expected):
    assert parse_amount(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["abc", "", "NaN", "Infinity"])
def test_bad_amounts_raise(text):
    with pytest.raises(ValueError):
        parse_amount(text)


def test_bad_rows_are_rejected_not_fatal(tmp_path):
    f = tmp_path / "expenses.csv"
    f.write_text("Date,Description,Amount,Category\n"
                 "13/45/2024,Bad date,-1.00,\n"
                 "2024-01-02,Bad amount,abc,\n"
                 "2024-01-03,,-1.00,\n"
                 "2024-01-04,Good,-2.00,Food\n")
    rows, rejected = read_source(f)
    assert [r.description for r in rows] == ["Good"]
    assert len(rejected) == 3
    assert "impossible date" in rejected[0].reason


def test_missing_column_is_fatal(tmp_path):
    f = tmp_path / "income.csv"
    f.write_text("Date,Description\n2024-01-01,Salary\n")
    with pytest.raises(DataError, match="Amount"):
        read_source(f)


def test_missing_file_is_fatal(tmp_path):
    with pytest.raises(DataError, match="missing input file"):
        load_all(tmp_path)


def test_sample_data_loads_cleanly(data_dir):
    sources, rejected = load_all(data_dir)
    assert rejected == []
    assert {k: len(v) for k, v in sources.items()} == {
        "bank_statement.csv": 18, "expenses.csv": 15, "income.csv": 6,
        "transactions_uncategorized.csv": 30,
    }
    uber = next(r for r in sources["transactions_uncategorized.csv"] if r.description.startswith("UBER"))
    assert uber.description == "UBER   *TRIP HELP.UBER.COM"  # raw text preserved


@pytest.mark.parametrize("raw,clean", [
    ("UBER   *TRIP HELP.UBER.COM", "uber trip help uber"),
    ("CHECKCARD 1314 SAFEWAY #2910", "safeway"),
    ("Netflix.com", "netflix"),
    ("SQ *BLUE BOTTLE COFFEE", "blue bottle coffee"),
    ("TST* JOE'S PIZZA DOWNTOWN", "joe's pizza downtown"),
    ("POS DEBIT THE HOME DEPOT 123", "the home depot"),
    ("AMZN MKTP US*AB12C3D4E", "amzn mktp us"),
    ("CVS/PHARMACY #4412", "cvs pharmacy"),
])
def test_merchant_cleaning(raw, clean):
    assert clean_merchant(raw) == clean
