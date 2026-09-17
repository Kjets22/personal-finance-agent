"""Stage UTF-8 text beside its destination, then atomically replace each file.

Multiple replacements are not a filesystem transaction. Callers put the
canonical JSON last so it acts as the final commit point for a normal run.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_texts(items: list[tuple[Path, str]]) -> None:
    staged = []
    try:
        for destination, text in items:
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_dir():
                raise IsADirectoryError(str(destination))
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             dir=destination.parent, delete=False) as handle:
                temporary = Path(handle.name)
                staged.append((temporary, destination))
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        for temporary, destination in staged:
            os.replace(temporary, destination)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
