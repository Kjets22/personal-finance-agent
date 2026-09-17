# Personal Finance Agent

A tool-using agent that reconciles four overlapping CSV exports into `report.json`
plus a human-readable summary. Python owns every amount, every merge and every sum.
A language model chooses tools and assigns spending categories — nothing else.

Python 3.10+, **no runtime dependencies.** 167 offline tests, 1 skipped.

---

## Run it

```bash
python -m pip install -e ".[dev]"        # pytest only, for the test run
python -m pytest -q

# End to end, offline, from the committed recording. This is the command to run.
python -m finance_agent --data sample_data --out report.json \
    --replay recordings/llm_recording.json --replay-strict

# The failure path, live: a model that returns nothing usable, ever.
python -m finance_agent --data sample_data --out examples/degraded_report.json --llm garbage
```

`FINANCE_AGENT_REPLAY` sets a default replay file if you prefer an environment
variable to a flag. Both commands work in PowerShell and POSIX shells.

The replay run prints:

```
status=ok income=11001.25 expenses=3911.30 net=7089.95 savings_rate=0.6445 flagged=8 warnings=0
```

| Income | Net expenses | Net | Savings rate | Transfers excluded |
| ---: | ---: | ---: | ---: | ---: |
| $11,001.25 | $3,911.30 | $7,089.95 | 64.45% | $500.00 |

These are sample-data results under the reconciliation rules below, not verified
account balances. Category totals depend on model classifications; **headline
totals do not.**

### Exit codes

| Exit | Meaning |
| --- | --- |
| 0 | Report written — inspect `status`, `warnings`, `excluded`, `flagged` |
| 2 | Unreadable input, path conflict, malformed recording, or a strict replay miss |
| 3 | The report could not be written |

`status: ok` means the agent finished, every spending row has a category, the
summary validated, and no row was rejected or flagged for reconciliation review.
It does **not** claim category accuracy. Normal exclusions such as a pending
authorization do not by themselves degrade a report.

---

## Provider and model

**No LLM provider was reachable while this was assembled** — no Ollama daemon, no
API key. So, plainly:

- `recordings/llm_recording.json` is a **hand-authored fixture, not a capture of a
  real model run.** Its own `provenance` field says so, and
  `tests/test_regressions.py::test_committed_recording_declares_its_provenance`
  asserts that field is present.
- It was produced by `scripts/make_recording.py` driving the agent through the
  **real** `RecordingClient`, so the record/replay machinery is genuinely
  exercised end to end. Only the reply *text* is authored rather than generated.
- `OllamaClient` (`finance_agent/llm.py`) is written to Ollama's documented
  `/api/generate` contract with `temperature: 0` and a fixed seed. It is **the
  only unexercised code path in the project.**
- `tests/test_e2e_replay.py::test_committed_real_recording_replays` is the one
  skipped test. It activates when a genuine recording is committed at
  `recordings/llm_responses.json`. **It is deliberately left skipped rather than
  pointed at the fixture** — relabelling a scripted fixture as a live recording is
  the one thing that would make this submission dishonest.

On a machine with Ollama:

```bash
ollama pull qwen2.5:7b
python -m finance_agent --data sample_data --out report.json --record recordings/llm_responses.json
python -m finance_agent --data sample_data --out examples/live_replay.json \
    --replay recordings/llm_responses.json --replay-strict
python -m pytest -q          # the skipped test now runs
```

Keep the live report, its summary and its recording from the same run.

---

## Time spent

**→ Fill this in before submitting.** The brief asks for a rough figure and no
honest one can be supplied on your behalf.

---

## Data-cleaning rules

69 raw rows across four files reconcile to **47 movements plus 1 excluded pending
row.**

**Dates.** ISO year-first (`2024-01-02`), unpadded ISO (`2024-1-6`), and slashed
US month-first (`01/03/2024`). Normalised to `YYYY-MM-DD`; nothing is guessed. The
month-first reading is confirmed by the data: `01/03/2024` and `01/10/2024` each
sit between their ISO-dated neighbours, which only holds if the first field is the
month. An unparseable date or amount is **never silently dropped** — the row is
rejected with a source reference and surfaced.

**Money.** Strict fixed-point: at most two decimals, at most 12 integer digits,
correctly grouped commas and an optional `$`. Malformed grouping, scientific
notation, non-finite values and fractional cents are **rejected, not repaired.**
`Decimal` throughout; `float` appears only at the JSON boundary.

**Kinds** (decided by code, never by the model, because they change the totals):

| Rule | Effect |
| --- | --- |
| amount is 0, or `PENDING` appears **anywhere** in the description | excluded entirely, listed in `excluded` |
| `TRANSFER TO/FROM …` | excluded from income *and* expenses, reported as `transfers` |
| positive **and** `refund`/`return`/`reversal`/`chargeback` | a refund: reduces expenses, is not income |
| any other positive | income |
| negative | expense |

ATM and Venmo outflows stay expenses under this brief's cash-flow convention: the
money left the account. They are flagged, because the end use is unknowable.

**Overlapping records** — the real difficulty, and no file is authoritative.
`bank_statement.csv` repeats most of `expenses.csv` and `income.csv`, usually a day
later; the card export repeats Netflix, Spotify and car insurance. Summing the
files naively double-counts January.

Two rows are one event when **all** of these hold:

1. amounts are exactly equal,
2. kinds are equal,
3. dates are within 2 days of **every** row already in the group,
4. cleaned descriptions share a non-stopword token with **every** row in the
   group,
5. they come from **different files** — at most one row per source file.

Clause 4 is required pairwise rather than in aggregate, because a token union lets
`Alpha` → `Alpha Beta` → `Beta` chain unrelated rows together. Clause 5 is what
makes two genuine `WHOLEFDS MKT #10452 −87.34` charges 17 days apart stay two
charges: one export never lists the same charge twice, so a same-file look-alike is
a repeat purchase, flagged rather than folded. Equally near candidates merge
nothing and raise a review warning — visible uncertainty beats a silent arbitrary
merge. The bank's posting date wins, and every row keeps `sources` back to each
file and line it came from.

**Clause 4 has one deliberate relaxation.** A statement row can be a bare
reference like `POS PURCHASE 4471`, which carries no merchant word at all — so
token overlap is *impossible*, not merely absent, and refusing the merge
double-counts a real charge. When clauses 1–3 and 5 hold, one side has no merchant
token, the candidate is a single row, and no competing candidate exists, the rows
merge and a **warning is recorded that degrades the report.** Two descriptions that
both carry merchant words which simply differ are two merchants and never merge.

**Inconsistent input labels.** The sample data contradicts itself: `expenses.csv`
labels Pharmacy `Healthcare` and Gym Membership `Health`. A CSV's own label is held
to exactly the same standard as model output — canonicalised, or discarded and sent
to the model. Without this, `by_category` would carry two near-identical buckets.

**Noisy merchants.** Only payment rails are stripped (`SQ *`, `TST*`, `PAYPAL *`,
`POS DEBIT`, `CHECKCARD 1314`, `ACH CREDIT`, store and reference numbers). The
cleaned string is used for matching and prompts; the **original description is
what lands in `report.json`.**

---

## Where the LLM/code line is drawn

**The model does exactly two things:** it picks the next tool, and it maps a
merchant string to one of the fixed categories. It also selects summary wording
from an approved list. That is all.

**Totals are computed at `finance_agent/totals.py:34`, in `compute_totals()`.**
That is the line to point at. It reads only `kind`, `amount` and `category`.

The boundary is structural, not aspirational:

- `totals.py` imports nothing that touches the model.
- `Transaction` is frozen (`models.py:141`); categorisation rebuilds rows with
  `dataclasses.replace(..., category=...)`, so a tool cannot reach in and alter an
  amount or a kind.
- `report.finalize_report` always recomputes totals from the transaction list — it
  never reads a number back out of a model observation.
- `state.totals` is invalidated whenever a category changes (`tools.py:93`), so
  writing a report against stale totals is impossible.
- The categorizer prompt is given descriptions, ids and direction — **not the
  amount field.**
- Category validation is a whitelist with one corrective retry. An off-list label
  becomes `Uncategorized` and is flagged. `Other` means *the model looked and could
  not tell*; `Uncategorized` means *we never got a valid answer* — different
  failures, kept distinct.
- **The summary.** The model selects one of three pre-approved factual templates
  and code fills the placeholders. `validate_summary` rejects unapproved wording,
  unknown or swapped placeholders, digits outside placeholders, and number-words.
  Anything invalid falls back to the default template. The report records this as
  `summary_source: "llm_selected_template"` — not `"llm"` — because that is
  honestly all the model contributed.

---

## The agent loop

`agent.py:85`, `run_agent()`. Each turn the controller sees the tool specs, the
current state and the recent observations, and replies with one JSON object. Code
dispatches it and appends an observation. The model chooses order and arguments.

Four selectable tools with real contracts: `list_uncategorized`,
`categorize_batch{ids}`, `compute_totals`, `write_report{summary}`. `write_report`
**refuses** while any row lacks a category or totals are stale, and says so as an
observation the model can act on.

Guarantees enforced by control flow, not by the prompt:

| Bound | Value |
| --- | --- |
| model turns | 10 |
| consecutive bad turns before giving up | 3 |
| identical repeated actions before giving up | 3 |
| rows per categorisation batch | 8, with one corrective retry |

Unparseable replies, unknown tools, bad argument types and an unreachable model
all become observations rather than exceptions. **Every exit path that is not an
unrecoverable I/O error writes a report** via `report.finalize_fallback`, which
makes no model call at all.

The committed recording **deliberately opens with an unparseable reply**, so the
default replay command demonstrates recovery with no extra flags:

```
step 1 tool=None ok=False obs={'error': 'could not read your reply as a tool call (no JSON object found)'}
step 2 list_uncategorized  ok=True
step 3 categorize_batch    ok=True   {'categorized': 8, 'fell_back_to_uncategorized': 0, 'remaining': 8}
...
step 7 write_report        ok=True
```

`--llm garbage` is the harder demo: the model never returns anything usable, the
loop stops after 3 failed turns, and the deterministic fallback still produces
correct headline totals with `status=degraded` and 24 flagged rows.

---

## Optional extras

Took **one**: duplicate and anomaly detection into `flagged` (`flags.py`).
**Skipped** categorization-accuracy measurement — with a hand-authored recording it
would score 100% by construction, a meaningless number dressed as a metric.

---

## AI usage and decisions

**Tools.** Claude wrote the initial implementation, tests and documentation.
ChatGPT/Codex reviewed and revised it. A later Claude session wrote a second,
independent implementation of the same brief from the assignment text alone, then
had a separate reviewer agent — given only the brief, both implementations labelled
A and B, and a neutral rubric — compare them. This tree is the stronger of the two
plus the fixes that comparison produced.

**What differential testing against a second implementation found.** Both
implementations produced *byte-identical* totals and `by_category` on the sample
data, so the defects below only appear on inputs the sample data does not contain.
Each was demonstrated with a failing case before being fixed, and each has a
regression test in `tests/test_regressions.py`:

1. **Returns and reversals were counted as income** (`classify.py`). Only the
   literal word `refund` matched, so `RETURN WIDGET CO +60.00` became income:
   income read 1075.00 instead of 1000.00 and expenses 72.00 instead of −15.00,
   inflating the savings rate. Now `refund|return|returned|reversal|chargeback`.
2. **`PENDING` was only detected at the start of a description.**
   `AUTH PENDING TARGET 99 −12.00` was settled as real spending. Now matched
   anywhere, with a test that `PENDLETON WOOLEN MILLS` is not caught by it.
3. **A renamed charge was double-counted** (`reconcile.py`). Requiring a shared
   merchant token meant one real charge appearing as `Dentist Visit` and
   `POS PURCHASE 4471` stayed two rows — expenses 4331.30 where 4121.30 was
   correct. Fixed by the narrow relaxation described above.
4. **`summary_source` claimed `"llm"`** for wording the model only selected from an
   approved list. Relabelled `"llm_selected_template"`.
5. **2,225 lines of documentation were deleted** — `docs/archive/` and a 523-line
   `docs/CODE_CHANGES.patch`, material the previous README itself called possibly
   outdated. A change log against a prior revision is not a deliverable, and
   nobody can defend it in a 30-minute walkthrough.

**What was rejected.** A first attempt at fix 3 allowed any merge lacking a shared
token. A pre-existing test
(`test_transitive_merchant_matches_cannot_collapse_unrelated_rows`) caught it
immediately: that rule reopens the `Alpha` → `Alpha Beta` → `Beta` chain the token
requirement exists to prevent. The rule was narrowed to *one side has no merchant
word at all*, which is a different and much smaller claim. Also rejected: adopting
the second implementation's amount-plus-date-window merge key, which is simpler but
silently converts a real expense two days from a transfer into a transfer, dropping
it from expenses entirely.

**Decisions to defend.** The reconciliation clauses and why each exists; refunds as
negative spend; the frozen `Transaction` and the recompute-don't-read-back rule;
approved-template summaries over free model prose; `Other` versus `Uncategorized`;
and a degraded run exiting 0 while only I/O and input errors exit non-zero.

---

## Walkthrough notes

1. **Run the replay command, then break it.** Replay already recovers from an
   unparseable first reply at step 1. Then `--llm garbage` breaks the model
   completely and the run still exits 0 with correct headline totals.
2. **Trace `CHECKCARD 1314 SAFEWAY #2910`.** CSV → `parse_date` → `classify_kind` →
   `clean_merchant` gives `safeway`, the only string the model sees → whitelisted
   `Food` → `by_category`. The original description is what appears in
   `report.json`, and `sources` names the file and line.
3. **Point at `totals.py:34`** and walk the four reasons model output cannot reach
   it: no import, frozen dataclass, recompute-never-read-back, and no amount in the
   categorizer prompt.
4. **Show the reconciliation table** — why `Car Insurance` and `GEICO AUTO
   INSURANCE` are one charge (they share `insurance`), why two `WHOLEFDS −87.34`
   charges 17 days apart are two, and why `POS PURCHASE 4471` needs the relaxation.
5. **Be upfront about the recording.** Hand-authored, provenance in the file, the
   real-recording test left skipped on purpose, and `OllamaClient` never executed.

---

## What was deliberately not built, and what is unverified

Not built: any LLM framework, provider registry or config layer; structured
function-calling (small local models handle it poorly, and tolerating malformed
output is the exercise); a database, caching or incremental runs; multi-month,
multi-currency or multi-account balance reconciliation; a UI; CI; Docker.

**With two more hours:** run a real Ollama model, commit a genuine recording, and
measure category accuracy against the nine pre-labelled rows — with a real model
that number would finally mean something. Then replace token matching with a stable
`(account, transaction_id)` key if the exports ever carry one, which would retire
the entire reconciliation heuristic.

**Unverified:** `OllamaClient.complete()` has never run against a live server. No
category-accuracy figure is claimed. A zero-income savings rate is reported as
numeric `0` for output-contract compatibility though it is mathematically
undefined. `transfers` is a gross row magnitude and would count both sides if both
accounts were supplied. Concatenated merchant spellings (`WIDGET CO` against
`WIDGETCO`) still fail to match and stay separate, flagged. No parallel-run locking
and no complete prompt-injection defence is claimed. Everything else stated here
was produced by running the code.
