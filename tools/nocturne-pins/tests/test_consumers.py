"""Floor and grace in the lock, the release history, the consumer registry and the sweep (no network)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_upstream import World, make_root, package_files, run, world  # noqa: F401 (fixture)

from nocturne_pins import cli, jsonio
from nocturne_pins.check import check_repo
from nocturne_pins.repo import Catalog, Repo
from nocturne_pins.schema import validate
from nocturne_pins.upstream import consumers as cons
from nocturne_pins.upstream.notes import Consumer, load_consumers
from nocturne_pins.upstream.policy import POLICY, assess, floor_fields
from nocturne_pins.upstream.specs import DUSK_NETWORKS as SPEC
from nocturne_pins.upstream.sync import sync

T0 = "2026-10-30T12:00:00Z"
LOCK: dict[str, Any] = {
    "version": "0.5.2", "verified_at": T0, "latest": "0.5.2", "latest_at": T0,
    "floor": "0.5.0", "grace_until": "2026-11-01T12:00:00Z", "policy": dict(POLICY),
}


def consumer(kind: str = "npm", path: str = "package.json", policy: str = "immediate", status: str = "active",
             visibility: str = "private", repo: str = "o/app") -> Consumer:
    return Consumer(repo, path, kind, policy, ("S",), status, visibility)


# ---------------------------------------------------------------- policy


def test_floor_bootstrap_and_moves() -> None:
    first = floor_fields(None, "0.3.0", (), T0)
    assert first == {"latest": "0.3.0", "latest_at": T0, "floor": "0.3.0",
                     "grace_until": "2026-11-01T12:00:00Z", "policy": POLICY}
    # An old lock without the fields keeps the time the same release was first locked.
    old = {"version": "0.3.0", "verified_at": "2026-10-01T00:00:00Z"}
    assert floor_fields(old, "0.3.0", (), T0)["latest_at"] == "2026-10-01T00:00:00Z"
    assert floor_fields(old, "0.3.0", (), T0)["grace_until"] == "2026-10-03T00:00:00Z"
    lock = {"version": "0.3.0", **first}
    later = "2026-11-10T00:00:00Z"
    d_only = floor_fields(lock, "0.3.1", ("D", "A"), later)
    assert (d_only["floor"], d_only["grace_until"], d_only["latest"], d_only["latest_at"]) == (
        "0.3.0", "2026-11-01T12:00:00Z", "0.3.1", later)
    safety = floor_fields(lock, "0.4.0", ("S",), later)
    assert (safety["floor"], safety["grace_until"]) == ("0.4.0", "2026-11-12T00:00:00Z")
    assert floor_fields(lock, "0.3.0", ("S",), later) == first  # same release: nothing moves
    down = floor_fields({"version": "0.4.0", **safety}, "0.3.0", (), later)
    assert down["floor"] == "0.3.0" and down["latest"] == "0.3.0"


@pytest.mark.parametrize(("pinned", "policy", "now", "status", "text"), [
    ("0.5.2", "immediate", T0, "ok", "locked version"),
    ("0.5.1", "immediate", T0, "lag-warn", "within max_lag"),
    ("0.4.9", "immediate", T0, "lag-warn", "update before 2026-11-01T12:00:00Z"),
    ("0.4.9", "immediate", "2026-11-01T12:00:00Z", "below-floor-past-grace", "grace ended"),
    ("0.4.9", "next-release", "2026-11-02T00:00:00Z", "lag-warn", "policy next-release"),
    ("0.5.0", "immediate", "2026-12-15T00:00:00Z", "lag-past-max", "more than 30 days"),
    ("0.5.0", "next-release", "2026-12-15T00:00:00Z", "lag-past-max", "more than 30 days"),
    ("0.4.9", "next-release", "2026-12-15T00:00:00Z", "lag-past-max", "past max_lag"),
    ("0.6.0", "immediate", T0, "ahead", "not verified"),
    ("^0.5.2", "immediate", T0, "not-exact-pin", "not an exact"),
])
def test_assess(pinned: str, policy: str, now: str, status: str, text: str) -> None:
    result = assess(pinned, policy, LOCK, now)
    assert result.status == status and text in result.detail


def test_assess_minor_lag() -> None:
    lock = {**LOCK, "version": "0.7.0", "latest": "0.7.0", "floor": "0.3.0"}
    result = assess("0.5.9", "next-release", lock, T0)
    assert result.status == "lag-past-max" and "more than 1 minor" in result.detail
    assert "within max_lag" in assess("0.6.0", "next-release", lock, T0).detail
    assert assess("0.2.0", "immediate", lock, "2026-11-05T00:00:00Z").status == "below-floor-past-grace"


# ---------------------------------------------------------------- pin forms


def test_npm_pin() -> None:
    assert cons.npm_pin('{"dependencies": {"@dusk-network/networks": "0.3.0"}}').version == "0.3.0"
    caret = cons.npm_pin('{"dependencies": {"@dusk-network/networks": "^0.3.0"}}')
    assert caret.found and caret.problem and caret.version is None
    split = cons.npm_pin('{"dependencies": {"@dusk-network/networks": "0.3.0"},'
                         ' "devDependencies": {"@dusk-network/networks": "0.2.0"}}')
    assert "disagree" in split.problem
    assert not cons.npm_pin('{"dependencies": {"left-pad": "1.0.0"}}').found
    assert not cons.npm_pin("not json").found


def test_jsr_pin() -> None:
    deno = {"imports": {"@dusk/networks": "jsr:@dusk/networks@0.3.0",
                        "@dusk/networks/": "jsr:/@dusk/networks@0.3.0/", "x": "npm:x@1"}}
    assert cons.jsr_pin(json.dumps(deno)).version == "0.3.0"
    assert cons.jsr_pin(json.dumps({"imports": {"n": "jsr:@dusk/networks@^0.3.0"}})).problem
    assert not cons.jsr_pin(json.dumps({"imports": {"n": "jsr:@std/path@1.0.0"}})).found


GIT = "https://github.com/Nocturne-Standards/nocturne-deployments"


def test_cargo_pin() -> None:
    inline = f'[dependencies]\ndusk-networks = {{ git = "{GIT}", tag = "nocturne-deployments-v0.4.0" }}\n'
    assert cons.cargo_pin(inline).tag == "nocturne-deployments-v0.4.0"
    table = f'[workspace.dependencies.dusk-networks]\ngit = "{GIT}.git"\ntag = "nocturne-deployments-v0.4.0"\n'
    assert cons.cargo_pin(table).tag == "nocturne-deployments-v0.4.0"
    rev = f'[dependencies]\nnocturne-deployments = {{ git = "{GIT}", rev = "5811377", optional = true }}\n'
    assert "rev = 5811377" in cons.cargo_pin(rev).problem
    bad = f'[dependencies]\ndusk-networks = {{ git = "{GIT}", tag = "v0.4.0" }}\n'
    assert "is not nocturne-deployments-vX.Y.Z" in cons.cargo_pin(bad).problem
    other = '[dependencies]\ndusk-networks = { git = "https://github.com/evil/nocturne-deployments", tag = "x" }\n'
    assert not cons.cargo_pin(other).found


def test_vendored_pin() -> None:
    assert cons.vendored_pin("0.3.0\n").version == "0.3.0"
    assert cons.vendored_pin("0.3\n").problem
    assert cons.pin_file(consumer("vendored", "docs/dusk/")) == "docs/dusk/VERSION"


# ---------------------------------------------------------------- sweep


class FakeSource:
    label = "fake"

    def __init__(self, files: dict[tuple[str, str], bytes | Exception], locks: dict[str, dict[str, Any]]) -> None:
        self.files = files
        self.locks = locks

    def read(self, c: Consumer, path: str) -> bytes | None:
        value = self.files.get((c.repo, path))
        if isinstance(value, Exception):
            raise value
        return value

    def lock_at(self, tag: str) -> dict[str, Any] | None:
        return self.locks.get(tag)


def npm(version: str) -> bytes:
    return json.dumps({"dependencies": {"@dusk-network/networks": version}}).encode()


def test_sweep_statuses() -> None:
    late = "2026-11-02T00:00:00Z"
    registry = [
        consumer(repo="o/ok"), consumer(repo="o/lag"), consumer(repo="o/floor"), consumer(repo="o/range"),
        consumer(repo="o/secret"), consumer(repo="o/gone"), consumer(repo="o/none"),
        consumer("rust-crate", "Cargo.toml", repo="o/rust"), consumer("rust-crate", "Cargo.toml", repo="o/old"),
        consumer(repo="o/plan", status="planned"), consumer(repo="o/plan-pins", status="planned"),
        consumer(repo="o/soft", policy="next-release"),
    ]
    tag = 'dusk-networks = {{ git = "{}", tag = "nocturne-deployments-v{}" }}\n'
    files: dict[tuple[str, str], bytes | Exception] = {
        ("o/ok", "package.json"): npm("0.5.2"),
        ("o/lag", "package.json"): npm("0.5.1"),
        ("o/floor", "package.json"): npm("0.4.0"),
        ("o/range", "package.json"): npm("~0.5.2"),
        ("o/secret", "package.json"): cons.Unreadable("403"),
        ("o/none", "package.json"): b"{}",
        ("o/rust", "Cargo.toml"): ("[dependencies]\n" + tag.format(GIT, "0.9.0")).encode(),
        ("o/old", "Cargo.toml"): ("[dependencies]\n" + tag.format(GIT, "0.2.0")).encode(),
        ("o/plan-pins", "package.json"): npm("0.4.0"),
        ("o/soft", "package.json"): npm("0.4.0"),
    }
    source = FakeSource(files, {"nocturne-deployments-v0.9.0": {"version": "0.5.2"}})
    rows = {r.consumer.repo: r for r in cons.sweep(registry, LOCK, source, late)}
    status = {repo: row.status for repo, row in rows.items()}
    assert status == {
        "o/ok": "ok", "o/lag": "lag-warn", "o/floor": "below-floor-past-grace", "o/range": "not-exact-pin",
        "o/secret": "unreadable", "o/gone": "no-pin", "o/none": "no-pin", "o/rust": "ok", "o/old": "no-pin",
        "o/plan": "planned", "o/plan-pins": "below-floor-past-grace", "o/soft": "lag-warn",
    }
    assert rows["o/rust"].pinned == "nocturne-deployments-v0.9.0 = 0.5.2"
    assert "predates" in rows["o/old"].detail
    assert "set status active" in rows["o/plan-pins"].detail
    assert cons.exit_code(list(rows.values())) == 1
    assert cons.exit_code([rows["o/ok"], rows["o/plan"]]) == 0
    assert cons.exit_code([rows["o/ok"], rows["o/lag"]]) == 2


def test_gh_source_maps_private_404_to_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 1, b"", b"gh: Not Found (HTTP 404)\n")

    monkeypatch.setattr(cons.subprocess, "run", fake_run)
    source = cons.GhApiSource("Nocturne-Standards/nocturne-deployments", SPEC.lock_path)
    with pytest.raises(cons.Unreadable, match="cannot read this private repo"):
        source.read(consumer(), "package.json")
    assert source.read(consumer(visibility="public"), "package.json") is None
    assert source.lock_at("nocturne-deployments-v0.1.0") is None

    def forbidden(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 1, b"", b"gh: Resource not accessible (HTTP 403)\n")

    monkeypatch.setattr(cons.subprocess, "run", forbidden)
    with pytest.raises(cons.Unreadable, match="HTTP 403"):
        source.read(consumer(visibility="public"), "package.json")


def test_markdown_and_issue_update() -> None:
    registry = [consumer(repo="o/a"), consumer(repo="o/b")]
    source = FakeSource({("o/a", "package.json"): npm("0.5.2"), ("o/b", "package.json"): npm("0.5.1")}, {})
    rows = cons.sweep(registry, LOCK, source, T0)
    text = cons.markdown(SPEC, LOCK, rows, source.label)
    assert text == cons.markdown(SPEC, LOCK, rows, source.label)
    assert "2 consumers: 1 lag-warn, 1 ok." in text and "| `o/a` | `package.json` |" in text
    body = "\n".join([
        "Intro", "", "## Consumers", "",
        "- [ ] `o/a` `package.json` (npm, immediate): S; within 48 h. Uses `x`",
        "- [ ] `o/b` `package.json` (npm, immediate): S; within 48 h. Uses `x`",
        "", "## Changes", "", "- [ ] `o/a` `package.json` outside the section stays", "",
    ])
    once = cons.update_issue(body, rows, "0.5.2", LOCK)
    assert "- [x] `o/a` `package.json` (npm" in once and "- [ ] `o/b` `package.json` (npm" in once
    assert "- [ ] `o/a` `package.json` outside the section stays" in once
    assert once.count(cons.SWEEP_START) == 1 and "## Consumer sweep" in once
    assert cons.update_issue(once, rows, "0.5.2", LOCK) == once
    event = {"version": "0.5.2", "at": T0, "from": "0.5.1", "classes": ["S"], "floor": "0.5.0",
             "grace_until": "2026-11-01T12:00:00Z"}
    fresh = cons.new_issue("dusk-networks v0.5.2 consumers", "0.5.2", rows, LOCK, event)
    assert "diff classes: S" in fresh and "- [x] `o/a` `package.json` (npm, immediate)" in fresh
    assert "- [ ] `o/b` `package.json`" in fresh and fresh.count(cons.SWEEP_START) == 1
    lagging = cons.sweep(registry, LOCK, source, "2026-12-15T00:00:00Z")
    assert [r.status for r in lagging] == ["ok", "lag-past-max"] and cons.exit_code(lagging) == 1
    assert cons.issue_version("dusk-networks v0.5.2 (S, A)") == "0.5.2"


# ---------------------------------------------------------------- sync: lock fields and history


def files_with(version: str, testnet: bytes) -> dict[str, bytes]:
    files = package_files(version)
    files["networks/testnet.json"] = testnet
    return files


def history(root: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in jsonio.read_lines(root / SPEC.history_path)]


def errors_of(root: Path) -> list[str]:
    home = Repo.open(root)
    return check_repo(Catalog([home]), home)


def test_sync_floor_and_history(tmp_path: Path, world: World) -> None:
    root = make_root(tmp_path)
    run(root, world, "0.3.0")
    lock = jsonio.load(root / SPEC.lock_path)
    assert (lock["latest"], lock["floor"], lock["grace_until"]) == ("0.3.0", "0.3.0", "2026-11-01T12:00:00Z")
    assert [e["version"] for e in history(root)] == ["0.3.0"] and history(root)[0]["from"] is None
    assert errors_of(root) == []

    # D change (label): floor stays, latest moves, one event.
    labelled = b'{"chainId": 2, "name": "testnet", "label": "T"}\n'
    world.publish("0.3.1", files=files_with("0.3.1", labelled), commit="d1" * 20)
    sync(root, SPEC, "0.3.1", world, world, clock=lambda: "2026-11-05T00:00:00Z")
    lock = jsonio.load(root / SPEC.lock_path)
    assert (lock["latest"], lock["floor"], lock["latest_at"]) == ("0.3.1", "0.3.0", "2026-11-05T00:00:00Z")
    assert history(root)[-1]["classes"] == ["D"] and history(root)[-1]["from"] == "0.3.0"

    # S change (a core contract): floor moves with a 48 h grace.
    testnet = b'{"chainId": 2, "name": "testnet", "label": "T", "contracts": {"transfer": "aa"}}\n'
    world.publish("0.4.0", files=files_with("0.4.0", testnet), commit="d2" * 20)
    sync(root, SPEC, "0.4.0", world, world, clock=lambda: "2026-11-06T00:00:00Z")
    lock = jsonio.load(root / SPEC.lock_path)
    assert (lock["floor"], lock["grace_until"]) == ("0.4.0", "2026-11-08T00:00:00Z")
    assert history(root)[-1]["classes"] == ["S"]

    # Re-sync: nothing written, no event.
    before = (root / SPEC.history_path).read_bytes()
    assert not sync(root, SPEC, "0.4.0", world, world, clock=lambda: "2026-12-01T00:00:00Z").changed
    assert (root / SPEC.history_path).read_bytes() == before
    assert errors_of(root) == []
    for event in history(root):
        assert validate(event, "nocturne.upstream-history.v1") == []


def test_history_checks(tmp_path: Path, world: World) -> None:
    root = make_root(tmp_path)
    run(root, world, "0.3.0")
    log = root / SPEC.history_path
    good = log.read_text()
    event = json.loads(good)
    log.write_text(json.dumps(event) + "\n")
    assert any("canonical form" in e for e in errors_of(root))
    log.write_text(jsonio.dump_line({**event, "floor": "0.2.0"}) + "\n")
    assert any("last event floor" in e for e in errors_of(root))
    log.write_text(jsonio.dump_line({**event, "event": "released"}) + "\n")
    assert any("released" in e for e in errors_of(root))
    log.unlink()
    assert any("missing or empty" in e for e in errors_of(root))
    log.write_text(good)
    lock = jsonio.load(root / SPEC.lock_path)
    del lock["grace_until"]
    (root / SPEC.lock_path).write_text(jsonio.dump_file(lock))
    assert any("missing 'grace_until'" in e for e in errors_of(root))


def test_registry_check(tmp_path: Path, world: World) -> None:
    root = make_root(tmp_path)
    run(root, world, "0.3.0")
    item = {"repo": "o/a", "path": "package.json", "kind": "npm", "policy": "immediate", "uses": ["S"],
            "status": "planned", "visibility": "private"}
    doc = {"schema": "nocturne.upstream-consumers.v1", "upstream": "dusk-networks", "consumers": [item]}
    (root / SPEC.consumers_path).write_text(json.dumps(doc))
    assert errors_of(root) == []
    loaded = load_consumers(root, SPEC)
    assert loaded is not None and loaded[0].status == "planned"
    for bad, text in (({**item, "path": "../x"}, "does not match"), ({**item, "uses": []}, "uses is empty"),
                      ({**item, "kind": "pip"}, "not in")):
        (root / SPEC.consumers_path).write_text(json.dumps({**doc, "consumers": [bad]}))
        assert any(text in e for e in errors_of(root)), bad
    (root / SPEC.consumers_path).write_text(json.dumps({**doc, "consumers": [item, item]}))
    assert any("listed twice" in e for e in errors_of(root))


def test_public_repo_has_no_registry() -> None:
    # Product names stay private: the registry lives in the private companion repo.
    repo_root = Path(__file__).resolve().parents[3]
    assert not (repo_root / SPEC.consumers_path).exists()
    assert not (repo_root / SPEC.consumers_md_path).exists()


# ---------------------------------------------------------------- CLI offline


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def test_cli_offline(tmp_path: Path, world: World, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_root(tmp_path)
    run(root, world, "0.3.0")
    git(root, "init", "-q")
    git(root, "-c", "user.name=t", "-c", "user.email=t@e", "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "x")
    git(root, "tag", "nocturne-deployments-v0.9.0")
    siblings = tmp_path / "src"
    (siblings / "app").mkdir(parents=True)
    (siblings / "app" / "package.json").write_bytes(npm("0.3.0"))
    (siblings / "crate").mkdir()
    (siblings / "crate" / "Cargo.toml").write_text(
        f'[dependencies]\ndusk-networks = {{ git = "{GIT}", tag = "nocturne-deployments-v0.9.0" }}\n')
    registry = {"schema": "nocturne.upstream-consumers.v1", "upstream": "dusk-networks", "consumers": [
        {"repo": "o/app", "path": "package.json", "kind": "npm", "policy": "immediate", "uses": ["S"],
         "status": "active", "visibility": "private"},
        {"repo": "o/crate", "path": "Cargo.toml", "kind": "rust-crate", "policy": "immediate", "uses": ["S"],
         "status": "active", "visibility": "private"},
        {"repo": "o/missing", "path": "deno.json", "kind": "jsr", "policy": "next-release", "uses": ["D"],
         "status": "planned", "visibility": "private"},
    ]}
    private = tmp_path / "private"
    (private / "upstream").mkdir(parents=True)
    (private / SPEC.consumers_path).write_text(json.dumps(registry))
    code = cli.main(["upstream", "consumers", "--root", str(private), "--lock-root", str(root), "--offline",
                     "--source-root", str(siblings), "--write", "--json", "--now", "2026-10-31T00:00:00Z",
                     "--issue-title", "dusk-networks v0.3.0 consumers", "--new-issue-body", str(tmp_path / "i.md")])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [(c["repo"], c["status"], c["pinned"]) for c in out["consumers"]] == [
        ("o/app", "ok", "0.3.0"), ("o/crate", "ok", "nocturne-deployments-v0.9.0 = 0.3.0"),
        ("o/missing", "planned", None)]
    assert "read through local checkouts" in (private / "upstream/CONSUMERS.md").read_text()
    assert not (root / "upstream/CONSUMERS.md").exists()
    issue = (tmp_path / "i.md").read_text()
    assert "- [x] `o/app` `package.json`" in issue and "- [ ] `o/missing` `deno.json`" in issue
    (siblings / "app" / "package.json").write_bytes(npm("0.2.0"))
    common = ["upstream", "consumers", "--root", str(private), "--lock-root", str(root), "--offline",
              "--source-root", str(siblings)]
    assert cli.main([*common, "--now", "2026-11-05T00:00:00Z"]) == 1
    assert cli.main([*common, "--now", "yesterday"]) == 1
    assert cli.main(["upstream", "consumers", "--root", str(root), "--offline"]) == 1  # no registry here
