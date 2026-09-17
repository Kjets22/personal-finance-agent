"""Wiring: deterministic setup, then the agent."""

from __future__ import annotations

from pathlib import Path

from .agent import run_agent
from .ingest import load_all
from .llm import LLMClient
from .paths import validate_paths
from .reconcile import build_ledger
from .state import AgentState


def prepare_state(data_dir: Path, out_path: Path, llm: LLMClient) -> AgentState:
    validate_paths(Path(data_dir), Path(out_path))
    sources, rejected = load_all(Path(data_dir))
    ledger = build_ledger(sources, rejected)
    return AgentState(
        llm=llm,
        out_path=Path(out_path),
        transactions=list(ledger.transactions),
        excluded=ledger.excluded,
        bank_period=ledger.bank_period,
        period=ledger.period,
        warnings=list(ledger.warnings),
    )


def run(data_dir: Path, out_path: Path, llm: LLMClient) -> dict:
    return run_agent(prepare_state(data_dir, out_path, llm))
