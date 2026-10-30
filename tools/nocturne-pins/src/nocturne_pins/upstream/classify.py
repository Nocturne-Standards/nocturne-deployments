"""Classifier plug-in: what a release changes, from the diff, in three classes.

- ``S`` safety: values that move funds or decide where they go (endpoints, contracts,
  routes, the blocklist, chain IDs). Consumers that move value update within the grace.
- ``D`` data: labels, links, estimates, fallbacks. Next release, within the lag limit.
- ``A`` API or schema: ``schema.json``, package ``exports``, type declarations, any 0.x
  minor bump. Opt in; strict readers check.

The input is two package views (package-relative path -> bytes, read in memory from the
tarballs; nothing is run). The upstream's CHANGELOG is never used to classify. A path the
tables do not name is ``S`` and the change says so: the safe side is to look.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterator

from .model import Change, Classification

S, D, A = "S", "D", "A"
CLASS_ORDER = (S, D, A)
# Worst first when one change spans classes (a whole route added holds both fees and estimates).
_RANK = {S: 0, A: 1, D: 2}
UNKNOWN = "not in the class table, treated as safety"
JS_SUFFIXES = (".js", ".mjs", ".cjs")
JS_NOT_COMPARED = "JavaScript implementation (src/*.js): not compared, only its declared API (.d.ts)"
CHANGELOG_NOT_USED = "CHANGELOG.md: quoted for people, never used to classify"


@dataclass(frozen=True)
class Rule:
    pattern: tuple[str, ...]  # path segments; "*" one segment, "**" any number, "[]" an array item
    cls: str
    reason: str

    @property
    def text(self) -> str:
        return render(self.pattern)


def rule(pattern: str, cls: str, reason: str) -> Rule:
    return Rule(tuple(pattern.replace("[]", ".[]").split(".")), cls, reason)


# First match wins, so the narrow rules come before the broad ones.
NETWORK_RULES: tuple[Rule, ...] = (
    rule("chainId", S, "chain ID"),
    rule("urls.**", S, "node, archive or prover endpoint"),
    rule("contracts.**", S, "core contract"),
    rule("sozu.hub", S, "Sozu hub"),
    rule("sozu.fallbacks.**", D, "Sozu fallback (the hub is authoritative)"),
    rule("display.blocklist.**", S, "recipient blocklist"),
    rule("display.labels.**", D, "display label"),
    rule("label", D, "network label"),
    rule("apps.**", D, "app link"),
    rule("operator.**", D, "operator details"),
    rule("duskDomains.manifest.**", S, "Dusk Domains manifest pin"),
    rule("duskDomains.site", D, "Dusk Domains site link"),
    rule("duskEvm.chainId", S, "DuskEVM chain ID"),
    rule("duskEvm.rpcUrl", S, "DuskEVM endpoint"),
    rule("duskEvm.l1AdapterUrl", S, "DuskEVM endpoint"),
    rule("duskEvm.blockscoutApiUrl", S, "DuskEVM endpoint"),
    rule("duskEvm.currency.**", S, "DuskEVM currency"),
    rule("duskEvm.contracts.**", S, "DuskEVM L1 or bridge contract"),
    rule("duskEvm.predeploys.**", S, "DuskEVM bridge predeploy"),
    rule("duskEvm.drivers.**", S, "bridge contract driver pin"),
    rule("duskEvm.name", D, "DuskEVM name"),
    rule("duskEvm.explorerUrl", D, "DuskEVM explorer link"),
    rule("duskEvm.source.**", D, "DuskEVM genesis source hashes"),
    rule("routes.**.typicalSeconds", D, "route time estimate"),
    rule("routes.chains.*.name", D, "route chain name"),
    rule("routes.chains.*.tokenLabel", D, "route token label"),
    rule("routes.chains.*.explorerUrl", D, "route chain explorer link"),
    rule("routes.chains.**", S, "route chain: ID, RPC, token or currency"),
    rule("routes.migrations.**", S, "migration route: contract, payout account or running state"),
    rule("routes.services.**", S, "bridge route: account, payout address, fee or running state"),
    rule("routes.duskEvm.**", S, "DuskEVM route: direction or running state"),
    rule("routes.retired.**", S, "retired route contracts"),
)

PACKAGE_A = ("exports", "main", "module", "types", "typings", "type", "files", "engines", "bin",
             "dependencies", "peerDependencies", "optionalDependencies", "sideEffects")
# Package metadata is A, not D: nobody reads it as data, and A forces no update.
PACKAGE_METADATA = ("description", "keywords", "homepage", "bugs", "author", "contributors", "funding",
             "scripts", "devDependencies", "publishConfig")

# ---------------------------------------------------------------- paths


Seg = tuple[str, str]  # ("k", key) or ("i", label of an array item)
_IDENT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$-]*$")
_ITEM_LABEL = re.compile(r"^[A-Za-z0-9_.:-]+$")


def render(segments: tuple[str, ...]) -> str:
    """Normalized segments as text: ``routes.services[].payoutAddress``, ``exports["./gas"]``."""
    return _join(tuple(("i", "") if seg == "[]" else ("k", seg) for seg in segments), normalized=True)


def _join(path: tuple[Seg, ...], normalized: bool = False) -> str:
    out = ""
    for kind, value in path:
        if kind == "i":
            out += "[]" if normalized else f"[{value}]"
        elif value in ("*", "**") or _IDENT.match(value):
            out += f".{value}" if out else value
        else:
            out += f"[{json.dumps(value, ensure_ascii=False)}]"
    return out


def _norm(path: tuple[Seg, ...]) -> tuple[str, ...]:
    return tuple("[]" if kind == "i" else value for kind, value in path)


def matches(pattern: tuple[str, ...], segments: tuple[str, ...]) -> bool:
    if not pattern:
        return not segments
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(matches(rest, segments[i:]) for i in range(len(segments) + 1))
    if not segments:
        return False
    if head == "*" or head == segments[0]:
        return matches(rest, segments[1:])
    return False


# ---------------------------------------------------------------- JSON diff


@dataclass(frozen=True)
class _Raw:
    path: tuple[Seg, ...]
    kind: str
    old: Any
    new: Any


def _same(a: Any, b: Any) -> bool:
    # json.dumps keeps true apart from 1, which == does not.
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _identity(old: list[Any], new: list[Any]) -> str | None:
    """A key that names every item on both sides once (``id``, else ``account``, else ``contract``)."""
    for key in ("id", "account", "contract"):
        ok = True
        for items in (old, new):
            values = [item.get(key) if isinstance(item, dict) else None for item in items]
            if not all(isinstance(v, str) for v in values) or len(set(values)) != len(values):
                ok = False
                break
        if ok and (old or new):
            return key
    return None


def _item_label(value: str) -> str:
    return value if _ITEM_LABEL.match(value) else json.dumps(value, ensure_ascii=False)


def diff(old: Any, new: Any, path: tuple[Seg, ...] = ()) -> Iterator[_Raw]:
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            sub = (*path, ("k", key))
            if key not in old:
                yield _Raw(sub, "added", None, new[key])
            elif key not in new:
                yield _Raw(sub, "removed", old[key], None)
            else:
                yield from diff(old[key], new[key], sub)
        return
    if isinstance(old, list) and isinstance(new, list):
        if _same(old, new):
            return
        key = _identity(old, new)
        if key is not None:
            before = {item[key]: item for item in old}
            after = {item[key]: item for item in new}
            for ident in sorted(set(before) | set(after)):
                sub = (*path, ("i", _item_label(ident)))
                if ident not in before:
                    yield _Raw(sub, "added", None, after[ident])
                elif ident not in after:
                    yield _Raw(sub, "removed", before[ident], None)
                else:
                    yield from diff(before[ident], after[ident], sub)
            return
        containers = any(isinstance(item, (dict, list)) for item in [*old, *new])
        if containers and len(old) == len(new):
            for index, (a, b) in enumerate(zip(old, new)):
                yield from diff(a, b, (*path, ("i", str(index))))
            return
        yield _Raw(path, "changed", old, new)
        return
    if not _same(old, new):
        yield _Raw(path, "changed", old, new)


def _leaves(segments: tuple[str, ...], value: Any) -> Iterator[tuple[str, ...]]:
    if isinstance(value, dict) and value:
        for key, item in value.items():
            yield from _leaves((*segments, key), item)
    elif isinstance(value, list) and value:
        for item in value:
            yield from _leaves((*segments, "[]"), item)
    else:
        yield segments


def classify_path(segments: tuple[str, ...], rules: tuple[Rule, ...]) -> tuple[str, str]:
    for item in rules:
        if matches(item.pattern, segments):
            return item.cls, item.reason
    return S, UNKNOWN


def _classify_raw(raw: _Raw, rules: tuple[Rule, ...]) -> tuple[str, str]:
    """A whole object added or removed is as serious as the worst value in it."""
    segments = _norm(raw.path)
    sides = [v for v in (raw.old, raw.new) if isinstance(v, (dict, list)) and v]
    leaves = sorted({leaf for side in sides for leaf in _leaves(segments, side)}) or [segments]
    verdicts = [classify_path(leaf, rules) for leaf in leaves]
    # Worst class first; among equals, a named reason before the unknown fallback.
    return min(verdicts, key=lambda v: (_RANK[v[0]], v[1] == UNKNOWN))


def _set_summary(old: Any, new: Any) -> str:
    if not (isinstance(old, list) and isinstance(new, list)):
        return ""
    before = [json.dumps(v, ensure_ascii=False) for v in old]
    after = [json.dumps(v, ensure_ascii=False) for v in new]
    added = [v for v in after if v not in before]
    removed = [v for v in before if v not in after]
    parts = [f"+{', +'.join(added)}" if added else "", f"-{', -'.join(removed)}" if removed else ""]
    text = " ".join(p for p in parts if p)
    return text or "reordered"


# ---------------------------------------------------------------- classifier


def _load(data: bytes, name: str) -> Any:
    try:
        return json.loads(data)
    except ValueError:
        return _NotJson(name)


@dataclass(frozen=True)
class _NotJson:
    name: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DataDiffClassifier:
    """Diff network files, schema, package manifest and type declarations; classify each change."""

    def __init__(self, network_files: tuple[str, ...], schema_file: str, package_file: str = "package.json",
                 types_suffix: str = ".d.ts", ignored: tuple[str, ...] = ("CHANGELOG.md", "README.md"),
                 rules: tuple[Rule, ...] = NETWORK_RULES) -> None:
        self.network_files = network_files
        self.schema_file = schema_file
        self.package_file = package_file
        self.types_suffix = types_suffix
        self.ignored = ignored
        self.rules = rules

    def classify(self, old: dict[str, bytes] | None, new: dict[str, bytes]) -> Classification:
        if old is None:
            return Classification(classes=(), note="first snapshot: nothing to compare")
        changes: list[Change] = []
        not_compared: list[str] = [JS_NOT_COMPARED, CHANGELOG_NOT_USED]
        handled = {*self.network_files, self.schema_file, self.package_file, *self.ignored}
        for name in self.network_files:
            changes += self._json_file(old, new, name, not_compared, self._network_change)
        changes += self._json_file(old, new, self.schema_file, not_compared, self._schema_change)
        changes += self._package(old, new, not_compared)
        changes += self._types(old, new, not_compared)
        for name in sorted((set(old) | set(new)) - handled):
            if name.endswith(self.types_suffix) or name.endswith(JS_SUFFIXES):
                continue
            changes += self._other(old, new, name, not_compared)
        changes.sort(key=lambda c: (c.file, c.path, c.kind))
        classes = tuple(c for c in CLASS_ORDER if any(ch.cls == c for ch in changes))
        return Classification(
            classes=classes, note=summary(changes), changes=tuple(changes), not_compared=tuple(not_compared),
        )

    # -- per file

    def _json_file(self, old: dict[str, bytes], new: dict[str, bytes], name: str, not_compared: list[str],
                   make: Any) -> list[Change]:
        if name not in old or name not in new:
            not_compared.append(f"{name}: not in both releases' files")
            return []
        if old[name] == new[name]:
            return []
        before, after = _load(old[name], name), _load(new[name], name)
        if isinstance(before, _NotJson) or isinstance(after, _NotJson):
            return [Change(S, name, "", f"file:{name}", "changed", _sha256(old[name]), _sha256(new[name]),
                           f"changed: not JSON on one side ({UNKNOWN})")]
        return [make(name, raw) for raw in diff(before, after)]

    def _network_change(self, name: str, raw: _Raw) -> Change:
        cls, reason = _classify_raw(raw, self.rules)
        return Change(cls, name, _join(raw.path), render(_norm(raw.path)), raw.kind, raw.old, raw.new,
                      f"{raw.kind}: {reason}")

    def _schema_change(self, name: str, raw: _Raw) -> Change:
        norm = _norm(raw.path)
        last = norm[-1] if norm else ""
        parent = norm[-2] if len(norm) > 1 else ""
        if last == "required":
            what = f"required keys {_set_summary(raw.old, raw.new)}".rstrip()
        elif last == "enum":
            what = f"allowed values {_set_summary(raw.old, raw.new)}".rstrip()
        elif last == "additionalProperties":
            what = "additional keys rule"
        elif parent == "properties" and raw.kind == "added":
            what = f"new key {last}"
        elif parent == "properties" and raw.kind == "removed":
            what = f"key {last} removed"
        elif last in ("description", "title", "$comment", "examples"):
            what = "wording"
        else:
            what = "schema rule"
        return Change(A, name, _join(raw.path), f"schema:{render(norm)}", raw.kind, raw.old, raw.new,
                      f"{raw.kind}: {what}")

    def _package(self, old: dict[str, bytes], new: dict[str, bytes], not_compared: list[str]) -> list[Change]:
        name = self.package_file
        if name not in old or name not in new:
            not_compared.append(f"{name} (exports, version): not in both releases' files")
            return []
        before, after = _load(old[name], name), _load(new[name], name)
        if not isinstance(before, dict) or not isinstance(after, dict):
            return [Change(S, name, "", f"package:{name}", "changed", None, None, f"changed: not a JSON object ({UNKNOWN})")]
        out: list[Change] = []
        version = _version_change(before.get("version"), after.get("version"))
        if version is not None:
            out.append(Change(A, name, "version", "package:version", "changed", before.get("version"),
                              after.get("version"), f"changed: {version}"))
        for raw in diff({k: v for k, v in before.items() if k != "version"},
                        {k: v for k, v in after.items() if k != "version"}):
            top = raw.path[0][1]
            if top in PACKAGE_A:
                cls, what = A, "exported entry point" if top == "exports" else f"package {top}"
            elif top in PACKAGE_METADATA:
                cls, what = A, "package metadata"
            else:
                cls, what = S, UNKNOWN
            out.append(Change(cls, name, _join(raw.path), f"package:{render(_norm(raw.path))}", raw.kind,
                              raw.old, raw.new, f"{raw.kind}: {what}"))
        return out

    def _types(self, old: dict[str, bytes], new: dict[str, bytes], not_compared: list[str]) -> list[Change]:
        before = {k for k in old if k.endswith(self.types_suffix)}
        after = {k for k in new if k.endswith(self.types_suffix)}
        if not before or not after:
            not_compared.append(f"type declarations (*{self.types_suffix}): not in both releases' files")
            return []
        out: list[Change] = []
        for name in sorted(before | after):
            a, b = old.get(name), new.get(name)
            if a == b:
                continue
            kind = "added" if a is None else "removed" if b is None else "changed"
            lines_a = a.decode("utf-8", "replace").splitlines() if a is not None else []
            lines_b = b.decode("utf-8", "replace").splitlines() if b is not None else []
            plus = minus = 0
            for line in difflib.unified_diff(lines_a, lines_b, lineterm="", n=0):
                if line.startswith("+") and not line.startswith("+++"):
                    plus += 1
                elif line.startswith("-") and not line.startswith("---"):
                    minus += 1
            out.append(Change(
                A, name, "", f"types:{name}", kind,
                None if a is None else {"sha256": _sha256(a), "lines": len(lines_a)},
                None if b is None else {"sha256": _sha256(b), "lines": len(lines_b)},
                f"{kind}: declared API (+{plus} -{minus} lines)",
            ))
        return out

    def _other(self, old: dict[str, bytes], new: dict[str, bytes], name: str, not_compared: list[str]) -> list[Change]:
        if name not in old or name not in new:
            kind = "added" if name not in old else "removed"
            data = new.get(name, old.get(name, b""))
            return [Change(S, name, "", f"file:{name}", kind, None if kind == "added" else _sha256(data),
                           _sha256(data) if kind == "added" else None, f"{kind}: file ({UNKNOWN})")]
        if old[name] == new[name]:
            return []
        before, after = _load(old[name], name), _load(new[name], name)
        if isinstance(before, _NotJson) or isinstance(after, _NotJson):
            return [Change(S, name, "", f"file:{name}", "changed", _sha256(old[name]), _sha256(new[name]),
                           f"changed: file ({UNKNOWN})")]
        return [Change(S, name, _join(raw.path), f"file:{name}", raw.kind, raw.old, raw.new,
                       f"{raw.kind}: {UNKNOWN}") for raw in diff(before, after)]


def _version_change(old: Any, new: Any) -> str | None:
    """Major bumps, and minor bumps below 1.0, may break the API under semver."""
    parts = []
    for value in (old, new):
        if not isinstance(value, str) or not re.match(r"^\d+\.\d+\.\d+$", value):
            return None if old == new else "version not plain X.Y.Z"
        parts.append(tuple(int(p) for p in value.split(".")))
    (a_major, a_minor, _), (b_major, b_minor, _) = parts
    if a_major != b_major:
        return "major version bump"
    if a_major == 0 and a_minor != b_minor:
        return "0.x minor version bump (may break the API)"
    return None


def summary(changes: list[Change]) -> str:
    if not changes:
        return "no classified change"
    counts = ", ".join(f"{c} {sum(1 for ch in changes if ch.cls == c)}" for c in CLASS_ORDER
                       if any(ch.cls == c for ch in changes))
    return f"{len(changes)} change{'s' if len(changes) != 1 else ''} ({counts})"


def dusk_networks_classifier() -> DataDiffClassifier:
    return DataDiffClassifier(
        network_files=("networks/devnet.json", "networks/mainnet.json", "networks/testnet.json"),
        schema_file="schema.json",
    )
