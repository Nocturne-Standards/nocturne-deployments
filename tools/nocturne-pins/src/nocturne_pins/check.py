"""Repo checks run by CI in both pin repos."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from . import base_check, jsonio, legacy
from .names import NameError_, parse
from .repo import Catalog, Repo, RepoError, entry_key
from .schema import SCHEMA_FILES, schema_text, validate
from .write import BAKED_REFS, verify_entry

REPO_SCHEMA_DIR = "schema"


def check_repo(catalog: Catalog, home: Repo) -> list[str]:
    errors: list[str] = []
    errors += home.index_errors()
    errors += _check_schema_copies(home)
    errors += _check_entries(home)
    errors += _check_shadows(catalog, home)
    errors += _check_unique_names(catalog)
    errors += _check_refs(catalog, home)
    errors += _check_aliases(catalog, home)
    errors += base_check.check_base(catalog, home)
    if home.index.get("legacy"):
        errors += [f"{path}: out of date (run nocturne-pins legacy --write)" for path in legacy.stale(catalog, home)]
    return errors


def _check_schema_copies(home: Repo) -> list[str]:
    assert home.root is not None
    errors: list[str] = []
    for schema_id, file_name in SCHEMA_FILES.items():
        path = home.root / REPO_SCHEMA_DIR / file_name
        if not path.exists() or path.read_text(encoding="utf-8") != schema_text(schema_id):
            errors.append(f"{path}: missing or differs from the schema shipped with nocturne-pins")
    return errors


def _check_entries(home: Repo) -> list[str]:
    assert home.root is not None
    errors: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for item in home.entries():
        key = entry_key(item)
        ident = (key.product, key.network, key.tier)
        if ident in seen:
            errors.append(f"index: {key} listed twice")
        seen.add(ident)
        want_current = f"products/{key.product}/{key.network}.{key.tier}.json"
        want_history = f"history/{key.product}/{key.network}.{key.tier}.jsonl"
        if item["current"] != want_current:
            errors.append(f"index: {key} current should be {want_current}")
        if item["history"] != want_history:
            errors.append(f"index: {key} history should be {want_history}")
        current_path = home.root / item["current"]
        if current_path.exists():
            current = jsonio.load(current_path)
            errors += validate(current, "nocturne.pins.v2", item["current"])
            if (current.get("product"), current.get("network"), current.get("tier")) != ident:
                errors.append(f"{item['current']}: product/network/tier do not match the index")
        errors += verify_entry(home, key)
    errors += _check_strays(home, seen)
    errors += _check_calls(home)
    return errors


def _check_calls(home: Repo) -> list[str]:
    assert home.root is not None
    if home.is_public and (home.index.get("calls") or (home.root / "calls").exists()):
        return ["the public repo keeps no calls logs (demo traffic stays private)"]
    errors: list[str] = []
    listed = {item["path"] for item in home.index.get("calls", [])}
    if (home.root / "calls").exists():
        for path in sorted((home.root / "calls").rglob("*.jsonl")):
            rel = str(path.relative_to(home.root))
            if rel not in listed:
                errors.append(f"{rel}: not listed in index.json calls")
    for item in home.index.get("calls", []):
        key = entry_key(item)
        want = f"calls/{key.product}/{key.network}.{key.tier}.jsonl"
        if item["path"] != want:
            errors.append(f"index: calls for {key} should be {want}")
        for n, line in enumerate(home.calls_lines(key), start=1):
            event = json.loads(line)
            if jsonio.dump_line(event) != line:
                errors.append(f"{item['path']}:{n}: line is not in canonical form")
            errors += validate(event, "nocturne.history.v1", f"{item['path']}:{n}")
            if event.get("event") != "call":
                errors.append(f"{item['path']}:{n}: only 'call' events belong in a calls log")
    return errors


def _check_strays(home: Repo, listed: set[tuple[str, str, str]]) -> list[str]:
    assert home.root is not None
    errors: list[str] = []
    for folder, suffix in (("products", ".json"), ("history", ".jsonl")):
        base = home.root / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob(f"*{suffix}")):
            rel = path.relative_to(home.root)
            parts = rel.parts
            if len(parts) != 3:
                errors.append(f"{rel}: not in {folder}/<product>/<network>.<tier>{suffix}")
                continue
            stem = parts[2][: -len(suffix)]
            if "." not in stem:
                errors.append(f"{rel}: file name should be <network>.<tier>{suffix}")
                continue
            network, tier = stem.rsplit(".", 1)
            if (parts[1], network, tier) not in listed:
                errors.append(f"{rel}: not listed in index.json")
    return errors


def _check_shadows(catalog: Catalog, home: Repo) -> list[str]:
    errors: list[str] = []
    for item in home.entries():
        key = entry_key(item)
        others = [r for r in catalog.repos if r is not home and r.entry(key) is not None]
        if others and item.get("shadows") not in [r.name for r in others]:
            errors.append(f"index: {key} also exists in {others[0].name}; mark it \"shadows\": \"{others[0].name}\"")
        if "shadows" in item and not others and len(catalog.repos) > 1:
            errors.append(f"index: {key} shadows {item['shadows']} but that repo has no such entry")
    return errors


def _check_unique_names(catalog: Catalog) -> list[str]:
    # The legacy flat files key contracts globally, so names must stay unique per network.
    errors: list[str] = []
    owners: dict[tuple[str, str], str] = {}
    for repo, key in catalog.entries():
        current = repo.current(key)
        if current is None:
            continue
        for name in current["contracts"]:
            ident = (key.network, name)
            if ident in owners and owners[ident] != str(key):
                errors.append(f"{name} on {key.network} is in both {owners[ident]} and {key}")
            owners.setdefault(ident, str(key))
    return errors


def _check_aliases(catalog: Catalog, home: Repo) -> list[str]:
    """``alias.<layer>.<old name>`` state keys must point at a live contract of the same entry."""
    errors: list[str] = []
    for item in home.entries():
        key = entry_key(item)
        current = home.current(key)
        if current is None:
            continue
        for state_key, value in current["state"].items():
            if state_key.startswith("alias.") and value["value"] not in current["contracts"]:
                errors.append(f"{key}: {state_key} -> {value['value']!r} is not a live contract")
    return errors


def _check_refs(catalog: Catalog, home: Repo) -> list[str]:
    errors: list[str] = []
    for item in home.entries():
        key = entry_key(item)
        current = home.current(key)
        if current is None:
            continue
        reader = "public" if key.tier == "public" else "own"
        for name, contract in current["contracts"].items():
            where = f"{key.product}/{name}/{key.network}/{key.tier}"
            for alias, ref in contract.get("refs", {}).items():
                errors += _check_ref(catalog, home, key.tier, reader, where, alias, ref, contract)
    return errors


def _check_ref(
    catalog: Catalog,
    home: Repo,
    tier: str,
    reader: str,
    where: str,
    alias: str,
    ref: str,
    contract: dict[str, Any],
) -> list[str]:
    try:
        target = parse(ref)
    except NameError_ as exc:
        return [f"{where}: ref {alias}: {exc}"]
    if target.tier is None:
        return [f"{where}: ref {alias} must name a tier"]
    if tier == "public" and target.tier != "public":
        return [f"{where}: public entries may only reference public entries (ref {alias} = {ref})"]
    try:
        target_repo, _, target_contract = catalog.find(target, reader)
    except RepoError as exc:
        return [f"{where}: ref {alias} does not resolve: {exc}"]
    if home.is_public and not target_repo.is_public:
        return [f"{where}: ref {alias} points into private repo {target_repo.name}"]
    errors: list[str] = []
    for field, (product, name) in BAKED_REFS.items():
        if product == target.product and name == target.name and field in contract["record"]:
            if contract["record"][field] != target_contract["id"]:
                errors.append(
                    f"{where}: baked {field} {contract['record'][field][:12]} != {ref} {target_contract['id'][:12]}"
                )
    return errors


def check_append_only(home: Repo, base: str) -> list[str]:
    """Every log that existed at ``base`` must still start with its old bytes."""
    assert home.root is not None
    root = str(home.root)
    listing = subprocess.run(
        ["git", "-C", root, "ls-tree", "-r", "--name-only", base, "--", "history", "calls"],
        capture_output=True, text=True,
    )
    if listing.returncode != 0:
        return [f"git ls-tree {base}: {listing.stderr.strip()}"]
    errors: list[str] = []
    for rel in listing.stdout.split():
        if not rel.endswith(".jsonl"):
            continue
        old = subprocess.run(["git", "-C", root, "show", f"{base}:{rel}"], capture_output=True).stdout
        path = home.root / rel
        if not path.exists():
            errors.append(f"{rel}: removed since {base} (history is append-only)")
            continue
        if not path.read_bytes().startswith(old):
            errors.append(f"{rel}: changed since {base} other than by appending")
    return errors


def describe(errors: list[str]) -> str:
    return "\n".join(f"error: {e}" for e in errors)

