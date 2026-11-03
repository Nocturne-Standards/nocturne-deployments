"""Canonical JSON for pin files and history lines."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def dump_file(value: Any) -> str:
    """Current files, index and legacy files: 2-space indent, trailing newline."""
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def dump_line(event: dict[str, Any]) -> str:
    """One history line, compact, no newline."""
    return json.dumps(event, separators=(",", ":"), ensure_ascii=False)


def line_hash(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def read_lines(path: Path) -> list[str]:
    """Non-empty lines of a jsonl file, without newline. Missing file = no lines."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [line for line in text.split("\n") if line.strip() != ""]


def write_atomic(path: Path, text: str) -> None:
    """Write via a temp file in the same directory; 0644 so git and readers see normal files."""
    write_atomic_bytes(path, text.encode("utf-8"))


def append_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    write_atomic(path, existing + "".join(line + "\n" for line in lines))


def write_atomic_bytes(path: Path, data: bytes) -> None:
    """Atomic write of exact bytes (also used for vendored upstream snapshots)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
