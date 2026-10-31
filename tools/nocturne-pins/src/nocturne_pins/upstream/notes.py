"""Markdown from a ``check-release`` report: the update PR body, the tracking issue body, a title.

Everything that comes from upstream (paths, values, the CHANGELOG) is shown as code, so a
crafted value cannot add links, mentions or markup. The CHANGELOG is quoted as data in a
fenced block; the classes come from the diff.

Consumers come from ``upstream/<name>.consumers.json`` when it exists: a JSON list (or an
object with a ``consumers`` list, ``nocturne.upstream-consumers.v1``) of ``{repo, path, kind,
policy, uses}``, optionally ``status`` (``planned``: no pin yet), ``visibility`` and ``note``. ``uses`` holds
classes (``S``, ``D``, ``A``), ``*``, or path prefixes matched against a change's ``ref``
(``routes.services``, ``package:exports``, ``schema:``, ``types:src/gas.d.ts``). Only
consumers whose ``uses`` overlap the change are listed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .classify import CLASS_ORDER
from .model import UpstreamError
from .specs import UpstreamSpec

KINDS = ("npm", "jsr", "rust-crate", "vendored")
POLICIES = ("immediate", "next-release")
WHEN = {
    "S": "within 48 h, before any deploy",
    "D": "next release; at most 30 days or one minor version behind",
    "A": "opt in; never forced",
}
CLASS_NAMES = {"S": "safety", "D": "data", "A": "API or schema"}
MAX_ROWS = 150
NO_REGISTRY = "Consumer impact is tracked internally, not in this repository."


STATUSES = ("active", "planned")
VISIBILITIES = ("public", "private")


@dataclass(frozen=True)
class Consumer:
    repo: str
    path: str
    kind: str
    policy: str
    uses: tuple[str, ...]
    # planned: does not pin the upstream yet; listed so release issues still name it.
    status: str = "active"
    visibility: str = "private"
    note: str = ""


def load_consumers(root: Path, spec: UpstreamSpec) -> list[Consumer] | None:
    """The registry, or None when the file does not exist. A malformed file is an error."""
    path = root / spec.consumers_path
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise UpstreamError(f"{spec.consumers_path}: not JSON: {exc}") from exc
    items = data.get("consumers") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise UpstreamError(f"{spec.consumers_path}: want a list of consumers")
    out: list[Consumer] = []
    for index, item in enumerate(items):
        where = f"{spec.consumers_path}[{index}]"
        if not isinstance(item, dict):
            raise UpstreamError(f"{where}: not an object")
        for key in ("repo", "path"):
            if not isinstance(item.get(key), str) or not item[key]:
                raise UpstreamError(f"{where}: {key} must be a non-empty string")
        if item.get("kind") not in KINDS:
            raise UpstreamError(f"{where}: kind must be one of {', '.join(KINDS)}")
        if item.get("policy") not in POLICIES:
            raise UpstreamError(f"{where}: policy must be one of {', '.join(POLICIES)}")
        uses = item.get("uses")
        if not isinstance(uses, list) or not uses or not all(isinstance(u, str) and u for u in uses):
            raise UpstreamError(f"{where}: uses must be a non-empty list of strings")
        status = item.get("status", "active")
        if status not in STATUSES:
            raise UpstreamError(f"{where}: status must be one of {', '.join(STATUSES)}")
        visibility = item.get("visibility", "private")
        if visibility not in VISIBILITIES:
            raise UpstreamError(f"{where}: visibility must be one of {', '.join(VISIBILITIES)}")
        out.append(Consumer(item["repo"], item["path"], item["kind"], item["policy"], tuple(uses), status,
                            visibility, item.get("note", "") if isinstance(item.get("note"), str) else ""))
    return sorted(out, key=lambda c: (c.repo, c.path))


def uses_match(entry: str, change: dict[str, Any]) -> bool:
    if entry == "*" or entry == change["class"]:
        return True
    ref = change["ref"]
    if entry in CLASS_ORDER:
        return False
    if entry.endswith((":", ".", "/")):
        return ref.startswith(entry)
    return ref == entry or (ref.startswith(entry) and ref[len(entry)] in ".[:/")


def affected(consumers: list[Consumer], changes: list[dict[str, Any]]) -> list[tuple[Consumer, list[dict[str, Any]]]]:
    out = []
    for consumer in consumers:
        hits = [c for c in changes if any(uses_match(u, c) for u in consumer.uses)]
        if hits:
            out.append((consumer, hits))
    return out


# ---------------------------------------------------------------- markdown helpers


def code(value: Any, limit: int = 80) -> str:
    """Inline code safe inside a table cell."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = re.sub(r"[\r\n\t]+", " ", text)
    if len(text) > limit:
        keep = limit - 13
        text = f"{text[:keep]}…{text[-12:]}"
    text = text.replace("|", "\\|")
    run = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * (run + 1)
    pad = " " if text.startswith("`") or text.endswith("`") or not text else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def fenced(text: str) -> str:
    run = max((len(m) for m in re.findall(r"`{3,}", text)), default=2)
    fence = "`" * max(3, run + 1)
    return f"{fence}text\n{text}\n{fence}"


def _classes(report: dict[str, Any]) -> str:
    return ", ".join(report["classes"]) or "none"


def _compared(compared: dict[str, Any]) -> str:
    where = "the locked" if compared["source"] == "lock" else "the registry's (dry run, not the lock)"
    return f"{where} {code(compared['version'])}"


def title(report: dict[str, Any]) -> str:
    candidate = report.get("candidate") or {}
    version = candidate.get("version", "?")
    return f"{report['upstream']} v{version} ({_classes(report)})"


def _header(report: dict[str, Any]) -> list[str]:
    candidate = report["candidate"]
    compared = report["compared_with"]
    lines = [
        f"Package {code(report['package'])} {code(candidate['version'])} "
        f"(tag {code(candidate['tag'])}, commit {code(candidate['commit'])}) "
        f"compared with {_compared(compared)}.",
        "",
        "| Class | Meaning | Changes | When consumers update |",
        "|---|---|---|---|",
    ]
    for cls in report["classes"]:
        count = sum(1 for c in report["changes"] if c["class"] == cls)
        lines.append(f"| {cls} | {CLASS_NAMES[cls]} | {count} | {WHEN[cls]} |")
    if not report["classes"]:
        lines.append("| - | no classified change | 0 | - |")
    return lines


def _verification(report: dict[str, Any]) -> list[str]:
    candidate = report["candidate"]
    lines = ["## Verification", ""]
    lines += [f"- verified: {item}" for item in report["verified"]]
    lines += [f"- **not verified**: {item}" for item in report["not_verified"]]
    lines += ["", f"Tarball sha256 {code(candidate['tarball']['sha256'])}, "
                  f"integrity {code(candidate['tarball']['integrity'], 200)}."]
    rekor = (candidate.get("provenance") or {}).get("rekor_log_index")
    if rekor is not None:
        lines.append(f"Rekor log index {code(rekor)} (recorded, not checked offline).")
    return lines


def _changes(report: dict[str, Any]) -> list[str]:
    changes = report["changes"]
    lines = ["## Changes", "", "| Class | File | Path | Old | New | Summary |", "|---|---|---|---|---|---|"]
    for change in changes[:MAX_ROWS]:
        lines.append(
            f"| {change['class']} | {code(change['file'])} | {code(change['path'] or '-')} | "
            f"{code(change['old'], 48)} | {code(change['new'], 48)} | {code(change['summary'], 100)} |"
        )
    if len(changes) > MAX_ROWS:
        lines.append(f"\n{len(changes) - MAX_ROWS} more changes: see the JSON report attached to the workflow run.")
    if not changes:
        lines.append("| - | - | - | - | - | no classified change |")
    if report["not_compared"]:
        lines += ["", "Not compared:", ""] + [f"- {item}" for item in report["not_compared"]]
    return lines


def _changelog(report: dict[str, Any]) -> list[str]:
    lines = ["## Upstream CHANGELOG", ""]
    if report.get("changelog") is None:
        return lines + ["No section for this version in the package's CHANGELOG.md."]
    return lines + [
        "Quoted from the package as data. Classes above come from the diff, not from this text.",
        "",
        fenced(report["changelog"]),
    ]


def _checklist(report: dict[str, Any], consumers: list[Consumer] | None) -> list[str]:
    lines = ["## Consumers", ""]
    if consumers is None:
        return lines + [NO_REGISTRY]
    hits = affected(consumers, report["changes"])
    if not hits:
        return lines + [f"No registered consumer uses what changed ({len(consumers)} registered)."]
    for consumer, changes in hits:
        classes = [c for c in CLASS_ORDER if any(ch["class"] == c for ch in changes)]
        refs = sorted({ch["ref"] for ch in changes})
        shown = ", ".join(code(r, 60) for r in refs[:5]) + (f" and {len(refs) - 5} more" if len(refs) > 5 else "")
        planned = ", planned: no pin yet" if consumer.status == "planned" else ""
        lines.append(
            f"- [ ] {code(consumer.repo)} {code(consumer.path)} ({consumer.kind}, {consumer.policy}{planned}): "
            f"{', '.join(classes)}; {WHEN[classes[0]]}. Uses {shown}"
        )
    return lines


def issue_body(report: dict[str, Any], consumers: list[Consumer] | None) -> str:
    _require_candidate(report)
    lines = _header(report) + [""] + _checklist(report, consumers) + [""] + _changes(report)
    lines += [""] + _verification(report) + [""] + _changelog(report)
    return "\n".join(lines) + "\n"


def pr_body(report: dict[str, Any], consumers: list[Consumer] | None, issue_url: str | None = None) -> str:
    _require_candidate(report)
    lines = _header(report) + [""]
    lines += [
        "Written by `nocturne-pins upstream sync`: snapshot under `upstream/`, lock (with `latest`, `floor`, "
        "`grace_until`), `index.json` `base` and one event in `history/dusk-networks.jsonl`.",
    ]
    if issue_url:
        lines.append(f"Tracking issue: {issue_url}")
    lines += [""] + _verification(report) + [""] + _changes(report) + [""] + _changelog(report)
    lines += [""] + _checklist(report, consumers)
    return "\n".join(lines) + "\n"


def summary_text(report: dict[str, Any]) -> str:
    """Short report for the terminal and the job log."""
    latest = report["latest"]
    compared = report["compared_with"]
    lines = [
        f"{report['upstream']}: {report['status']} (exit {report['exit_code']}); "
        f"compared with {compared['source']} {compared['version']}",
        f"latest: npm {latest['npm']}, jsr {latest['jsr']}, git {latest['git']}",
    ]
    if report["candidate"]:
        c = report["candidate"]
        lines.append(f"candidate: {c['version']} ({c['tag']}, commit {c['commit']})")
        lines += [f"verified: {item}" for item in report["verified"]]
        lines += [f"not verified: {item}" for item in report["not_verified"]]
        lines.append(f"classes: {_classes(report)}; {report.get('summary')}")
        for ch in report["changes"]:
            lines.append(f"  {ch['class']} {ch['file']} {ch['path'] or '-'} {ch['kind']}: "
                         f"{json.dumps(ch['old'], ensure_ascii=False)[:60]} -> "
                         f"{json.dumps(ch['new'], ensure_ascii=False)[:60]} ({ch['summary']})")
        lines += [f"not compared: {item}" for item in report["not_compared"]]
    lines += [f"::warning::{w}" for w in report["warnings"]]
    lines += [f"error: {e}" for e in report["errors"]]
    return "\n".join(lines) + "\n"


def _require_candidate(report: dict[str, Any]) -> None:
    if report.get("status") != "newer" or not report.get("candidate"):
        raise UpstreamError(f"report status is {report.get('status')!r}: no verified newer release to describe")


def render(kind: str, report: dict[str, Any], root: Path, spec: UpstreamSpec, issue_url: str | None = None) -> str:
    if kind == "title":
        _require_candidate(report)
        return title(report) + "\n"
    if kind == "summary":
        return summary_text(report)
    consumers = load_consumers(root, spec)
    if kind == "issue":
        return issue_body(report, consumers)
    if kind == "pr":
        return pr_body(report, consumers, issue_url)
    raise UpstreamError(f"unknown notes kind {kind!r}")
