"""Prevent output paths from overwriting inputs or each other."""
from pathlib import Path

from .ingest import DataError, SOURCE_FILES


def _same(a: Path, b: Path) -> bool:
    return a.resolve() == b.resolve() or (a.exists() and b.exists() and a.samefile(b))


def validate_paths(data: Path, out: Path, record=None, replay=None) -> None:
    summary = out.with_name(out.stem + "_summary.md")
    inputs = [data / name for name in SOURCE_FILES]
    if replay:
        inputs.append(Path(replay))
    outputs = [out, summary]
    if record:
        outputs.append(Path(record))
    for n, destination in enumerate(outputs):
        if any(_same(destination, other) for other in inputs + outputs[:n]):
            raise DataError(f"output path overlaps an input or another output: {destination}")
