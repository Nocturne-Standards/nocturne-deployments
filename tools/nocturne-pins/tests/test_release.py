"""Classifier, ``upstream check-release`` and the PR/issue generator (no network).

``fixtures/upstream/dusk-networks-0.2.0`` and ``-0.3.0`` are excerpts of the published
``@dusk-network/networks`` tarballs (MPL-2.0): the files the 0.2.0 -> 0.3.0 change touches.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from test_upstream import REGISTRY, World, make_root, make_tarball, package_files, run

from nocturne_pins import cli, jsonio
from nocturne_pins.upstream import UpstreamError
from nocturne_pins.upstream import net as upstream_net
from nocturne_pins.upstream.classify import UNKNOWN, DataDiffClassifier, dusk_networks_classifier
from nocturne_pins.upstream.net import GIT_OUTAGE, Unreachable
from nocturne_pins.upstream.notes import (
    NO_REGISTRY, Consumer, affected, code, fenced, issue_body, load_consumers, pr_body, render, title, uses_match,
)
from nocturne_pins.upstream.release import changelog_section, check_release
from nocturne_pins.upstream.specs import DUSK_NETWORKS as SPEC

FIXTURES = Path(__file__).parent / "fixtures" / "upstream"
JSR = "https://jsr.io/@dusk/networks/meta.json"
SNAPSHOTS = FIXTURES / "notes"


def fixture_view(version: str) -> dict[str, bytes]:
    base = FIXTURES / f"dusk-networks-{version}"
    return {str(p.relative_to(base)): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}


def doc(**over: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "chainId": 2, "name": "testnet", "label": "Testnet",
        "urls": {"node": "https://node"}, "apps": {"explorer": "https://explorer"},
        "contracts": {"transfer": "01" * 32},
        "sozu": {"hub": "aa" * 32, "fallbacks": {"pool": "bb" * 32}},
        "duskEvm": None,
        "routes": {"services": [{"id": "dusk-to-bsc", "account": "acc", "feeLux": "1", "typicalSeconds": 60,
                                 "running": True}]},
        "display": {"labels": {"aa" * 32: "Sozu hub"}, "blocklist": [{"account": "bad", "reason": "scam"}]},
    }
    value.update(over)
    return value


def classify_docs(old: dict[str, Any], new: dict[str, Any]):  # noqa: ANN201
    name = "networks/testnet.json"
    return dusk_networks_classifier().classify({name: json.dumps(old).encode()}, {name: json.dumps(new).encode()})


def classes_by_path(result: Any) -> dict[str, str]:
    return {c.path: c.cls for c in result.changes}


# ---------------------------------------------------------------- classifier


def test_real_0_2_0_to_0_3_0() -> None:
    result = dusk_networks_classifier().classify(fixture_view("0.2.0"), fixture_view("0.3.0"))
    assert result.classes == ("S", "A")
    got = [(c.cls, c.file, c.path, c.kind) for c in result.changes]
    assert got == [
        ("S", "networks/mainnet.json", "routes.services[dusk-to-bsc].payoutAddress", "added"),
        ("A", "package.json", "description", "changed"),
        ("A", "package.json", 'exports["./account"]', "added"),
        ("A", "package.json", 'exports["./gas"]', "added"),
        ("A", "package.json", "version", "changed"),
        ("A", "schema.json", "properties.routes.properties.services.items.properties.payoutAddress", "added"),
        ("A", "schema.json", "properties.routes.properties.services.items.required", "changed"),
    ]
    payout = result.changes[0]
    assert payout.new == "0xe6D0c4d8CA2C12b357A5b5470c53E86123Ab16da"
    assert payout.ref == "routes.services[].payoutAddress"
    assert "payout address" in payout.summary
    assert 'required keys +"payoutAddress"' in result.changes[-1].summary
    assert any("not in both" in item and ".d.ts" in item for item in result.not_compared)


@pytest.mark.parametrize(
    ("change", "path", "cls"),
    [
        ({"chainId": 3}, "chainId", "S"),
        ({"urls": {"node": "https://other"}}, "urls.node", "S"),
        ({"contracts": {"transfer": "02" * 32}}, "contracts.transfer", "S"),
        ({"sozu": {"hub": "cc" * 32, "fallbacks": {"pool": "bb" * 32}}}, "sozu.hub", "S"),
        ({"sozu": {"hub": "aa" * 32, "fallbacks": {"pool": "dd" * 32}}}, "sozu.fallbacks.pool", "D"),
        ({"label": "Testnet 2"}, "label", "D"),
        ({"apps": {"explorer": "https://x"}}, "apps.explorer", "D"),
        ({"operator": {"name": "Dusk"}}, "operator", "D"),
    ],
)
def test_classes_per_path(change: dict[str, Any], path: str, cls: str) -> None:
    result = classify_docs(doc(), doc(**change))
    assert classes_by_path(result) == {path: cls}


def test_routes_labels_blocklist_and_estimates() -> None:
    new = doc()
    service = new["routes"]["services"][0]
    service["typicalSeconds"] = 90
    service["feeLux"] = "2"
    new["display"]["labels"]["aa" * 32] = "Sozu hub (new)"
    new["display"]["blocklist"].append({"account": "worse", "reason": "compromised"})
    result = classify_docs(doc(), new)
    assert classes_by_path(result) == {
        "routes.services[dusk-to-bsc].typicalSeconds": "D",
        "routes.services[dusk-to-bsc].feeLux": "S",
        f"display.labels.{'aa' * 32}": "D",
        "display.blocklist[worse]": "S",
    }


def test_whole_route_added_is_safety() -> None:
    new = doc()
    new["routes"]["services"].append({"id": "dusk-to-eth", "account": "acc2", "typicalSeconds": None, "running": False})
    (change,) = classify_docs(doc(), new).changes
    assert (change.cls, change.path, change.kind) == ("S", "routes.services[dusk-to-eth]", "added")


def test_duskevm_appears_classified_by_its_values() -> None:
    evm = {"name": "DuskEVM", "chainId": 745, "rpcUrl": "https://rpc", "explorerUrl": "https://x",
           "contracts": {"optimism_portal": {"contractId": "ee" * 32, "evmAddress": "0x" + "1" * 40}}}
    (change,) = classify_docs(doc(), doc(duskEvm=evm)).changes
    assert change.cls == "S" and UNKNOWN not in change.summary
    only_data = classify_docs(doc(duskEvm=evm), doc(duskEvm={**evm, "explorerUrl": "https://y"}))
    assert classes_by_path(only_data) == {"duskEvm.explorerUrl": "D"}


def test_unknown_path_is_safety_and_says_so() -> None:
    result = classify_docs(doc(), doc(newThing={"x": 1}))
    (change,) = result.changes
    assert change.cls == "S" and change.path == "newThing" and UNKNOWN in change.summary
    other = DataDiffClassifier(network_files=(), schema_file="schema.json").classify(
        {"fixtures/v.json": b'{"a": 1}'}, {"fixtures/v.json": b'{"a": 2}'})
    assert [(c.cls, c.file, c.path) for c in other.changes] == [("S", "fixtures/v.json", "a")]
    assert UNKNOWN in other.changes[0].summary


def test_true_is_not_one() -> None:
    result = classify_docs(doc(chainId=1), doc(chainId=True))
    assert classes_by_path(result) == {"chainId": "S"}


def test_schema_changes_are_api() -> None:
    old = {"type": "object", "additionalProperties": False, "required": ["a"],
           "properties": {"a": {"enum": ["x"]}, "b": {"description": "old"}}}
    new = copy.deepcopy(old)
    new["required"] = ["a", "c"]
    new["additionalProperties"] = True
    new["properties"]["a"]["enum"] = ["x", "y"]
    new["properties"]["b"]["description"] = "new"
    new["properties"]["c"] = {"type": "string"}
    result = dusk_networks_classifier().classify({"schema.json": json.dumps(old).encode()},
                                                 {"schema.json": json.dumps(new).encode()})
    assert result.classes == ("A",)
    summaries = {c.path: c.summary for c in result.changes}
    assert summaries == {
        "additionalProperties": "changed: additional keys rule",
        "properties.a.enum": 'changed: allowed values +"y"',
        "properties.b.description": "changed: wording",
        "properties.c": "added: new key c",
        "required": 'changed: required keys +"c"',
    }


@pytest.mark.parametrize(
    ("old", "new", "flagged"),
    [("0.3.0", "0.3.1", False), ("0.3.0", "0.4.0", True), ("1.2.0", "1.3.0", False), ("1.2.0", "2.0.0", True)],
)
def test_version_bumps(old: str, new: str, flagged: bool) -> None:
    result = dusk_networks_classifier().classify(
        {"package.json": json.dumps({"version": old}).encode()}, {"package.json": json.dumps({"version": new}).encode()})
    assert [c.path for c in result.changes] == (["version"] if flagged else [])
    assert all(c.cls == "A" for c in result.changes)


def test_package_keys() -> None:
    old = {"version": "0.3.0", "exports": {".": "./src/index.js"}, "dependencies": {}, "license": "MPL-2.0"}
    new = {"version": "0.3.0", "exports": {".": "./src/index.js", "./x": "./src/x.js"}, "dependencies": {"x": "1"},
           "license": "MIT"}
    result = dusk_networks_classifier().classify({"package.json": json.dumps(old).encode()},
                                                 {"package.json": json.dumps(new).encode()})
    assert classes_by_path(result) == {'exports["./x"]': "A", "dependencies.x": "A", "license": "S"}
    assert next(c.ref for c in result.changes if c.path == 'exports["./x"]') == 'package:exports["./x"]'


def test_type_declarations() -> None:
    old = {"src/a.d.ts": b"export declare const a: 1;\n", "src/b.d.ts": b"x\n", "src/a.js": b"1"}
    new = {"src/a.d.ts": b"export declare const a: 2;\n", "src/c.d.ts": b"y\n", "src/a.js": b"2"}
    result = dusk_networks_classifier().classify(old, new)
    assert [(c.cls, c.file, c.kind) for c in result.changes] == [
        ("A", "src/a.d.ts", "changed"), ("A", "src/b.d.ts", "removed"), ("A", "src/c.d.ts", "added"),
    ]
    assert result.changes[0].summary == "changed: declared API (+1 -1 lines)"
    # JavaScript is never classified, and the result says so.
    assert any("src/*.js" in item for item in result.not_compared)
    one_side = dusk_networks_classifier().classify({}, {"src/a.d.ts": b"x"})
    assert one_side.changes == () and any(".d.ts" in item for item in one_side.not_compared)


def test_output_is_deterministic() -> None:
    new = doc(chainId=9, label="X", urls={"node": "https://other"})
    shuffled_old = dict(reversed(list(doc().items())))
    first = classify_docs(doc(), new)
    second = classify_docs(shuffled_old, dict(reversed(list(new.items()))))
    assert first == second
    assert [c.path for c in first.changes] == sorted(c.path for c in first.changes)


def test_first_snapshot_has_nothing_to_compare() -> None:
    assert dusk_networks_classifier().classify(None, {"schema.json": b"{}"}).note.startswith("first snapshot")


def test_sync_reports_the_classification(tmp_path: Path) -> None:
    world = World()
    world.publish("0.3.0")
    root = make_root(tmp_path)
    assert run(root, world, "0.3.0").classification.note.startswith("first snapshot")
    files = package_files("0.4.0")
    files["networks/testnet.json"] = b'{"chainId": 2, "name": "testnet", "urls": {"node": "https://n"}}\n'
    world.publish("0.4.0", files=files, commit="d1" * 20)
    result = run(root, world, "0.4.0")
    assert result.classification.classes == ("S",)
    assert any("package.json" in item for item in result.classification.not_compared)


# ---------------------------------------------------------------- check-release


@dataclass
class Remote(World):
    """``World`` plus tag listing by pattern and JSR metadata."""

    jsr: str | None = None
    down: tuple[str, ...] = ()

    def get(self, url: str) -> bytes:
        if any(url.startswith(prefix) for prefix in self.down):
            raise Unreachable(f"{url}: HTTP 503")
        if url == JSR and self.jsr is not None:
            return json.dumps({"scope": "dusk", "name": "networks", "latest": self.jsr}).encode()
        return super().get(url)

    def ls_remote(self, url: str, refs: list[str]) -> dict[str, str]:
        if "git" in self.down:
            raise Unreachable("git ls-remote: Could not resolve host")
        if refs == ["refs/tags/v*"]:
            return {ref: sha for ref, sha in self.refs.items() if ref.startswith("refs/tags/v")}
        return super().ls_remote(url, refs)


def real_files(version: str) -> dict[str, bytes]:
    files = package_files(version)
    files.update(fixture_view(version))
    return files


def locked_at(tmp_path: Path, version: str = "0.2.0") -> tuple[Path, Remote]:
    world = Remote()
    world.publish(version, files=real_files(version))
    root = make_root(tmp_path)
    run(root, world, version)
    world.jsr = version
    return root, world


def test_up_to_date(tmp_path: Path) -> None:
    root, world = locked_at(tmp_path)
    code, report = check_release(root, SPEC, world, world)
    assert (code, report["status"]) == (0, "up-to-date")
    assert report["latest"] == {"npm": "0.2.0", "jsr": "0.2.0", "git": "0.2.0"}
    assert report["warnings"] == [] and report["errors"] == []


def test_newer_release_verified_and_classified(tmp_path: Path) -> None:
    root, world = locked_at(tmp_path)
    world.publish("0.3.0", files=real_files("0.3.0"), commit="d1" * 20)
    world.jsr = "0.3.0"
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    code, report = check_release(root, SPEC, world, world)
    assert (code, report["status"]) == (2, "newer")
    assert report["candidate"]["version"] == "0.3.0" and report["candidate"]["commit"] == "d1" * 20
    assert report["classes"] == ["S", "A"]
    assert ("S", "routes.services[dusk-to-bsc].payoutAddress") in {(c["class"], c["path"]) for c in report["changes"]}
    assert report["changelog"].startswith("## [0.3.0]") and "## [0.2.0]" not in report["changelog"]
    assert any("Sigstore" in item for item in report["not_verified"])
    # Nothing written.
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_verification_failure_is_exit_1(tmp_path: Path) -> None:
    root, world = locked_at(tmp_path)
    github = real_files("0.3.0")
    github["schema.json"] = b"{}"
    world.publish("0.3.0", files=real_files("0.3.0"), github=github, commit="d1" * 20)
    code, report = check_release(root, SPEC, world, world)
    assert (code, report["status"]) == (1, "failed")
    assert any("schema.json in the tarball differs" in e for e in report["errors"])


def test_locked_tarball_tampered_is_exit_1(tmp_path: Path) -> None:
    root, world = locked_at(tmp_path)
    lock = jsonio.load(root / "upstream/dusk-networks.lock.json")
    world.publish("0.3.0", files=real_files("0.3.0"), commit="d1" * 20)
    world.responses[lock["tarball"]["url"]] = make_tarball(real_files("0.3.0"))
    code, report = check_release(root, SPEC, world, world)
    assert code == 1 and any("dist.integrity" in e or "sha512" in e for e in report["errors"])


def test_latest_moved_back_is_exit_1(tmp_path: Path) -> None:
    root, world = locked_at(tmp_path)
    world.publish("0.1.0", commit="d1" * 20)
    code, report = check_release(root, SPEC, world, world)
    assert code == 1 and "older than 0.2.0" in report["errors"][0]


def test_tag_ahead_of_npm_warns(tmp_path: Path) -> None:
    root, world = locked_at(tmp_path)
    world.refs["refs/tags/v0.4.0"] = "d2" * 20
    code, report = check_release(root, SPEC, world, world)
    assert code == 0 and report["latest"]["git"] == "0.4.0"
    assert any("not on npm yet" in w for w in report["warnings"])


@pytest.mark.parametrize("down", [(REGISTRY,), (JSR,), ("git",)])
def test_outage_fails_without_flag_and_warns_with_it(tmp_path: Path, down: tuple[str, ...]) -> None:
    root, world = locked_at(tmp_path)
    world.down = down
    code, report = check_release(root, SPEC, world, world)
    assert (code, report["status"]) == (1, "unreachable")
    code, report = check_release(root, SPEC, world, world, warn_unreachable=True)
    assert code == 0 and any("unreachable" in w for w in report["warnings"])


def test_outage_of_one_signal_still_finds_a_newer_release(tmp_path: Path) -> None:
    root, world = locked_at(tmp_path)
    world.publish("0.3.0", files=real_files("0.3.0"), commit="d1" * 20)
    world.down = (JSR,)
    code, report = check_release(root, SPEC, world, world, warn_unreachable=True)
    assert code == 2 and report["latest"]["jsr"] is None


def test_outage_while_fetching_the_candidate(tmp_path: Path) -> None:
    root, world = locked_at(tmp_path)
    world.publish("0.3.0", files=real_files("0.3.0"), commit="d1" * 20)
    world.down = (f"{REGISTRY}/-/npm/v1/attestations",)
    assert check_release(root, SPEC, world, world)[0] == 1
    code, report = check_release(root, SPEC, world, world, warn_unreachable=True)
    assert (code, report["status"]) == (0, "unreachable")


def test_no_lock(tmp_path: Path) -> None:
    code, report = check_release(make_root(tmp_path), SPEC, Remote(), Remote())
    assert code == 1 and "no lock" in report["errors"][0]


def test_dry_run_from_a_registry_version(tmp_path: Path) -> None:
    root, world = locked_at(tmp_path, "0.3.0")
    world.publish("0.2.0", files=real_files("0.2.0"), commit="d0" * 20)
    world.publish("0.3.0", files=real_files("0.3.0"))
    code, report = check_release(root, SPEC, world, world, base="0.2.0")
    assert code == 2 and report["compared_with"] == {"source": "registry", "version": "0.2.0"}
    assert report["classes"] == ["S", "A"]


def test_cli_check_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                           capsys: pytest.CaptureFixture[str]) -> None:
    root, world = locked_at(tmp_path)
    monkeypatch.setattr(upstream_net, "UrllibHttp", lambda: world)
    monkeypatch.setattr(upstream_net, "SubprocessGit", lambda: world)
    args = ["upstream", "check-release", "--root", str(root)]
    assert cli.main(args) == 0
    assert "up-to-date" in capsys.readouterr().out
    world.publish("0.3.0", files=real_files("0.3.0"), commit="d1" * 20)
    out = tmp_path / "report.json"
    assert cli.main([*args, "--json", "--report-out", str(out)]) == 2
    assert json.loads(capsys.readouterr().out) == json.loads(out.read_text())
    assert cli.main(["upstream", "release-notes", "--report", str(out), "--kind", "title"]) == 0
    assert capsys.readouterr().out == "dusk-networks v0.3.0 (S, A)\n"


def test_git_outage_messages() -> None:
    assert GIT_OUTAGE.search("fatal: unable to access 'https://github.com/x/': Could not resolve host: github.com")
    assert GIT_OUTAGE.search("The requested URL returned error: 503")
    assert not GIT_OUTAGE.search("fatal: repository 'https://github.com/x/' not found")


def test_changelog_section() -> None:
    text = "# Changelog\n\n## [0.3.0]\n\n- a\n\n## 0.2.0\n\n- b\n"
    assert changelog_section(text, "0.3.0") == "## [0.3.0]\n\n- a"
    assert changelog_section(text, "0.2.0") == "## 0.2.0\n\n- b"
    assert changelog_section(text, "0.3") is None
    assert changelog_section("## v1.0.0\nx", "1.0.0") == "## v1.0.0\nx"


# ---------------------------------------------------------------- generator


def snapshot_report(tmp_path: Path) -> dict[str, Any]:
    root, world = locked_at(tmp_path)
    world.publish("0.3.0", files=real_files("0.3.0"), commit="d1" * 20)
    world.jsr = "0.3.0"
    code, report = check_release(root, SPEC, world, world)
    assert code == 2
    # gzip headers carry a timestamp: pin the tarball fields so the snapshot is stable.
    report["candidate"]["tarball"] = {"url": "https://registry.npmjs.org/x.tgz", "integrity": "sha512-pinned",
                                      "sha256": "0" * 64}
    return report


def write_registry(root: Path, consumers: Any) -> None:
    (root / SPEC.consumers_path).write_text(json.dumps(consumers))


def check_snapshot(name: str, text: str) -> None:
    path = SNAPSHOTS / name
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    assert text == path.read_text()


def test_generator_snapshots(tmp_path: Path) -> None:
    report = snapshot_report(tmp_path)
    check_snapshot("issue.md", issue_body(report, None))
    check_snapshot("pr.md", pr_body(report, None, "https://github.com/o/r/issues/7"))
    registry = [
        {"repo": "o/wallet", "path": "package.json", "kind": "npm", "policy": "immediate", "uses": ["S"]},
        {"repo": "o/docs", "path": "deno.json", "kind": "jsr", "policy": "next-release", "uses": ["display.labels"]},
        {"repo": "o/bridge", "path": "package.json", "kind": "npm", "policy": "immediate",
         "uses": ["routes.services", "package:exports"]},
    ]
    consumers = [Consumer(c["repo"], c["path"], c["kind"], c["policy"], tuple(c["uses"])) for c in registry]
    check_snapshot("issue-with-consumers.md", issue_body(report, consumers))


def test_generator_is_stable_and_quotes_upstream_text(tmp_path: Path) -> None:
    report = snapshot_report(tmp_path)
    assert issue_body(report, None) == issue_body(copy.deepcopy(report), None)
    assert title(report) == "dusk-networks v0.3.0 (S, A)"
    body = issue_body(report, None)
    assert NO_REGISTRY in body
    assert "```text\n## [0.3.0]" in body
    report["changelog"] = "```\n@everyone [x](https://evil)\n```"
    assert "````text\n```\n@everyone" in issue_body(report, None)


def test_generator_refuses_a_report_without_a_release(tmp_path: Path) -> None:
    root, world = locked_at(tmp_path)
    _, report = check_release(root, SPEC, world, world)
    with pytest.raises(UpstreamError, match="no verified newer release"):
        render("pr", report, root, SPEC)
    assert "up-to-date" in render("summary", report, root, SPEC)


def test_code_escapes_table_breakers() -> None:
    assert code("a|b") == "`a\\|b`"
    assert code("x`y") == "``x`y``"
    assert code("line\nbreak") == "`line break`"
    assert code("z" * 200, 40).count("…") == 1
    assert fenced("plain") == "```text\nplain\n```"


def test_consumer_filter() -> None:
    change = {"class": "S", "ref": "routes.services[].payoutAddress"}
    assert uses_match("S", change) and uses_match("*", change)
    assert uses_match("routes", change) and uses_match("routes.services", change)
    assert uses_match("routes.services[].payoutAddress", change)
    assert not uses_match("routes.service", change)
    assert not uses_match("D", change) and not uses_match("display", change)
    assert uses_match("schema:", {"class": "A", "ref": "schema:properties.x"})
    assert uses_match("package:exports", {"class": "A", "ref": 'package:exports["./gas"]'})


def test_consumer_registry(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    (root / "upstream").mkdir()
    assert load_consumers(root, SPEC) is None
    write_registry(root, {"consumers": [
        {"repo": "o/b", "path": "p", "kind": "npm", "policy": "immediate", "uses": ["A"]},
        {"repo": "o/a", "path": "p", "kind": "rust-crate", "policy": "next-release", "uses": ["display.labels"]},
    ]})
    consumers = load_consumers(root, SPEC)
    assert consumers is not None and [c.repo for c in consumers] == ["o/a", "o/b"]
    hits = affected(consumers, [{"class": "A", "ref": "schema:required"}])
    assert [c.repo for c, _ in hits] == ["o/b"]
    for bad in ({"x": 1}, [{"repo": "o/a", "path": "p", "kind": "pip", "policy": "immediate", "uses": ["S"]}],
                [{"repo": "o/a", "path": "p", "kind": "npm", "policy": "immediate", "uses": []}]):
        write_registry(root, bad)
        with pytest.raises(UpstreamError):
            load_consumers(root, SPEC)


def test_no_consumer_affected(tmp_path: Path) -> None:
    report = snapshot_report(tmp_path)
    body = issue_body(report, [Consumer("o/docs", "deno.json", "jsr", "next-release", ("D",))])
    assert "No registered consumer uses what changed (1 registered)." in body


FIXTURE_SHA256 = {
    "0.2.0": {
        "package.json": "13c445f4d8311d3b09396e87486ce4e7af0b504480aedc2432d85deafafa0824",
        "schema.json": "9d572436ba194958810fc05a29cd7271969b9a370e4a0a594c865d33c06d0f88",
        "networks/mainnet.json": "d7dd9dfae3edefd9737bf5c7a0b811e92a9229504d077f3b8225e4edc52170cd",
    },
    "0.3.0": {
        "CHANGELOG.md": "68bdecc0fb8c890867ca7f6ba80700ec7985faf3d7861715c0014be1264f6ba8",
        "package.json": "9575a1360e2c82ea6c70f4345e9ad4252566ae09a57974475833e4ff26bac4b4",
        "schema.json": "4bc7cdb717c5a3f5156705c1c0d78e175997c7bb69ab41a2ed8dc49f9b3a9d89",
        "networks/mainnet.json": "eb1f5516715989b656be38b626b0a7367f4d4c3fbcf797d7b18e1f45b2af47fc",
    },
}


@pytest.mark.parametrize("version", sorted(FIXTURE_SHA256))
def test_recorded_fixtures_are_the_published_files(version: str) -> None:
    """The fixtures are byte copies of files in the npm tarballs (0.3.0's equal the locked snapshot)."""
    view = fixture_view(version)
    assert {name: hashlib.sha256(data).hexdigest() for name, data in view.items()} == FIXTURE_SHA256[version]
