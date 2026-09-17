# Recordings

Two files, with different jobs. Both are loaded through the same
`ReplayClient`, and both are fingerprinted by `model + system + prompt` and
validated on load — regenerate after changing any prompt, because a stale
prompt misses rather than silently replaying the wrong answer.

## `llm_responses.json` — the real one

A **genuine capture of Ollama `qwen2.5:7b`** (`temperature: 0`, `seed: 42`,
`format: json`), recorded by `RecordingClient` while the agent ran against a
live daemon on 2026-09-17: 13 calls in 62 seconds, macOS 26.5 / Apple silicon.
This is the file the documented replay command uses, and `../report.json` plus
`../report_summary.md` are the artifacts of that same run.

`tests/test_e2e_replay.py::test_committed_real_recording_replays` replays it
under `--replay-strict` and asserts the result equals the committed
`../report.json`. That test was skipped until this capture existed.

The run finished **`degraded`, and that is the real outcome, not a defect.** On
its final turn the model wrote its own digits into the summary instead of the
placeholders, and claimed `0 items are flagged` when 9 were. `validate_summary`
rejected the sentence, code substituted the approved template, and the run
recorded the warning. Do not "fix" this by re-recording until it looks clean.

To regenerate it, keep the recording, the report and the summary from one run:

```sh
ollama serve                             # in its own shell
python -m finance_agent --data sample_data --out report.json \
    --record recordings/llm_responses.json
python -m finance_agent --data sample_data --out /tmp/live_replay.json \
    --replay recordings/llm_responses.json --replay-strict
python -m pytest -q
```

A re-run will not reproduce this file byte for byte. Temperature 0 and a fixed
seed constrain the text the model generates, not which tools it chooses.

## `llm_recording.json` — the fixture

A **hand-authored fixture, not a capture of a real model run.** Its own
`provenance` field says so and a test asserts that field is present. It was
produced by `python scripts/make_recording.py`, which drives the agent through
the real `RecordingClient`, so the record/replay machinery is exercised — only
the reply *text* is authored.

It is kept rather than retired because it covers a failure the live model never
produced: its first controller reply is not JSON at all, and `format: json`
meant all 13 live replies parsed. It is the regression tests' only coverage of
the unparseable-reply path.

Do not repoint the real-recording test at this file, and do not describe it as
evidence of a model run. It is a test fixture.

---

Recordings contain prompts and responses, which include transaction
descriptions. Use sample data for demos, never real personal financial data.
