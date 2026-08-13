#!/usr/bin/env python3
"""Validate alias targets exist in pin file contracts maps."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


def check_pin_file(path: Path) -> list[str]:
    """Return list of dangling alias errors for one pin file."""
    data = json.loads(path.read_text())
    aliases = data.get("aliases") or {}
    contracts = data.get("contracts")
    if contracts is None:
        return []
    if not contracts:
        return []

    errors: list[str] = []
    for alias_name, target in aliases.items():
        if target not in contracts:
            errors.append(
                f"{path}: alias {alias_name!r} -> {target!r} missing from contracts"
            )
    return errors


def check_repo(root: Path) -> list[str]:
    index_path = root / "index.json"
    if not index_path.is_file():
        return [f"missing catalog: {index_path}"]

    catalog = json.loads(index_path.read_text())
    errors: list[str] = []
    for entry in catalog.get("files", []):
        rel = entry.get("path")
        if not rel:
            continue
        pin_path = root / rel
        if not pin_path.is_file():
            errors.append(f"missing pin file: {pin_path}")
            continue
        errors.extend(check_pin_file(pin_path))
    return errors


def _self_test_dangling_alias_fails() -> None:
    """RED/GREEN: non-empty contracts must resolve every alias value."""
    bad_pin = {
        "schema": "nocturne.pins.v1",
        "aliases": {"LegacyName": "missing-contract"},
        "contracts": {"knot-registry": {"current": {}}},
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pin_path = root / "bad.json"
        pin_path.write_text(json.dumps(bad_pin))
        errors = check_pin_file(pin_path)
        assert errors, "expected dangling alias to fail when contracts is non-empty"


def _self_test_empty_contracts_stub_ok() -> None:
    stub = {
        "schema": "nocturne.pins.v1",
        "aliases": {"LoanRegister": "loan-register"},
        "contracts": {},
    }
    with tempfile.TemporaryDirectory() as tmp:
        pin_path = Path(tmp) / "stub.json"
        pin_path.write_text(json.dumps(stub))
        assert check_pin_file(pin_path) == []


def run_self_tests() -> None:
    _self_test_dangling_alias_fails()
    _self_test_empty_contracts_stub_ok()


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        run_self_tests()
        print("self-test: ok")
        return 0

    root = Path(__file__).resolve().parent.parent
    errors = check_repo(root)
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
