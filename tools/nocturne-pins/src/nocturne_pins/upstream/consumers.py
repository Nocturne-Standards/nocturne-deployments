"""Consumer sweep: read every registered consumer's real pin and measure it against the lock.

Each consumer in ``upstream/<name>.consumers.json`` names a repo, the file holding its pin and
how it pins (``kind``). The sweep reads that file through a ``PinSource`` (the GitHub API in
CI, sibling checkouts offline, a fake in tests), finds the pinned version and gives one status:

- ``ok``: on the locked version.
- ``lag-warn``: behind ``latest`` within ``max_lag``, or below the floor while the grace
  runs, or below it after the grace with ``policy: next-release``.
- ``lag-past-max``: behind ``latest`` by more than ``max_lag`` (30 days after ``latest_at``,
  or more than one minor). Fails the sweep, whatever the policy.
- ``below-floor-past-grace``: ``policy: immediate``, below the floor after ``grace_until``.
  Fails the sweep.
- ``not-exact-pin``: a range (``^``, ``~``), a git ``rev``/``branch`` instead of a release tag.
- ``ahead``: newer than the lock (a version we have not verified).
- ``no-pin``: the file is there but does not pin the upstream, or the path is missing.
- ``unreadable``: the API answered 403/404 for a private repo (the token cannot see it).
- ``planned``: registered with ``status: planned`` and not pinning yet. It is still read: a
  planned consumer found pinning gets the status of its pin.

Pin forms (spec D6): npm exact ``"@dusk-network/networks": "X.Y.Z"``; Deno exact
``jsr:@dusk/networks@X.Y.Z``; Rust a git dependency on ``Nocturne-Standards/nocturne-deployments``
with ``tag = "nocturne-deployments-vX.Y.Z"``, whose Dusk version is the ``version`` of the lock
at that tag; vendored ``<dir>/VERSION``.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .model import UpstreamError
from .notes import Consumer, code
from .npm import VERSION, version_key
from .policy import FAILURES, POLICY, SEVERITY, assess, worst
from .specs import UpstreamSpec

NPM_PACKAGE = "@dusk-network/networks"
JSR_PACKAGE = "@dusk/networks"
CRATE_REPO = "nocturne-standards/nocturne-deployments"
CRATE_NAMES = ("dusk-networks", "nocturne-deployments")
TAG_PREFIX = "nocturne-deployments-v"
NPM_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
SWEEP_START = "<!-- consumers-sweep -->"
SWEEP_END = "<!-- /consumers-sweep -->"


class Unreadable(UpstreamError):
    """The source could not read the file (no access, outage). Not evidence about the pin."""


class PinSource(Protocol):
    label: str

    def read(self, consumer: Consumer, path: str) -> bytes | None:
        """Bytes of ``path`` in the consumer's repo; None when it is not there; ``Unreadable`` otherwise."""
        ...

    def lock_at(self, tag: str) -> dict[str, Any] | None:
        """The upstream lock in our own repo at ``tag``; None when that tag has no lock."""
        ...


# ---------------------------------------------------------------- pin parsing


@dataclass(frozen=True)
class Pin:
    found: bool
    spec: str = ""  # what the file says, for people
    version: str | None = None  # exact X.Y.Z when the form is exact
    tag: str | None = None  # rust-crate: the release tag, mapped to a version later
    problem: str = ""  # why the form is not exact


def npm_pin(text: str) -> Pin:
    try:
        manifest = json.loads(text)
    except ValueError as exc:
        return Pin(False, problem=f"package.json is not JSON: {exc}")
    specs = []
    for section in NPM_SECTIONS:
        deps = manifest.get(section) if isinstance(manifest, dict) else None
        if isinstance(deps, dict) and NPM_PACKAGE in deps:
            specs.append(str(deps[NPM_PACKAGE]))
    return _agree(specs, "npm")


_JSR = re.compile(r"^jsr:/?@dusk/networks@([^/]+)(/.*)?$")


def jsr_pin(text: str) -> Pin:
    try:
        manifest = json.loads(text)
    except ValueError as exc:
        return Pin(False, problem=f"deno.json is not JSON: {exc}")
    imports = manifest.get("imports") if isinstance(manifest, dict) else None
    specs = []
    for value in (imports or {}).values() if isinstance(imports, dict) else ():
        match = _JSR.match(value) if isinstance(value, str) else None
        if match:
            specs.append(match.group(1))
    return _agree(specs, "jsr")


def _agree(specs: list[str], kind: str) -> Pin:
    if not specs:
        return Pin(False)
    shown = ", ".join(sorted(set(specs)))
    if len(set(specs)) > 1:
        return Pin(True, shown, problem=f"{kind} pins disagree: {shown}")
    spec = specs[0]
    if VERSION.match(spec) is None:
        return Pin(True, spec, problem=f"{spec!r} is not an exact X.Y.Z version")
    return Pin(True, spec, version=spec)


_HEADER = re.compile(r"^\s*\[([^\]]+)\]\s*(#.*)?$")
_INLINE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=\s*\{(.*)\}\s*(#.*)?$")
_KV = re.compile(r'([A-Za-z0-9_-]+)\s*=\s*("([^"]*)"|true|false|[0-9]+)')


def cargo_entries(text: str) -> list[dict[str, str]]:
    """Dependency entries of a Cargo.toml as flat ``{key: value}`` maps, with ``_name``.

    Covers ``name = { git = "...", tag = "..." }`` and ``[dependencies.name]`` tables. Not a
    TOML parser: enough for dependency lines, which is all a pin is.
    """
    entries: list[dict[str, str]] = []
    table: dict[str, str] | None = None
    for line in text.splitlines():
        header = _HEADER.match(line)
        if header:
            name = header.group(1).strip()
            parts = name.split(".")
            if len(parts) >= 2 and parts[-2].endswith("dependencies"):
                table = {"_name": parts[-1].strip('"')}
                entries.append(table)
            else:
                table = None
            continue
        inline = _INLINE.match(line)
        if inline:
            entry = {"_name": inline.group(1)}
            entry.update({k: (v[1:-1] if v.startswith('"') else v) for k, v, _ in _KV.findall(inline.group(2))})
            entries.append(entry)
            continue
        if table is not None:
            for key, value, _ in _KV.findall(line.split("#", 1)[0]):
                table[key] = value[1:-1] if value.startswith('"') else value
    return entries


def cargo_pin(text: str) -> Pin:
    hits = []
    for entry in cargo_entries(text):
        git = entry.get("git", "").lower().removesuffix(".git").rstrip("/")
        if not git.endswith(CRATE_REPO):
            continue
        if entry.get("package", entry["_name"]) not in CRATE_NAMES:
            continue
        hits.append(entry)
    if not hits:
        return Pin(False)
    tags = {e.get("tag", "") for e in hits}
    if any("tag" not in e for e in hits):
        other = sorted({f"{k} = {e[k]}" for e in hits for k in ("rev", "branch") if k in e}) or ["no tag"]
        return Pin(True, ", ".join(other), problem=f"git dependency without a release tag ({', '.join(other)})")
    if len(tags) > 1:
        return Pin(True, ", ".join(sorted(tags)), problem=f"tags disagree: {', '.join(sorted(tags))}")
    tag = tags.pop()
    version = tag.removeprefix(TAG_PREFIX)
    if not tag.startswith(TAG_PREFIX) or VERSION.match(version) is None:
        return Pin(True, tag, problem=f"tag {tag!r} is not {TAG_PREFIX}X.Y.Z")
    return Pin(True, tag, tag=tag)


def vendored_pin(text: str) -> Pin:
    spec = text.strip()
    if not spec:
        return Pin(False, problem="VERSION is empty")
    if VERSION.match(spec) is None:
        return Pin(True, spec, problem=f"VERSION {spec!r} is not an exact X.Y.Z version")
    return Pin(True, spec, version=spec)


PARSERS = {"npm": npm_pin, "jsr": jsr_pin, "rust-crate": cargo_pin, "vendored": vendored_pin}


def pin_file(consumer: Consumer) -> str:
    return f"{consumer.path.rstrip('/')}/VERSION" if consumer.kind == "vendored" else consumer.path


# ---------------------------------------------------------------- sweep


@dataclass(frozen=True)
class Row:
    consumer: Consumer
    pinned: str | None
    status: str
    detail: str

    def to_json(self) -> dict[str, Any]:
        c = self.consumer
        return {"repo": c.repo, "path": c.path, "kind": c.kind, "policy": c.policy, "uses": list(c.uses),
                "consumer_status": c.status, "visibility": c.visibility, "pinned": self.pinned,
                "status": self.status, "severity": SEVERITY[self.status], "detail": self.detail}


def check_consumer(consumer: Consumer, lock: dict[str, Any], source: PinSource, now: str) -> Row:
    row = _check(consumer, lock, source, now)
    if consumer.status != "planned":
        return row
    # A planned consumer is read too: once it pins, it is measured like any other, so a pin
    # added before the registry is updated cannot hide below the floor.
    if row.pinned is None:
        state = {"no-pin": "no pin yet", "unreadable": "not read"}.get(row.status, row.status)
        return Row(consumer, None, "planned", f"{state}: {row.detail}")
    return Row(consumer, row.pinned, row.status, f"{row.detail} (registry says planned: set status active)")


def _check(consumer: Consumer, lock: dict[str, Any], source: PinSource, now: str) -> Row:
    path = pin_file(consumer)
    try:
        data = source.read(consumer, path)
    except Unreadable as exc:
        return Row(consumer, None, "unreadable", str(exc))
    if data is None:
        return Row(consumer, None, "no-pin", f"{path} not found")
    pin = PARSERS[consumer.kind](data.decode("utf-8", errors="replace"))
    if not pin.found:
        return Row(consumer, None, "no-pin", pin.problem or f"{path} does not pin the upstream")
    if pin.problem:
        return Row(consumer, pin.spec, "not-exact-pin", pin.problem)
    version = pin.version
    prefix = ""
    if pin.tag is not None:
        try:
            tagged = source.lock_at(pin.tag)
        except Unreadable as exc:
            return Row(consumer, pin.spec, "unreadable", f"lock at {pin.tag}: {exc}")
        if tagged is None or not isinstance(tagged.get("version"), str):
            return Row(consumer, pin.spec, "no-pin", f"{pin.tag} has no Dusk lock (tag predates the base layer)")
        version = tagged["version"]
        prefix = f"{pin.tag} = "
    assert version is not None
    result = assess(version, consumer.policy, lock, now)
    return Row(consumer, f"{prefix}{version}", result.status, result.detail)


def sweep(consumers: list[Consumer], lock: dict[str, Any], source: PinSource, now: str) -> list[Row]:
    return [check_consumer(c, lock, source, now) for c in consumers]


def exit_code(rows: list[Row]) -> int:
    """0 all ok or planned; 1 below the floor past grace (immediate) or past max_lag; 2 warnings only."""
    status = worst([r.status for r in rows])
    if status in FAILURES:
        return 1
    return 2 if SEVERITY[status] == "warn" else 0


# ---------------------------------------------------------------- sources


class GhApiSource:
    """``gh api repos/<repo>/contents/<path>`` (raw). The token decides which private repos it sees."""

    label = "the GitHub API"

    def __init__(self, home_repo: str, lock_path: str) -> None:
        self.home_repo = home_repo
        self.lock_path = lock_path

    def _get(self, repo: str, path: str, ref: str | None = None) -> bytes | None:
        endpoint = f"repos/{repo}/contents/{path}" + (f"?ref={ref}" if ref else "")
        try:
            proc = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.raw", endpoint],
                                  capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Unreadable(f"gh api: {exc}") from exc
        if proc.returncode == 0:
            return proc.stdout
        err = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
        text = err[-1] if err else f"exit {proc.returncode}"
        if "HTTP 404" in text:
            return None
        raise Unreadable(f"gh api {endpoint}: {text}")

    def read(self, consumer: Consumer, path: str) -> bytes | None:
        data = self._get(consumer.repo, path)
        if data is None and consumer.visibility == "private":
            # GitHub answers 404 for a private repo the token cannot see.
            raise Unreadable(f"404 for {consumer.repo}:{path} (missing, or the token cannot read this private repo)")
        return data

    def lock_at(self, tag: str) -> dict[str, Any] | None:
        data = self._get(self.home_repo, self.lock_path, tag)
        return None if data is None else json.loads(data)


class LocalSource:
    """Sibling checkouts under ``root`` (``<root>/<repo name>``), working tree as it is."""

    label = "local checkouts"

    def __init__(self, root: Path, home: Path, lock_path: str) -> None:
        self.root = root
        self.home = home
        self.lock_path = lock_path

    def checkout(self, consumer: Consumer) -> Path:
        return self.root / consumer.repo.split("/", 1)[1]

    def read(self, consumer: Consumer, path: str) -> bytes | None:
        base = self.checkout(consumer)
        if not base.is_dir():
            raise Unreadable(f"no checkout at {base.name}/ under the source root")
        file = (base / path).resolve()
        if not file.is_relative_to(base.resolve()):
            raise Unreadable(f"{path} leaves the checkout")
        return file.read_bytes() if file.is_file() else None

    def lock_at(self, tag: str) -> dict[str, Any] | None:
        proc = subprocess.run(["git", "-C", str(self.home), "show", f"refs/tags/{tag}:{self.lock_path}"],
                              capture_output=True)
        if proc.returncode != 0:
            known = subprocess.run(["git", "-C", str(self.home), "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
                                   capture_output=True)
            if known.returncode != 0:
                raise Unreadable(f"tag {tag} not in the local clone (git fetch --tags)")
            return None
        return json.loads(proc.stdout)


# ---------------------------------------------------------------- output


def policy_line(lock: dict[str, Any]) -> str:
    policy = lock.get("policy", POLICY)
    floor = (f"floor {code(lock['floor'])}, grace until {code(lock['grace_until'])}"
             if "floor" in lock else "no floor published")
    return (f"Lock {code(lock['version'])} (latest since {code(lock.get('latest_at', lock['verified_at']))}), {floor}. "
            f"Policy: S grace {policy['s_grace_hours']} h for `immediate` consumers; "
            f"lag at most {policy['max_lag_days']} days or {policy['max_lag_minors']} minor version.")


def table(rows: list[Row]) -> list[str]:
    lines = ["| Repo | Pin | Kind | Policy | Uses | Pinned | Status | Detail |", "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        c = row.consumer
        uses = ", ".join(code(u, 40) for u in c.uses)
        lines.append(f"| {code(c.repo)} | {code(c.path)} | {c.kind} | {c.policy} | {uses} | "
                     f"{code(row.pinned) if row.pinned else '-'} | {row.status} | {code(row.detail, 140)} |")
    return lines


def markdown(spec: UpstreamSpec, lock: dict[str, Any], rows: list[Row], source_label: str) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    summary = ", ".join(f"{n} {status}" for status, n in sorted(counts.items(), key=lambda kv: kv[0]))
    lines = [
        f"# Consumers of {spec.package}",
        "",
        f"Generated by `nocturne-pins upstream consumers --write` from `{spec.consumers_path}` and each",
        f"consumer's pin, read through {source_label}. Do not edit by hand; the weekly consumer",
        "sweep regenerates it.",
        "",
        policy_line(lock),
        "",
        f"{len(rows)} consumers: {summary}.",
        "",
    ]
    lines += table(rows)
    return "\n".join(lines) + "\n"


def text_report(rows: list[Row]) -> str:
    out = []
    for row in rows:
        c = row.consumer
        out.append(f"{row.status:<24} {c.repo} {c.path} ({c.kind}, {c.policy}) "
                   f"pinned {row.pinned or '-'}: {row.detail}")
    return "\n".join(out) + "\n"


_CHECKLIST = re.compile(r"^- \[( |x)\] (`+) ?([^`]+?) ?\2 (`+) ?([^`]+?) ?\4 ")
_ISSUE_VERSION = re.compile(r"\bv(\d+\.\d+\.\d+)\b")


def issue_version(title: str) -> str | None:
    match = _ISSUE_VERSION.search(title)
    return match.group(1) if match else None


def _done(row: Row, version: str) -> bool:
    if row.status in ("not-exact-pin", "unreadable", "no-pin", "planned") or not row.pinned:
        return False
    pinned = row.pinned.rsplit(" = ", 1)[-1]
    return VERSION.match(pinned) is not None and version_key(pinned) >= version_key(version)


def update_issue(body: str, rows: list[Row], version: str, lock: dict[str, Any]) -> str:
    """Tick checklist items whose consumer pins ``version`` or newer; refresh the sweep block.

    Only the ``## Consumers`` section is touched, and only ``- [ ]``/``- [x]`` lines naming a
    registered repo and path. The sweep table goes between markers, replaced on each run.
    """
    by_key = {(r.consumer.repo, r.consumer.path): r for r in rows}
    out: list[str] = []
    section = ""
    for line in body.split("\n"):
        if line.startswith("## "):
            section = line[3:].strip()
        match = _CHECKLIST.match(line) if section == "Consumers" else None
        if match:
            row = by_key.get((match.group(3), match.group(5)))
            if row is not None:
                mark = "x" if _done(row, version) else " "
                line = f"- [{mark}]{line[5:]}"
        out.append(line)
    text = "\n".join(out)
    if SWEEP_START in text and SWEEP_END in text and text.index(SWEEP_START) < text.index(SWEEP_END):
        head, rest = text.split(SWEEP_START, 1)
        tail = rest.split(SWEEP_END, 1)[1]
    else:
        head, tail = text.rstrip("\n") + "\n\n", "\n"
    block = [SWEEP_START, "## Consumer sweep", "", f"Pins against v{version}: a box is ticked when the pin is "
             f"v{version} or newer. {policy_line(lock)}", ""] + table(rows) + [SWEEP_END]
    return head + "\n".join(block) + tail


def new_issue(title: str, version: str, rows: list[Row], lock: dict[str, Any], event: dict[str, Any] | None) -> str:
    """Body of a fresh consumer tracking issue: every consumer as a checklist item, then the sweep."""
    package = code(lock.get("package", "the upstream"))
    lines = [f"Consumers of {package} {code('v' + version)}: a box is ticked once that repo pins this "
             f"version or newer.", ""]
    if event is not None and event.get("version") == version:
        classes = ", ".join(event.get("classes") or []) or "none"
        lines += [f"Locked {code(event['at'])} from {code(event.get('from') or '-')}; diff classes: {classes}; "
                  f"floor {code(event['floor'])}, grace until {code(event['grace_until'])}.", ""]
    lines += ["## Consumers", ""]
    for row in rows:
        c = row.consumer
        planned = ", planned: no pin yet" if c.status == "planned" else ""
        lines.append(f"- [ ] {code(c.repo)} {code(c.path)} ({c.kind}, {c.policy}{planned}): "
                     f"uses {', '.join(code(u, 40) for u in c.uses)}")
    return update_issue("\n".join(lines) + "\n", rows, version, lock)
