# Design and approach

## Objective and scope

Build an inspectable agent that turns the supplied overlapping exports into a
cash-flow report, with usable output even when the model fails. The original
assignment text was not included separately; this revision preserves the CLI,
core report contract and agent structure documented in the supplied project.

## Data flow

1. `ingest.py` reads the required files, validates structure and parses dates and
   fixed-point amounts. Bad rows are retained as exclusions; broken file structure
   and encoding are input errors.
2. `classify.py` assigns transaction kind deterministically. `reconcile.py` groups
   plausible cross-source duplicates, preserves provenance and exposes conflicts.
3. `agent.py` asks the controller for one tool call at a time. `tools.py` checks
   arguments and preconditions before changing state.
4. `categorize.py` accepts only allowed spending categories for requested IDs.
   Valid partial answers survive a single retry; unresolved rows become
   `Uncategorized`. `Other` means the model could not determine the purpose.
5. `totals.py` computes money with `Decimal`. The report recomputes totals from
   transactions instead of trusting the controller's observations.
6. `report.py` validates the selected summary template and constructs the report.
   `fileio.py` stages UTF-8 files and atomically replaces individual destinations.

## Why keep this architecture?

The assignment's central demonstration is tool selection with observations and
recovery. A hand-written loop keeps that visible. A framework, vector store,
web interface or extra provider would increase the review burden without fixing
the correctness gaps discovered here.

The model is useful for ambiguous merchant labels, not for arithmetic or identity
resolution. Deterministic code makes the latter decisions auditable, but does not
make imperfect business assumptions automatically correct. The report explicitly
exposes reconciliation uncertainty and excludes malformed rows visibly.

## Trust boundary

A model response can select a known tool, supply validated IDs, assign an allowed
category or choose approved summary wording. It cannot supply executable code,
file paths, amounts, kinds or replacement totals. The categorizer has no dedicated
amount field; the controller receives totals produced by code. Descriptions are
untrusted data. Schema validation limits the damage of bad output, but a wrong
allowed category remains possible and affects category totals.

Frozen transaction records make accidental mutation harder. They do not by
themselves prove immutability of the entire program: the actual protection is the
limited tool implementations and regression tests around the boundary.

## Reconciliation tradeoff

Each additional source row must agree with every existing group member on amount,
kind, date window and merchant overlap. This prevents a token-union chain from
making unrelated rows look equivalent. A unique nearest candidate can still be
wrong; better matching needs account and transaction identifiers absent here.

Equally near candidates stay separate and trigger degraded status. This trades
possible overcounting for visible uncertainty instead of a silent arbitrary
merge. The source references give a reviewer a direct route to resolving it.
This is not a globally optimal transaction-matching algorithm.

Requiring merchant overlap has one failure of its own: a statement row can be a
bare reference like `POS PURCHASE 4471`, which carries no merchant word, so
overlap is *impossible* rather than merely absent, and refusing the merge
double-counts a real charge. The relaxation in `reconcile._find_match` handles
exactly that case -- a single-row candidate where one side has no merchant token
at all, and no competing candidate -- and records a warning that degrades the
report. Two descriptions that both carry merchant words which simply differ are
treated as two merchants and never merged. Concatenated spellings
(`WIDGET CO` against `WIDGETCO`) still fail to match and remain separate; fixing
that needs fuzzier matching than this project takes on, and the unmatched-row
flag surfaces it.

## Why constrain summary wording?

Placeholder validation alone does not validate meaning. A model can write
"income was {expenses}" while every number still comes from code. Approved
complete templates bind the wording to the correct fields and disallow unsupported
claims. The model retains a small wording choice; arbitrary personalized advice
is deliberately outside this report's scope.

## Failure behavior

Input corruption fails early or produces visible row exclusions. Bad model calls
produce observations and eventually a deterministic degraded report. Strict replay
misses fail intentionally. Output and recording failures return exit 3.

Atomic replacement protects individual files, not the pair as a transaction.
Both payloads are staged before promotion and the JSON is promoted last. A crash
between promotions can leave a summary from another run; JSON is authoritative.

## Verification and honest limits

167 offline tests pass and one is skipped, awaiting a real Ollama recording.
Strict replay from the committed recording and the broken-model CLI example were
both run. The sample headline totals are unchanged by every fix listed in the
README. No live-model performance, category accuracy, production readiness or
compliance assessment is claimed.
