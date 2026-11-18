"""Small file helpers shared by the ledger, the backlog and the agent: locks, atomic writes, JSONL.

WHY files: the files a human can read are the state (docs/SPEC.md). Every write holds an fcntl lock; every whole-file
write goes through a temp file and os.replace, so a reader never sees half a document.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterator


class FileLock:
    """An exclusive fcntl lock on one file, re-entrant within one object (nested writes do not deadlock)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._depth = 0
        self._handle = None

    @contextlib.contextmanager
    def held(self) -> Iterator[None]:
        if self._depth == 0:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "a+")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            self._handle = handle
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0 and self._handle is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                self._handle.close()
                self._handle = None


def dumps(obj: Any) -> str:
    """One canonical JSON line: sorted keys, so the same state is the same bytes."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def append_jsonl(path: str | Path, rows) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(dumps(row) + "\n")


def read_jsonl(path: str | Path) -> list[dict]:
    """Every parsable JSON object in the file, oldest first; unparsable lines are skipped; a missing file is []."""
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def write_json_atomic(path: str | Path, obj: Any, *, indent: int | None = 2) -> None:
    """Write JSON through a temp file in the same directory and os.replace it into place."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=indent) + "\n"
    write_text_atomic(path, text)


def write_text_atomic(path: str | Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as tmp:
            tmp.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def read_json(path: str | Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return default
