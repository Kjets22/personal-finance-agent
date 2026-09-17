"""CLI.

    python -m finance_agent --data sample_data/ --out report.json [--replay FILE | --record FILE]

Exit codes: 0 report written (status ok or degraded), 2 bad input or strict
replay miss, 3 report could not be written.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .ingest import DataError
from .llm import GarbageClient, OllamaClient, RecordingClient, ReplayClient, ReplayStrictError, RecordingError
from .pipeline import run
from .paths import validate_paths
from .report import OutputError, summary_path_for

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_OUTPUT = 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="finance_agent", description="Personal finance agent")
    p.add_argument("--data", required=True, help="directory with the four CSV files")
    p.add_argument("--out", required=True, help="path for report.json")
    p.add_argument("--replay", default=os.environ.get("FINANCE_AGENT_REPLAY"),
                   help="replay model responses from this recording (env: FINANCE_AGENT_REPLAY)")
    p.add_argument("--replay-strict", action="store_true",
                   help="exit 2 instead of degrading when a prompt is not in the recording")
    p.add_argument("--record", help="save every model response to this file")
    p.add_argument("--llm", choices=("ollama", "garbage"), default="ollama",
                   help="'garbage' simulates a broken model (demo)")
    p.add_argument("--model", default=os.environ.get("FINANCE_AGENT_MODEL", "qwen2.5:7b"))
    p.add_argument("--ollama-host", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    return p


def build_llm(args: argparse.Namespace):
    if args.replay_strict and not args.replay:
        raise ValueError("--replay-strict requires --replay")
    if args.replay and args.record:
        raise ValueError("--record and --replay cannot be combined")
    if args.replay:
        client = ReplayClient(args.replay, strict=args.replay_strict)
    elif args.llm == "garbage":
        client = GarbageClient()
    else:
        client = OllamaClient(model=args.model, host=args.ollama_host)
    if args.record:
        client = RecordingClient(client, args.record)
    return client


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validate_paths(Path(args.data), Path(args.out), args.record, args.replay)
        llm = build_llm(args)
    except (DataError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    out = Path(args.out)
    try:
        report = run(Path(args.data), out, llm)
    except (DataError, ReplayStrictError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    except (OutputError, RecordingError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_OUTPUT

    t = report["totals"]
    print(report["summary"])
    print()
    print(f"status={report['status']} income={t['income']:.2f} expenses={t['expenses']:.2f} "
          f"net={t['net']:.2f} savings_rate={t['savings_rate']:.4f} "
          f"flagged={len(report['flagged'])} warnings={len(report['warnings'])}")
    print(f"wrote {out} and {summary_path_for(out)}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
