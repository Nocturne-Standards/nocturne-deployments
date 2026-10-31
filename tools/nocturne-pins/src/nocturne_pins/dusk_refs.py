"""Refs from wiring values and record fields to Dusk's values (``dusk/<name>/<network>/public``).

Most of what we take from Dusk is not a deploy ref but a value: a messenger id passed to an
``init_*`` call, the L2 messenger predeploy or the chain id in an EVM deploy record. Writers
put ``refs`` and ``resolved`` on the event when its wiring key or record field is in the
tables below and the value is Dusk's current one.

Logs are append-only, so past rows get their refs from one ``note`` event each
(``nocturne-pins annotate-refs``). A note's ``refs`` keys say what they cover, as a path
into the current file: ``state.<wiring key>`` or ``contracts.<name>.record.<field>``. On a
``wired`` event the key is the wiring key; on a ``deployed`` event, the record field.

``check`` (offline) then warns, without failing, when:

- a ``dusk/...`` ref in a log does not resolve, or the id it resolved to is no longer
  Dusk's (Dusk changed it in a later release: re-wire, or note the new id);
- a value in a current file (state or contract record) equals one of Dusk's ids and no
  ref covers it.

Known copies kept on purpose go in ``dusk-refs.allow.json`` with a reason. A ref to a
value Dusk says to read live (``fallback``) fails, as for contract refs.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator

from . import dusk_names
from .base import BaseError, BaseLayer, of_catalog
from .names import EntryKey, NameError_, parse
from .schema import validate

if TYPE_CHECKING:
    from .repo import Catalog, Repo

# Wiring keys whose value is one of Dusk's ids: ``contract.fn`` pattern -> Dusk name.
WIRING_REFS: tuple[tuple[str, str], ...] = (
    ("*.init_l1_messenger_contract", "evm-l1-cross-domain-messenger"),
    ("*.init_messenger", "evm-l1-cross-domain-messenger"),
)


@dataclass(frozen=True)
class RecordRef:
    field: str
    names: tuple[str, ...]  # candidates; the one whose value matches is the ref
    # A value that matches none is worth a warning only where the field can only mean Dusk's
    # value (a chain id). A ``messenger`` may be a messenger of our own.
    warn_other: bool


# Record fields (of ``deployed`` events) whose value is one of Dusk's values.
RECORD_REFS: tuple[RecordRef, ...] = (
    RecordRef("messenger", ("evm-predeploy-l2-cross-domain-messenger", "evm-l1-cross-domain-messenger"), False),
    RecordRef("dusk_chain_id", ("chain-id",), True),
)

ALLOW_FILE = "dusk-refs.allow.json"
ALLOW_SCHEMA = "nocturne.dusk-refs-allow.v1"
CONFIG_KINDS: tuple[str, ...] = ("wired", "admin", "proposal")

_HEX64 = re.compile(r"(0x)?[0-9a-f]{64}")
_ADDR = re.compile(r"0x[0-9a-f]{40}")


def full_name(name: str, network: str) -> str:
    return f"{dusk_names.PRODUCT}/{name}/{network}/{dusk_names.TIER}"


def is_dusk_ref(ref: str) -> bool:
    return ref.startswith(dusk_names.PRODUCT + "/")


def norm(value: Any) -> str | None:
    """Comparable form: hex lower case, a 32-byte id without ``0x``, an integer as text."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if _HEX64.fullmatch(text) and text.startswith("0x"):
        return text[2:]
    return text


def is_id_like(value: Any) -> bool:
    """A 32-byte hex id (``0x`` optional) or a 20-byte EVM address."""
    if not isinstance(value, str):
        return False
    text = value.strip().lower()
    return bool(_HEX64.fullmatch(text) or _ADDR.fullmatch(text))


def matches(entry: dict[str, Any], value: Any) -> bool:
    """``value`` is this Dusk entry's id (or its EVM address)."""
    wanted = norm(value)
    if wanted is None:
        return False
    return wanted in {norm(entry["id"]), norm(entry.get("evm_address"))}


def short(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= 16 else text[:12] + "…"


def wiring_name(wiring_key: str) -> str | None:
    parts = wiring_key.split(".")
    if len(parts) < 2:
        return None
    head = f"{parts[0]}.{parts[1]}"
    for pattern, name in WIRING_REFS:
        if fnmatch.fnmatchcase(head, pattern):
            return name
    return None


class Base:
    """Dusk's entries per network, or nothing when no repo in view declares the base."""

    def __init__(self, catalog: "Catalog", reader: str = "own") -> None:
        self.layer: BaseLayer | None
        try:
            self.layer = of_catalog(catalog, reader)
        except BaseError:
            self.layer = None
        self._entries: dict[str, dict[str, dict[str, Any]]] = {}

    @property
    def version(self) -> str:
        return self.layer.version if self.layer is not None else "?"

    def entries(self, network: str) -> dict[str, dict[str, Any]]:
        if self.layer is None:
            return {}
        if network not in self._entries:
            try:
                self._entries[network] = dusk_names.entries(self.layer.network(network))
            except BaseError:
                self._entries[network] = {}
        return self._entries[network]

    def entry(self, name: str, network: str) -> dict[str, Any] | None:
        return self.entries(network).get(name)

    def ids(self, network: str) -> dict[str, str]:
        """Comparable id or EVM address -> Dusk name. Chain ids are numbers, not ids."""
        out: dict[str, str] = {}
        for name, item in self.entries(network).items():
            if item["kind"] == "chain-id":
                continue
            for value in (item["id"], item.get("evm_address")):
                key = norm(value)
                if key is not None:
                    out.setdefault(key, name)
        return out


# --- writers --------------------------------------------------------------------------

Refs = tuple[dict[str, str], dict[str, str], list[str]]


def for_call(base: Base, network: str, wiring_key: str, value: Any) -> Refs:
    """Refs for a config call whose key is in WIRING_REFS: ``{wiring key: dusk/...}``."""
    name = wiring_name(wiring_key)
    if name is None:
        return {}, {}, []
    ref = full_name(name, network)
    if base.layer is None:
        return {}, {}, [f"{wiring_key}: no Dusk base layer in view; no ref to {ref} written"]
    entry = base.entry(name, network)
    if entry is None:
        return {}, {}, [f"{wiring_key}: Dusk publishes no {name} for {network}; no ref written"]
    if not matches(entry, value):
        return {}, {}, [
            f"{wiring_key}: {short(value)} is not {ref} ({short(entry['id'])} in Dusk {base.version}); "
            "no ref written. If Dusk moved it, sync the base first"
        ]
    return {wiring_key: ref}, {wiring_key: str(value)}, []


def for_record(base: Base, network: str, record: dict[str, Any]) -> Refs:
    """Refs for record fields in RECORD_REFS: ``{field: dusk/...}``."""
    refs: dict[str, str] = {}
    resolved: dict[str, str] = {}
    warnings: list[str] = []
    for row in RECORD_REFS:
        value = record.get(row.field)
        if value is None:
            continue
        found = next(
            (n for n in row.names if (e := base.entry(n, network)) is not None and matches(e, value)), None
        )
        if found is not None:
            refs[row.field] = full_name(found, network)
            resolved[row.field] = str(value)
        elif row.warn_other and base.layer is not None:
            wanted = ", ".join(full_name(n, network) for n in row.names)
            warnings.append(f"record {row.field} = {short(value)} is not {wanted}; no ref written")
    return refs, resolved, warnings


# --- what refs cover ------------------------------------------------------------------


@dataclass(frozen=True)
class Covered:
    ref: str
    resolved: str
    line: int


def _drop_contract(out: dict[str, Covered], name: str) -> None:
    prefix = f"contracts.{name}.record."
    for target in [t for t in out if t.startswith(prefix)]:
        del out[target]


def coverage(events: list[dict[str, Any]]) -> dict[str, Covered]:
    """Live ``dusk/...`` refs after replaying a log: target path in the current file -> ref.

    A new deploy of a contract or a new value of a state key drops what covered the old one.
    """
    out: dict[str, Covered] = {}
    for n, event in enumerate(events, start=1):
        kind = event["event"]
        refs = {k: v for k, v in (event.get("refs") or {}).items() if is_dusk_ref(v)}
        resolved = event.get("resolved") or {}
        if kind in ("deployed", "retired"):
            _drop_contract(out, event["name"])
        for key in event.get("sets") or {}:
            out.pop(f"state.{key}", None)
        for alias, ref in refs.items():
            if kind == "deployed":
                target = f"contracts.{event['name']}.record.{alias}"
            elif kind in CONFIG_KINDS:
                target = f"state.{alias}"
            elif kind == "note" and alias.startswith(("state.", "contracts.")):
                target = alias
            else:
                continue
            out[target] = Covered(ref, str(resolved.get(alias, "")), n)
    return out


def _id_strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _id_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _id_strings(item)
    elif is_id_like(value):
        yield value


def current_values(current: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """``(target, value)`` for every id-like value in a current file's state and records."""
    seen: set[tuple[str, str]] = set()
    for key, item in current["state"].items():
        for value in _id_strings(item["value"]):
            if (f"state.{key}", value) not in seen:
                seen.add((f"state.{key}", value))
                yield f"state.{key}", value
    for name, contract in current["contracts"].items():
        for field_name, field_value in contract["record"].items():
            target = f"contracts.{name}.record.{field_name}"
            for value in _id_strings(field_value):
                if (target, value) not in seen:
                    seen.add((target, value))
                    yield target, value


# --- allowlist --------------------------------------------------------------------------


@dataclass
class Allow:
    items: list[dict[str, Any]]
    used: set[int]

    def permits(self, check: str, key: EntryKey, target: str, value: str) -> bool:
        for i, item in enumerate(self.items):
            if (item["check"], item["entry"], item["where"]) == (check, str(key), target) and norm(item["value"]) == norm(value):
                self.used.add(i)
                return True
        return False

    def unused(self) -> list[str]:
        return [
            f"{ALLOW_FILE}: allow[{i}] ({item['check']} {item['entry']} {item['where']}) matches nothing; remove it"
            for i, item in enumerate(self.items)
            if i not in self.used
        ]


def load_allow(home: "Repo") -> tuple[Allow, list[str]]:
    text = home.read_text(ALLOW_FILE)
    if text is None:
        return Allow([], set()), []
    try:
        doc = json.loads(text)
    except ValueError as exc:
        return Allow([], set()), [f"{ALLOW_FILE}: not JSON: {exc}"]
    errors = validate(doc, ALLOW_SCHEMA, ALLOW_FILE)
    if errors:
        return Allow([], set()), errors
    return Allow(list(doc["allow"]), set()), []


# --- check ------------------------------------------------------------------------------


def _target(catalog: "Catalog", ref: str) -> tuple[dict[str, Any] | None, str | None]:
    from .repo import RepoError

    try:
        name = parse(ref)
        if name.tier is None:
            return None, "a ref must name its tier"
        _, _, found = catalog.find(name, "own")
    except (NameError_, RepoError) as exc:
        return None, str(exc)
    return found, None


def check(catalog: "Catalog", home: "Repo") -> tuple[list[str], list[str]]:
    """Errors (bad allowlist, refs to live-read values) and warnings (D7 checks 3 and 4)."""
    from .repo import entry_key

    allow, errors = load_allow(home)
    warnings: list[str] = []
    base = Base(catalog)
    for item in home.entries():
        key = entry_key(item)
        events = [json.loads(line) for line in home.history_lines(key)]
        current = home.current(key)
        in_current = {
            (alias, ref)
            for contract in (current or {}).get("contracts", {}).values()
            for alias, ref in contract.get("refs", {}).items()
        }
        e, w = _check_log_refs(catalog, item["history"], events, in_current)
        errors += e
        warnings += w
        live = coverage(events)
        warnings += _check_resolved(catalog, base, key, live, allow, item["history"])
        if current is not None:
            warnings += _check_known_ids(base, key, current, live, allow)
    warnings += allow.unused()
    return errors, warnings


def _check_log_refs(
    catalog: "Catalog", where: str, events: list[dict[str, Any]], in_current: set[tuple[str, str]]
) -> tuple[list[str], list[str]]:
    """Check 3 (every ``dusk/...`` ref resolves) and 5 (none names a live-read value).

    Refs a current file carries are checked with the contract refs already.
    """
    errors: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for n, event in enumerate(events, start=1):
        for alias, ref in (event.get("refs") or {}).items():
            if not is_dusk_ref(ref) or (alias, ref) in in_current or ref in seen:
                continue
            seen.add(ref)
            found, problem = _target(catalog, ref)
            if problem is not None:
                warnings.append(f"{where}:{n}: ref {alias} = {ref} does not resolve: {problem}")
                continue
            assert found is not None
            fallback = dusk_names.ref_problem(found, ref)
            if fallback is not None:
                errors.append(f"{where}:{n}: ref {alias}: {fallback}")
    return errors, warnings


def _check_resolved(
    catalog: "Catalog", base: Base, key: EntryKey, live: dict[str, Covered], allow: Allow, where: str
) -> list[str]:
    """Check 3: the id a live ref resolved to is still Dusk's."""
    warnings: list[str] = []
    for target, covered in live.items():
        found, problem = _target(catalog, covered.ref)
        if problem is not None or found is None:
            continue
        if matches(found, covered.resolved):
            continue
        if allow.permits("stale-ref", key, target, covered.resolved):
            continue
        warnings.append(
            f"{key} {target}: Dusk changed {covered.ref} in {base.version}: "
            f"{where}:{covered.line} resolved {short(covered.resolved)}, Dusk now has {short(found['id'])}; "
            "re-wire, or note the new id"
        )
    return warnings


def _check_known_ids(
    base: Base, key: EntryKey, current: dict[str, Any], live: dict[str, Covered], allow: Allow
) -> list[str]:
    """Check 4: a value equal to one of Dusk's ids carries a ref, or is allowlisted."""
    ids = base.ids(key.network)
    if not ids:
        return []
    warnings: list[str] = []
    for target, value in current_values(current):
        name = ids.get(norm(value) or "")
        if name is None:
            continue
        covered = live.get(target)
        if covered is not None and norm(covered.resolved) == norm(value):
            continue
        if allow.permits("known-id", key, target, value):
            continue
        warnings.append(
            f"{key} {target}: {short(value)} is {full_name(name, key.network)} copied without a ref; "
            f"run nocturne-pins annotate-refs, or allowlist it in {ALLOW_FILE} with a reason"
        )
    return warnings


# --- past rows ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Annotation:
    key: EntryKey
    line: int
    event: dict[str, Any]


def _row_refs(base: Base, network: str, event: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    refs: dict[str, str] = {}
    resolved: dict[str, str] = {}
    kind = event["event"]
    if kind in CONFIG_KINDS:
        for wiring_key, value in (event.get("sets") or {}).items():
            r, v, _ = for_call(base, network, wiring_key, value)
            for alias in r:
                refs[f"state.{alias}"] = r[alias]
                resolved[f"state.{alias}"] = v[alias]
    elif kind == "deployed":
        r, v, _ = for_record(base, network, event.get("record") or {})
        for alias in r:
            refs[f"contracts.{event['name']}.record.{alias}"] = r[alias]
            resolved[f"contracts.{event['name']}.record.{alias}"] = v[alias]
    return refs, resolved


def note_text(n: int, event: dict[str, Any]) -> str:
    what = event.get("key") or event.get("name") or "?"
    tx = f", tx {event['tx'][:12]}" if event.get("tx") else ""
    return f"Dusk refs for line {n} ({event['event']} {what}{tx})"


def plan(catalog: "Catalog", home: "Repo", at: str) -> list[Annotation]:
    """One ``note`` per past row whose value is Dusk's and carries no ref. Idempotent."""
    from .repo import entry_key

    base = Base(catalog)
    out: list[Annotation] = []
    for item in home.entries():
        key = entry_key(item)
        events = [json.loads(line) for line in home.history_lines(key)]
        done = {e.get("text") for e in events if e["event"] == "note"}
        for n, event in enumerate(events, start=1):
            if any(is_dusk_ref(r) for r in (event.get("refs") or {}).values()):
                continue
            refs, resolved = _row_refs(base, key.network, event)
            text = note_text(n, event)
            if not refs or text in done:
                continue
            out.append(Annotation(key, n, {"at": at, "event": "note", "text": text, "refs": refs, "resolved": resolved}))
    return out
