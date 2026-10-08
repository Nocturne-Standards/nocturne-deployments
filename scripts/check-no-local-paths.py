#!/usr/bin/env python3
"""Fail if a pin file stores a machine path.

Public pins keep contract_id, wasm_sha256, and tx_id. wasm_path and
dd_wasm_path stay null. A home-directory string anywhere in the tree fails too.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT_MARKERS = ("/" + "Users/", "/" + "home/", "/" + "private/var/folders/")
PATH_KEYS = ('"wasm_path": ' + '"/', '"dd_wasm_path": ' + '"/')


def tracked_files(root: Path) -> list[Path]:
    out = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
    return [root / name.decode() for name in out.split(b"\0") if name]


def scan_file(path: Path, root: Path) -> list[str]:
    text = path.read_text(errors="replace")
    errors: list[str] = []
    rel = path.relative_to(root)
    for marker in ROOT_MARKERS:
        if marker in text:
            errors.append(f"{rel}: contains {marker}")
    for key in PATH_KEYS:
        if key in text:
            errors.append(f"{rel}: {key}… is set")
    return errors


def check_repo(root: Path) -> list[str]:
    errors: list[str] = []
    for path in tracked_files(root):
        if path.is_file():
            errors.extend(scan_file(path, root))
    return errors


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    errors = check_repo(root)
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
