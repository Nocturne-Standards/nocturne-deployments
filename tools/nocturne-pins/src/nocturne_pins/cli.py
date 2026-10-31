"""``nocturne-pins`` command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__, base_check, check, jsonio, legacy, write
from .names import NameError_, parse, parse_entry
from .replay import ReplayError, replay
from .repo import Catalog, Repo, RepoError
from .schema import SCHEMA_FILES, schema_text
from .upstream import UpstreamError, net, specs
from .upstream import consumers as upstream_consumers
from .upstream import notes as upstream_notes
from .upstream import policy as upstream_policy
from .upstream import release as upstream_release
from .upstream import sync as upstream_sync


def home_catalog(root: str, extra: list[str] | None) -> tuple[Catalog, Repo]:
    """The checkout at ``root`` first, then ``--with`` repos, then its declared upstreams."""
    home = Repo.open(root)
    repos = [home]
    for location in extra or []:
        repos.append(Repo.open(location))
    named = {r.name for r in repos}
    for upstream in home.index.get("upstream", []):
        if upstream["repo"] in named:
            continue
        assert home.root is not None
        sibling = home.root.parent / upstream.get("sibling", "")
        location = str(sibling) if upstream.get("sibling") and (sibling / "index.json").exists() else upstream["url"]
        repo = Repo.open(location)
        if repo.name != upstream["repo"]:
            raise RepoError(f"{location} is {repo.name}, not the upstream {upstream['repo']}; pass --with")
        print(f"note: reading upstream {repo.name} from {repo.where}", file=sys.stderr)
        repos.append(repo)
    return Catalog(repos), home


def _catalog(args: argparse.Namespace) -> Catalog:
    return Catalog.open(args.index or None)


def _read_record(source: str) -> dict[str, Any]:
    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise write.WriteError("entry JSON must be an object")
    return value


def cmd_resolve(args: argparse.Namespace) -> int:
    catalog = _catalog(args)
    for text in args.names:
        print(catalog.resolve(parse(text), args.reader))
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    catalog = _catalog(args)
    repo, key, contract = catalog.find(parse(args.name), args.reader)
    out = {"name": f"{key.product}/{parse(args.name).name}/{key.network}/{key.tier}", "repo": repo.name, **contract}
    print(json.dumps(out, indent=2))
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    catalog = _catalog(args)
    state = catalog.state(parse_entry(args.entry))
    if args.key:
        if args.key not in state:
            print(f"error: {args.entry}: no state key {args.key!r}", file=sys.stderr)
            return 1
        print(json.dumps(state[args.key]["value"]))
        return 0
    print(json.dumps(state, indent=2))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    catalog = _catalog(args)
    for repo, key in catalog.entries():
        if args.reader == "public" and key.tier != "public":
            continue
        current = repo.current(key) or {"contracts": {}}
        for name, contract in current["contracts"].items():
            print(f"{key.product}/{name}/{key.network}/{key.tier}\t{contract['layer']}\t{contract['id']}\t{repo.name}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    catalog, home = home_catalog(args.root, args.with_)
    errors = check.check_repo(catalog, home)
    if args.base:
        errors += check.check_append_only(home, args.base)
    if errors:
        print(check.describe(errors), file=sys.stderr)
        print(f"{len(errors)} problem(s) in {home.where}", file=sys.stderr)
        return 1
    print(f"ok: {home.name} ({len(home.entries())} entries; repos read: {', '.join(r.name for r in catalog.repos)})")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    catalog, home = home_catalog(args.root, args.with_)
    keys = [parse_entry(args.entry)] if args.entry else [parse_entry(f"{i['product']}/{i['network']}/{i['tier']}") for i in home.entries()]
    status = 0
    for key in keys:
        item = home.entry(key)
        if item is None or home.root is None:
            print(f"error: {key}: not an entry of {home.where}", file=sys.stderr)
            return 1
        if args.write:
            # Recovery after a crash between the history append and the current rewrite.
            with write.locked(home.root):
                lines = jsonio.read_lines(home.root / item["history"])
                current = replay(key, lines, item["history"])
                jsonio.write_atomic(home.root / item["current"], jsonio.dump_file(current))
            print(f"wrote {item['current']} ({current['events']} events)")
            continue
        lines = jsonio.read_lines(home.root / item["history"])
        current = replay(key, lines, item["history"])
        problems = write.verify_entry(home, key)
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
            status = 1
        if not problems:
            print(f"ok: {key} ({current['events']} events)")
    if args.write:
        write.refresh_legacy(catalog)
    return status


def cmd_legacy(args: argparse.Namespace) -> int:
    catalog, home = home_catalog(args.root, args.with_)
    if args.write:
        with write.locked(home.root):
            for path in legacy.write(catalog, home):
                print(f"wrote {path}")
        return 0
    stale = legacy.stale(catalog, home)
    for path in stale:
        print(f"error: {path}: out of date", file=sys.stderr)
    return 1 if stale else 0


def cmd_record_contract(args: argparse.Namespace) -> int:
    catalog, _ = home_catalog(args.root, args.with_)
    lines = write.record_contract(
        catalog, args.name, args.layer, _read_record(args.entry_json), args.network, args.product, args.tier,
        args.allow_public_home,
    )
    print(f"{len(lines)} event(s) appended" if lines else "unchanged")
    return 0


def cmd_record_call(args: argparse.Namespace) -> int:
    catalog, _ = home_catalog(args.root, args.with_)
    kind, lines = write.record_call(
        catalog, args.key, args.layer, _read_record(args.entry_json), args.network, args.product, args.tier,
        args.allow_public_home,
    )
    if kind == "demo" and not lines:
        print("demo call: not pinned (no calls log for this entry)")
    elif kind == "demo":
        print("demo call: appended to the calls log")
    else:
        print(f"{kind}: {len(lines)} event(s) appended")
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    catalog, home = home_catalog(args.root, args.with_)
    sets = json.loads(args.sets) if args.sets else None
    write.note(catalog, home, parse_entry(args.entry), args.text, sets)
    print("note appended")
    return 0


def cmd_current_record(args: argparse.Namespace) -> int:
    catalog, _ = home_catalog(args.root, args.with_)
    record = catalog.current_record(args.key, args.network)
    if record is None:
        return 0 if args.field else 1
    if args.field:
        value = record.get(args.field)
        if value is not None:
            print(value if isinstance(value, str) else json.dumps(value))
        return 0
    print(json.dumps(record, indent=2))
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    print(write.classify(args.key))
    return 0


def cmd_upstream_sync(args: argparse.Namespace) -> int:
    spec = specs.get(args.upstream)
    result = upstream_sync.sync(
        Path(args.root), spec, args.version, net.UrllibHttp(), net.SubprocessGit(), args.allow_downgrade,
    )
    for line in result.lock["verified"]:
        print(f"verified: {line}")
    for line in result.lock["not_verified"]:
        print(f"not verified: {line}")
    print(f"classification: {result.classification.note}")
    if not result.changed:
        print(f"unchanged: {spec.name} {result.version} (commit {result.commit})")
        return 0
    for path in result.written:
        print(f"wrote {path}")
    return 0


def cmd_upstream_status(args: argparse.Namespace) -> int:
    spec = specs.get(args.upstream)
    report = upstream_sync.status(Path(args.root), spec, None if args.offline else net.UrllibHttp())
    for line in report.lines:
        print(line, file=sys.stderr if line.startswith("error:") else sys.stdout)
    return report.code


def cmd_upstream_check_release(args: argparse.Namespace) -> int:
    spec = specs.get(args.upstream)
    code, report = upstream_release.check_release(
        Path(args.root), spec, net.UrllibHttp(), net.SubprocessGit(), args.warn_unreachable, args.base_version,
    )
    if args.report_out:
        Path(args.report_out).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(upstream_notes.summary_text(report))
    return code


def cmd_upstream_release_notes(args: argparse.Namespace) -> int:
    spec = specs.get(args.upstream)
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpstreamError(f"report {args.report}: {exc}") from exc
    if not isinstance(report, dict) or report.get("schema") != upstream_release.REPORT_SCHEMA:
        raise UpstreamError(f"{args.report} is not a {upstream_release.REPORT_SCHEMA} report")
    sys.stdout.write(upstream_notes.render(args.kind, report, Path(args.root), spec, args.issue_url))
    return 0


def cmd_upstream_consumers(args: argparse.Namespace) -> int:
    spec = specs.get(args.upstream)
    root = Path(args.root).resolve()
    registry = upstream_notes.load_consumers(root, spec)
    if registry is None:
        raise UpstreamError(f"no consumer registry at {spec.consumers_path}")
    # The lock, its history and the release tags live in the public repo; the registry may
    # live elsewhere (a private companion repo), so they can come from two checkouts.
    lock_root = Path(args.lock_root).resolve() if args.lock_root else root
    lock = upstream_sync.read_lock(lock_root, spec)
    if lock is None:
        raise UpstreamError(f"no lock at {lock_root / spec.lock_path}")
    source: upstream_consumers.PinSource
    if args.offline:
        source_root = Path(args.source_root).resolve() if args.source_root else root.parent
        source = upstream_consumers.LocalSource(source_root, lock_root, spec.lock_path)
    else:
        home_repo = jsonio.load(lock_root / "index.json")["repo"]
        source = upstream_consumers.GhApiSource(home_repo, spec.lock_path)
    now = args.now or write.now()
    try:
        upstream_policy.parse_time(now)
    except ValueError as exc:
        raise UpstreamError(f"--now {now!r}: want YYYY-MM-DDTHH:MM:SSZ") from exc
    rows = upstream_consumers.sweep(registry, lock, source, now)
    if args.write:
        text = upstream_consumers.markdown(spec, lock, rows, source.label)
        target = root / spec.consumers_md_path
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            jsonio.write_atomic(target, text)
            print(f"wrote {spec.consumers_md_path}", file=sys.stderr)
    if args.new_issue_body:
        version = upstream_consumers.issue_version(args.issue_title or "")
        if version is None:
            raise UpstreamError(f"issue title {args.issue_title!r} names no vX.Y.Z")
        event = upstream_sync.last_synced(lock_root, spec)
        Path(args.new_issue_body).write_text(
            upstream_consumers.new_issue(args.issue_title, version, rows, lock, event), encoding="utf-8")
    if args.issue_body:
        version = upstream_consumers.issue_version(args.issue_title or "")
        if version is None:
            raise UpstreamError(f"issue title {args.issue_title!r} names no vX.Y.Z")
        body = Path(args.issue_body).read_text(encoding="utf-8")
        updated = upstream_consumers.update_issue(body, rows, version, lock)
        Path(args.issue_out or args.issue_body).write_text(updated, encoding="utf-8")
    if args.json:
        out = {"upstream": spec.name, "lock": {k: lock.get(k) for k in ("version", "latest", "latest_at", "floor",
                                                                          "grace_until", "policy")},
               "source": source.label, "now": now, "consumers": [r.to_json() for r in rows]}
        sys.stdout.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    elif args.markdown:
        sys.stdout.write(upstream_consumers.markdown(spec, lock, rows, source.label))
    else:
        sys.stdout.write(upstream_consumers.text_report(rows))
    code = upstream_consumers.exit_code(rows)
    if code == 1:
        print("error: a consumer is past max_lag, or (policy immediate) below the safety floor after the grace",
              file=sys.stderr)
    return code


def cmd_schema(args: argparse.Namespace) -> int:
    sys.stdout.write(schema_text(args.schema_id))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nocturne-pins", description="Resolve and record nocturne contract pins.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    def readers(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--index", action="append", help="index.json location (path or URL); repeat, first wins")
        sp.add_argument("--reader", choices=["own", "public"], default="own", help="own reads own then public")

    def writers(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--root", default=".", help="pin repo checkout to write or check")
        sp.add_argument("--with", dest="with_", action="append", help="extra repo to read refs from")
        sp.add_argument("--allow-public-home", action="store_true",
                        help="allow writes into the public repo without the private checkout in view")

    sp = sub.add_parser("resolve", help="print contract ids for full names")
    sp.add_argument("names", nargs="+", help="product/name[/network[/tier]]")
    readers(sp)
    sp.set_defaults(func=cmd_resolve)

    sp = sub.add_parser("get", help="print one contract entry as JSON")
    sp.add_argument("name")
    readers(sp)
    sp.set_defaults(func=cmd_get)

    sp = sub.add_parser("state", help="print configuration state of product/network/tier")
    sp.add_argument("entry")
    sp.add_argument("key", nargs="?")
    readers(sp)
    sp.set_defaults(func=cmd_state)

    sp = sub.add_parser("list", help="list live contracts")
    readers(sp)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("check", help="CI: schema, replay, refs, tiers, legacy files")
    sp.add_argument("--base", help="git ref; logs must only have grown since (append-only)")
    writers(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("replay", help="check (or rebuild) current files from history")
    sp.add_argument("entry", nargs="?")
    sp.add_argument("--write", action="store_true", help="rewrite current files from history")
    writers(sp)
    sp.set_defaults(func=cmd_replay)

    sp = sub.add_parser("legacy", help="check (or write) the generated v1 monolith files")
    sp.add_argument("--write", action="store_true")
    writers(sp)
    sp.set_defaults(func=cmd_legacy)

    sp = sub.add_parser("record-contract", help="record a deployment (deploy engine)")
    sp.add_argument("--name", required=True, help="contract name, e.g. knot-registry")
    sp.add_argument("--layer", choices=["duskds", "duskevm"], default="duskds")
    sp.add_argument("--entry-json", required=True, help="v1 entry object (file or -)")
    sp.add_argument("--network", default="testnet")
    sp.add_argument("--product")
    sp.add_argument("--tier", choices=["own", "public"])
    writers(sp)
    sp.set_defaults(func=cmd_record_contract)

    sp = sub.add_parser("record-call", help="record a contract call (wire engine)")
    sp.add_argument("--key", required=True, help="wiring key contract.fn[.label]")
    sp.add_argument("--layer", choices=["duskds", "duskevm"], default="duskds")
    sp.add_argument("--entry-json", required=True)
    sp.add_argument("--network", default="testnet")
    sp.add_argument("--product")
    sp.add_argument("--tier", choices=["own", "public"])
    writers(sp)
    sp.set_defaults(func=cmd_record_call)

    sp = sub.add_parser("note", help="append a note event (optionally setting state)")
    sp.add_argument("entry")
    sp.add_argument("--text", required=True)
    sp.add_argument("--sets", help="JSON object of state keys")
    writers(sp)
    sp.set_defaults(func=cmd_note)

    sp = sub.add_parser("current-record", help="latest v1-shaped row of a contract or wiring key (deploy engine reads)")
    sp.add_argument("--key", required=True)
    sp.add_argument("--field", help="print one field (empty if absent)")
    sp.add_argument("--network", default="testnet")
    writers(sp)
    sp.set_defaults(func=cmd_current_record)

    sp = sub.add_parser("classify", help="config or demo for a wiring key")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_classify)

    up = sub.add_parser("upstream", help="upstream base layers (Dusk networks package)")
    up_sub = up.add_subparsers(dest="upstream_command", required=True)

    sp = up_sub.add_parser("sync", help="fetch, verify and write the snapshot and lock of one version")
    sp.add_argument("--version", required=True, help="plain X.Y.Z release")
    sp.add_argument("--upstream", default="dusk-networks", choices=sorted(specs.UPSTREAMS))
    sp.add_argument("--root", default=".", help="pin repo checkout to write")
    sp.add_argument("--allow-downgrade", action="store_true", help="allow a version older than the lock")
    sp.set_defaults(func=cmd_upstream_sync)

    sp = up_sub.add_parser("status", help="locked version, file hashes, newer release (exit 2)")
    sp.add_argument("--upstream", default="dusk-networks", choices=sorted(specs.UPSTREAMS))
    sp.add_argument("--root", default=".", help="pin repo checkout to read")
    sp.add_argument("--offline", action="store_true", help="skip the registry query")
    sp.set_defaults(func=cmd_upstream_status)
    base_check.add_cli(up_sub)

    sp = up_sub.add_parser(
        "check-release",
        help="compare npm, JSR and git tags with the lock; verify and classify a newer release (exit 2)",
    )
    sp.add_argument("--upstream", default="dusk-networks", choices=sorted(specs.UPSTREAMS))
    sp.add_argument("--root", default=".", help="pin repo checkout to read")
    sp.add_argument("--json", action="store_true", help="print the JSON report instead of the summary")
    sp.add_argument("--report-out", help="also write the JSON report to this file")
    sp.add_argument(
        "--warn-unreachable", action="store_true",
        help="exit 0 with a warning when a registry or GitHub cannot be reached; mismatches still fail",
    )
    sp.add_argument(
        "--from", dest="base_version", metavar="VERSION",
        help="dry run: compare with this registry version instead of the lock (checked against the registry only)",
    )
    sp.set_defaults(func=cmd_upstream_check_release)

    sp = up_sub.add_parser("release-notes", help="PR body, issue body or title from a check-release report")
    sp.add_argument("--report", required=True, help="JSON report from check-release --report-out")
    sp.add_argument("--kind", choices=["pr", "issue", "title", "summary"], default="issue")
    sp.add_argument("--upstream", default="dusk-networks", choices=sorted(specs.UPSTREAMS))
    sp.add_argument("--root", default=".", help="pin repo checkout (reads the consumer registry)")
    sp.add_argument("--issue-url", help="tracking issue to link from the PR body")
    sp.set_defaults(func=cmd_upstream_release_notes)

    sp = up_sub.add_parser(
        "consumers",
        help="read every registered consumer's pin and compare it with the lock (exit 1: past max_lag or floor)",
    )
    sp.add_argument("--upstream", default="dusk-networks", choices=sorted(specs.UPSTREAMS))
    sp.add_argument("--root", default=".", help="checkout holding the consumer registry (and CONSUMERS.md)")
    sp.add_argument("--lock-root", help="public pin repo checkout with the lock, history and tags (default: --root)")
    sp.add_argument("--offline", action="store_true",
                    help="read sibling checkouts under --source-root instead of the GitHub API")
    sp.add_argument("--source-root", help="directory holding the consumer checkouts (default: parent of --root)")
    sp.add_argument("--write", action="store_true", help="regenerate upstream/CONSUMERS.md")
    sp.add_argument("--json", action="store_true", help="print the rows as JSON")
    sp.add_argument("--markdown", action="store_true", help="print the CONSUMERS.md text")
    sp.add_argument("--now", help="evaluate at this UTC time (YYYY-MM-DDTHH:MM:SSZ) instead of the clock")
    sp.add_argument("--issue-body", help="tracking issue body file: tick done consumers, refresh the sweep table")
    sp.add_argument("--issue-title", help="title of that issue (names the version, vX.Y.Z)")
    sp.add_argument("--issue-out", help="write the updated body here (default: in place)")
    sp.add_argument("--new-issue-body", help="write a fresh tracking issue body (checklist of every consumer) here")
    sp.set_defaults(func=cmd_upstream_consumers)

    sp = sub.add_parser("schema", help="print a bundled schema")
    sp.add_argument("schema_id", choices=sorted(SCHEMA_FILES))
    sp.set_defaults(func=cmd_schema)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except (RepoError, NameError_, ReplayError, write.WriteError, UpstreamError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
