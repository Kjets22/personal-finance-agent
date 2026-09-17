"""Pull one JSON object out of untrusted model text."""

from __future__ import annotations

import json
import re

MAX_RESPONSE_CHARS = 20_000
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json_object(text: object) -> dict:
    """Return the first JSON object found, checking fenced blocks first.

    Raises ValueError if there is none. Never evaluates anything.
    """
    if not isinstance(text, str):
        raise ValueError("response is not text")
    if len(text) > MAX_RESPONSE_CHARS:
        raise ValueError("response exceeds maximum length")
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for candidate in candidates:
        obj = _first_object(candidate)
        if obj is not None:
            return obj
    raise ValueError("no JSON object found")


def _first_object(s: str) -> dict | None:
    start = s.find("{")
    while start != -1:
        end = _matching_brace(s, start)
        if end is not None:
            try:
                value = json.loads(s[start:end + 1], object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
            except json.JSONDecodeError:
                value = None
            if isinstance(value, dict):
                return value
        start = s.find("{", start + 1)
    return None


def _matching_brace(s: str, start: int) -> int | None:
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"non-standard JSON constant: {value}")
