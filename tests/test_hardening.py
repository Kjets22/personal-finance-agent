"""Regression cases for concrete failures discovered during review."""
import json
import shutil
from datetime import date
from decimal import Decimal

import pytest

from finance_agent.__main__ import main
from finance_agent.categorize import _as_mapping
from finance_agent.fileio import atomic_write_texts
from finance_agent.ingest import DataError, parse_amount, read_source
from finance_agent.jsonutil import extract_json_object
from finance_agent.llm import ReplayClient, ReplayMissError, ReplayStrictError, cache_key
from finance_agent.models import RawRow
from finance_agent.pipeline import run
from finance_agent.reconcile import build_ledger
from finance_agent.report import APPROVED_SUMMARIES, render_summary
from tests import fakes
from tests.test_report import VALUES


@pytest.mark.parametrize('text', ['1,2.34', '12,34.56', '1$2', '$$12', '1e3',
                                      '0.001', '1.999', '9999999999999', '1e999999'])
def test_invalid_money_is_not_silently_repaired(text):
    with pytest.raises(ValueError):
        parse_amount(text)


def test_currency_and_cents_remain_exact():
    assert parse_amount('-$1,234.50') == Decimal('-1234.50')
    assert parse_amount('$1,234.50') == Decimal('1234.50')


@pytest.mark.parametrize('header', ['Date,Description,Amount,Amount', 'Date,Description,Amount,'])
def test_ambiguous_csv_header_fails(tmp_path, header):
    path = tmp_path / 'x.csv'
    path.write_text(header + '\n2024-01-01,Shop,-10,20\n')
    with pytest.raises(DataError, match='column name'):
        read_source(path)


@pytest.mark.parametrize('row', ['2024-01-01,Shop,-10,extra', '2024-01-01,Shop'])
def test_wrong_width_row_is_excluded(tmp_path, row):
    path = tmp_path / 'x.csv'
    path.write_text('Date,Description,Amount\n' + row + '\n')
    rows, rejected = read_source(path)
    assert rows == [] and len(rejected) == 1
    assert 'column count' in rejected[0].reason


@pytest.mark.parametrize('payload', [b'\xff\xfe', b'Date,Description,Amount\n2024-01-01,"unterminated,-10'])
def test_bad_encoding_and_broken_quotes_are_input_errors(tmp_path, payload):
    path = tmp_path / 'x.csv'
    path.write_bytes(payload)
    with pytest.raises(DataError):
        read_source(path)


def row(source, line, day, description='Market', label=None):
    return RawRow(source, line, date(2024, 1, day), description, Decimal('-10'), label)


def test_equal_distance_matches_are_not_arbitrarily_deduplicated():
    ledger = build_ledger({
        'bank_statement.csv': [row('bank_statement.csv', 2, 1), row('bank_statement.csv', 3, 3)],
        'expenses.csv': [row('expenses.csv', 2, 2)],
    }, [])
    assert len(ledger.transactions) == 3
    assert any('equally plausible' in warning for warning in ledger.warnings)


def test_transitive_merchant_matches_cannot_collapse_unrelated_rows():
    ledger = build_ledger({
        'bank_statement.csv': [row('bank_statement.csv', 2, 2, 'Alpha')],
        'expenses.csv': [row('expenses.csv', 2, 2, 'Alpha Beta')],
        'transactions_uncategorized.csv': [row('transactions_uncategorized.csv', 2, 2, 'Beta')],
    }, [])
    assert len(ledger.transactions) == 2
    assert sorted(len(t.sources) for t in ledger.transactions) == [1, 2]


def test_dates_must_agree_with_every_member_of_a_merge():
    ledger = build_ledger({
        'bank_statement.csv': [row('bank_statement.csv', 2, 3)],
        'expenses.csv': [row('expenses.csv', 2, 1)],
        'transactions_uncategorized.csv': [row('transactions_uncategorized.csv', 2, 5)],
    }, [])
    assert len(ledger.transactions) == 2


def test_conflicting_source_labels_are_visible():
    ledger = build_ledger({
        'bank_statement.csv': [row('bank_statement.csv', 2, 1, label='Food')],
        'expenses.csv': [row('expenses.csv', 2, 1, label='Shopping')],
    }, [])
    assert len(ledger.transactions) == 1
    assert any('conflicting CSV categories' in w for w in ledger.warnings)


def recording(tmp_path):
    path = tmp_path / 'rec.json'
    path.write_text(json.dumps({'format': 1, 'model': 'test', 'entries': [{
        'key': cache_key('test', '', 'hello'), 'system': '', 'prompt': 'hello', 'response': '{}'
    }]}))
    return path


@pytest.mark.parametrize('strict,error', [(False, ReplayMissError), (True, ReplayStrictError)])
def test_replay_cannot_reuse_a_response_forever(tmp_path, strict, error):
    client = ReplayClient(recording(tmp_path), strict=strict)
    assert client.complete('hello') == '{}'
    with pytest.raises(error, match='exhausted'):
        client.complete('hello')


@pytest.mark.parametrize('mutation', ['format', 'entries', 'entry', 'key', 'response', 'both'])
def test_bad_recording_schema_is_rejected(tmp_path, mutation):
    path = recording(tmp_path)
    data = json.loads(path.read_text())
    if mutation == 'format': data['format'] = 99
    if mutation == 'entries': data['entries'] = None
    if mutation == 'entry': data['entries'][0] = []
    if mutation == 'key': data['entries'][0]['key'] = 'tampered'
    if mutation == 'response': data['entries'][0]['response'] = 42
    if mutation == 'both': data['entries'][0]['error'] = 'oops'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        ReplayClient(path)


@pytest.mark.parametrize('text', ['{"tool":"compute_totals","tool":"write_report"}',
                                      '{"x": NaN}', '{"x": Infinity}', '{}'+(' '*20001)])
def test_ambiguous_or_oversized_model_json_is_rejected(text):
    with pytest.raises(ValueError):
        extract_json_object(text)


def test_duplicate_category_list_ids_are_not_silently_overwritten():
    with pytest.raises(ValueError, match='duplicate'):
        _as_mapping({'results': [{'id': 't001', 'category': 'Food'}, {'id': 't001', 'category': 'Home'}]})


@pytest.mark.parametrize('draft', ['You earned {expenses} and spent {income}.',
                                      'You earned a million dollars.', 'You are debt-free.'])
def test_nondigit_hallucinations_cannot_enter_summary(draft):
    text, source, problem = render_summary(draft, VALUES)
    assert source == 'template' and problem
    assert text != draft


@pytest.mark.parametrize('draft', APPROVED_SUMMARIES)
def test_each_approved_wording_is_accepted(draft):
    text, source, problem = render_summary(draft, VALUES)
    assert source == 'llm_selected_template' and problem is None
    assert '$10.00' in text and '$5.00' in text


def test_rejected_data_cannot_produce_ok_status(data_dir, tmp_path):
    data = tmp_path / 'data'
    shutil.copytree(data_dir, data)
    with (data / 'expenses.csv').open('a') as fh:
        fh.write('2024-01-01,Malformed amount,1e999,Food\n')
    report = run(data, tmp_path / 'r.json', fakes.perfect_agent())
    assert report['status'] == 'degraded'
    assert any('rejected:' in r['reason'] for r in report['excluded'])


def test_staging_failure_preserves_existing_outputs(tmp_path):
    out = tmp_path / 'r.json'
    out.write_text('previous report')
    bad = tmp_path / 'directory'
    bad.mkdir()
    with pytest.raises(OSError):
        atomic_write_texts([(out, 'new report'), (bad, 'cannot write')])
    assert out.read_text() == 'previous report'
    assert {p.name for p in tmp_path.iterdir()} == {'r.json', 'directory'}


def test_cli_protects_input_and_recording_paths(data_dir, tmp_path):
    data = tmp_path / 'data'
    shutil.copytree(data_dir, data)
    original = (data / 'expenses.csv').read_bytes()
    assert main(['--data', str(data), '--out', str(data / 'expenses.csv'), '--llm', 'garbage']) == 2
    assert (data / 'expenses.csv').read_bytes() == original
    out = tmp_path / 'r.json'
    assert main(['--data', str(data), '--out', str(out), '--record', str(out), '--llm', 'garbage']) == 2
    assert not out.exists()


def test_strict_flag_requires_a_replay_file(data_dir, tmp_path):
    assert main(['--data', str(data_dir), '--out', str(tmp_path / 'r.json'), '--replay-strict']) == 2


def test_unwritable_recording_is_an_output_error(data_dir, tmp_path):
    blocker = tmp_path / 'blocked'
    blocker.write_text('file')
    assert main(['--data', str(data_dir), '--out', str(tmp_path / 'r.json'),
                 '--record', str(blocker / 'rec.json'), '--llm', 'garbage']) == 3
