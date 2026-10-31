"""Base layer checks: validator keywords, lock and snapshot, networks derived from Dusk's data (no network)."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from test_upstream import World, make_root, make_tarball, package_files, run

from nocturne_pins import cli, jsonio, legacy
from nocturne_pins.base_check import projection, verify_remote
from nocturne_pins.check import check_repo
from nocturne_pins.repo import Catalog, Repo
from nocturne_pins.schema import SCHEMA_FILES, load_schema, schema_text, unsupported, validate, validate_with
from nocturne_pins.upstream.specs import DUSK_NETWORKS as SPEC

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = REPO_ROOT / "upstream" / "dusk-networks"
LOCK = REPO_ROOT / "upstream" / "dusk-networks.lock.json"
HISTORY = REPO_ROOT / "history" / "dusk-networks.jsonl"
DUSK_EXPLORER = "https://apps.testnet.dusk.network/explorer"


# ---------------------------------------------------------------- validator


def test_validator_ref_anyof_propertynames() -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "propertyNames": {"pattern": "^[a-z]+$"},
        "additionalProperties": {"anyOf": [{"type": "null"}, {"$ref": "#/$defs/id"}]},
        "$defs": {"id": {"type": "string", "pattern": "^[0-9a-f]{4}$"}},
    }
    assert validate_with({"a": None, "b": "00ff"}, schema) == []
    assert any("matches none of anyOf" in e for e in validate_with({"a": "zz"}, schema))
    assert any("key 'A1'" in e for e in validate_with({"A1": None}, schema))


def test_validator_maximum_unique_and_json_equality() -> None:
    assert validate_with(37, {"type": "integer", "maximum": 36}) != []
    assert validate_with([1, 2, 1], {"type": "array", "uniqueItems": True}) != []
    assert validate_with([1, True], {"type": "array", "uniqueItems": True}) == []
    # true is not 1 in JSON, though Python says it is.
    assert validate_with(True, {"enum": [0, 1, 2, 3]}) != []
    assert validate_with(1, {"const": True}) != []


def test_unsupported_keywords_are_reported() -> None:
    assert unsupported({"oneOf": [{"type": "null"}]}) == ["#: oneOf"]
    assert unsupported({"properties": {"a": {"format": "uri"}}}) == ["#/properties/a: format"]
    assert unsupported({"$ref": "#/$defs/nope"}) != []
    assert unsupported({"$ref": "other.json#/x"}) != []
    for schema_id in SCHEMA_FILES:
        assert unsupported(load_schema(schema_id)) == [], schema_id


def test_vendored_dusk_schema_is_fully_supported_and_files_validate() -> None:
    schema = json.loads((SNAPSHOT / "schema.json").read_text())
    assert unsupported(schema) == []
    for name in ("mainnet", "testnet", "devnet"):
        assert validate_with(json.loads((SNAPSHOT / f"{name}.json").read_text()), schema, name) == []
    testnet = json.loads((SNAPSHOT / "testnet.json").read_text())
    testnet["chainId"] = 7
    testnet["contracts"]["stake"] = "0x" + "a" * 64
    testnet["duskEvm"]["contracts"]["extra"] = {"contractId": "a" * 64}
    errors = validate_with(testnet, schema)
    assert any(".chainId" in e for e in errors)
    assert any(".contracts.stake" in e for e in errors)
    assert any("missing 'evmAddress'" in e for e in errors)


def test_lock_schema() -> None:
    lock = json.loads(LOCK.read_text())
    assert validate(lock, "nocturne.upstream-lock.v1") == []
    lock["tarball"]["sha256"] = "x"
    lock["extra"] = 1
    errors = validate(lock, "nocturne.upstream-lock.v1")
    assert any("tarball.sha256" in e for e in errors) and any("'extra'" in e for e in errors)


def test_repo_schema_copies_are_bundled_copies() -> None:
    for schema_id, file_name in SCHEMA_FILES.items():
        assert (REPO_ROOT / "schema" / file_name).read_text() == schema_text(schema_id)


# ---------------------------------------------------------------- repo with the real snapshot


def make_base_repo(root: Path, **extra: Any) -> Path:
    """A public pin repo with no entries and this repo's vendored Dusk snapshot and lock."""
    (root / "schema").mkdir(parents=True)
    for schema_id, file_name in SCHEMA_FILES.items():
        (root / "schema" / file_name).write_text(schema_text(schema_id))
    shutil.copytree(SNAPSHOT, root / "upstream" / "dusk-networks")
    shutil.copy(LOCK, root / "upstream" / "dusk-networks.lock.json")
    (root / "history").mkdir()
    shutil.copy(HISTORY, root / "history" / "dusk-networks.jsonl")
    version = json.loads(LOCK.read_text())["version"]
    index: dict[str, Any] = {
        "schema": "nocturne.index.v1",
        "repo": "o/pub",
        "visibility": "public",
        "base": {"dusk": {"version": version, "lock": "upstream/dusk-networks.lock.json"}},
        "entries": [],
        **extra,
    }
    (root / "index.json").write_text(jsonio.dump_file(index))
    return root


def errors_of(root: Path) -> list[str]:
    home = Repo.open(root)
    return check_repo(Catalog([home]), home)


def relock(root: Path, rel: str) -> None:
    """Rewrite the lock hash of one file, as a forger who also edits the lock would."""
    lock_path = root / "upstream/dusk-networks.lock.json"
    lock = jsonio.load(lock_path)
    path = f"upstream/dusk-networks/{rel}"
    lock["files"][path]["sha256"] = hashlib.sha256((root / path).read_bytes()).hexdigest()
    lock_path.write_text(jsonio.dump_file(lock))


def test_clean_snapshot_passes(tmp_path: Path) -> None:
    assert errors_of(make_base_repo(tmp_path / "r")) == []


def test_edited_vendored_byte_fails(tmp_path: Path) -> None:
    root = make_base_repo(tmp_path / "r")
    path = root / "upstream/dusk-networks/testnet.json"
    data = bytearray(path.read_bytes())
    data[data.index(b"testnet.nodes")] = ord("T")
    path.write_bytes(bytes(data))
    assert any("testnet.json: sha256 differs" in e for e in errors_of(root))
    path.write_bytes(path.read_bytes() + b"x")  # no longer JSON: reported, not a crash
    assert any("testnet.json: sha256 differs" in e for e in errors_of(root))
    relock(root, "testnet.json")
    assert any("testnet.json: not JSON" in e for e in errors_of(root))


def test_stray_missing_and_version_mismatch(tmp_path: Path) -> None:
    root = make_base_repo(tmp_path / "r")
    (root / "upstream/dusk-networks/extra.json").write_text("{}")
    (root / "upstream/dusk-networks/LICENSE").unlink()
    index = jsonio.load(root / "index.json")
    index["base"]["dusk"]["version"] = "9.9.9"
    (root / "index.json").write_text(jsonio.dump_file(index))
    errors = errors_of(root)
    assert any("extra.json: not in" in e for e in errors)
    assert any("LICENSE: missing" in e for e in errors)
    assert any("9.9.9" in e for e in errors)


def test_invalid_lock_fails(tmp_path: Path) -> None:
    root = make_base_repo(tmp_path / "r")
    lock = jsonio.load(root / "upstream/dusk-networks.lock.json")
    del lock["commit"]
    (root / "upstream/dusk-networks.lock.json").write_text(jsonio.dump_file(lock))
    assert any("missing 'commit'" in e for e in errors_of(root))


def test_relocked_but_schema_invalid_snapshot_fails(tmp_path: Path) -> None:
    root = make_base_repo(tmp_path / "r")
    path = root / "upstream/dusk-networks/testnet.json"
    path.write_bytes(path.read_bytes().replace(b'"chainId": 2', b'"chainId": 9', 1))
    relock(root, "testnet.json")
    assert any("testnet.json.chainId" in e for e in errors_of(root))


def test_schema_with_unchecked_keyword_fails(tmp_path: Path) -> None:
    root = make_base_repo(tmp_path / "r")
    path = root / "upstream/dusk-networks/schema.json"
    schema = json.loads(path.read_text())
    schema["properties"]["label"]["format"] = "hostname"
    path.write_text(json.dumps(schema))
    relock(root, "schema.json")
    assert any("does not check" in e and "format" in e for e in errors_of(root))


# ---------------------------------------------------------------- index.networks


def test_projection_from_base(tmp_path: Path) -> None:
    facts = projection(Repo.open(make_base_repo(tmp_path / "r")))
    assert facts["testnet"]["duskds"] == {
        "chain_id": 2, "rpc": "https://testnet.nodes.dusk.network", "explorer": DUSK_EXPLORER,
    }
    assert facts["testnet"]["duskevm"] == {
        "chain_id": 745, "rpc": "https://rpc.testnet.evm.dusk.network",
        "explorer": "https://explorer.testnet.evm.dusk.network",
    }
    assert "duskevm" not in facts["mainnet"]  # Dusk lists no DuskEVM on mainnet in this version


@pytest.mark.parametrize("key,value", [("chain_id", 3), ("explorer", None), ("rpc", "https://mirror.example")])
def test_override_of_a_dusk_fact_fails(tmp_path: Path, key: str, value: Any) -> None:
    root = make_base_repo(tmp_path / "r", networks={"testnet": {"duskds": {key: value}}})
    errors = errors_of(root)
    assert len(errors) == 1 and f"networks.testnet.duskds.{key}" in errors[0] and "overrides Dusk" in errors[0]


def test_new_keys_and_equal_values_pass(tmp_path: Path) -> None:
    networks = {
        "testnet": {"duskds": {"chain_id": 2, "rpc_mirror": "https://mirror.example"}},
        "localnet": {"duskds": {"chain_id": 0, "rpc": "http://127.0.0.1:8080"}},
    }
    assert errors_of(make_base_repo(tmp_path / "r", networks=networks)) == []


def test_private_overlay_checked_against_public_base_and_legacy_uses_dusk(tmp_path: Path) -> None:
    public = make_base_repo(tmp_path / "pub")
    lg = {"testnet": {"flat": "testnet.json", "duskds": "duskds/testnet.json", "duskevm": "duskevm/testnet.json"}}
    private_root = tmp_path / "priv"
    (private_root / "schema").mkdir(parents=True)
    for schema_id, file_name in SCHEMA_FILES.items():
        (private_root / "schema" / file_name).write_text(schema_text(schema_id))
    index = {
        "schema": "nocturne.index.v1", "repo": "o/priv", "visibility": "private",
        "networks": {"testnet": {"duskds": {"explorer": None, "gateway": "https://gw.example"}}},
        "entries": [], "legacy": lg,
    }
    (private_root / "index.json").write_text(jsonio.dump_file(index))
    private, pub = Repo.open(private_root), Repo.open(public)
    catalog = Catalog([private, pub])
    errors = check_repo(catalog, private)
    assert any("networks.testnet.duskds.explorer = null overrides Dusk" in e for e in errors)
    legacy.write(catalog, private)
    duskds = jsonio.load(private_root / "duskds/testnet.json")
    duskevm = jsonio.load(private_root / "duskevm/testnet.json")
    flat = jsonio.load(private_root / "testnet.json")
    assert (duskds["rpc"], duskds["explorer"]) == ("https://testnet.nodes.dusk.network", DUSK_EXPLORER)
    assert (duskevm["chain_id"], duskevm["rpc"]) == (745, "https://rpc.testnet.evm.dusk.network")
    assert flat["chain_id"] == 2
    # Without the override the private repo is clean.
    del index["networks"]
    (private_root / "index.json").write_text(jsonio.dump_file(index))
    private = Repo.open(private_root)
    assert check_repo(Catalog([private, pub]), private) == []


# ---------------------------------------------------------------- online check (fake registry)


def test_verify_remote(tmp_path: Path) -> None:
    world = World()
    world.publish("0.3.0")
    root = make_root(tmp_path)
    run(root, world, "0.3.0")
    assert verify_remote(root, SPEC, world) == []

    (root / "upstream/dusk-networks/devnet.json").write_bytes(b'{"chainId": 3, "name": "devnet "}\n')
    assert any("devnet.json: differs" in e for e in verify_remote(root, SPEC, world))
    (root / "upstream/dusk-networks/devnet.json").write_bytes(package_files("0.3.0")["networks/devnet.json"])

    url = world.versions["0.3.0"]["dist"]["tarball"]
    files = package_files("0.3.0")
    files["networks/testnet.json"] = b'{"chainId": 2, "name": "other"}\n'
    world.responses[url] = make_tarball(files)
    assert any("refetched tarball" in e for e in verify_remote(root, SPEC, world))

    world.versions["0.3.0"]["dist"]["integrity"] = "sha512-" + "A" * 86 + "=="
    assert any("registry dist.integrity" in e for e in verify_remote(root, SPEC, world))


def test_cli_verify_is_registered() -> None:
    args = cli.parser().parse_args(["upstream", "verify", "--root", "."])
    assert args.func.__name__ == "cmd_verify"


class _Down:
    def get(self, url: str) -> bytes:
        from nocturne_pins.upstream.net import Unreachable

        raise Unreachable(f"{url}: HTTP 503")


def test_cli_verify_warns_on_outage_but_fails_on_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from nocturne_pins import base_check

    world = World()
    world.publish("0.3.0")
    root = make_root(tmp_path)
    run(root, world, "0.3.0")

    monkeypatch.setattr(base_check, "UrllibHttp", _Down)
    verify = ["upstream", "verify", "--root", str(root)]
    assert cli.main(verify) == 1
    assert cli.main([*verify, "--warn-unreachable"]) == 0
    assert "::warning::" in capsys.readouterr().out

    monkeypatch.setattr(base_check, "UrllibHttp", lambda: world)
    (root / "upstream/dusk-networks/devnet.json").write_bytes(b'{"chainId": 3, "name": "devnet "}\n')
    assert cli.main([*verify, "--warn-unreachable"]) == 1
