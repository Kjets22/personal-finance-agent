"""Regenerate tests/fixtures/ from the scripted PerfectAgent.

This is NOT a real model run. It exists so the replay path is tested even
before a real recording is committed. Run after changing any prompt:

    python scripts/make_fixture.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finance_agent.llm import RecordingClient  # noqa: E402
from finance_agent.pipeline import run  # noqa: E402
from tests.fakes import perfect_agent  # noqa: E402

fixtures = ROOT / "tests" / "fixtures"
recording = fixtures / "scripted_recording.json"
recording.unlink(missing_ok=True)
run(ROOT / "sample_data", fixtures / "scripted_report.json", RecordingClient(perfect_agent(), recording))
(fixtures / "scripted_report_summary.md").unlink(missing_ok=True)
print(f"wrote {recording} and {fixtures / 'scripted_report.json'}")
