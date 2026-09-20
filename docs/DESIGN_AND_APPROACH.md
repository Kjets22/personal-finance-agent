# Design and approach

The README is the two-page summary. This document is the long form: every rule,
every worked example, the full live trace, and the reasoning behind each decision.
Everything here was produced by running the code; line references are to the
current tree.

## Objective and scope

Build an inspectable agent that turns the supplied overlapping exports into a
cash-flow report, with usable output even when the model fails. The assignment's
central demonstration is tool selection with observations and recovery, so the
loop is hand-written and visible. A framework, vector store, web interface or
extra provider would raise the review burden without fixing a correctness gap.

The model is useful for ambiguous merchant labels, not for arithmetic or identity
resolution. Deterministic code makes the latter auditable — but auditable is not
the same as correct, so the report exposes reconciliation uncertainty and
excludes malformed rows visibly rather than hiding either.

## Data flow

1. `ingest.py` reads the four files, validates structure, parses dates and
   fixed-point amounts. Bad rows are retained as exclusions with a source
   reference; broken file structure and encoding are input errors (exit 2).
2. `classify.py` assigns transaction *kind* deterministically.
3. `reconcile.py` groups plausible cross-source duplicates, preserves provenance
   and exposes conflicts as warnings.
4. `agent.py` asks the controller model for one tool call at a time. `tools.py`
   checks arguments and preconditions before touching state.
5. `categorize.py` sends only rows lacking a category to the model and accepts
   only whitelisted answers. Valid partial answers survive one retry; unresolved
   rows become `Uncategorized`.
6. `totals.py` computes money with `Decimal`. `report.py` recomputes totals from
   the transaction list, validates the selected summary template, and writes
   `report.json` plus `report_summary.md` atomically.

Five of the six stages are ordinary Python. One calls a model.

## The dividing rule

The line is not "words versus numbers." It is: **does this decision move a number
in the report?**

- *Kind* (income / expense / refund / transfer / pending) lands directly on
  `income` and `expenses`. Code decides it.
- *Identity* (are these two rows one event?) changes the total by the whole
  amount. Code decides it.
- *Category* moves money between buckets that all live inside `expenses`. The
  sum of `by_category` equals `expenses` regardless of what the model says
  (`test_by_category_sums_exactly_to_expenses`), and headline totals cannot move
  (`test_changing_only_categories_never_changes_headline_totals`). The model
  decides it — because it is semantic, financially inert, and has no single
  right answer. Is Costco `Food` or `Shopping`? Debatable. Is `TRANSFER TO
  SAVINGS` a transfer? Not debatable.

The live run demonstrated the asymmetry. It labelled `COSTCO WHSE #0421`
`Shopping` where the hand-authored fixture said `Food`: `Food` 787.87 → 631.09,
`Shopping` 229.99 → 386.77, and income, expenses, net and savings rate did not
change by a cent.

## Data-cleaning rules in full

69 raw rows across four files reconcile to 47 movements plus 1 excluded pending
row. Summing the files naively gives income 15,436.24 and expenses 5,418.32
against the correct 11,001.25 and 3,911.30 — income about 40% too high, with
nothing in the output looking obviously wrong.

**Dates.** ISO year-first (`2024-01-02`), unpadded ISO (`2024-1-6`) and slashed US
month-first (`01/03/2024`) are accepted and normalised to `YYYY-MM-DD`. Nothing is
guessed. The month-first reading is confirmed by the data: `01/03/2024` and
`01/10/2024` each sit between their ISO-dated neighbours, which only holds if the
first field is the month. An unparseable date or amount is never silently dropped;
the row is rejected with `file:line` and surfaced in `excluded`.

**Money.** Strict fixed-point: at most two decimals, at most 12 integer digits,
correctly grouped commas, optional `$`. Malformed grouping, scientific notation,
non-finite values and fractional cents are rejected, not repaired. `Decimal`
throughout; `float` appears only in `to_money()` at the JSON boundary
(`test_no_float_drift`).

**Kinds.** `classify.py`, first match wins:

| Test | Kind |
| --- | --- |
| amount == 0, or `\bpending\b` anywhere in the description | `pending` — excluded |
| `\btransfer\s+(to\|from)\b` | `transfer` — neither total |
| amount > 0 **and** `\b(refund\|return\|returned\|reversal\|chargeback)\b` | `refund` — reduces expenses |
| amount > 0 | `income` |
| otherwise | `expense` |

Three things in that table are deliberate:

- *Order.* `PENDING TRANSFER TO SAVINGS` matches two rules; pending wins, because a
  movement that has not happened should not be classified at all.
- *Sign.* `Refund AMAZON.COM +34.99` is a refund; `Refund processing charge −3.50`
  is an expense. The word alone is not enough.
- *Word boundaries.* A substring check on `pending` matches `SPENDING ACCOUNT FEE`
  and `SUSPENDING SERVICE`, silently excluding real spending. `\bpending\b` does
  not, and still catches `AUTH PENDING TARGET 99` mid-string
  (`test_pending_is_detected_anywhere_in_the_description`,
  `test_the_word_pending_does_not_swallow_a_real_merchant`).

ATM and Venmo outflows stay expenses under the brief's cash-flow convention — the
money left the account — and are flagged because the end use is unknowable.

**Merchant cleaning** (`normalize.py`). Six prefix patterns (`SQ *`, `TST*`,
`PAYPAL *`, `POS DEBIT`, `CHECKCARD nnnn`, `ACH CREDIT`) are stripped, `#store`
numbers removed, `* / .` treated as separators, and any token containing a digit
or in `{com, usa, inc}` dropped:

```
WHOLEFDS MKT #10452           -> wholefds mkt
CHECKCARD 1314 SAFEWAY #2910  -> safeway
SQ *BLUE BOTTLE COFFEE        -> blue bottle coffee
POS DEBIT THE HOME DEPOT 123  -> the home depot
Netflix.com                   -> netflix
```

The cleaned string is used for matching and prompts. The original description is
what lands in `report.json`.

**Inconsistent input labels.** `expenses.csv` labels Pharmacy `Healthcare` and Gym
Membership `Health`. A CSV's own category is passed through the same
`resolve_category` whitelist as model output, so `Health` resolves to `Healthcare`
via the alias table rather than creating a second bucket. One row in the sample
takes that path (`category_source: "alias"`).

## Reconciliation

No file is authoritative. `bank_statement.csv` repeats most of `expenses.csv` and
`income.csv`, usually posting a day later; the card export repeats Netflix,
Spotify and car insurance.

**Two rows are one event when all five hold:**

1. amounts are exactly equal;
2. kinds are equal;
3. dates are within 2 days of **every** row already in the group;
4. cleaned descriptions share a non-stopword token with **every** row in the group;
5. they come from **different files** — at most one row per source file.

The bank's posting date wins for the merged row, and every row keeps `sources`
back to each `file:line` it came from.

**18 groups merged on the sample data**, including three-way merges:

```
2024-01-05  Amazon Purchase        -89.99  <- bank_statement.csv:6 + expenses.csv:5
2024-01-06  Netflix Subscription   -15.99  <- bank:7 + expenses:6 + transactions_uncategorized:6
2024-01-14  Car Insurance         -125.00  <- bank:15 + expenses:15 + transactions_uncategorized:14
2024-01-01  Salary                3000.00  <- bank_statement.csv:2 + income.csv:2
```

`Car Insurance` and `GEICO AUTO INSURANCE` merge because they share `insurance`.

**What deliberately did not merge** — identical amounts, kept separate:

```
-156.78  Home Depot (01-10, bank+expenses)   vs  COSTCO WHSE (01-29)      19 days, no shared word
 -89.99  Amazon Purchase (01-05)             vs  COMCAST CABLE (01-27)    22 days, no shared word
 -87.34  WHOLEFDS MKT #10452 (01-02)         vs  WHOLEFDS MKT #10452 (01-19)   same file, 17 days
3000.00  Salary (01-01, bank+income)         vs  Salary (01-15, income only)   two paychecks
```

The Whole Foods pair passes clauses 1, 2 and 4 outright. Clauses 3 and 5 keep it
as two purchases. Clause 5 is the load-bearing one: one export never lists the
same charge twice, so a same-file look-alike is a repeat purchase and is flagged
rather than folded (`test_same_file_lookalikes_are_still_never_merged`).

**Clause 4 is pairwise, not aggregate.** Matching against the *union* of group
tokens lets `Alpha` → `Alpha Beta` → `Beta` chain three unrelated rows into one;
requiring a shared token with every existing member closes the chain
(`test_transitive_merchant_matches_cannot_collapse_unrelated_rows`).

**The one relaxation.** A statement row can be a bare reference like
`POS PURCHASE 4471`, which carries no merchant word at all, so token overlap is
*impossible* rather than merely absent, and refusing the merge double-counts a
real charge (expenses 4331.30 where 4121.30 is correct on the probe input). When
clauses 1–3 and 5 hold, one side has no merchant token, the candidate is a single
row, and no competing candidate exists, the rows merge and a warning is recorded
that degrades the report. Two descriptions that both carry merchant words which
simply differ are two merchants and never merge
(`test_relaxation_needs_a_token_less_side_not_merely_a_mismatch`).

**Ties.** Equally near candidates merge nothing and raise a review warning that
degrades the report. This trades a possible overcount for visible uncertainty
instead of a silent arbitrary merge. Better matching needs account and
transaction identifiers that these exports do not carry.

The second implementation's simpler key — amount plus a date window, no
descriptions — was rejected because it silently converts a real expense dated two
days from a transfer into a transfer, dropping it from expenses entirely.
Concatenated spellings (`WIDGET CO` against `WIDGETCO`) still fail to match and
remain separate, flagged; fixing that needs fuzzier matching than this project
takes on.

## Trust boundary

A model response can select a known tool, supply validated ids, assign a
whitelisted category or choose approved summary wording. It cannot supply
executable code, file paths, amounts, kinds or replacement totals.

`compute_totals` (`totals.py:34`) reads `kind`, `amount` and `category` only.
Model output cannot reach it, for four independent reasons — any one suffices:

1. **Import isolation.** `totals.py` imports `Category`, `Kind`, `Decimal`,
   `defaultdict`. Nothing from `llm`, `agent` or `categorize`. Statically
   checkable with `grep`.
2. **Frozen records.** `Transaction` is `@dataclass(frozen=True)` (`models.py:141`).
   Categorisation uses `dataclasses.replace(t, category=...)`, which builds a new
   object; there is no assignment path to `amount` or `kind`.
3. **Recompute, never read back.** The controller sees totals as an observation at
   `compute_totals` time, but `finalize_report` calls `compute_totals` again from
   the transaction list. Nothing reads a number out of a model turn.
   `state.totals` is also invalidated whenever a category changes (`tools.py:93`).
4. **Prompt starvation.** The categorizer prompt carries `id`, `merchant`, `raw`
   and `direction`. Not `amount`. The model cannot echo a number it was never
   given.

Frozen records make accidental mutation harder; they do not by themselves prove
immutability of the whole program. The actual protection is the limited tool
implementations plus the regression tests around the boundary.

**Categories.** `resolve_category` is a whitelist with a small alias table:
exact match (case- and whitespace-insensitive), then alias (`Groceries` → `Food`,
`Health` → `Healthcare`, `Rent` → `Housing`, `ATM` → `Cash`), else rejected.
Non-strings are rejected outright. A rejected label gets one corrective retry
naming the specific problem for only the failed ids; still invalid becomes
`Uncategorized` and is flagged. `Other` and `Uncategorized` are kept distinct:
the first means the model looked and could not tell (`VENMO *JOHN SMITH`), the
second means no valid answer was ever obtained. Collapsing them would hide model
failures inside legitimate ambiguity.

Descriptions are untrusted data, and the categorizer system prompt says so. No
complete prompt-injection defence is claimed; the whitelist bounds the damage.

## Why constrain summary wording

Placeholder validation alone does not validate meaning. A model can write
"income was {expenses}" while every number still comes from code, and no check
on the numbers catches the swapped label.

So the model does not write prose. It selects one of three approved complete
sentences, and code fills the placeholders. `validate_summary` rejects unapproved
wording, unknown or swapped placeholders, any digit outside a placeholder, and
number-words. Anything invalid falls back to the default template with a warning
that degrades the report.

When the model's selection is accepted, the report records
`summary_source: "llm_selected_template"` — not `"llm"` — because selection is all
it contributed. When the selection is rejected, it records `"template"`.

**The live run exercised the rejection path.** At step 10, qwen2.5:7b replied:

> "In the period you received 11001.25 and spent 3911.3, leaving 7089.95 (64.45
> savings rate). The largest spending category was Housing at 1800.0. 0 items are
> flagged for review."

Digits where placeholders belong; the `%` dropped from the rate; and the flag
count was simply wrong — 9 rows were flagged, not 0. The sentence was rejected
whole, the approved template was filled from computed totals, and the report
records `summary_source: "template"` with the warning `model summary rejected
(contains digits outside placeholders); template used`. A design that validated
numbers inside model prose would have caught the first two problems and shipped
the third, because `0` is a valid number.

## The agent loop

`run_agent` (`agent.py:85`). Each turn the controller receives the tool
specifications, a state summary (`transactions`, `needing_category`,
`totals_computed`) and the last six observations, and must reply with one JSON
object `{"tool", "args", "reason"}`. Code parses it, dispatches it, appends the
observation. The model chooses order and arguments; code enforces everything
else.

| Tool | Arguments | Contract |
| --- | --- | --- |
| `list_uncategorized` | `limit` 1–8 | count and ids/merchants still needing a category |
| `categorize_batch` | `ids`, 1–8 | rejects duplicates, unknown ids, and ids that already have a category |
| `compute_totals` | none | code-computed totals and top three categories, as an observation |
| `write_report` | `summary` | refuses while any row lacks a category or totals are stale |

Bounds are enforced by control flow, not by the prompt: 10 model turns, 3
consecutive failed turns, 3 identical repeated actions, 8 rows per batch with one
corrective retry. A prompt is a request; a loop counter is a guarantee.

**The live trace** (`report.json`, `agent_trace`, reproduced by the default replay):

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

Three model errors, all absorbed by tool contracts rather than by anything in the
prompt:

1. **Step 4** — `write_report` with 8 rows still uncategorised. The precondition
   refused and the observation named the missing ids; the model went back to
   categorising.
2. **Step 6** — re-sent the batch it had just completed. `categorize_batch`
   refused rather than re-labelling settled rows; the model recovered by calling
   `list_uncategorized`.
3. **Step 10** — digits and a fabricated flag count in the summary, rejected as
   described above.

It finished on turn 10 of 10. The step limit held with no margin. That is
reported rather than tuned away: bumping a limit without knowing why it was
reached is cargo cult, and the real remedies are batch size or richer
observations, which are listed under "with more time."

**The fixture trace** covers a failure the live model never produced. Ollama's
`format: json` constrains output to valid JSON, and all 13 live replies parsed. So
the unparseable-reply path — prose instead of a tool call — is exercised only by
the hand-authored `recordings/llm_recording.json`, which opens with exactly that:

```
step 1 tool=None ok=False obs={'error': 'could not read your reply as a tool call (no JSON object found)'}
step 2 list_uncategorized  ok=True
...
step 7 write_report        ok=True
```

**`--llm garbage`** is the hard case: every reply is unusable (`"lol no"`, a
truncated object, a fenced non-JSON block, an unknown tool, an empty string, a
JSON array). Three failed turns, the loop stops, `finalize_fallback` writes the
report with every remaining row `Uncategorized` and flagged: `status=degraded`,
25 flagged, and the same four headline numbers.

For this dataset a plain script would work and be simpler. The agent structure
earns its keep specifically in the error paths, and the live run put two of them
on the record.

## Failure behaviour

Three layers, each catching a different class of failure, and none of them an
exception that escapes the loop:

1. **Parsing.** A reply that is not a tool call becomes the observation
   `could not read your reply as a tool call (...)`. `extract_json_object` digs an
   object out of code fences and surrounding prose first.
2. **Tool contracts.** `ToolError` for bad arguments, unknown ids, duplicate ids,
   already-done ids, stale totals. Unknown tool names get `unknown tool 'x';
   choose one of [...]`. A tool that raises anything else is reported as
   `tool crashed: ...` rather than killing the run.
3. **Fallback.** Three consecutive failures, three identical repeats or ten turns
   → `finalize_fallback`, which makes no model call, categorises nothing, flags
   the rest, computes totals in code and exits 0 with `status: degraded`.

An unreachable server (`--ollama-host http://127.0.0.1:1` → `Connection refused`)
and a missing model (`--model nonexistent` → `HTTP 404`) both take the same path:
three failed observations, fallback, exit 0, identical headline totals.

Input corruption fails early (exit 2) or produces visible row exclusions. Strict
replay misses exit 2 on purpose. Output and recording failures exit 3. Atomic
replacement protects individual files; both payloads are staged before promotion
and the JSON is promoted last, so a crash between promotions can leave a summary
from another run — the JSON is authoritative.

Exit codes describe whether the tool worked; `status` describes whether the data
is clean. A degraded report is a working tool reporting a problem, so it exits 0.
Conflating the two channels would make a pipeline treat a legitimately flagged
month as a crash.

## Recording and replay

`RecordingClient` wraps any client and saves every call as
`{key, system, prompt, response | error}`, where
`key = sha256(model + "\n" + system + "\n" + prompt)`. `ReplayClient` recomputes
the key from the live prompt and looks it up, so:

- an edited prompt or a different model **misses loudly** rather than replaying a
  stale answer — editing one prompt in a copy of the committed recording makes
  replay exit 2 with `fingerprint mismatch at entry 0`;
- identical prompts are stored as a list with a cursor, so a retry replays the
  second recorded answer, not the first again;
- recorded failures replay as the same `LLMError`, so a bad live run is
  reproduced faithfully (`test_recorded_failures_replay_as_failures`).

`--replay-strict` turns a miss into exit 2, for tests and CI. Plain `--replay`
turns it into an observation and degrades, for demos. The same failure is fatal in
verification and survivable in production.

**The two committed recordings.** `llm_responses.json` is a genuine capture of
`qwen2.5:7b` (temperature 0, seed 42, `format: json`) made on 2026-09-17 on macOS
26.5 / Apple silicon: 13 calls in 62 seconds. `report.json` and
`report_summary.md` are from that run, and
`test_committed_real_recording_replays` asserts a strict replay equals the
committed report. `llm_recording.json` is a hand-authored fixture produced by
`scripts/make_recording.py` through the real `RecordingClient`; its `provenance`
field says so and `test_committed_recording_declares_its_provenance` asserts the
field is present. It is kept, labelled, because it is the only coverage of the
unparseable-reply path. Nothing describes it as a model run.

**Reproducibility, measured.** After the first capture the README said a re-run
would not reproduce the recording exactly, reasoning that temperature 0 constrains
generated text but not tool choice. That was written from assumption. A second
independent live run produced a byte-identical recording: 13 calls, the same tool
path, zero category differences, the same rejected summary. The claim was
corrected to what was observed. Two runs on one machine is evidence of determinism
*here*; it is not a claim about other GPUs, Ollama builds or quantisations.

## AI usage: what the second implementation found

A second implementation was built from the assignment text alone, and a reviewer
agent given only the brief, both trees labelled A and B, and a neutral rubric
chose between them. Both produced byte-identical totals and `by_category` on the
sample data, so every defect below appears only on inputs the sample does not
contain. Each was demonstrated with a failing case before being fixed and has a
regression test in `tests/test_regressions.py`:

1. **Returns and reversals were counted as income** (`classify.py`). Only the
   literal word `refund` matched, so `RETURN WIDGET CO +60.00` became income:
   income read 1075.00 instead of 1000.00 and expenses 72.00 instead of −15.00,
   inflating the savings rate. Now `refund|return|returned|reversal|chargeback`.
2. **`PENDING` was only detected at the start of a description.**
   `AUTH PENDING TARGET 99 −12.00` was settled as real spending. Now matched
   anywhere, with a test that `PENDLETON WOOLEN MILLS` is not caught.
3. **A renamed charge was double-counted** (`reconcile.py`): one charge appearing
   as `Dentist Visit` and `POS PURCHASE 4471` stayed two rows, expenses 4331.30
   where 4121.30 was correct. Fixed by the narrow relaxation above.
4. **`summary_source` claimed `"llm"`** for wording the model only selected.
   Relabelled `"llm_selected_template"`.
5. **The README claimed ATM withdrawals were flagged; they were not.** Only
   Venmo/Zelle/Cash App matched. Found by an independent verification pass.
   Cash-out is now its own flag rule; the sample run reports 9 flagged rows
   rather than 8 and the garbage run 25 rather than 24.
6. **2,225 lines of documentation were deleted** — `docs/archive/` and a
   523-line `CODE_CHANGES.patch`, material the previous README itself called
   possibly outdated.

**Rejected.** The first attempt at fix 3 allowed any merge lacking a shared token.
`test_transitive_merchant_matches_cannot_collapse_unrelated_rows` caught it
immediately: that rule reopens the `Alpha → Alpha Beta → Beta` chain. It was
narrowed to "one side has no merchant word at all," a much smaller claim. Also
rejected: the second tree's amount-plus-date merge key, for the transfer reason
given above.

**The final session** ran the model, captured the recording, and rewrote every
documentation claim the capture falsified — including one of its own. It added
no application code.

## Category accuracy: why no figure is claimed

The brief allows one optional extra; duplicate and anomaly detection was taken.
The original reason for skipping accuracy — a hand-authored recording scores 100%
by construction — expired when a real model ran. The current reason is narrower
and checkable: the nine rows in `transactions_uncategorized.csv` that carry a
ground-truth label all have `category_source: "csv"` in the report, meaning they
are exactly the rows the agent never sends to the model. Scoring accuracy needs a
separate run with those labels stripped, and a separate recording. That is a
different experiment, not a few more lines.

## Verification and honest limits

170 offline tests pass and none are skipped. Strict replay of both recordings,
`--llm garbage`, an unreachable server and a missing model were all run and all
exit 0 with identical headline totals. A fresh clone of the pushed branch with
`OLLAMA_HOST` pointed at a dead port passes the suite and reproduces the committed
`report.json`. The sample headline totals are unchanged by every fix listed here
and by the live run itself.

Not claimed: live-model performance in general, category accuracy, production
readiness, compliance, portability of the recording to other hardware. A
zero-income savings rate is reported as numeric `0` for output-contract
compatibility though it is undefined. `transfers` is a gross row magnitude and
would count both sides if both accounts were supplied. No parallel-run locking.
The step limit was reached exactly on the live run; a longer input would degrade.
