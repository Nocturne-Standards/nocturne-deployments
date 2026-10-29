"""End-to-end tests on small throwaway repos."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nocturne_pins import Catalog, jsonio, legacy, write
from nocturne_pins.check import check_repo
from nocturne_pins.names import EntryKey, FullName, NameError_, parse
from nocturne_pins.repo import Repo, RepoError
from nocturne_pins.replay import ReplayError, replay
from nocturne_pins.schema import SCHEMA_FILES, schema_text, validate

A1 = "a1" * 32
A2 = "a2" * 32
K1 = "b1" * 32
TX = ["%02x" % i * 32 for i in range(1, 10)]


def make_repo(root: Path, name: str, visibility: str, entries: list[tuple[str, str]], **extra: Any) -> Repo:
    root.mkdir(parents=True, exist_ok=True)
    (root / "schema").mkdir(exist_ok=True)
    for schema_id, file_name in SCHEMA_FILES.items():
        (root / "schema" / file_name).write_text(schema_text(schema_id))
    index: dict[str, Any] = {
        "schema": "nocturne.index.v1",
        "repo": name,
        "visibility": visibility,
        "networks": {"testnet": {"duskds": {"chain_id": 2, "rpc": "https://node", "explorer": None}}},
        "entries": [
            {"product": p, "network": "testnet", "tier": t,
             "current": f"products/{p}/testnet.{t}.json", "history": f"history/{p}/testnet.{t}.jsonl"}
            for p, t in entries
        ],
    }
    index.update(extra)
    (root / "index.json").write_text(jsonio.dump_file(index))
    repo = Repo.open(root)
    for p, t in entries:
        key = EntryKey(p, "testnet", t)
        (root / "history" / p).mkdir(parents=True, exist_ok=True)
        (root / f"history/{p}/testnet.{t}.jsonl").write_text("")
        (root / "products" / p).mkdir(parents=True, exist_ok=True)
        (root / f"products/{p}/testnet.{t}.json").write_text(jsonio.dump_file(replay(key, [])))
    return Repo.open(root)


def ds_record(cid: str, tx: str, at: str, **extra: Any) -> dict[str, Any]:
    return {"version": "0.1.0", "contract_id": cid, "tx_id": tx, "deployed_at": at, **extra}


def call_record(target: str, fn: str, args: Any, tx: str, at: str) -> dict[str, Any]:
    return {"target_contract_id": target, "fn_name": fn, "args": json.dumps(args), "tx_id": tx, "called_at": at}


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Catalog, Repo, Repo]:
    public = make_repo(tmp_path / "pub", "o/pub", "public", [("atlas", "own"), ("knot", "own"), ("tnst", "public")],
                       legacy={"testnet": {"flat": "testnet.json", "duskds": "duskds/testnet.json", "duskevm": "duskevm/testnet.json"}})
    private = make_repo(tmp_path / "priv", "o/priv", "private", [("wen", "own")],
                        calls=[{"product": "wen", "network": "testnet", "tier": "own", "path": "calls/wen/testnet.own.jsonl"}])
    return Catalog([private, public]), private, public


def test_names() -> None:
    assert parse("knot/knot-registry") == FullName("knot", "knot-registry", "testnet", None)
    assert parse("knot/knot-registry/testnet/own").tier == "own"
    with pytest.raises(NameError_):
        parse("knot/x/testnet/experiment")
    with pytest.raises(NameError_):
        parse("knot")


def test_record_resolve_supersede_and_legacy(repos: tuple[Catalog, Repo, Repo]) -> None:
    catalog, _, public = repos
    pub_catalog = Catalog([public])
    write.record_contract(pub_catalog, "atlas", "duskds", ds_record(A1, TX[0], "2026-01-01T00:00:00Z"), "testnet", "atlas", allow_public_home=True)
    write.record_contract(pub_catalog, "atlas", "duskds", ds_record(A2, TX[1], "2026-01-02T00:00:00Z"), "testnet", allow_public_home=True)
    assert catalog.resolve(parse("atlas/atlas")) == A2
    lines = public.history_lines(EntryKey("atlas", "testnet", "own"))
    assert [json.loads(l)["event"] for l in lines] == ["deployed", "deployed", "superseded"]
    # Recording the same row again changes nothing.
    assert write.record_contract(pub_catalog, "atlas", "duskds", ds_record(A2, TX[1], "2026-01-02T00:00:00Z"), "testnet", allow_public_home=True) == []
    flat = jsonio.load(public.root / "testnet.json")
    assert flat["atlas"]["current"]["contract_id"] == A2
    assert [r["contract_id"] for r in flat["atlas"]["history"]] == [A1]
    assert check_repo(pub_catalog, public) == []


def test_new_contract_needs_product(repos: tuple[Catalog, Repo, Repo]) -> None:
    _, _, public = repos
    with pytest.raises(write.WriteError, match="pass --product"):
        write.record_contract(Catalog([public]), "mystery", "duskds", ds_record(A1, TX[0], "2026-01-01T00:00:00Z"), "testnet", allow_public_home=True)


def test_hand_edit_is_refused(repos: tuple[Catalog, Repo, Repo]) -> None:
    _, _, public = repos
    cat = Catalog([public])
    write.record_contract(cat, "atlas", "duskds", ds_record(A1, TX[0], "2026-01-01T00:00:00Z"), "testnet", "atlas", allow_public_home=True)
    path = public.root / "products/atlas/testnet.own.json"
    doc = jsonio.load(path)
    doc["contracts"]["atlas"]["id"] = A2
    path.write_text(jsonio.dump_file(doc))
    public._cache.clear()
    with pytest.raises(write.WriteError, match="differs from the replay"):
        write.record_contract(cat, "atlas", "duskds", ds_record(A2, TX[1], "2026-01-02T00:00:00Z"), "testnet", allow_public_home=True)
    assert any("differs from the replay" in e for e in check_repo(cat, public))


def test_bad_superseded_does_not_replay() -> None:
    key = EntryKey("atlas", "testnet", "own")
    lines = [
        jsonio.dump_line({"at": "2026-01-01T00:00:00Z", "event": "deployed", "name": "atlas", "layer": "duskds", "id": A1, "record": {}}),
        jsonio.dump_line({"at": "2026-01-01T00:00:00Z", "event": "superseded", "name": "atlas", "id": A2, "by": A1}),
    ]
    with pytest.raises(ReplayError):
        replay(key, lines)


def test_calls_config_vs_demo(repos: tuple[Catalog, Repo, Repo]) -> None:
    catalog, private, public = repos
    write.record_contract(catalog, "pm", "duskds", ds_record(K1, TX[0], "2026-01-01T00:00:00Z"), "testnet", "wen")
    kind, _ = write.record_call(catalog, "pm.init_council", "duskds", call_record(K1, "init_council", "c0ffee", TX[1], "2026-01-01T01:00:00Z"), "testnet")
    assert kind == "wired"
    assert catalog.state(EntryKey("wen", "testnet", "own"))["pm.init_council"]["value"] == "c0ffee"
    kind, lines = write.record_call(catalog, "pm.trade", "duskds", call_record(K1, "trade", {"x": 1}, TX[2], "2026-01-01T02:00:00Z"), "testnet")
    assert kind == "demo" and len(lines) == 1
    assert len(private.history_lines(EntryKey("wen", "testnet", "own"))) == 2
    assert write.classify("knot-warden.execute_service.knot-registry") == "admin"
    assert write.classify("knot-proposals.finalize.accept_scheduler") == "proposal"
    assert write.classify("agent-settlement.pay") == "demo"


def test_refs_and_baked_ids(repos: tuple[Catalog, Repo, Repo]) -> None:
    _, _, public = repos
    cat = Catalog([public])
    write.record_contract(cat, "atlas", "duskds", ds_record(A1, TX[0], "2026-01-01T00:00:00Z"), "testnet", "atlas", allow_public_home=True)
    write.record_contract(cat, "knot-registry-data", "duskds", ds_record(K1, TX[1], "2026-01-01T01:00:00Z", atlas_pin=A1), "testnet", "knot", allow_public_home=True)
    current = public.current(EntryKey("knot", "testnet", "own"))
    assert current["contracts"]["knot-registry-data"]["refs"] == {"atlas": "atlas/atlas/testnet/own"}
    # A baked id that does not match the referenced entry is refused at write time.
    with pytest.raises(write.WriteError, match="does not match"):
        write.record_contract(cat, "knot-registry-data", "duskds", ds_record(K1, TX[2], "2026-01-01T02:00:00Z", atlas_pin=A2), "testnet", allow_public_home=True)
    # A later Atlas makes the baked id stale; the check reports it.
    write.record_contract(cat, "atlas", "duskds", ds_record(A2, TX[3], "2026-01-02T00:00:00Z"), "testnet", allow_public_home=True)
    assert any("baked atlas_pin" in e for e in check_repo(cat, public))


def test_public_may_not_reference_own(repos: tuple[Catalog, Repo, Repo]) -> None:
    _, _, public = repos
    cat = Catalog([public])
    write.record_contract(cat, "atlas", "duskds", ds_record(A1, TX[0], "2026-01-01T00:00:00Z"), "testnet", "atlas", allow_public_home=True)
    with pytest.raises(write.WriteError, match="no atlas/atlas entry a public reader can see"):
        write.record_contract(cat, "tnst-token", "duskds", ds_record(K1, TX[1], "2026-01-01T01:00:00Z", atlas_pin=A1), "testnet", "tnst", allow_public_home=True)


def test_tier_fallback_and_shadowing(tmp_path: Path) -> None:
    public = make_repo(tmp_path / "pub", "o/pub", "public", [("atlas", "own")])
    private = make_repo(tmp_path / "priv", "o/priv", "private", [("atlas", "own")])
    write.record_contract(Catalog([public]), "atlas", "duskds", ds_record(A1, TX[0], "2026-01-01T00:00:00Z"), "testnet", "atlas", allow_public_home=True)
    write.record_contract(Catalog([private]), "atlas", "duskds", ds_record(A2, TX[1], "2026-01-02T00:00:00Z"), "testnet", "atlas")
    assert Catalog([public]).resolve(parse("atlas/atlas")) == A1
    both = Catalog([private, public])
    assert both.resolve(parse("atlas/atlas")) == A2
    with pytest.raises(RepoError):
        both.resolve(parse("atlas/atlas"), reader="public")
    # The private entry must say it shadows the public one.
    assert any("shadows" in e for e in check_repo(both, private))


def test_schema_validator() -> None:
    assert validate({"at": "2026-01-01T00:00:00Z", "event": "note", "text": "x"}, "nocturne.history.v1") == []
    assert validate({"at": "2026-01-01T00:00:00Z", "event": "deployed"}, "nocturne.history.v1") != []
    assert validate({"at": "yesterday", "event": "note", "text": "x"}, "nocturne.history.v1") != []
    assert validate({"at": "2026-01-01T00:00:00Z", "event": "note", "text": "x", "extra": 1}, "nocturne.history.v1") != []


def test_legacy_stale_detected(repos: tuple[Catalog, Repo, Repo]) -> None:
    _, _, public = repos
    cat = Catalog([public])
    write.record_contract(cat, "atlas", "duskds", ds_record(A1, TX[0], "2026-01-01T00:00:00Z"), "testnet", "atlas", allow_public_home=True)
    (public.root / "testnet.json").write_text("{}\n")
    assert legacy.stale(cat, public)
    legacy.write(cat, public)
    assert not legacy.stale(cat, public)


def test_writer_picks_checkout_and_refreshes_all_legacy(tmp_path: Path) -> None:
    lg = {"testnet": {"flat": "testnet.json", "duskds": "duskds/testnet.json", "duskevm": "duskevm/testnet.json"}}
    public = make_repo(tmp_path / "pub", "o/pub", "public", [("knot", "own")], legacy=lg)
    private = make_repo(tmp_path / "priv", "o/priv", "private", [("wen", "own")], legacy=lg)
    both = Catalog([private, public])
    write.record_contract(both, "knot-registry", "duskds", ds_record(K1, TX[0], "2026-01-01T00:00:00Z"), "testnet", "knot")
    write.record_contract(both, "pm", "duskds", ds_record(A1, TX[1], "2026-01-01T01:00:00Z"), "testnet", "wen")
    assert public.current(EntryKey("knot", "testnet", "own"))["contracts"]["knot-registry"]["id"] == K1
    # The private legacy files carry both repos; the public ones only the public repo.
    assert set(jsonio.load(private.root / "testnet.json")) >= {"knot-registry", "pm"}
    assert "pm" not in jsonio.load(public.root / "testnet.json")


def test_out_of_order_event_is_refused(repos: tuple[Catalog, Repo, Repo]) -> None:
    _, _, public = repos
    cat = Catalog([public])
    write.record_contract(cat, "atlas", "duskds", ds_record(A1, TX[0], "2026-01-02T00:00:00Z"), "testnet", "atlas", allow_public_home=True)
    with pytest.raises(write.WriteError, match="older than"):
        write.record_contract(cat, "atlas", "duskds", ds_record(A2, TX[1], "2026-01-01T00:00:00Z"), "testnet", allow_public_home=True)


def test_public_home_never_sees_private_refs(repos: tuple[Catalog, Repo, Repo]) -> None:
    catalog, private, public = repos
    # Public Atlas 0.4-like, private Atlas newer (unreleased); chit-like entry lives in public.
    write.record_contract(Catalog([public]), "atlas", "duskds", ds_record(A1, TX[0], "2026-01-01T00:00:00Z"), "testnet", "atlas", allow_public_home=True)
    (private.root / "index.json").write_text(jsonio.dump_file({**private.index, "entries": private.index["entries"] + [
        {"product": "atlas", "network": "testnet", "tier": "own", "current": "products/atlas/testnet.own.json",
         "history": "history/atlas/testnet.own.jsonl", "shadows": "o/pub"}]}))
    (private.root / "history/atlas").mkdir(parents=True)
    (private.root / "history/atlas/testnet.own.jsonl").write_text("")
    (private.root / "products/atlas").mkdir(parents=True)
    (private.root / "products/atlas/testnet.own.json").write_text(jsonio.dump_file(replay(EntryKey("atlas", "testnet", "own"), [])))
    private = Repo.open(private.root)
    both = Catalog([private, public])
    write.record_contract(both, "atlas", "duskds", ds_record(A2, TX[1], "2026-01-02T00:00:00Z"), "testnet", "atlas")
    assert both.resolve(parse("atlas/atlas")) == A2
    # A knot row baked against the unreleased Atlas must not land in the public repo.
    with pytest.raises(write.WriteError, match="does not match"):
        write.record_contract(both, "knot-registry-data", "duskds", ds_record(K1, TX[2], "2026-01-03T00:00:00Z", atlas_pin=A2), "testnet", "knot")
    assert public.history_lines(EntryKey("knot", "testnet", "own")) == []


def test_public_only_catalog_refuses_writes_by_default(repos: tuple[Catalog, Repo, Repo]) -> None:
    _, _, public = repos
    with pytest.raises(write.WriteError, match="private checkout"):
        write.record_contract(Catalog([public]), "atlas", "duskds", ds_record(A1, TX[0], "2026-01-01T00:00:00Z"), "testnet", "atlas")


def test_replay_rejects_reordered_and_double_superseded() -> None:
    key = EntryKey("atlas", "testnet", "own")
    d1 = jsonio.dump_line({"at": "2026-01-02T00:00:00Z", "event": "note", "text": "b"})
    d0 = jsonio.dump_line({"at": "2026-01-01T00:00:00Z", "event": "note", "text": "a"})
    with pytest.raises(ReplayError, match="older"):
        replay(key, [d1, d0])
    dep = lambda i, at: jsonio.dump_line({"at": at, "event": "deployed", "name": "atlas", "layer": "duskds", "id": i, "record": {}})
    sup = jsonio.dump_line({"at": "2026-01-02T00:00:00Z", "event": "superseded", "name": "atlas", "id": A1, "by": A2})
    replay(key, [dep(A1, "2026-01-01T00:00:00Z"), dep(A2, "2026-01-02T00:00:00Z"), sup])
    with pytest.raises(ReplayError):
        replay(key, [dep(A1, "2026-01-01T00:00:00Z"), dep(A2, "2026-01-02T00:00:00Z"), sup, sup])
