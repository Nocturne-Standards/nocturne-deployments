"""Names of Dusk's contracts and accounts: ``dusk/<name>/<network>/public``.

Product ``dusk`` is reserved. Its entries come only from the base layer (Dusk's network
files), through the fixed table below, never from a catalog entry. Tier ``public`` here
means Dusk announces changes (their releases), not us.

``fallback`` marks values Dusk says apps must read live (Sozu's contracts behind its hub,
DuskEVM's dispute game hubs); the file's value is only a starting point.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .base import BaseError, of_catalog
from .names import FullName

if TYPE_CHECKING:
    from .repo import Catalog, Repo

PRODUCT = "dusk"
TIER = "public"


@dataclass(frozen=True)
class DuskName:
    name: str
    path: str  # JSON path in the network file; ``[id=X]`` picks a route by its id
    layer: str  # duskds, duskevm, or external (a route's other chain)
    kind: str  # contract, account (Dusk account), address (EVM address) or chain-id
    fallback: bool = False
    # For external values: the key under routes.chains.
    chain: str | None = None


def _ds(name: str, path: str, fallback: bool = False, kind: str = "contract") -> DuskName:
    return DuskName(name, path, "duskds", kind, fallback)


def _evm_l1(key: str) -> DuskName:
    # DuskEVM's L1 contracts are DuskDS contracts; the EVM address is carried alongside.
    # The game hubs are read live; the factory is the stable entry point to them, as
    # ``sozu.hub`` is to Sozu. ``evm_tx_router`` keeps one ``evm``.
    fallback = "dispute_game_hub" in key
    return DuskName("evm-" + key.removeprefix("evm_").replace("_", "-"), f"duskEvm.contracts.{key}", "duskds", "contract", fallback)


def _evm_predeploy(key: str, name: str) -> DuskName:
    return DuskName(name, f"duskEvm.predeploys.{key}", "duskevm", "address")


def _ext(name: str, path: str, chain: str, kind: str) -> DuskName:
    layer = "duskds" if kind == "account" else "external"
    return DuskName(name, path, layer, kind, False, None if layer == "duskds" else chain)


# Fixed table. A name never changes meaning; a new upstream key needs a new row (check
# reports values the table does not name).
TABLE: tuple[DuskName, ...] = (
    _ds("transfer", "contracts.transfer"),
    _ds("stake", "contracts.stake"),
    _ds("multicall", "contracts.multicall"),
    _ds("sozu-hub", "sozu.hub"),
    _ds("sozu-pool", "sozu.fallbacks.pool", fallback=True),
    _ds("sozu-relayer", "sozu.fallbacks.relayer", fallback=True),
    _ds("sozu-stdusk", "sozu.fallbacks.stDusk", fallback=True),
    _ds("sozu-substrate", "sozu.fallbacks.substrate", fallback=True),
    _ds("sozu-vault", "sozu.fallbacks.vault", fallback=True),
    *(
        _evm_l1(key)
        for key in (
            "anchor_state_registry",
            "delayed_weth",
            "dispute_game_factory",
            "evm_tx_router",
            "fault_dispute_game_hub",
            "l1_cross_domain_messenger",
            "l1_erc721_bridge",
            "l1_standard_bridge",
            "mips",
            "optimism_mintable_erc20_factory",
            "optimism_mintable_erc721_factory",
            "optimism_portal",
            "permissioned_dispute_game_hub",
            "preimage_oracle",
            "protocol_versions",
            "proxy_admin",
            "superchain_config",
            "system_config",
            "system_config_impl",
            "system_config_legacy",
            "transparent_proxy",
            "tx_meta_registry",
        )
    ),
    _evm_predeploy("l1Block", "evm-predeploy-l1-block"),
    _evm_predeploy("l2CrossDomainMessenger", "evm-predeploy-l2-cross-domain-messenger"),
    _evm_predeploy("l2StandardBridge", "evm-predeploy-l2-standard-bridge"),
    _evm_predeploy("l2ToL1MessagePasser", "evm-predeploy-l2-to-l1-message-passer"),
    _ext("bsc-token", "routes.chains.bsc.token", "bsc", "address"),
    _ext("ethereum-token", "routes.chains.ethereum.token", "ethereum", "address"),
    _ext("bsc-to-dusk-contract", "routes.migrations[id=bsc-to-dusk].contract", "bsc", "address"),
    _ext("bsc-to-dusk-payout-account", "routes.migrations[id=bsc-to-dusk].payoutAccount", "bsc", "account"),
    _ext("ethereum-to-dusk-contract", "routes.migrations[id=ethereum-to-dusk].contract", "ethereum", "address"),
    _ext("ethereum-to-dusk-payout-account", "routes.migrations[id=ethereum-to-dusk].payoutAccount", "ethereum", "account"),
    _ext("dusk-to-bsc-account", "routes.services[id=dusk-to-bsc].account", "bsc", "account"),
    _ext("dusk-to-bsc-payout-address", "routes.services[id=dusk-to-bsc].payoutAddress", "bsc", "address"),
    _ext("dusk-to-ethereum-account", "routes.services[id=dusk-to-ethereum].account", "ethereum", "account"),
    _ext("dusk-to-ethereum-payout-address", "routes.services[id=dusk-to-ethereum].payoutAddress", "ethereum", "address"),
    # Chain ids are values records copy too (``dusk_chain_id``); the id is the number as text.
    DuskName("chain-id", "chainId", "duskds", "chain-id"),
    DuskName("evm-chain-id", "duskEvm.chainId", "duskevm", "chain-id"),
)
BY_NAME: dict[str, DuskName] = {row.name: row for row in TABLE}
if len(BY_NAME) != len(TABLE):  # pragma: no cover - guards edits to the table
    raise RuntimeError("dusk name table has a duplicate name")


def _get(doc: Any, path: str) -> Any:
    value = doc
    for part in path.split("."):
        if value is None:
            return None
        if "[id=" in part:
            key, wanted = part[:-1].split("[id=")
            items = value.get(key) or []
            value = next((item for item in items if item.get("id") == wanted), None)
        else:
            value = value.get(part) if isinstance(value, dict) else None
    return value


def entry(doc: dict[str, Any], row: DuskName) -> dict[str, Any] | None:
    """One name on one network, shaped like a catalog contract (``layer``, ``id``)."""
    raw = _get(doc, row.path)
    if raw is None:
        return None
    evm_address = None
    if isinstance(raw, dict):
        ident, evm_address = raw.get("contractId"), raw.get("evmAddress")
    elif row.kind == "chain-id" and isinstance(raw, int) and not isinstance(raw, bool):
        ident = str(raw)
    else:
        ident = raw
    if not isinstance(ident, str):
        return None
    out: dict[str, Any] = {"layer": row.layer, "id": ident, "kind": row.kind, "path": row.path}
    if evm_address is not None:
        out["evm_address"] = evm_address
    if row.chain is not None:
        chain = (doc.get("routes", {}).get("chains", {}) or {}).get(row.chain) or {}
        out["chain"] = row.chain
        out["chain_id"] = chain.get("chainId")
    out["label"] = doc.get("display", {}).get("labels", {}).get(ident)
    out["fallback"] = row.fallback
    return out


def entries(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every name the network publishes a value for, in table order."""
    out: dict[str, dict[str, Any]] = {}
    for row in TABLE:
        found = entry(doc, row)
        if found is not None:
            out[row.name] = found
    return out


def find(catalog: "Catalog", name: FullName, reader: str) -> tuple["Repo", dict[str, Any]]:
    """Resolve ``dusk/<name>/<network>[/public]`` from the base layer."""
    from .repo import RepoError

    if name.tier not in (None, TIER):
        raise RepoError(f"{name}: product {PRODUCT} has tier {TIER} only")
    row = BY_NAME.get(name.name)
    if row is None:
        raise RepoError(f"{name}: not a Dusk name (see nocturne-pins labels, or the table in dusk_names.py)")
    try:
        layer = of_catalog(catalog, reader)
        found = entry(layer.network(name.network), row)
    except BaseError as exc:
        raise RepoError(f"{name}: {exc}") from exc
    if found is None:
        raise RepoError(f"{name}: Dusk publishes no {row.path} for {name.network}")
    return layer.repo, found


# Where the file holds ids or accounts; check fails on a value no table row names.
def _candidate_paths(doc: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in (doc.get("contracts") or {}):
        paths.append(f"contracts.{key}")
    sozu = doc.get("sozu") or {}
    if "hub" in sozu:
        paths.append("sozu.hub")
    for key in sozu.get("fallbacks") or {}:
        paths.append(f"sozu.fallbacks.{key}")
    evm = doc.get("duskEvm") or {}
    for key in evm.get("contracts") or {}:
        paths.append(f"duskEvm.contracts.{key}")
    for key in evm.get("predeploys") or {}:
        paths.append(f"duskEvm.predeploys.{key}")
    routes = doc.get("routes") or {}
    for key, chain in (routes.get("chains") or {}).items():
        if "token" in chain:
            paths.append(f"routes.chains.{key}.token")
    for item in routes.get("migrations") or []:
        paths += [f"routes.migrations[id={item.get('id')}].{f}" for f in ("contract", "payoutAccount") if f in item]
    for item in routes.get("services") or []:
        paths += [f"routes.services[id={item.get('id')}].{f}" for f in ("account", "payoutAddress") if f in item]
    return paths


def unnamed(doc: dict[str, Any]) -> list[str]:
    """Paths (with a value) and labelled ids that no table row names."""
    known = {row.path for row in TABLE}
    problems = [p for p in _candidate_paths(doc) if p not in known and _get(doc, p) is not None]
    ids = {e["id"] for e in entries(doc).values()}
    problems += [f"display.labels[{i}]" for i in doc.get("display", {}).get("labels", {}) if i not in ids]
    return problems


def check(catalog: "Catalog", home: "Repo") -> list[str]:
    """Reserved product, refs to live-read values, and table coverage of the base layer."""
    errors = _check_reserved(catalog, home) + check_table_file(home)
    layer = None
    try:
        layer = of_catalog(catalog)
    except BaseError:
        pass
    if layer is not None and layer.repo is home:
        try:
            for doc in layer.networks():
                errors += [f"base {doc['name']}: {p} has no dusk name (add a row to dusk_names.TABLE)" for p in unnamed(doc)]
        except (BaseError, ValueError) as exc:
            errors.append(f"base: {exc}")
    return errors


def _check_reserved(catalog: "Catalog", home: "Repo") -> list[str]:
    errors: list[str] = []
    for repo in catalog.repos:
        for item in repo.entries():
            if item.get("product") == PRODUCT:
                errors.append(f"{repo.name} index: product {PRODUCT} is reserved for Dusk's base layer")
                break
    if home.root is None:
        return errors
    for folder in ("products", "history", "calls"):
        if (home.root / folder / PRODUCT).exists():
            errors.append(f"{folder}/{PRODUCT}/: product {PRODUCT} is reserved for Dusk's base layer")
    for item in home.entries():
        path = home.root / item["current"]
        if path.exists():
            try:
                product = json.loads(path.read_text(encoding="utf-8")).get("product")
            except ValueError:
                continue
            if product == PRODUCT:
                errors.append(f"{item['current']}: product {PRODUCT} is reserved for Dusk's base layer")
    return errors


# Generated copy of TABLE for the Rust crate, which embeds it (one table, two readers).
TABLE_FILE = "crates/dusk-networks/dusk-names.json"
TABLE_SCHEMA = "nocturne.dusk-names.v1"


def table_json() -> str:
    """TABLE as JSON, the format ``crates/dusk-networks`` reads."""
    rows = [
        {"name": r.name, "path": r.path, "layer": r.layer, "kind": r.kind, "fallback": r.fallback, "chain": r.chain}
        for r in TABLE
    ]
    doc = {
        "schema": TABLE_SCHEMA,
        "note": "Generated from nocturne_pins.dusk_names.TABLE by nocturne-pins name-table --write. Do not edit.",
        "product": PRODUCT,
        "tier": TIER,
        "names": rows,
    }
    return json.dumps(doc, indent=2) + "\n"


def check_table_file(home: "Repo") -> list[str]:
    """The crate's copy of the table, where the repo has the crate, equals TABLE."""
    if home.root is None:
        return []
    path = home.root / TABLE_FILE
    if not path.exists():
        return []
    if path.read_text(encoding="utf-8") != table_json():
        return [f"{TABLE_FILE}: differs from dusk_names.TABLE (run nocturne-pins name-table --write)"]
    return []


def ref_problem(target: dict[str, Any], ref: str) -> str | None:
    """A ref to a live-read value pins what Dusk says to read from the chain."""
    if target.get("fallback"):
        return f"{ref} is a fallback Dusk says to read live ({target['path']}); read it at runtime instead"
    return None

