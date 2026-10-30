"""Reserved product ``dusk``: names from the vendored base layer (offline)."""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from nocturne_pins import Catalog, cli, jsonio, repo as repo_mod
from nocturne_pins.base import NETWORKS, field_value, layer_view, of_catalog
from nocturne_pins.check import check_repo
from nocturne_pins.dusk_names import BY_NAME, TABLE, entries, unnamed
from nocturne_pins.names import EntryKey, parse
from nocturne_pins.replay import replay
from nocturne_pins.repo import PUBLIC_INDEX_URL, Repo, RepoError

from test_pins import make_repo

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / "upstream" / "dusk-networks"
LOCK = ROOT / "upstream" / "dusk-networks.lock.json"
BASE = {"dusk": {"version": "0.3.0", "lock": "upstream/dusk-networks.lock.json"}}
MESSENGER = "63cd2c93016296b2ec357e31fd44b716cf2cda3b31217d45087f17ccc6440530"
T1 = "c1" * 32


def network(name: str) -> dict[str, Any]:
    return json.loads((SNAPSHOT / f"{name}.json").read_text())


@pytest.fixture
def real() -> Catalog:
    return Catalog([Repo.open(ROOT)])


def with_base(tmp_path: Path, entries_: list[tuple[str, str]], **extra: Any) -> Repo:
    root = tmp_path / "pub"
    make_repo(root, "o/pub", "public", entries_, base=BASE, **extra)
    shutil.copytree(SNAPSHOT, root / "upstream" / "dusk-networks")
    shutil.copy(LOCK, root / "upstream" / "dusk-networks.lock.json")
    # Network data comes from the base; make_repo's sample networks would override it.
    index = json.loads((root / "index.json").read_text())
    del index["networks"]
    (root / "index.json").write_text(jsonio.dump_file(index))
    return Repo.open(root)


def put_history(repo: Repo, key: EntryKey, events: list[dict[str, Any]]) -> None:
    assert repo.root is not None
    lines = [jsonio.dump_line(e) for e in events]
    (repo.root / f"history/{key.product}/{key.network}.{key.tier}.jsonl").write_text("".join(l + "\n" for l in lines))
    (repo.root / f"products/{key.product}/{key.network}.{key.tier}.json").write_text(jsonio.dump_file(replay(key, lines)))
    repo._cache.clear()


def deployed(name: str, refs: dict[str, str]) -> dict[str, Any]:
    return {"at": "2026-01-01T00:00:00Z", "event": "deployed", "name": name, "layer": "duskds", "id": T1,
            "record": {"contract_id": T1}, "refs": refs}


# --- the table ---------------------------------------------------------------

def test_table_names_are_kebab_and_unique() -> None:
    assert len(BY_NAME) == len(TABLE)
    for row in TABLE:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", row.name), row.name
        assert row.layer in ("duskds", "duskevm", "external")
        assert row.kind in ("contract", "account", "address")


def test_table_names_every_value_of_the_snapshot() -> None:
    for name in NETWORKS:
        assert unnamed(network(name)) == [], name


def test_unnamed_reports_new_upstream_keys() -> None:
    doc = copy.deepcopy(network("testnet"))
    doc["contracts"]["newthing"] = "ab" * 32
    doc["display"]["labels"]["cd" * 32] = "Something"
    assert unnamed(doc) == ["contracts.newthing", f"display.labels[{'cd' * 32}]"]


def test_known_names_paths_and_fallbacks() -> None:
    doc = network("testnet")
    got = entries(doc)
    assert got["transfer"]["id"] == "01" + "00" * 31
    assert got["sozu-hub"] == {
        "layer": "duskds", "id": doc["sozu"]["hub"], "kind": "contract", "path": "sozu.hub",
        "label": "Sozu hub", "fallback": False,
    }
    for name in ("sozu-pool", "sozu-relayer", "sozu-stdusk", "sozu-substrate", "sozu-vault",
                 "evm-fault-dispute-game-hub", "evm-permissioned-dispute-game-hub"):
        assert got[name]["fallback"] is True, name
    assert got["evm-dispute-game-factory"]["fallback"] is False
    assert [n for n, e in got.items() if e["fallback"]] == [
        "sozu-pool", "sozu-relayer", "sozu-stdusk", "sozu-substrate", "sozu-vault",
        "evm-fault-dispute-game-hub", "evm-permissioned-dispute-game-hub",
    ]
    messenger = got["evm-l1-cross-domain-messenger"]
    assert (messenger["layer"], messenger["id"], messenger["label"]) == ("duskds", MESSENGER, "DuskEVM messenger")
    assert messenger["evm_address"] == "0x8B6Aa15321d9656c91ad2d5fBc2AE8b750aD6F9a"
    assert got["evm-tx-router"]["path"] == "duskEvm.contracts.evm_tx_router"
    l2 = got["evm-predeploy-l2-cross-domain-messenger"]
    assert (l2["layer"], l2["kind"], l2["id"]) == ("duskevm", "address", "0x4200000000000000000000000000000000000007")
    account = got["dusk-to-ethereum-account"]
    assert (account["layer"], account["kind"], account["label"]) == ("duskds", "account", "Sepolia bridge")
    token = got["ethereum-token"]
    assert (token["layer"], token["chain"], token["chain_id"]) == ("external", "ethereum", 11155111)
    # Null values are not published.
    assert "ethereum-to-dusk-payout-account" not in got
    assert "dusk-to-ethereum-payout-address" not in got


def test_mainnet_routes_and_devnet_gaps() -> None:
    main = entries(network("mainnet"))
    assert main["bsc-to-dusk-payout-account"]["label"] == "BSC bridge"
    assert main["dusk-to-bsc-account"]["id"] == main["bsc-to-dusk-payout-account"]["id"]
    assert main["dusk-to-bsc-payout-address"]["chain_id"] == 56
    assert main["ethereum-to-dusk-payout-account"]["label"] == "Ethereum migration payouts"
    assert not any(n.startswith("evm-") for n in main)
    assert list(entries(network("devnet"))) == ["transfer", "stake"]


# --- resolve -----------------------------------------------------------------

@pytest.mark.parametrize("reader", ["own", "public"])
def test_resolve_from_base_layer_for_both_readers(real: Catalog, reader: str) -> None:
    assert real.resolve(parse("dusk/sozu-hub/testnet/public"), reader) == network("testnet")["sozu"]["hub"]
    assert real.resolve(parse("dusk/evm-l1-cross-domain-messenger/testnet"), reader) == MESSENGER
    repo, key, contract = real.find(parse("dusk/stake/mainnet/public"), reader)
    assert key == EntryKey("dusk", "mainnet", "public") and repo.is_public
    assert contract["label"] == "Stake contract"


def test_dusk_is_not_a_catalog_entry(real: Catalog) -> None:
    assert all(key.product != "dusk" for _, key in real.entries())


@pytest.mark.parametrize("text, match", [
    ("dusk/sozu-hub/testnet/own", "tier public only"),
    ("dusk/nope/testnet/public", "not a Dusk name"),
    ("dusk/multicall/devnet/public", "publishes no contracts.multicall"),
    ("dusk/sozu-hub/localnet/public", "unknown Dusk network"),
])
def test_resolve_errors(real: Catalog, text: str, match: str) -> None:
    with pytest.raises(RepoError, match=match):
        real.resolve(parse(text))


def test_no_base_declared(tmp_path: Path) -> None:
    bare = make_repo(tmp_path / "bare", "o/bare", "public", [])
    with pytest.raises(RepoError, match="declares index.json base.dusk"):
        Catalog([bare]).resolve(parse("dusk/transfer/testnet/public"))


def test_public_reader_never_reads_a_private_base(tmp_path: Path) -> None:
    private = make_repo(tmp_path / "priv", "o/priv", "private", [], base=BASE)
    shutil.copytree(SNAPSHOT, tmp_path / "priv" / "upstream" / "dusk-networks")
    catalog = Catalog([Repo.open(tmp_path / "priv")])
    assert catalog.resolve(parse("dusk/transfer/testnet"), "own") == "01" + "00" * 31
    with pytest.raises(RepoError, match="declares"):
        catalog.resolve(parse("dusk/transfer/testnet"), "public")
    public = with_base(tmp_path, [])
    assert of_catalog(Catalog([Repo.open(tmp_path / "priv"), public])).repo is public


# --- check -------------------------------------------------------------------

def test_public_entry_may_ref_dusk(tmp_path: Path) -> None:
    home = with_base(tmp_path, [("tnst", "public")])
    put_history(home, EntryKey("tnst", "testnet", "public"),
                [deployed("tnst-bridge", {"messenger": "dusk/evm-l1-cross-domain-messenger/testnet/public"})])
    assert check_repo(Catalog([home]), home) == []


def test_ref_to_live_read_value_fails(tmp_path: Path) -> None:
    home = with_base(tmp_path, [("tnst", "public")])
    put_history(home, EntryKey("tnst", "testnet", "public"), [deployed("tnst-x", {"pool": "dusk/sozu-pool/testnet/public"})])
    errors = check_repo(Catalog([home]), home)
    assert len(errors) == 1 and "fallback Dusk says to read live" in errors[0]


def test_ref_to_unknown_dusk_name_fails(tmp_path: Path) -> None:
    home = with_base(tmp_path, [("tnst", "public")])
    put_history(home, EntryKey("tnst", "testnet", "public"), [deployed("tnst-x", {"x": "dusk/nope/testnet/public"})])
    assert any("does not resolve" in e and "not a Dusk name" in e for e in check_repo(Catalog([home]), home))


def test_reserved_product_in_index(tmp_path: Path) -> None:
    home = with_base(tmp_path, [("dusk", "public")])
    errors = check_repo(Catalog([home]), home)
    assert any("o/pub index: product dusk is reserved" in e for e in errors)
    assert any("products/dusk/: product dusk is reserved" in e for e in errors)
    assert any("history/dusk/: product dusk is reserved" in e for e in errors)


def test_reserved_product_in_other_repo_and_stray_files(tmp_path: Path) -> None:
    home = with_base(tmp_path, [])
    other = make_repo(tmp_path / "priv", "o/priv", "private", [("dusk", "own")])
    assert any("o/priv index: product dusk is reserved" in e for e in check_repo(Catalog([other, home]), home))
    assert home.root is not None
    (home.root / "history" / "dusk").mkdir(parents=True)
    assert any("history/dusk/" in e for e in check_repo(Catalog([home]), home))


def test_reserved_product_in_current_file(tmp_path: Path) -> None:
    home = with_base(tmp_path, [("tnst", "public")])
    assert home.root is not None
    path = home.root / "products/tnst/testnet.public.json"
    doc = jsonio.load(path)
    doc["product"] = "dusk"
    path.write_text(jsonio.dump_file(doc))
    assert any("products/tnst/testnet.public.json: product dusk is reserved" in e for e in check_repo(Catalog([Repo.open(home.root)]), home))


def test_check_reports_unnamed_base_values(tmp_path: Path) -> None:
    home = with_base(tmp_path, [])
    assert home.root is not None
    path = home.root / "upstream/dusk-networks/testnet.json"
    doc = json.loads(path.read_text())
    doc["sozu"]["fallbacks"]["oracle"] = "ef" * 32
    path.write_text(json.dumps(doc))
    # The edited file also fails its locked sha256; the naming error must be reported too.
    assert "base testnet: sozu.fallbacks.oracle has no dusk name (add a row to dusk_names.TABLE)" in check_repo(
        Catalog([home]), home
    )


# --- network views -----------------------------------------------------------

def test_layer_views_and_fields() -> None:
    doc = network("testnet")
    assert "duskEvm" not in layer_view(doc, "duskds")
    assert layer_view(doc, "duskevm")["chainId"] == 745
    assert field_value(doc, "urls.node") == "https://testnet.nodes.dusk.network"
    assert field_value(doc, "display.blocklist.0.reason") == "compromised"


# --- CLI ---------------------------------------------------------------------

def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main([*argv, "--index", str(ROOT)])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_network(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "network", "testnet")
    assert code == 0 and json.loads(out) == network("testnet")
    assert run(capsys, "network", "--chain-id", "1", "--field", "urls.node")[1] == "https://nodes.dusk.network\n"
    assert run(capsys, "network", "testnet", "--layer", "duskevm", "--field", "chainId")[1] == "745\n"
    code, out, _ = run(capsys, "network", "testnet", "--layer", "duskds")
    assert "duskEvm" not in json.loads(out)
    code, _, err = run(capsys, "network", "mainnet", "--layer", "duskevm")
    assert code == 1 and "no DuskEVM" in err
    code, _, err = run(capsys, "network", "--chain-id", "9")
    assert code == 1 and "no Dusk network with chain id 9" in err
    code, _, err = run(capsys, "network", "testnet", "--chain-id", "2")
    assert code == 1 and "not both" in err
    code, _, err = run(capsys, "network", "testnet", "--field", "urls.nope")
    assert code == 1 and "no field" in err


def test_cli_labels_and_resolve(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "labels", "testnet")
    lines = out.splitlines()
    assert code == 0
    assert lines[0] == f"dusk/transfer/testnet/public\tduskds\t{'01' + '00' * 31}\tTransfer contract"
    pool = next(l for l in lines if l.startswith("dusk/sozu-pool/"))
    assert pool.endswith("\tSozu pool\tfallback")
    assert any(l.startswith("dusk/evm-predeploy-l2-cross-domain-messenger/testnet/public\tduskevm\t0x42") for l in lines)
    code, out, _ = run(capsys, "labels")
    assert {l.split("/")[2] for l in out.splitlines()} == set(NETWORKS)
    code, out, _ = run(capsys, "resolve", "dusk/sozu-hub/testnet/public")
    assert out.strip() == network("testnet")["sozu"]["hub"]
    code, out, _ = run(capsys, "resolve", "--reader", "public", "dusk/evm-l1-cross-domain-messenger/testnet")
    assert out.strip() == MESSENGER
    code, out, _ = run(capsys, "get", "dusk/sozu-pool/mainnet/public")
    got = json.loads(out)
    assert (got["name"], got["fallback"], got["repo"]) == ("dusk/sozu-pool/mainnet/public", True, "Nocturne-Standards/nocturne-deployments")


# --- remote ------------------------------------------------------------------

def test_remote_reads_base_from_index_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    base_url = PUBLIC_INDEX_URL[: -len("index.json")]
    fetched: list[str] = []

    def fake_fetch(url: str) -> str:
        fetched.append(url)
        assert url.startswith(base_url), url
        path = ROOT / url[len(base_url):]
        if not path.is_file():
            raise OSError(f"404 {url}")
        return path.read_text(encoding="utf-8")

    monkeypatch.setattr(repo_mod, "_fetch", fake_fetch)
    monkeypatch.setenv("NOCTURNE_PINS", "")
    monkeypatch.chdir("/")
    catalog = Catalog.open(None)
    assert [r.base_url for r in catalog.repos] == [base_url]
    assert catalog.resolve(parse("dusk/sozu-hub/testnet/public"), "public") == network("testnet")["sozu"]["hub"]
    assert of_catalog(catalog).by_chain_id(1)["urls"]["node"] == "https://nodes.dusk.network"
    assert fetched == [
        base_url + "index.json",
        base_url + "upstream/dusk-networks/testnet.json",
        base_url + "upstream/dusk-networks/mainnet.json",
    ]
