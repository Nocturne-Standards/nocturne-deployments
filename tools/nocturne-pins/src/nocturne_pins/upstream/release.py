"""``upstream check-release``: is a newer release out, does it verify, and what does it change.

Reads release signals (npm ``dist-tags.latest``, JSR ``latest``, git tags) and compares them
with the lock. When npm has a newer plain release, it is fetched and verified exactly as
``upstream sync`` would (nothing is written), the locked tarball is fetched again and checked
against the lock, and the classifier compares the two. The result is a JSON report; the
PR and issue bodies are generated from it (``notes``).

Exit codes: 0 up to date, 2 a newer release verified and classified, 1 verification failed or
the registry contradicts the lock. An outage (``Unreachable``) is 1, or with
``warn_unreachable`` a warning and 0: an outage is not evidence of tampering.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from .model import Evidence, UpstreamError
from .net import Git, Http, Unreachable
from .npm import VERSION, check_integrity, read_selected, version_key
from .specs import UpstreamSpec
from .sync import read_lock, sha256

REPORT_SCHEMA = "nocturne.upstream-release-report.v1"
CHANGELOG_LIMIT = 6000


class _Outage(Exception):
    """An ``Unreachable`` that ends the run (no warning flag, or nothing left to compare)."""


def _plain(version: Any) -> bool:
    return isinstance(version, str) and VERSION.match(version) is not None


def latest_git_tag(spec: UpstreamSpec, git: Git) -> str | None:
    refs = git.ls_remote(spec.repository, [f"refs/tags/{spec.tag_prefix}*"])
    versions = []
    for ref in refs:
        name = ref.removeprefix("refs/tags/").removesuffix("^{}")
        if name.startswith(spec.tag_prefix) and _plain(name[len(spec.tag_prefix):]):
            versions.append(name[len(spec.tag_prefix):])
    return max(versions, key=version_key) if versions else None


def latest_jsr(spec: UpstreamSpec, http: Http) -> str | None:
    if spec.jsr_meta is None:
        return None
    body = http.get(spec.jsr_meta)  # outside the try: Unreachable is a ValueError too
    try:
        meta = json.loads(body)
    except ValueError as exc:
        raise UpstreamError(f"JSR metadata {spec.jsr_meta} is not JSON: {exc}") from exc
    latest = meta.get("latest") if isinstance(meta, dict) else None
    if not isinstance(latest, str):
        raise UpstreamError(f"JSR metadata {spec.jsr_meta} gives no latest version")
    return latest


def changelog_section(text: str, version: str) -> str | None:
    """The ``## [X.Y.Z]`` (or ``## X.Y.Z`` / ``## vX.Y.Z``) section, heading included, as plain text."""
    heading = re.compile(rf"^##\s+\[?v?{re.escape(version)}\]?(\s|$)")
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    for line in lines:
        if out and line.startswith("## "):
            break
        if out or heading.match(line):
            out.append(line)
    if not out:
        return None
    section = "\n".join(out).strip()
    if len(section) > CHANGELOG_LIMIT:
        section = section[:CHANGELOG_LIMIT].rstrip() + "\n[... cut at the size limit]"
    return section


def _report(spec: UpstreamSpec, lock: dict[str, Any] | None, base: str | None) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "upstream": spec.name,
        "package": spec.package,
        "repository": spec.repository,
        "status": "failed",
        "exit_code": 1,
        "locked": None if lock is None else {
            "version": lock["version"], "tag": lock["tag"], "commit": lock["commit"],
            "verified_at": lock["verified_at"],
        },
        # What the release is compared with: the lock, or a registry version (``--from``, a dry run).
        "compared_with": {
            "source": "lock" if base is None else "registry",
            "version": base if base is not None else (lock or {}).get("version"),
        },
        "latest": {"npm": None, "jsr": None, "git": None},
        "candidate": None,
        "verified": [],
        "not_verified": [],
        "classes": [],
        "summary": None,
        "changes": [],
        "not_compared": [],
        "changelog": None,
        "warnings": [],
        "errors": [],
    }


def _done(report: dict[str, Any], status: str, code: int) -> tuple[int, dict[str, Any]]:
    report["status"] = status
    report["exit_code"] = code
    return code, report


def check_release(root: Path, spec: UpstreamSpec, http: Http, git: Git, warn_unreachable: bool = False,
                  base: str | None = None) -> tuple[int, dict[str, Any]]:
    """Compare release signals with the lock (or with the registry's ``base`` version, a dry run)."""
    lock = read_lock(root, spec)
    report = _report(spec, lock, base)
    if lock is None and base is None:
        report["errors"].append(f"no lock at {spec.lock_path} (run upstream sync --version X)")
        return _done(report, "failed", 1)

    def outage(where: str, exc: Unreachable) -> None:
        message = f"{where} unreachable: {exc}"
        if not warn_unreachable:
            report["errors"].append(message)
            raise _Outage(message)
        report["warnings"].append(message)

    try:
        return _check(spec, http, git, lock, base, report, outage)
    except _Outage:
        return _done(report, "unreachable", 1)
    except Unreachable as exc:
        # Past the signals: the candidate or the locked tarball could not be fetched. Try again next run.
        message = f"registry or GitHub unreachable: {exc}"
        (report["warnings"] if warn_unreachable else report["errors"]).append(message)
        return _done(report, "unreachable", 0 if warn_unreachable else 1)
    except UpstreamError as exc:
        report["errors"].append(str(exc))
        return _done(report, "failed", 1)


def _check(spec: UpstreamSpec, http: Http, git: Git, lock: dict[str, Any] | None, base: str | None,
           report: dict[str, Any], outage: Callable[[str, Unreachable], None]) -> tuple[int, dict[str, Any]]:
    source = spec.source(spec, http)
    current = base if base is not None else lock["version"]  # type: ignore[index]
    if not _plain(current):
        raise UpstreamError(f"compared version {current!r} is not a plain X.Y.Z release")
    latest = report["latest"]
    try:
        latest["npm"] = source.latest()
    except Unreachable as exc:
        outage("npm registry", exc)
    try:
        latest["jsr"] = latest_jsr(spec, http)
    except Unreachable as exc:
        outage("JSR", exc)
    except UpstreamError as exc:
        report["warnings"].append(f"JSR: {exc}")
    try:
        latest["git"] = latest_git_tag(spec, git)
    except Unreachable as exc:
        outage("git remote", exc)
    except UpstreamError as exc:
        report["warnings"].append(f"git tags: {exc}")

    npm = latest["npm"]
    if npm is None:
        return _done(report, "unreachable", 0)
    if not _plain(npm):
        report["warnings"].append(f"npm dist-tags.latest is {npm!r}, not a plain release; nothing to compare")
        return _done(report, "up-to-date", 0)
    for where in ("jsr", "git"):
        other = latest[where]
        if other is not None and other != npm:
            ahead = _plain(other) and version_key(other) > version_key(npm)
            report["warnings"].append(
                f"{where} latest is {other}, npm latest is {npm}" + (" (not on npm yet)" if ahead else "")
            )
    if version_key(npm) < version_key(current):
        report["errors"].append(
            f"npm dist-tags.latest {npm} is older than {current}: the registry moved latest back; "
            "check upstream before anything else"
        )
        return _done(report, "failed", 1)
    if version_key(npm) == version_key(current):
        return _done(report, "up-to-date", 0)

    # A newer release: verify it as sync would, without writing.
    release = source.release(npm)
    evidence = Evidence()
    artifact = source.fetch(release, evidence)
    for verifier in spec.verifiers(spec, http, git):
        verifier.verify(artifact, evidence)
    if evidence.commit is None:
        raise UpstreamError("no verifier established the commit")
    evidence.not_verified.extend(spec.unchecked)
    report["candidate"] = {
        "version": release.version,
        "tag": release.tag,
        "commit": evidence.commit,
        "tarball": {"url": release.tarball_url, "integrity": release.integrity, "sha256": sha256(artifact.tarball)},
        "provenance": evidence.provenance,
    }
    report["verified"] = list(evidence.verified)
    report["not_verified"] = list(evidence.not_verified)

    new_view = read_selected(artifact.tarball, spec.in_view)
    old_view = _old_view(spec, http, lock, base, source)
    classification = spec.classifier().classify(old_view, new_view)
    report["classes"] = list(classification.classes)
    report["changes"] = [change.to_json() for change in classification.changes]
    report["not_compared"] = list(classification.not_compared)
    report["summary"] = classification.note
    changelog = new_view.get("CHANGELOG.md")
    if changelog is not None:
        report["changelog"] = changelog_section(changelog.decode("utf-8", "replace"), npm)
    return _done(report, "newer", 2)


def _old_view(spec: UpstreamSpec, http: Http, lock: dict[str, Any] | None, base: str | None,
              source: Any) -> dict[str, bytes]:
    """The compared release's files: the locked tarball checked against the lock, or ``base`` from the registry."""
    if base is not None:
        release = source.release(base)
        tarball = http.get(release.tarball_url)
        check_integrity(tarball, release.integrity)
        return read_selected(tarball, spec.in_view)
    assert lock is not None
    tarball = http.get(lock["tarball"]["url"])
    check_integrity(tarball, lock["tarball"]["integrity"])
    if sha256(tarball) != lock["tarball"]["sha256"]:
        raise UpstreamError(f"locked tarball sha256 {sha256(tarball)} != lock {lock['tarball']['sha256']}")
    view = read_selected(tarball, spec.in_view)
    for path, item in lock["files"].items():
        source_path = item["source"]
        if source_path not in view or sha256(view[source_path]) != item["sha256"]:
            raise UpstreamError(f"locked tarball's {source_path} does not match {path} in the lock")
    return view
