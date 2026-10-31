"""Refs from wiring values and record fields to Dusk's values; check 3-5 as warnings (offline)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nocturne_pins import Catalog, cli, dusk_names, dusk_refs, write
from nocturne_pins.check import check_repo
from nocturne_pins.names import EntryKey, parse
from nocturne_pins.repo import Repo
from nocturne_pins.schema import validate

from test_dusk_names import MESSENGER, put_history, with_base
from test_pins import call_record, ds_record

KNOT = EntryKey("knot", "testnet", "own")
L2_MESSENGER = "0x4200000000000000000000000000000000000007"
OURS = "e1" * 32
OLD = "0d" * 32
SOZU_POOL = "72883945ac1aa032a88543aacc9e358d1dfef07717094c05296ce675f23078f2"
STAKE = "02" + "00" * 31


def repo(tmp_path: Path) -> Repo:
    return with_base(tmp_path, [("knot", "own")])


def checked(home: Repo) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    errors = check_repo(Catalog([home]), home, warnings)
    return errors, warnings


def lines(home: Repo, key: EntryKey = KNOT) -> list[dict[str, Any]]:
    return [json.loads(line) for line in home.history_lines(key)]


def wired(key: str, value: Any, at: str = "2026-01-02T00:00:00Z", **extra: Any) -> dict[str, Any]:
    name, fn = key.split(".")[:2]
    return {"at": at, "event": "wired", "name": name, "layer": "duskds", "key": key, "fn": fn,
            "sets": {key: value}, **extra, "record": {"fn_name": fn}}


def deploy(name: str = "knot-registry", record: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {"at": "2026-01-01T00:00:00Z", "event": "deployed", "name": name, "layer": "duskds", "id": OURS,
            **extra, "record": record or {"contract_id": OURS}}


def call(home: Repo, key: str, value: Any, warnings: list[str] | None = None) -> list[str]:
    record = call_record(OURS, key.split(".")[1], value, "aa" * 32, "2026-01-02T00:00:00Z")
    _, new = write.record_call(Catalog([home]), key, "duskds", record, "testnet", "knot", "own", True, warnings)
    return new


# --- tables ------------------------------------------------------------------------------

@pytest.mark.parametrize(("key", "name"), [
    ("knot-registry.init_l1_messenger_contract", "evm-l1-cross-domain-messenger"),
    ("compliance-gate.init_l1_messenger_contract", "evm-l1-cross-domain-messenger"),
    ("chit-session-projector.init_messenger", "evm-l1-cross-domain-messenger"),
    ("chit-session-projector.init_messenger.second", "evm-l1-cross-domain-messenger"),
    ("knot-registry.init_guardian", None),
    ("init_messenger", None),
])
def test_wiring_table(key: str, name: str | None) -> None:
    assert dusk_refs.wiring_name(key) == name


def test_table_names_exist() -> None:
    names = {name for _, name in dusk_refs.WIRING_REFS} | {n for row in dusk_refs.RECORD_REFS for n in row.names}
    assert names <= set(dusk_names.BY_NAME)


def test_chain_ids_resolve_as_names() -> None:
    real = Catalog([Repo.open(Path(__file__).resolve().parents[3])])
    assert real.resolve(parse("dusk/chain-id/testnet/public")) == "2"
    assert real.resolve(parse("dusk/evm-chain-id/testnet/public")) == "745"


# --- writers -----------------------------------------------------------------------------

def test_record_call_writes_refs_and_resolved(tmp_path: Path) -> None:
    home = repo(tmp_path)
    warnings: list[str] = []
    new = call(home, "knot-registry.init_l1_messenger_contract", MESSENGER, warnings)
    event = json.loads(new[0])
    key = "knot-registry.init_l1_messenger_contract"
    assert event["refs"] == {key: "dusk/evm-l1-cross-domain-messenger/testnet/public"}
    assert event["resolved"] == {key: MESSENGER}
    assert list(event)[-1] == "record"
    assert validate(event, "nocturne.history.v1") == []
    assert warnings == []
    assert checked(home) == ([], [])


def test_record_call_other_value_is_recorded_without_ref(tmp_path: Path) -> None:
    home = repo(tmp_path)
    warnings: list[str] = []
    event = json.loads(call(home, "knot-registry.init_messenger", OTHER := "ab" * 32, warnings)[0])
    assert "refs" not in event and event["sets"] == {"knot-registry.init_messenger": OTHER}
    assert len(warnings) == 1 and "is not dusk/evm-l1-cross-domain-messenger/testnet/public" in warnings[0]


def test_record_call_outside_table_has_no_refs(tmp_path: Path) -> None:
    home = repo(tmp_path)
    event = json.loads(call(home, "knot-registry.init_guardian", MESSENGER)[0])
    assert "refs" not in event
    # A Dusk id under a key the table does not know: check 4 warns.
    _, warnings = checked(home)
    assert len(warnings) == 1 and "state.knot-registry.init_guardian" in warnings[0]


def test_record_contract_record_fields(tmp_path: Path) -> None:
    home = repo(tmp_path)
    record = {"address": "0x" + "ee" * 20, "deployed_block": 1, "deployed_at": "2026-01-01T00:00:00Z",
              "messenger": L2_MESSENGER, "dusk_chain_id": 2}
    warnings: list[str] = []
    new = write.record_contract(Catalog([home]), "knot-evm-root", "duskevm", record, "testnet", "knot", "own", True, warnings)
    event = json.loads(new[0])
    assert event["refs"] == {"messenger": "dusk/evm-predeploy-l2-cross-domain-messenger/testnet/public",
                             "dusk_chain_id": "dusk/chain-id/testnet/public"}
    assert event["resolved"] == {"messenger": L2_MESSENGER, "dusk_chain_id": "2"}
    assert home.current(KNOT)["contracts"]["knot-evm-root"]["refs"] == event["refs"]
    assert warnings == []
    assert checked(home) == ([], [])


def test_record_contract_other_values(tmp_path: Path) -> None:
    home = repo(tmp_path)
    record = {"address": "0x" + "ee" * 20, "deployed_at": "2026-01-01T00:00:00Z",
              "messenger": "0x" + "12" * 20, "dusk_chain_id": 3}
    warnings: list[str] = []
    new = write.record_contract(Catalog([home]), "knot-evm-root", "duskevm", record, "testnet", "knot", "own", True, warnings)
    assert "refs" not in json.loads(new[0])
    # Our own messenger is not worth a word; a chain id other than Dusk's is.
    assert warnings == ["knot-evm-root: record dusk_chain_id = 3 is not dusk/chain-id/testnet/public; no ref written"]


def test_atlas_pin_and_dusk_refs_together(tmp_path: Path) -> None:
    home = with_base(tmp_path, [("atlas", "own"), ("knot", "own")])
    catalog = Catalog([home])
    write.record_contract(catalog, "atlas", "duskds", ds_record("a1" * 32, "01" * 32, "2026-01-01T00:00:00Z"),
                          "testnet", "atlas", "own", True)
    record = ds_record(OURS, "02" * 32, "2026-01-01T00:00:01Z", atlas_pin="a1" * 32, messenger=MESSENGER)
    event = json.loads(write.record_contract(catalog, "knot-registry", "duskds", record, "testnet", "knot", "own", True)[0])
    assert event["refs"] == {"atlas": "atlas/atlas/testnet/own",
                             "messenger": "dusk/evm-l1-cross-domain-messenger/testnet/public"}


# --- check 3: refs resolve and still match ------------------------------------------------

def test_known_id_without_ref_warns_and_allowlist_quiets_it(tmp_path: Path) -> None:
    home = repo(tmp_path)
    put_history(home, KNOT, [deploy(), wired("knot-registry.init_messenger", MESSENGER)])
    errors, warnings = checked(home)
    assert errors == []
    assert len(warnings) == 1
    assert "state.knot-registry.init_messenger: 63cd2c930162… is dusk/evm-l1-cross-domain-messenger/testnet/public" in warnings[0]
    allow = {"schema": "nocturne.dusk-refs-allow.v1", "allow": [
        {"check": "known-id", "entry": "knot/testnet/own", "where": "state.knot-registry.init_messenger",
         "value": MESSENGER.upper(), "reason": "kept on purpose for this test case"},
        {"check": "known-id", "entry": "knot/testnet/own", "where": "state.knot-registry.other",
         "value": MESSENGER, "reason": "matches nothing, so check says so"},
    ]}
    assert home.root is not None
    (home.root / dusk_refs.ALLOW_FILE).write_text(json.dumps(allow))
    errors, warnings = checked(home)
    assert errors == []
    assert warnings == [f"{dusk_refs.ALLOW_FILE}: allow[1] (known-id knot/testnet/own state.knot-registry.other) matches nothing; remove it"]


def test_bad_allowlist_fails(tmp_path: Path) -> None:
    home = repo(tmp_path)
    assert home.root is not None
    (home.root / dusk_refs.ALLOW_FILE).write_text(json.dumps({"schema": "nocturne.dusk-refs-allow.v1", "allow": [
        {"check": "known-id", "entry": "knot/testnet/own", "where": "state.x", "value": MESSENGER, "reason": "short"}]}))
    errors, _ = checked(home)
    assert any("reason" in e for e in errors)


def test_nested_and_prefixed_ids_and_records_are_scanned(tmp_path: Path) -> None:
    home = repo(tmp_path)
    put_history(home, KNOT, [
        deploy(record={"contract_id": OURS, "hub": "0x" + SOZU_POOL.upper()}),
        wired("knot-registry.init_pool", {"pool": {"id": SOZU_POOL}, "other": "x" + SOZU_POOL}),
    ])
    _, warnings = checked(home)
    assert len(warnings) == 2
    assert "state.knot-registry.init_pool" in warnings[0]
    assert "contracts.knot-registry.record.hub" in warnings[1] and "dusk/sozu-pool/testnet/public" in warnings[1]


def test_dusk_changed_the_id(tmp_path: Path) -> None:
    home = repo(tmp_path)
    key = "knot-registry.init_messenger"
    ref = "dusk/evm-l1-cross-domain-messenger/testnet/public"
    put_history(home, KNOT, [deploy(), wired(key, OLD, refs={key: ref}, resolved={key: OLD})])
    _, warnings = checked(home)
    assert len(warnings) == 1
    assert f"Dusk changed {ref} in 0.3.0" in warnings[0] and "re-wire, or note the new id" in warnings[0]
    assert "history/knot/testnet.own.jsonl:2 resolved 0d0d0d0d0d0d…" in warnings[0]
    assert home.root is not None
    (home.root / dusk_refs.ALLOW_FILE).write_text(json.dumps({"schema": "nocturne.dusk-refs-allow.v1", "allow": [
        {"check": "stale-ref", "entry": "knot/testnet/own", "where": f"state.{key}", "value": OLD,
         "reason": "the old messenger stays wired until the next deploy"}]}))
    assert checked(home) == ([], [])


def test_rewire_without_ref_drops_coverage(tmp_path: Path) -> None:
    home = repo(tmp_path)
    key = "knot-registry.init_messenger"
    ref = "dusk/evm-l1-cross-domain-messenger/testnet/public"
    put_history(home, KNOT, [
        deploy(),
        wired(key, OLD, refs={key: ref}, resolved={key: OLD}),
        wired(key, MESSENGER, at="2026-01-03T00:00:00Z"),
    ])
    _, warnings = checked(home)
    assert len(warnings) == 1 and "copied without a ref" in warnings[0]


def test_redeploy_drops_record_coverage(tmp_path: Path) -> None:
    home = repo(tmp_path)
    ref = "dusk/evm-l1-cross-domain-messenger/testnet/public"
    put_history(home, KNOT, [
        deploy(record={"contract_id": OURS, "messenger": MESSENGER}, refs={"messenger": ref}, resolved={"messenger": MESSENGER}),
    ])
    assert checked(home) == ([], [])
    events = lines(home) + [{"at": "2026-01-05T00:00:00Z", "event": "deployed", "name": "knot-registry", "layer": "duskds",
                             "id": "e2" * 32, "record": {"contract_id": "e2" * 32, "messenger": MESSENGER}},
                            {"at": "2026-01-05T00:00:00Z", "event": "superseded", "name": "knot-registry", "id": OURS, "by": "e2" * 32}]
    put_history(home, KNOT, events)
    _, warnings = checked(home)
    assert len(warnings) == 1 and "contracts.knot-registry.record.messenger" in warnings[0]


def test_unresolvable_ref_in_a_log_warns(tmp_path: Path) -> None:
    home = repo(tmp_path)
    put_history(home, KNOT, [deploy(), {"at": "2026-01-02T00:00:00Z", "event": "note", "text": "x",
                                        "refs": {"state.k.init_x": "dusk/no-such-name/testnet/public"},
                                        "resolved": {"state.k.init_x": OLD}}])
    errors, warnings = checked(home)
    assert errors == []
    assert len(warnings) == 1 and "history/knot/testnet.own.jsonl:2: ref state.k.init_x" in warnings[0]
    assert "does not resolve" in warnings[0]


# --- check 5: no refs to live-read values --------------------------------------------------

def test_wired_ref_to_fallback_fails(tmp_path: Path) -> None:
    home = repo(tmp_path)
    key = "knot-registry.init_pool"
    put_history(home, KNOT, [deploy(), wired(key, SOZU_POOL, refs={key: "dusk/sozu-pool/testnet/public"},
                                             resolved={key: SOZU_POOL})])
    errors, _ = checked(home)
    assert len(errors) == 1 and "fallback Dusk says to read live" in errors[0]


# --- past rows -----------------------------------------------------------------------------

def test_annotate_past_rows(tmp_path: Path) -> None:
    home = repo(tmp_path)
    record = {"address": "0x" + "ee" * 20, "messenger": L2_MESSENGER, "dusk_chain_id": 2}
    put_history(home, KNOT, [
        deploy(),
        wired("knot-registry.init_l1_messenger_contract", MESSENGER, tx="ab" * 32),
        {"at": "2026-01-03T00:00:00Z", "event": "deployed", "name": "knot-evm-root", "layer": "duskevm",
         "id": record["address"], "record": record},
        wired("knot-registry.init_guardian", OURS, at="2026-01-04T00:00:00Z"),
    ])
    assert home.root is not None
    before = (home.root / "history/knot/testnet.own.jsonl").read_bytes()
    assert len(checked(home)[1]) == 2
    catalog = Catalog([home])
    planned = dusk_refs.plan(catalog, home, "2026-02-01T00:00:00Z")
    assert [(a.line, a.event["text"]) for a in planned] == [
        (2, "Dusk refs for line 2 (wired knot-registry.init_l1_messenger_contract, tx abababababab)"),
        (3, "Dusk refs for line 3 (deployed knot-evm-root)"),
    ]
    assert planned[1].event["refs"] == {
        "contracts.knot-evm-root.record.messenger": "dusk/evm-predeploy-l2-cross-domain-messenger/testnet/public",
        "contracts.knot-evm-root.record.dusk_chain_id": "dusk/chain-id/testnet/public",
    }
    for item in planned:
        assert validate(item.event, "nocturne.history.v1") == []
    write.append(catalog, home, KNOT, [a.event for a in planned])
    after = (home.root / "history/knot/testnet.own.jsonl").read_bytes()
    assert after.startswith(before) and after.count(b"\n") == before.count(b"\n") + 2
    assert checked(home) == ([], [])
    assert dusk_refs.plan(catalog, home, "2026-02-02T00:00:00Z") == []


def test_rows_that_already_carry_refs_are_not_annotated(tmp_path: Path) -> None:
    home = repo(tmp_path)
    call(home, "knot-registry.init_messenger", MESSENGER)
    assert dusk_refs.plan(Catalog([home]), home, "2026-02-01T00:00:00Z") == []


# --- command line ----------------------------------------------------------------------------

def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_cli_check_warnings_exit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = repo(tmp_path)
    put_history(home, KNOT, [deploy(), wired("knot-registry.init_messenger", MESSENGER)])
    code, out, err = run(capsys, "check", "--root", str(home.root))
    assert code == 0 and "1 warning(s)" in out and err.startswith("warning: knot/testnet/own state.")
    code, _, _ = run(capsys, "check", "--root", str(home.root), "--warn-exit", "2")
    assert code == 2


def test_cli_annotate_dry_run_then_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = repo(tmp_path)
    put_history(home, KNOT, [deploy(), wired("knot-registry.init_messenger", MESSENGER)])
    assert home.root is not None
    before = (home.root / "history/knot/testnet.own.jsonl").read_text()
    code, out, _ = run(capsys, "annotate-refs", "--root", str(home.root))
    assert code == 0 and "knot/testnet/own line 2: " in out and "dry run: 1 note(s) for 1 entry" in out
    assert (home.root / "history/knot/testnet.own.jsonl").read_text() == before
    code, out, _ = run(capsys, "annotate-refs", "--root", str(home.root), "--write")
    assert code == 0 and "knot/testnet/own: 1 note(s) appended" in out
    code, out, _ = run(capsys, "annotate-refs", "--root", str(home.root))
    assert out.strip() == "nothing to annotate"


def test_cli_record_call_prints_warning(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = repo(tmp_path)
    assert home.root is not None
    row = home.root / "row.json"
    row.write_text(json.dumps(call_record(OURS, "init_messenger", OLD, "aa" * 32, "2026-01-02T00:00:00Z")))
    code, out, err = run(capsys, "record-call", "--root", str(home.root), "--key", "knot-registry.init_messenger",
                         "--entry-json", str(row), "--product", "knot", "--tier", "own", "--allow-public-home")
    assert code == 0 and out.startswith("wired: 1 event(s)")
    assert err.startswith("warning: knot-registry.init_messenger: 0d0d0d0d0d0d… is not dusk/evm-l1-cross-domain-messenger")


# --- the table the Rust crate embeds ----------------------------------------------------------

def test_name_table_export(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    doc = json.loads(dusk_names.table_json())
    assert doc["schema"] == "nocturne.dusk-names.v1" and doc["product"] == "dusk" and doc["tier"] == "public"
    assert [r["name"] for r in doc["names"]] == [r.name for r in dusk_names.TABLE]
    assert set(doc["names"][0]) == {"name", "path", "layer", "kind", "fallback", "chain"}
    home = repo(tmp_path)
    assert home.root is not None
    code, _, err = run(capsys, "name-table", "--root", str(home.root), "--write")
    assert code == 1 and "no such directory" in err
    (home.root / "crates" / "dusk-networks").mkdir(parents=True)
    assert run(capsys, "name-table", "--root", str(home.root), "--write")[0] == 0
    assert checked(home)[0] == []
    path = home.root / dusk_names.TABLE_FILE
    path.write_text(path.read_text().replace('"sozu-hub"', '"sozu-hub2"'))
    errors, _ = checked(home)
    assert errors == [f"{dusk_names.TABLE_FILE}: differs from dusk_names.TABLE (run nocturne-pins name-table --write)"]

