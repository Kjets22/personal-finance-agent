"""The agentic loop: pick a tool -> dispatch -> observe -> decide.

Guarantees, enforced by control flow rather than by the model:
* at most MAX_STEPS model turns,
* MAX_CONSECUTIVE_FAILURES bad turns in a row ends the loop,
* the same action STALL_REPEATS times in a row ends the loop,
* every exit path that is not an unrecoverable error writes a report.
"""

from __future__ import annotations

import json

from .jsonutil import extract_json_object
from .llm import LLMError, ReplayStrictError, RecordingError
from .report import OutputError, finalize_fallback, write_outputs
from .state import AgentState
from .tools import TOOL_SPECS, TOOLS, ToolError

MAX_STEPS = 10
MAX_CONSECUTIVE_FAILURES = 3
STALL_REPEATS = 3
HISTORY_WINDOW = 6
TRACE_TEXT_LIMIT = 300

SYSTEM = (
    "[controller] You are a personal-finance agent that works only by calling tools. "
    "Code does all arithmetic; you decide which tool to call next. "
    "Reply with exactly one JSON object and nothing else."
)


def build_prompt(state: AgentState, history: list[dict]) -> str:
    status = {
        "transactions": len(state.transactions),
        "needing_category": len(state.needing()),
        "totals_computed": state.totals is not None,
    }
    lines = [
        "Goal: give every transaction a category, compute totals, then write the report.",
        "Usual order: list_uncategorized -> categorize_batch (repeat until needing_category is 0)"
        " -> compute_totals -> write_report.",
        "",
        "TOOLS:",
        json.dumps(TOOL_SPECS, indent=1),
        "",
        "In write_report's summary never type digits; use the placeholders and code fills them in.",
        "",
        "STATE: " + json.dumps(status),
        "",
        "HISTORY (most recent last):",
    ]
    recent = history[-HISTORY_WINDOW:]
    if not recent:
        lines.append("(none yet)")
    for h in recent:
        lines.append(f"OBSERVATION step {h['step']} {h['tool']}: {json.dumps(h['observation'], ensure_ascii=False)}")
    lines += ["", 'Respond with: {"tool": "<name>", "args": {...}, "reason": "<short>"}']
    return "\n".join(lines)


def parse_action(text: object) -> tuple[str, dict]:
    obj = extract_json_object(text)
    name = obj.get("tool")
    if not isinstance(name, str) or not name:
        raise ValueError('missing "tool" name')
    args = obj.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError('"args" must be a JSON object')
    return name, args


def _clip(value: object) -> object:
    if isinstance(value, str):
        return value if len(value) <= TRACE_TEXT_LIMIT else value[:TRACE_TEXT_LIMIT] + "..."
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


def run_agent(state: AgentState) -> dict:
    history: list[dict] = []
    consecutive_failures = 0
    last_signature: str | None = None
    repeats = 0
    stop_reason = f"step limit of {MAX_STEPS} reached"

    for step in range(1, MAX_STEPS + 1):
        entry: dict = {"step": step, "tool": None}
        observation: dict
        ok = False

        try:
            reply = state.llm.complete(build_prompt(state, history), system=SYSTEM)
        except LLMError as exc:
            reply = None
            observation = {"error": f"model call failed: {exc}"}

        if reply is not None:
            try:
                name, args = parse_action(reply)
            except ValueError as exc:
                name = None
                observation = {"error": f"could not read your reply as a tool call ({exc})"}
                entry["model_reply"] = _clip(reply)
            if name is not None:
                entry.update(tool=name, args=_clip(args))
                signature = json.dumps([name, args], sort_keys=True, default=str)
                repeats = repeats + 1 if signature == last_signature else 1
                last_signature = signature
                tool = TOOLS.get(name)
                if tool is None:
                    observation = {"error": f"unknown tool {name!r}; choose one of {sorted(TOOLS)}"}
                else:
                    try:
                        observation = tool(state, args)
                        ok = True
                    except (OutputError, ReplayStrictError, RecordingError):
                        raise
                    except ToolError as exc:
                        observation = {"error": str(exc)}
                    except Exception as exc:  # a tool bug must not kill the run
                        observation = {"error": f"tool crashed: {type(exc).__name__}: {exc}"}

        entry.update(ok=ok, observation=_clip(observation))
        state.trace.append(entry)
        history.append({"step": step, "tool": entry["tool"] or "invalid", "observation": observation})

        if ok:
            consecutive_failures = 0
            if state.report is not None:
                write_outputs(state)
                return state.report
        else:
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                stop_reason = f"{consecutive_failures} failed steps in a row"
                break
        if repeats >= STALL_REPEATS:
            stop_reason = f"same action repeated {repeats} times"
            break

    return finalize_fallback(state, stop_reason)
