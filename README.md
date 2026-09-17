# Personal Finance Agent

A tool-using agent that reconciles four overlapping CSV exports into `report.json`
plus a human-readable summary. Python owns every amount, every merge and every sum.
A language model chooses tools and assigns spending categories — nothing else.

Python 3.10+, **no runtime dependencies.** 170 offline tests, none skipped.

---

## Run it

```bash
python -m pip install -e ".[dev]"        # pytest only, for the test run
python -m pytest -q

# End to end, offline, replaying a real qwen2.5:7b run. This is the command to run.
python -m finance_agent --data sample_data --out report.json \
    --replay recordings/llm_responses.json --replay-strict

# The same machinery against the hand-authored fixture the regression tests use.
python -m finance_agent --data sample_data --out /tmp/fixture_replay.json \
    --replay recordings/llm_recording.json --replay-strict

# The failure path, live: a model that returns nothing usable, ever.
python -m finance_agent --data sample_data --out examples/degraded_report.json --llm garbage
```

`FINANCE_AGENT_REPLAY` sets a default replay file if you prefer an environment
variable to a flag. All three commands work in PowerShell and POSIX shells.

The first command replays the committed live run and prints:

```
status=degraded income=11001.25 expenses=3911.30 net=7089.95 savings_rate=0.6445 flagged=9 warnings=1
```

**That `degraded` is the real result of the real run, not a fault in the replay.**
On its last turn qwen2.5:7b wrote its own numbers into the summary instead of using
the placeholders, and got one of them wrong — it claimed `0 items are flagged` when
9 were. `validate_summary` rejected the string whole, code substituted the approved
template, and the run recorded `model summary rejected (contains digits outside
placeholders); template used`. That is the untrusted-output rule catching a real
model in the act; [the agent loop](#the-agent-loop) walks the full trace.

The fixture replay prints `status=ok ... warnings=0`. Both agree on the headline
numbers:

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

**Ollama, `qwen2.5:7b`, `temperature: 0`, `seed: 42`, `format: json`**, over
Ollama's documented `/api/generate` endpoint at `http://localhost:11434`
(`OllamaClient`, `finance_agent/llm.py:50`). No API key, no paid service and
nothing to configure — that model string is the client's default.

`recordings/llm_responses.json` is a **genuine capture of that model**, written by
`RecordingClient` while the agent ran against the live daemon on 2026-09-17:
13 calls in 62 seconds on macOS 26.5, Apple silicon. `report.json` and
`report_summary.md` are the artifacts of that same run.
`tests/test_e2e_replay.py::test_committed_real_recording_replays` replays it under
`--replay-strict` and asserts the result equals the committed `report.json`. That
test used to be the suite's one skip; it now runs, and the suite has no skips left.

`recordings/llm_recording.json` is **still a hand-authored fixture and is still
labelled as one.** Its `provenance` field says so and
`tests/test_regressions.py::test_committed_recording_declares_its_provenance`
asserts that field is present. It is kept on purpose rather than retired: it opens
with a reply that is not JSON at all, and `format: json` meant the live model never
produced one — all 13 live replies parsed. So the fixture is the regression tests'
only coverage of the unparseable-reply path. It is a test fixture, not evidence of
a model run, and nothing in this tree describes it as one.

To reproduce the capture instead of replaying it:

```bash
ollama pull qwen2.5:7b                   # ~4.7 GB
ollama serve                             # in its own shell
python -m finance_agent --data sample_data --out report.json \
    --record recordings/llm_responses.json
python -m finance_agent --data sample_data --out /tmp/live_replay.json \
    --replay recordings/llm_responses.json --replay-strict
```

Recordings are keyed by a `model + system + prompt` fingerprint that is validated
on load, so a different model — or an edited prompt — misses loudly rather than
replaying the wrong answer at you. Keep a live report, its summary and its
recording from the same run: regenerating one means regenerating all three. **A
re-run will not reproduce this recording exactly.** `temperature: 0` and a fixed
seed constrain the model, not the tool path it chooses, and the trace below is one
sample of that behaviour rather than a fixed point.

---

## Time spent

Roughly **6 to 8 hours** in total: the first build, a second independent
implementation of the same brief and the blind A/B review that chose between them,
then this session's live model run and the documentation it invalidated.

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
  Anything invalid falls back to the default template. When the model's choice is
  accepted the report records `summary_source: "llm_selected_template"` — not
  `"llm"` — because that is honestly all the model contributed. **The committed
  live run records `"template"` instead**, because qwen2.5:7b's summary was
  rejected and code wrote the sentence unaided. The field distinguishes the two,
  so the report never overstates the model's contribution.

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

**The live run is the loop's best demonstration, because qwen2.5:7b got three
things wrong and the loop absorbed all three.** This is the actual `agent_trace`
from the committed `report.json`, reproduced by the default replay command:

```
step 1  list_uncategorized  ok=True   remaining=16
step 2  categorize_batch    ok=True   {'categorized': 8, 'fell_back_to_uncategorized': 0, 'remaining': 8}
step 3  compute_totals      ok=True   income=11001.25 expenses=3911.30
step 4  write_report        ok=False  '8 transactions still need a category (e.g. [t031, ...]); call categorize_batch first'
step 5  categorize_batch    ok=True   {'categorized': 5, 'remaining': 3}
step 6  categorize_batch    ok=False  'these ids do not need a category: [t031, t033, t036, t037, t039]'
step 7  list_uncategorized  ok=True   remaining=3
step 8  categorize_batch    ok=True   {'categorized': 3, 'remaining': 0}
step 9  compute_totals      ok=True
step 10 write_report        ok=True   summary_source='template'
```

1. **Step 4 — it tried to write the report half-finished.** `write_report`'s
   precondition refused and the observation named the ids still missing a category.
   The model read it and went back to categorising.
2. **Step 6 — it re-sent the batch it had just completed.** `categorize_batch`
   refused instead of re-labelling settled rows, and the model recovered by calling
   `list_uncategorized` to find what was actually left.
3. **Step 10 — it wrote its own numbers into the summary.** It replied
   `"In the period you received 11001.25 and spent 3911.3 ... 0 items are flagged
   for review."`: digits where placeholders belong, and the flag count was flatly
   **wrong** — 9 rows were flagged, not 0. `validate_summary` rejected the sentence
   whole, code substituted the approved template filled from computed totals, and
   the run finished `degraded` with a warning naming the reason. **This is the most
   useful thing the live model did.** A build that pasted model prose into the
   report would have shipped a false sentence over correct totals.

It finished on turn 10 of 10 — the step limit held with nothing to spare, which is
a finding worth reporting rather than quietly tuning away afterwards.

The **fixture** recording covers a failure the live model never produced. With
`format: json` every live reply parsed, so `recordings/llm_recording.json` opens
with a reply that is not JSON at all, keeping the unparseable-reply path under
test:

```
step 1 tool=None ok=False obs={'error': 'could not read your reply as a tool call (no JSON object found)'}
step 2 list_uncategorized  ok=True
...
step 7 write_report        ok=True
```

`--llm garbage` is the harder demo: the model never returns anything usable, the
loop stops after 3 failed turns, and the deterministic fallback still produces
correct headline totals with `status=degraded` and 25 flagged rows.

---

## Optional extras

Took **one**: duplicate and anomaly detection into `flagged` (`flags.py`; 9 entries on the sample data).
**Skipped** categorization-accuracy measurement. The brief allows at most one
extra, and the original reason for skipping — a hand-authored recording scores 100%
by construction — expired the moment a real model ran. The honest current reason is
narrower: the rows carrying ground-truth labels are exactly the rows the agent
never shows the model. All nine keep `category_source: "csv"`. Measuring accuracy
therefore needs a second run with those labels stripped, and a second recording to
go with it — a different experiment, not a few more lines.

---

## AI usage and decisions

**Tools.** Claude wrote the initial implementation, tests and documentation.
ChatGPT/Codex reviewed and revised it. A later Claude session wrote a second,
independent implementation of the same brief from the assignment text alone, then
had a separate reviewer agent — given only the brief, both implementations labelled
A and B, and a neutral rubric — compare them. This tree is the stronger of the two
plus the fixes that comparison produced. A final session, on a machine that could
actually run Ollama, captured the live recording and rewrote every claim in this
README that the capture falsified; it added no application code.

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
5. **The README claimed ATM withdrawals were flagged; they were not.** Only
   Venmo/Zelle/Cash App matched, so a $100 cash withdrawal with untraceable end
   use passed silently. Found by an independent verification pass over this tree,
   not by me. Cash-out is now its own flag rule, which is why the sample run
   reports 9 flagged rows rather than 8.
6. **2,225 lines of documentation were deleted** — `docs/archive/` and a 523-line
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
5. **Open `report.json`'s trace at step 10.** qwen2.5:7b wrote `0 items are
   flagged for review` when 9 were; `validate_summary` discarded the whole sentence
   and code wrote the report. Then say what is still only a fixture — the
   unparseable-reply path, which the live model never triggered — and why that
   fixture stays labelled as one.

---

## What was deliberately not built, and what is unverified

Not built: any LLM framework, provider registry or config layer; structured
function-calling (small local models handle it poorly, and tolerating malformed
output is the exercise); a database, caching or incremental runs; multi-month,
multi-currency or multi-account balance reconciliation; a UI; CI; Docker.

**With two more hours:** capture the run several more times and see how stable a
7B model's tool choices actually are — one run is an existence proof, not a
reliability claim, and step 10 of 10 says the margin is thin. Then measure category
accuracy properly, by re-running with the pre-labelled rows' categories stripped so
the model has to reproduce them. Then replace token matching with a stable
`(account, transaction_id)` key if the exports ever carry one, which would retire
the entire reconciliation heuristic.

**Unverified:** `OllamaClient.complete()` has now executed against a live Ollama
daemon, but every claim here about model behaviour rests on **one run of one model
on one machine** and characterises nothing in general. No category-accuracy figure
is claimed. A zero-income savings rate is reported as
numeric `0` for output-contract compatibility though it is mathematically
undefined. `transfers` is a gross row magnitude and would count both sides if both
accounts were supplied. Concatenated merchant spellings (`WIDGET CO` against
`WIDGETCO`) still fail to match and stay separate, flagged. No parallel-run locking
and no complete prompt-injection defence is claimed. Everything else stated here
was produced by running the code.
