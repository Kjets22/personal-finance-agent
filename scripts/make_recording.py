"""Regenerate recordings/llm_recording.json -- the committed replay source.

PROVENANCE, STATED PLAINLY: no LLM provider was reachable when this project was
assembled (no Ollama daemon, no API key), so this file is NOT a capture of a real
model run. It is generated here by driving the agent through the real
RecordingClient with hand-authored replies that imitate a small local model.
The record/replay machinery is genuinely exercised end to end; the reply text is
the author's, not a model's.

The FIRST controller reply is deliberately unparseable, so the documented replay
command demonstrates the recovery path with no extra flags.

    python scripts/make_recording.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finance_agent.llm import RecordingClient, ScriptedClient  # noqa: E402
from finance_agent.pipeline import run  # noqa: E402
from tests.fakes import perfect_categorizer, perfect_controller  # noqa: E402

PROVENANCE = (
    "HAND-AUTHORED FIXTURE, NOT A CAPTURE OF A REAL MODEL RUN. No LLM provider was "
    "reachable when this project was assembled (no Ollama daemon, no API key). Replies "
    "were written to imitate a small local model and recorded through the real "
    "RecordingClient by scripts/make_recording.py. The record/replay machinery is real; "
    "the reply text is not model output. The first controller reply is deliberately "
    "unparseable so that plain replay exercises the recovery path."
)

# What a small local model does when it ignores "reply with exactly one JSON object".
JUNK_FIRST_REPLY = (
    "Sure! To build this report I should probably start by looking at which "
    "transactions are still missing a category, and then add everything up."
)


def recovering_agent() -> ScriptedClient:
    """Perfect, except the very first controller turn is unusable."""
    controller_turns = 0

    def handler(prompt: str, system: str) -> str:
        nonlocal controller_turns
        if system.startswith("[controller]"):
            controller_turns += 1
            if controller_turns == 1:
                return JUNK_FIRST_REPLY
            return perfect_controller(prompt)
        if system.startswith("[categorizer]"):
            return perfect_categorizer(prompt)
        raise AssertionError(f"unexpected system prompt: {system[:40]}")

    return ScriptedClient(handler, model="hand-authored-fixture")


def main() -> int:
    recording = ROOT / "recordings" / "llm_recording.json"
    recording.parent.mkdir(parents=True, exist_ok=True)
    recording.unlink(missing_ok=True)

    scratch = ROOT / "recordings" / ".regen_report.json"
    report = run(ROOT / "sample_data", scratch, RecordingClient(recovering_agent(), recording))

    # Stamp provenance into the file. ReplayClient ignores unknown top-level keys,
    # so this is additive and replay still validates every fingerprint.
    data = json.loads(recording.read_text(encoding="utf-8"))
    data["provenance"] = PROVENANCE
    recording.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")

    for leftover in (scratch, scratch.with_name(scratch.stem + "_summary.md")):
        leftover.unlink(missing_ok=True)

    print(f"wrote {recording} ({len(data['entries'])} calls)")
    print(f"  status={report['status']} warnings={len(report['warnings'])}")
    print(f"  first reply parseable? "
          f"{'no (recovery path exercised)' if data['entries'][0]['response'] == JUNK_FIRST_REPLY else 'YES - EXPECTED NO'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
