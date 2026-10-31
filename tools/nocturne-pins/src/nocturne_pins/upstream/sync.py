"""``upstream sync`` and ``upstream status``: fetch, verify, write the snapshot and its lock.

The tool is the only writer of ``upstream/``. Nothing is written unless every
verifier passed, and a re-sync of the locked version with identical bytes writes nothing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .. import jsonio
from ..schema import validate
from ..write import locked, now as utc_now
from .model import Classification, Evidence, Release, UpstreamError
from .net import Git, Http
from .npm import VERSION, check_version, version_key
from .policy import floor_fields
from .specs import UpstreamSpec

LOCK_SCHEMA = "nocturne.upstream-lock.v1"
HISTORY_SCHEMA = "nocturne.upstream-history.v1"


@dataclass(frozen=True)
class SyncResult:
    version: str
    commit: str
    changed: bool
    written: tuple[str, ...]
    lock: dict[str, Any]
    classification: Classification


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_lock(root: Path, spec: UpstreamSpec) -> dict[str, Any] | None:
    path = root / spec.lock_path
    return jsonio.load(path) if path.exists() else None


def _read_snapshot(root: Path, spec: UpstreamSpec) -> dict[str, bytes] | None:
    base = root / spec.snapshot_dir
    if not base.exists():
        return None
    return {rel: (base / rel).read_bytes() for rel in spec.files if (base / rel).exists()}


def build_lock(spec: UpstreamSpec, evidence: Evidence, release: Release, tarball: bytes,
               snapshot: dict[str, bytes], verified_at: str,
               floor: dict[str, Any] | None = None) -> dict[str, Any]:
    files = {
        f"{spec.snapshot_dir}/{rel}": {"source": spec.files[rel], "sha256": sha256(snapshot[rel])}
        for rel in spec.files
    }
    return {
        "schema": LOCK_SCHEMA,
        "upstream": spec.name,
        "package": release.package,
        "registry": release.registry,
        "version": release.version,
        "tag": release.tag,
        "commit": evidence.commit,
        "tarball": {
            "url": release.tarball_url,
            "integrity": release.integrity,
            "sha256": sha256(tarball),
        },
        "files": files,
        "provenance": evidence.provenance,
        "verified_at": verified_at,
        "verified": evidence.verified,
        "not_verified": evidence.not_verified,
        **(floor or {}),
    }


def history_event(spec: UpstreamSpec, old_lock: dict[str, Any] | None, lock: dict[str, Any],
                  classes: tuple[str, ...], at: str) -> dict[str, Any]:
    """The ``synced`` event for a release that entered the lock."""
    return {
        "at": at,
        "event": "synced",
        "upstream": spec.name,
        "package": lock["package"],
        "version": lock["version"],
        "from": None if old_lock is None else old_lock["version"],
        "tag": lock["tag"],
        "commit": lock["commit"],
        "integrity": lock["tarball"]["integrity"],
        "classes": list(classes),
        "latest": lock["latest"],
        "floor": lock["floor"],
        "grace_until": lock["grace_until"],
        **({"note": "same release as before; the log starts here or the floor changed"}
           if old_lock is not None and old_lock["version"] == lock["version"] else {}),
    }


def last_synced(root: Path, spec: UpstreamSpec) -> dict[str, Any] | None:
    lines = jsonio.read_lines(root / spec.history_path)
    return json.loads(lines[-1]) if lines else None


def _needs_event(last: dict[str, Any] | None, lock: dict[str, Any]) -> bool:
    """A new event when the locked release or its floor differs from the last one logged."""
    if last is None:
        return True
    return any(last.get(key) != lock[key] for key in ("version", "commit", "floor", "grace_until"))


def _same_content(old: dict[str, Any] | None, new: dict[str, Any]) -> bool:
    """Equal apart from the verification time."""
    if old is None:
        return False
    return {k: v for k, v in old.items() if k != "verified_at"} == {k: v for k, v in new.items() if k != "verified_at"}


def with_base(index: dict[str, Any], key: str, entry: dict[str, str]) -> dict[str, Any]:
    """``index`` with ``base.<key>`` set; ``base`` keeps its place, else goes before ``entries``."""
    base = {**index.get("base", {}), key: entry}
    if "base" in index:
        return {name: (base if name == "base" else value) for name, value in index.items()}
    out: dict[str, Any] = {}
    for name, value in index.items():
        if name == "entries":
            out["base"] = base
        out[name] = value
    out.setdefault("base", base)
    return out


def sync(root: Path, spec: UpstreamSpec, version: str, http: Http, git: Git,
         allow_downgrade: bool = False, clock: Callable[[], str] = utc_now) -> SyncResult:
    check_version(version)
    index_path = root / "index.json"
    if not index_path.exists():
        raise UpstreamError(f"no index.json in {root}")
    old_lock = read_lock(root, spec)
    if old_lock is not None and not allow_downgrade:
        if version_key(version) < version_key(old_lock["version"]):
            raise UpstreamError(f"{version} is older than the locked {old_lock['version']}; pass --allow-downgrade")

    source = spec.source(spec, http)
    release = source.release(version)
    evidence = Evidence()
    artifact = source.fetch(release, evidence)
    for verifier in spec.verifiers(spec, http, git):
        verifier.verify(artifact, evidence)
    if evidence.commit is None:
        raise UpstreamError("no verifier established the commit; refusing to write")
    evidence.not_verified.extend(spec.unchecked)

    snapshot = {rel: artifact.files[src] for rel, src in spec.files.items()}
    # The classifier sees package paths. The old side is the snapshot only, so the manifest
    # and declarations are not compared here; ``upstream check-release`` compares both tarballs.
    on_disk_before = _read_snapshot(root, spec)
    old_view = None if on_disk_before is None else {spec.files[rel]: data for rel, data in on_disk_before.items()}
    classification = spec.classifier().classify(old_view, dict(artifact.files))
    at = clock()
    floor = floor_fields(old_lock, version, classification.classes, at)
    lock = build_lock(spec, evidence, release, artifact.tarball, snapshot, at, floor)
    lock_errors = validate(lock, LOCK_SCHEMA, spec.lock_path)
    if lock_errors:
        raise UpstreamError("lock would not validate: " + "; ".join(lock_errors))
    base_entry = {"version": version, "lock": spec.lock_path}

    with locked(root):
        index = jsonio.load(index_path)
        new_index = with_base(index, spec.base_key, base_entry)
        errors = validate(new_index, "nocturne.index.v1", "index.json")
        if errors:
            raise UpstreamError("index.json would not validate: " + "; ".join(errors))
        on_disk = _read_snapshot(root, spec)
        last = last_synced(root, spec)
        if last is None or (not _same_content(old_lock, lock) and _needs_event(last, lock)):
            event: dict[str, Any] | None = history_event(spec, old_lock, lock, classification.classes, at)
        else:
            event = None
        if event is not None:
            event_errors = validate(event, HISTORY_SCHEMA, spec.history_path)
            if event_errors:
                raise UpstreamError("history event would not validate: " + "; ".join(event_errors))
        if (old_lock is not None and _same_content(old_lock, lock) and on_disk == snapshot and new_index == index
                and event is None):
            return SyncResult(version, evidence.commit, False, (), old_lock, classification)
        written: list[str] = []
        base = root / spec.snapshot_dir
        for rel, data in snapshot.items():
            jsonio.write_atomic_bytes(base / rel, data)
            written.append(f"{spec.snapshot_dir}/{rel}")
        jsonio.write_atomic(root / spec.lock_path, jsonio.dump_file(lock))
        written.append(spec.lock_path)
        if new_index != index:
            jsonio.write_atomic(index_path, jsonio.dump_file(new_index))
            written.append("index.json")
        if event is not None:
            jsonio.append_lines(root / spec.history_path, [jsonio.dump_line(event)])
            written.append(spec.history_path)
    return SyncResult(version, evidence.commit, True, tuple(written), lock, classification)


@dataclass(frozen=True)
class Status:
    code: int  # 0 ok, 1 broken, 2 newer version available
    lines: tuple[str, ...]


def status(root: Path, spec: UpstreamSpec, http: Http | None) -> Status:
    lock = read_lock(root, spec)
    if lock is None:
        return Status(1, (f"{spec.name}: no lock at {spec.lock_path} (run upstream sync --version X)",))
    lines = [f"{spec.name}: locked {lock['version']} ({lock['tag']}, commit {lock['commit']}), verified {lock['verified_at']}"]
    code = 0
    if "floor" in lock:
        lines.append(f"policy: latest {lock['latest']} (since {lock['latest_at']}), floor {lock['floor']}, "
                     f"grace until {lock['grace_until']}")
    else:
        lines.append(f"error: {spec.lock_path}: no latest/floor/grace_until (run upstream sync --version {lock['version']})")
        code = 1
    for path, item in lock["files"].items():
        file = root / path
        if not file.exists():
            lines.append(f"error: {path}: missing")
            code = 1
        elif sha256(file.read_bytes()) != item["sha256"]:
            lines.append(f"error: {path}: sha256 differs from the lock")
            code = 1
    index = jsonio.load(root / "index.json")
    base = index.get("base", {}).get(spec.base_key)
    if base != {"version": lock["version"], "lock": spec.lock_path}:
        lines.append(f"error: index.json base.{spec.base_key} is {json.dumps(base)}, lock is {lock['version']}")
        code = 1
    if http is None:
        return Status(code, tuple(lines))
    latest = spec.source(spec, http).latest()
    if not VERSION.match(latest):
        lines.append(f"latest: {spec.package} dist-tags.latest is {latest!r}, not a plain release")
    elif version_key(latest) > version_key(lock["version"]):
        lines.append(f"newer: {spec.package} {latest} is published (locked {lock['version']})")
        code = code or 2
    else:
        lines.append(f"latest: {spec.package} {latest}")
    return Status(code, tuple(lines))
