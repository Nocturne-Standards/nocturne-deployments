"""The one writer path: append history events, then rewrite the current file from replay.

A write is refused when the committed current file no longer equals the replay of its log
(someone edited it by hand) or when the new events would not replay.
"""

from __future__ import annotations

import fcntl
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import jsonio, legacy
from .names import EntryKey, FullName, reader_tiers
from .replay import ReplayError, replay
from .repo import Catalog, Repo, RepoError
from .schema import validate

# Calls that configure a contract. Everything else (pay, trade, mint, push_*, ...) is demo traffic.
CONFIG_FN = re.compile(
    r"^(init_.*|set_.*|add_.*|grant_.*|register_pair_.*|register_commitment|enable_.*|publish"
    r"|schedule_service|execute_service|schedule_admin|execute_admin|finalize|service)$"
)
# EVM warden rows are keyed "<warden>.service.<name>".
ADMIN_FN = re.compile(
    r"^(schedule_service|execute_service|schedule_admin|execute_admin|set_scheduler|set_guardian|service)$"
)
# Contract ids a record may carry that were baked in at build time: field -> (product, name).
BAKED_REFS: dict[str, tuple[str, str]] = {"atlas_pin": ("atlas", "atlas")}


class WriteError(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify(wiring_key: str) -> str:
    """``demo`` or the history event type for a wiring key ``contract.fn[.label]``."""
    parts = wiring_key.split(".")
    if len(parts) < 2:
        raise WriteError(f"wiring key {wiring_key!r} is not contract.fn")
    fn = parts[1]
    if not CONFIG_FN.match(fn):
        return "demo"
    if parts[0] == "knot-proposals" and fn == "finalize":
        return "proposal"
    if ADMIN_FN.match(fn):
        return "admin"
    return "wired"


def contract_id(layer: str, record: dict[str, Any]) -> str:
    # DuskDS rows carry the deployer in "address"; only EVM rows use it for the contract.
    value = record.get("contract_id") if layer == "duskds" else record.get("address")
    if not value:
        raise WriteError(f"{layer} record has no contract id")
    return value


def wiring_value(layer: str, record: dict[str, Any]) -> Any:
    if layer == "duskevm":
        return {k: v for k, v in record.items() if k not in ("applied_by", "resolved_at_block")}
    args = record.get("args")
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return args
    return args


def record_time(record: dict[str, Any]) -> str | None:
    return record.get("deployed_at") or record.get("called_at")


@contextmanager
def locked(root: Path) -> Iterator[None]:
    with (root / ".nocturne-pins.lock").open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _paths(home: Repo, key: EntryKey) -> tuple[Path, Path]:
    item = home.entry(key)
    if item is None or home.root is None:
        raise WriteError(f"{key}: not an entry of {home.where}")
    return home.root / item["current"], home.root / item["history"]


def verify_entry(home: Repo, key: EntryKey) -> list[str]:
    """Errors if the committed current file differs from the replay of its log."""
    current_path, history_path = _paths(home, key)
    lines = jsonio.read_lines(history_path)
    try:
        expected = replay(key, lines, str(history_path))
    except ReplayError as exc:
        return [str(exc)]
    if not current_path.exists():
        return [f"{current_path}: missing (replay gives {expected['events']} events)"]
    text = current_path.read_text(encoding="utf-8")
    if text != jsonio.dump_file(expected):
        return [f"{current_path}: differs from the replay of {history_path.name}"]
    return []


def append(catalog: Catalog, home: Repo, key: EntryKey, events: list[dict[str, Any]]) -> list[str]:
    """Append events to the history log and rewrite the current file. Returns the new lines."""
    if not events:
        return []
    current_path, history_path = _paths(home, key)
    assert home.root is not None
    with locked(home.root):
        problems = verify_entry(home, key)
        if problems:
            raise WriteError("refusing to write: " + "; ".join(problems))
        old = jsonio.read_lines(history_path)
        last_at = json.loads(old[-1])["at"] if old else ""
        for event in events:
            # Append-only means in time order; a late backfill goes in as a note.
            if event["at"] < last_at:
                raise WriteError(f"refusing to write: event at {event['at']} is older than the log's last event ({last_at})")
            last_at = event["at"]
        new = [jsonio.dump_line(event) for event in events]
        try:
            current = replay(key, old + new, str(history_path))
        except ReplayError as exc:
            raise WriteError(f"refusing to write: {exc}") from exc
        jsonio.append_lines(history_path, new)
        jsonio.write_atomic(current_path, jsonio.dump_file(current))
        home._cache.clear()
    refresh_legacy(catalog)
    return new


def refresh_legacy(catalog: Catalog) -> None:
    """Regenerate legacy files in every checkout. Each one sees itself and the repos after it."""
    for i, repo in enumerate(catalog.repos):
        if repo.root is None or not repo.index.get("legacy"):
            continue
        with locked(repo.root):
            legacy.write(Catalog(catalog.repos[i:]), repo)


def append_calls(catalog: Catalog, key: EntryKey, events: list[dict[str, Any]]) -> list[str]:
    """Demo calls go to the first checkout in the catalog that keeps a calls log for the entry."""
    target = next((r for r in catalog.repos if r.root is not None and r.calls_path(key)), None)
    if target is None:
        return []
    assert target.root is not None
    calls_path = target.root / str(target.calls_path(key))
    lines: list[str] = []
    for event in events:
        errors = validate(event, "nocturne.history.v1")
        if errors or event["event"] != "call":
            raise WriteError("bad call event: " + "; ".join(errors or ["event must be 'call'"]))
        lines.append(jsonio.dump_line(event))
    with locked(target.root):
        jsonio.append_lines(calls_path, lines)
    target._cache.clear()
    refresh_legacy(catalog)
    return lines


def owner_of(home: Repo, name: str, network: str) -> EntryKey | None:
    """Entry in ``home`` that already holds contract ``name`` (live or in history)."""
    for item in home.entries():
        if item["network"] != network:
            continue
        key = EntryKey(item["product"], item["network"], item["tier"])
        current = home.current(key)
        if current is not None and name in current["contracts"]:
            return key
        for line in home.history_lines(key):
            event = json.loads(line)
            if event.get("name") == name:
                return key
    return None


def find_home(
    catalog: Catalog, name: str, network: str, product: str | None, tier: str | None, allow_public_home: bool = False
) -> tuple[Repo, EntryKey]:
    """The writable checkout and entry for ``name``: first checkout (private before public) that holds it.

    Without a private checkout in view, a write could land in the public repo for an entry the
    private repo shadows (an unreleased stack). Such writes need ``allow_public_home``.
    """
    checkouts = [r for r in catalog.repos if r.root is not None]
    if not any(not r.is_public for r in catalog.repos) and not allow_public_home and tier != "public":
        raise WriteError(
            "refusing to write without the private checkout in view (it may shadow this entry); "
            "pass --with <private checkout>, or --allow-public-home if this write is meant for the public repo"
        )
    if product is not None:
        for repo in checkouts:
            for item in repo.entries():
                if item["product"] == product and item["network"] == network and (tier is None or item["tier"] == tier):
                    return repo, pick_entry(repo, name, network, product, tier)
        raise WriteError(f"{product}/{network}/{tier or '*'}: no entry in {', '.join(r.where for r in checkouts)}")
    for repo in checkouts:
        found = owner_of(repo, name, network)
        if found is not None:
            return repo, found
    raise WriteError(f"{name}: new contract, pass --product (and --tier) to say where it belongs")


def pick_entry(home: Repo, name: str, network: str, product: str | None, tier: str | None) -> EntryKey:
    if product is not None:
        candidates = [
            EntryKey(i["product"], i["network"], i["tier"])
            for i in home.entries()
            if i["product"] == product and i["network"] == network and (tier is None or i["tier"] == tier)
        ]
        if len(candidates) != 1:
            raise WriteError(f"{product}/{network}/{tier or '*'}: {len(candidates)} entries in {home.where}")
        return candidates[0]
    found = owner_of(home, name, network)
    if found is None:
        raise WriteError(f"{name}: new contract, pass --product (and --tier) to say where it belongs")
    return found


def visible_to(catalog: Catalog, home: Repo) -> Catalog:
    """A public home may only see public repos (its refs must resolve for public readers)."""
    if not home.is_public:
        return catalog
    return Catalog([r for r in catalog.repos if r.is_public])


def _refs(catalog: Catalog, home: Repo, key: EntryKey, record: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    catalog = visible_to(catalog, home)
    refs: dict[str, str] = {}
    resolved: dict[str, str] = {}
    reader = "public" if key.tier == "public" else "own"
    for field, (product, name) in BAKED_REFS.items():
        baked = record.get(field)
        if not baked:
            continue
        target = None
        for tier in reader_tiers(reader):
            candidate = FullName(product, name, key.network, tier)
            try:
                target_id = catalog.resolve(candidate, reader)
            except RepoError:
                continue
            target = candidate
            break
        if target is None:
            raise WriteError(f"{field}: no {product}/{name} entry a {reader} reader can see")
        if target_id != baked:
            raise WriteError(f"{field} {baked[:12]} does not match {target} = {target_id[:12]}")
        refs[product] = str(target)
        resolved[product] = target_id
    return refs, resolved


def record_contract(
    catalog: Catalog,
    name: str,
    layer: str,
    record: dict[str, Any],
    network: str,
    product: str | None = None,
    tier: str | None = None,
    allow_public_home: bool = False,
) -> list[str]:
    home, key = find_home(catalog, name, network, product, tier, allow_public_home)
    new_id = contract_id(layer, record)
    current = home.current(key)
    live = current["contracts"].get(name) if current else None
    if live is not None and live["id"] == new_id and live["record"] == record:
        return []
    at = record_time(record)
    event: dict[str, Any] = {"at": at or now()}
    if at:
        event["at_source"] = "record"
    event.update({"event": "deployed", "name": name, "layer": layer, "id": new_id, "version": record.get("version")})
    if record.get("tx_id"):
        event["tx"] = record["tx_id"]
    if record.get("deployed_block") is not None:
        event["block"] = record["deployed_block"]
    refs, resolved = _refs(catalog, home, key, record)
    if refs:
        event["refs"] = refs
        event["resolved"] = resolved
    event["record"] = record
    events = [event]
    if live is not None and live["id"] != new_id:
        events.append({"at": event["at"], "event": "superseded", "name": name, "id": live["id"], "by": new_id})
    return append(catalog, home, key, events)


def call_event(wiring_key: str, layer: str, record: dict[str, Any], kind: str) -> dict[str, Any]:
    name, fn = wiring_key.split(".")[0], wiring_key.split(".")[1]
    at = record_time(record)
    event: dict[str, Any] = {"at": at or now()}
    if at:
        event["at_source"] = "record"
    event.update({"event": kind, "name": name, "layer": layer, "key": wiring_key, "fn": fn})
    if record.get("tx_id"):
        event["tx"] = record["tx_id"]
    if record.get("resolved_at_block") is not None:
        event["block"] = record["resolved_at_block"]
    if kind != "call":
        event["sets"] = {wiring_key: wiring_value(layer, record)}
    event["record"] = record
    return event


def record_call(
    catalog: Catalog,
    wiring_key: str,
    layer: str,
    record: dict[str, Any],
    network: str,
    product: str | None = None,
    tier: str | None = None,
    allow_public_home: bool = False,
) -> tuple[str, list[str]]:
    """Record one contract call. Config calls go to history; demo calls to the calls log (or nowhere)."""
    name = wiring_key.split(".")[0]
    home, key = find_home(catalog, name, network, product, tier, allow_public_home)
    kind = classify(wiring_key)
    if kind == "demo":
        return "demo", append_calls(catalog, key, [call_event(wiring_key, layer, record, "call")])
    return kind, append(catalog, home, key, [call_event(wiring_key, layer, record, kind)])


def note(catalog: Catalog, home: Repo, key: EntryKey, text: str, sets: dict[str, Any] | None = None) -> list[str]:
    event: dict[str, Any] = {"at": now(), "event": "note", "text": text}
    if sets:
        event["sets"] = sets
    return append(catalog, home, key, [event])
