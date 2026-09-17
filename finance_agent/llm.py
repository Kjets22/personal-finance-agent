"""LLM clients behind a one-method interface.

* OllamaClient     -- real calls (temperature 0, fixed seed, JSON mode)
* RecordingClient  -- wraps any client, saves every call to a JSON file
* ReplayClient     -- answers from that file; no network
* ScriptedClient   -- tests: a handler function or a queue of strings
* GarbageClient    -- demo: always returns unusable output
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Callable, Protocol, Sequence, Union

from .fileio import atomic_write_texts

RECORDING_FORMAT = 1


class LLMError(Exception):
    """A model call failed. Recoverable: the agent treats it as a bad step."""


class RecordingError(Exception):
    """A requested recording could not be persisted."""


class ReplayMissError(LLMError):
    """No recorded response for this prompt (non-strict replay)."""


class ReplayStrictError(Exception):
    """No recorded response in --replay-strict mode. Deliberately NOT an LLMError."""


class LLMClient(Protocol):
    model: str

    def complete(self, prompt: str, *, system: str = "") -> str: ...


def cache_key(model: str, system: str, prompt: str) -> str:
    return hashlib.sha256(f"{model}\n{system}\n{prompt}".encode("utf-8")).hexdigest()


class OllamaClient:
    def __init__(self, model: str = "qwen2.5:7b", host: str = "http://localhost:11434",
                 timeout: float = 180.0, seed: int = 42) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.seed = seed

    def complete(self, prompt: str, *, system: str = "") -> str:
        payload = json.dumps({
            "model": self.model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "seed": self.seed},
        }).encode("utf-8")
        last_error: Exception | None = None
        for _ in range(2):  # one retry for transient network errors
            try:
                request = urllib.request.Request(
                    f"{self.host}/api/generate", data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
            except (OSError, ValueError) as exc:  # URLError/timeouts are OSError
                last_error = exc
                continue
            text = body.get("response") if isinstance(body, dict) else None
            if not isinstance(text, str):
                raise LLMError("Ollama reply had no 'response' text")
            return text
        raise LLMError(f"Ollama request failed: {last_error}")


class RecordingClient:
    def __init__(self, inner: LLMClient, path: Union[str, Path]) -> None:
        self.inner = inner
        self.model = inner.model
        self.path = Path(path)
        self.entries: list[dict] = []

    def complete(self, prompt: str, *, system: str = "") -> str:
        entry = {"key": cache_key(self.model, system, prompt), "system": system, "prompt": prompt}
        try:
            text = self.inner.complete(prompt, system=system)
        except LLMError as exc:
            entry["error"] = str(exc)  # failures are replayed too
            self._append(entry)
            raise
        entry["response"] = text
        self._append(entry)
        return text

    def _append(self, entry: dict) -> None:
        self.entries.append(entry)
        try:
            atomic_write_texts([(self.path, json.dumps(
                {"format": RECORDING_FORMAT, "model": self.model, "entries": self.entries},
                indent=2, ensure_ascii=False, allow_nan=False) + "\n")])
        except OSError as exc:
            raise RecordingError(f"cannot save recording to {self.path}: {exc}") from exc



class ReplayClient:
    def __init__(self, path: Union[str, Path], strict: bool = False) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("model"), str):
            raise ValueError(f"{path}: not a recording file")
        if type(data.get("format")) is not int or data["format"] != RECORDING_FORMAT:
            raise ValueError(f"{path}: unsupported recording format")
        entries = data.get("entries")
        if not isinstance(entries, list):
            raise ValueError(f"{path}: entries must be a list")
        self.model = data["model"]
        self.strict = strict
        self._entries: dict[str, list[dict]] = defaultdict(list)
        for n, entry in enumerate(entries):
            if (not isinstance(entry, dict)
                    or not all(isinstance(entry.get(k), str) for k in ("key", "system", "prompt"))
                    or ("response" in entry) == ("error" in entry)
                    or not isinstance(entry.get("response", entry.get("error")), str)):
                raise ValueError(f"{path}: malformed recording entry {n}")
            if entry["key"] != cache_key(self.model, entry["system"], entry["prompt"]):
                raise ValueError(f"{path}: fingerprint mismatch at entry {n}")
            self._entries[entry["key"]].append(entry)
        self._position: dict[str, int] = defaultdict(int)

    def complete(self, prompt: str, *, system: str = "") -> str:
        key = cache_key(self.model, system, prompt)
        entries = self._entries.get(key)
        if not entries or self._position[key] >= len(entries):
            message = f"no unconsumed recorded response for prompt {key[:12]} (prompt changed or recording exhausted)"
            if self.strict:
                raise ReplayStrictError(message)
            raise ReplayMissError(message)
        index = self._position[key]
        self._position[key] += 1
        entry = entries[index]
        if "error" in entry:
            raise LLMError(entry["error"])  # same text as live, so later prompts match
        return entry["response"]


Handler = Callable[[str, str], str]


class ScriptedClient:
    def __init__(self, script: Union[Handler, Sequence[str]], model: str = "scripted") -> None:
        self.model = model
        self._handler = script if callable(script) else None
        self._queue = list(script) if not callable(script) else []
        self.calls: list[tuple[str, str]] = []

    def complete(self, prompt: str, *, system: str = "") -> str:
        self.calls.append((system, prompt))
        if self._handler is not None:
            return self._handler(prompt, system)
        if not self._queue:
            raise LLMError("scripted client has no responses left")
        return self._queue.pop(0)


class GarbageClient:
    """Deterministic nonsense, for the live 'make the model return garbage' demo."""

    RESPONSES = (
        "lol no",
        '{"tool": ',
        "```json\n{not: valid json}\n```",
        '{"tool": "launch_rockets", "args": {}}',
        "",
        '["an", "array", "not", "an", "object"]',
    )

    def __init__(self) -> None:
        self.model = "garbage"
        self._n = 0

    def complete(self, prompt: str, *, system: str = "") -> str:
        text = self.RESPONSES[self._n % len(self.RESPONSES)]
        self._n += 1
        return text
