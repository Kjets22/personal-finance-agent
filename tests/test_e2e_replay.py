import json
from pathlib import Path

import pytest

from finance_agent.__main__ import main
from finance_agent.llm import RecordingClient, ReplayClient
from finance_agent.pipeline import run
from tests import fakes

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
REAL_RECORDING = ROOT / "recordings" / "llm_responses.json"
REAL_REPORT = ROOT / "report.json"


def test_record_then_replay_is_identical(data_dir, tmp_path):
    recording = tmp_path / "rec.json"
    live = run(data_dir, tmp_path / "live.json", RecordingClient(fakes.perfect_agent(), recording))
    replayed = run(data_dir, tmp_path / "replay.json", ReplayClient(recording))
    assert live == replayed


def test_recorded_failures_replay_as_failures(data_dir, tmp_path):
    recording = tmp_path / "rec.json"
    live = run(data_dir, tmp_path / "live.json", RecordingClient(fakes.offline_model_agent(), recording))
    assert "error" in json.loads(recording.read_text())["entries"][0]
    replayed = run(data_dir, tmp_path / "replay.json", ReplayClient(recording))
    assert live == replayed


def test_committed_scripted_fixture_replays(data_dir, tmp_path):
    out = tmp_path / "report.json"
    assert main(["--data", str(data_dir), "--out", str(out),
                 "--replay", str(FIXTURES / "scripted_recording.json"), "--replay-strict"]) == 0
    assert json.loads(out.read_text()) == json.loads((FIXTURES / "scripted_report.json").read_text())


@pytest.mark.skipif(not REAL_RECORDING.exists(), reason="no real-model recording committed yet")
def test_committed_real_recording_replays(data_dir, tmp_path):
    out = tmp_path / "report.json"
    assert main(["--data", str(data_dir), "--out", str(out), "--replay", str(REAL_RECORDING), "--replay-strict"]) == 0
    assert json.loads(out.read_text()) == json.loads(REAL_REPORT.read_text())


def test_replay_miss_degrades_by_default(data_dir, tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"format": 1, "model": "x", "entries": []}))
    out = tmp_path / "report.json"
    assert main(["--data", str(data_dir), "--out", str(out), "--replay", str(empty)]) == 0
    assert json.loads(out.read_text())["status"] == "degraded"
    assert main(["--data", str(data_dir), "--out", str(out), "--replay", str(empty), "--replay-strict"]) == 2


def test_cli_exit_codes(data_dir, tmp_path, capsys):
    good = tmp_path / "r.json"
    assert main(["--data", str(data_dir), "--out", str(good), "--llm", "garbage"]) == 0
    assert "status=degraded" in capsys.readouterr().out
    assert main(["--data", str(tmp_path / "missing"), "--out", str(good), "--llm", "garbage"]) == 2
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert main(["--data", str(data_dir), "--out", str(blocker / "r.json"), "--llm", "garbage"]) == 3
    assert main(["--data", str(data_dir), "--out", str(good), "--replay", str(tmp_path / "nope.json")]) == 2
    assert main(["--data", str(data_dir), "--out", str(good), "--replay", "a", "--record", "b"]) == 2
