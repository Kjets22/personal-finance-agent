# Personal Finance Agent

A tool-using agent that reconciles four overlapping CSV exports into `report.json`
plus a human-readable summary. Python owns every amount, every merge and every sum.
A language model chooses tools and assigns spending categories — nothing else.

Python 3.10+, **no runtime dependencies.** 170 offline tests, none skipped.
The detailed design, the full live trace and every worked example are in
[`docs/DESIGN_AND_APPROACH.md`](docs/DESIGN_AND_APPROACH.md).

---

## Run it

```bash
python -m pip install -e ".[dev]"        # pytest only
python -m pytest -q                       # 170 passed

# End to end, offline, replaying a real qwen2.5:7b run. This is the command to run.
python -m finance_agent --data sample_data --out report.json \
    --replay recordings/llm_responses.json --replay-strict

# The failure path: a model that returns nothing usable, ever.
python -m finance_agent --data sample_data --out examples/degraded_report.json --llm garbage
```

`FINANCE_AGENT_REPLAY` sets a default replay file if you prefer an environment
variable. The replay prints:

```
status=degraded income=11001.25 expenses=3911.30 net=7089.95 savings_rate=0.6445 flagged=9 warnings=1
```

**`degraded` is the real result of the real run.** On its last turn the model wrote
its own digits into the summary instead of the placeholders and claimed `0 items
are flagged` when 9 were. `validate_summary` rejected the sentence, code wrote the
approved template, and the run recorded why. It was kept rather than re-rolled.

| Income | Net expenses | Net | Savings rate | Transfers excluded |
| ---: | ---: | ---: | ---: | ---: |
| $11,001.25 | $3,911.30 | $7,089.95 | 64.45% | $500.00 |

Category totals depend on model classifications; **headline totals do not.** The
`--llm garbage` run produces the same four headline numbers.

| Exit | Meaning |
| --- | --- |
| 0 | Report written — read `status`, `warnings`, `flagged` |
| 2 | Unreadable input, malformed recording, or a strict replay miss |
| 3 | The report could not be written |

---

## Provider and model

**Ollama, `qwen2.5:7b`, `temperature: 0`, `seed: 42`, `format: json`** via
`/api/generate` on `localhost:11434` (`OllamaClient`, `finance_agent/llm.py:50`).
No API key. The model string is the client's default.

`recordings/llm_responses.json` is a genuine capture of that model: 13 calls in
62 seconds on 2026-09-17, macOS 26.5, Apple silicon. `report.json` is from the
same run. A strict replay reproduces it exactly, and a second live capture
reproduced the recording byte for byte. Recordings are fingerprinted on
`model + system + prompt` and validated on load, so an edited prompt misses loudly.

`recordings/llm_recording.json` is a **hand-authored fixture and is labelled as
one** in its `provenance` field, which a test asserts. It is kept because it opens
with a non-JSON reply — a path `format: json` prevents the live model from
exercising — so it is the regression tests' only coverage of that branch.

---

## Time spent

Roughly **6 to 8 hours**: the first build, an independent second implementation
and the blind A/B review between them, then the live model run and the
documentation it changed.

---

## Data-cleaning rules

69 raw rows reconcile to **47 movements plus 1 excluded pending row.** Summing the
files naively gives income of $15,436.24 — about 40% too high.

**Dates:** ISO, unpadded ISO and slashed US month-first, all normalised to
`YYYY-MM-DD`; anything else is rejected with a source reference, never dropped.
**Money:** strict fixed-point, `Decimal` throughout, `float` only at the JSON
boundary.

**Kinds** are decided by code because they move the totals:

| Rule (first match wins) | Effect |
| --- | --- |
| amount is 0, or `\bpending\b` anywhere | excluded, listed in `excluded` |
| `transfer to/from` | neither income nor expense; reported as `transfers` |
| positive **and** refund/return/reversal/chargeback | reduces expenses; not income |
| any other positive / negative | income / expense |

**Overlapping records.** Two rows are one event only when *all* hold: equal
amounts, equal kinds, dates within 2 days of every row in the group, a shared
non-stopword merchant token with every row in the group, and **different source
files.** The last clause keeps two identical `WHOLEFDS −87.34` charges 17 days
apart as two purchases. Requiring the token match against *every* member stops
`Alpha → Alpha Beta → Beta` chaining. One relaxation: a statement row with no
merchant word at all (`POS PURCHASE 4471`) may merge with a single unambiguous
candidate, and records a warning that degrades the report. Ties merge nothing and
warn. Every row keeps `sources` back to file and line.

**Labels and merchants.** A CSV's own category is validated by the same whitelist
as model output (`Health` → `Healthcare`). Only payment rails and store numbers
are stripped from merchant strings; the original description is what lands in the
report.

---

## Where the LLM/code line is drawn

The model does two things: picks the next tool, and maps a merchant string to one
of eleven fixed categories. It also selects summary wording from three approved
templates. That is all.

**Totals are computed at `finance_agent/totals.py:34`, `compute_totals()`.** It
reads `kind`, `amount` and `category`. Model output cannot reach it, for four
independent reasons:

1. `totals.py` imports nothing that touches the model.
2. `Transaction` is frozen (`models.py:141`); categorisation uses
   `dataclasses.replace`, so no tool can alter an amount or kind.
3. The report **recomputes** totals from the transaction list; it never reads a
   number back out of a model observation.
4. The categorizer prompt carries ids, merchants and direction — **not amounts.**

Categories are a whitelist with one corrective retry; off-list becomes
`Uncategorized` and is flagged. `Other` means the model could not tell;
`Uncategorized` means it never gave a valid answer. The summary is a template the
model *selects*, not prose it writes: `validate_summary` rejects unapproved
wording, swapped placeholders, digits and number-words, because checking numbers
alone cannot catch "income was {expenses}".

---

## The agent loop

`agent.py:85`, `run_agent()`. Each turn the model sees the tool menu, the state and
the last six observations, and replies with one JSON object. Code dispatches it.

| Tool | Contract |
| --- | --- |
| `list_uncategorized{limit}` | ids and merchants still needing a category |
| `categorize_batch{ids}` | 1–8 ids; refuses duplicates, unknown or already-done ids |
| `compute_totals` | code-computed totals, as an observation |
| `write_report{summary}` | **refuses** while rows lack a category or totals are stale |

Bounds live in control flow, not the prompt: 10 turns, 3 consecutive failures,
3 identical repeats. Unparseable replies, unknown tools, bad arguments and an
unreachable server all become observations. Every non-I/O exit path writes a
report via a fallback that makes no model call.

In the live run the model tried to write the report half-finished (step 4),
re-sent a completed batch (step 6) and wrote digits into the summary (step 10);
the loop refused all three and finished on turn 10 of 10. The full trace is in
the design doc. `--llm garbage` stops after 3 failed turns with the same headline
totals and 25 flagged rows.

---

## Optional extras

Took **one**: duplicate and anomaly detection into `flagged` (9 entries). Skipped
accuracy measurement — the nine rows carrying ground-truth labels are exactly the
rows the agent never shows the model, so scoring it needs a separate run with those
labels stripped.

---

## AI usage and decisions

**The decision that shaped the architecture is mine.** The first AI proposals for
this brief were single-pass: hand the model all four files, let it categorise,
reconcile and compute the totals in one prompt, and spend as little compute as
possible. I rejected that. A general language model is not a validated calculator,
and it should not be making decisions that must not be wrong. I directed the design
that is here instead: code owns every amount, every merge and every sum; the model
does tool selection and the one semantic task that cannot change a total; and every
model output passes a safeguard before it can touch the report. The trade is a
dozen model calls instead of one or two, in exchange for a report in which no
number was ever generated by a model. The live run showed what the rejected design
would have shipped: at step 10 the model wrote "0 items are flagged" when nine
were. In a single-prompt design that sentence *is* the output. Here, code threw it
away.

**Tools.** Claude wrote the implementation, tests and docs against that direction;
ChatGPT/Codex reviewed. A later Claude session built a second implementation from
the brief alone, and a reviewer agent given only the brief, both trees labelled
A/B and a rubric chose between them. A final session captured the live recording
and rewrote every claim it falsified; it added no application code.

**My other calls.** The hand-authored recording was never to be labelled a real
run, and the real-recording test stayed skipped until one existed. The live
`degraded` result was kept, not re-rolled. No claim enters this README without a
command that produced it — which is how a wrong reproducibility sentence was
caught and corrected. The time figure is mine. The README was cut to the brief's
length.

**What the comparison found.** Both trees gave byte-identical totals on the sample
data, so each fix below only shows on inputs the sample lacks. Each was
demonstrated failing first and has a regression test:

1. `RETURN` / `REVERSAL` / `CHARGEBACK` counted as income — only `refund` matched.
2. `PENDING` matched only at the start; `AUTH PENDING TARGET 99` settled as spending.
3. A renamed charge (`Dentist Visit` / `POS PURCHASE 4471`) double-counted.
4. `summary_source` said `"llm"` for wording the model only selected.
5. The README claimed ATM withdrawals were flagged; they were not. Code fixed, not prose.
6. 2,225 lines of stale docs deleted.

**Rejected.** A first fix for (3) allowed any token-less merge; an existing test
caught it reopening the `Alpha → Alpha Beta → Beta` chain, so it was narrowed to
"one side has no merchant word at all." Also rejected: the other tree's
amount-plus-date merge key, which silently turns a real expense near a transfer
into a transfer.

**Decisions to defend.** The code/model split above; the five reconciliation
clauses; refunds as negative spend; recompute-never-read-back; templates over
prose; `Other` vs `Uncategorized`; degraded exits 0.

---

## Walkthrough notes

1. **Run replay, then break it.** `--llm garbage`, or `--ollama-host http://127.0.0.1:1`
   for a server that does not exist. Same headline totals, exit 0.
2. **Trace `CHECKCARD 1314 SAFEWAY #2910`.** CSV → `Decimal` → `expense` by regex →
   `safeway` by cleaner → the only string the model sees → whitelisted `Food` →
   `by_category`. Original description and `sources` survive.
3. **Point at `totals.py:34`** and give the four reasons model output cannot reach it.
4. **Show the reconciliation table** — `Car Insurance` ↔ `GEICO` merge on
   `insurance`; two `WHOLEFDS` stay two; `POS PURCHASE 4471` needs the relaxation.
5. **Open the trace at step 10.** A real model wrote `0 items are flagged` when 9
   were, and code threw the sentence away.

---

## Not built, with more time, and unverified

**Not built:** any LLM framework or config layer; structured function-calling
(small models handle it poorly, and tolerating malformed output is the exercise);
a database, caching, multi-currency, a UI, CI, Docker.

**With two more hours:** perturb the input — the run used all 10 turns, so find
where a longer CSV breaks the limit and fix batch size or observations, not the
number. Then measure category accuracy with labels stripped. Then a stable
`(account, transaction_id)` key if the exports ever carry one.

**Unverified:** two runs of one model on one machine characterise nothing about
`qwen2.5:7b` in general. No accuracy figure is claimed. Zero-income savings rate is
reported as `0`. `transfers` is a gross magnitude. Concatenated spellings
(`WIDGETCO`) do not match and are flagged. No complete prompt-injection defence is
claimed. Everything else here was produced by running the code.
