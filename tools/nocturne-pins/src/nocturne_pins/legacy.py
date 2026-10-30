"""Generate the old monolith files (``nocturne.pins.v1``) from history logs.

Transition only: readers that still load ``testnet.json`` / ``duskds/testnet.json`` /
``duskevm/testnet.json`` keep working while they move to the resolver. Removed in leaf 6.
"""

from __future__ import annotations

import heapq
import json
from pathlib import Path
from typing import Any, Iterator

from . import base_check, jsonio
from .names import EntryKey
from .repo import Catalog, Repo

ROW_EVENTS: tuple[str, ...] = ("wired", "admin", "proposal", "call")


def _events(catalog: Catalog, repo: Repo, key: EntryKey) -> list[dict[str, Any]]:
    events = [json.loads(line) for line in repo.history_lines(key)]
    calls = [json.loads(line) for r in catalog.repos for line in r.calls_lines(key)]
    # History and calls are each in time order; merge keeps each one's order.
    return list(heapq.merge(events, calls, key=lambda e: e["at"]))


def _merged(catalog: Catalog, network: str) -> Iterator[tuple[EntryKey, dict[str, Any]]]:
    streams = []
    for repo, key in catalog.entries():
        if key.network != network:
            continue
        streams.append([(event["at"], i, key, event) for i, event in enumerate(_events(catalog, repo, key))])
    for _, _, key, event in heapq.merge(*streams, key=lambda row: row[0]):
        yield key, event


def _row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"current": rows[-1], "history": list(reversed(rows[:-1]))}


def build(catalog: Catalog, network: str, networks: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return {"duskds": envelope, "duskevm": envelope, "flat": flat} for one network."""
    contracts: dict[str, dict[str, list[dict[str, Any]]]] = {"duskds": {}, "duskevm": {}}
    wiring: dict[str, dict[str, list[dict[str, Any]]]] = {"duskds": {}, "duskevm": {}}
    retired: set[tuple[str, str]] = set()
    aliases: dict[str, dict[str, str]] = {"duskds": {}, "duskevm": {}}
    extras: dict[str, dict[str, Any]] = {"duskds": {}, "duskevm": {}}

    for _, event in _merged(catalog, network):
        kind = event["event"]
        if kind == "deployed":
            layer = event["layer"]
            contracts[layer].setdefault(event["name"], []).append(event["record"])
            retired.discard((layer, event["name"]))
        elif kind == "retired":
            for layer in contracts:
                if event["name"] in contracts[layer]:
                    retired.add((layer, event["name"]))
        elif kind in ROW_EVENTS:
            wiring[event["layer"]].setdefault(event["key"], []).append(event["record"])
        for set_key, value in event.get("sets", {}).items():
            parts = set_key.split(".", 2)
            if len(parts) == 3 and parts[0] == "alias" and parts[1] in aliases:
                _assign(aliases[parts[1]], parts[2], value)
            elif len(parts) == 3 and parts[0] == "legacy" and parts[1] in extras:
                _assign(extras[parts[1]], parts[2], value)

    out: dict[str, dict[str, Any]] = {}
    for layer in ("duskds", "duskevm"):
        live = {name: _row(rows) for name, rows in contracts[layer].items() if (layer, name) not in retired}
        rows = {key: _row(items) for key, items in wiring[layer].items()}
        net = networks.get(network, {}).get(layer, {})
        doc: dict[str, Any] = {"schema": "nocturne.pins.v1", "layer": layer, "network": network}
        if layer == "duskds":
            doc.update(extras[layer])
            doc["rpc"] = net.get("rpc")
            doc["explorer"] = net.get("explorer")
            doc["aliases"] = aliases[layer]
            doc["contracts"] = live
            doc["wiring"] = rows
        else:
            doc["chain_id"] = net.get("chain_id")
            doc["rpc"] = net.get("rpc")
            doc["explorer"] = net.get("explorer")
            doc["aliases"] = aliases[layer]
            doc["contracts"] = live
            doc.update(extras[layer])
            doc["wiring"] = rows
        out[layer] = doc

    flat: dict[str, Any] = dict(out["duskds"]["contracts"])
    flat["wiring"] = out["duskds"]["wiring"]
    flat["chain_id"] = networks.get(network, {}).get("duskds", {}).get("chain_id")
    flat.update(extras["duskds"])
    out["flat"] = flat
    return out


def _assign(target: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        target.pop(key, None)
    else:
        target[key] = value


def render(catalog: Catalog, home: Repo) -> dict[Path, str]:
    """Legacy files for ``home`` (the repo whose index names them), built from the whole catalog."""
    if home.root is None:
        raise ValueError("legacy files are written into a checkout")
    out: dict[Path, str] = {}
    networks = _networks(catalog)
    for network, paths in home.index.get("legacy", {}).items():
        built = build(catalog, network, networks)
        for kind, rel in paths.items():
            out[home.root / rel] = jsonio.dump_file(built[kind])
    return out


def _networks(catalog: Catalog) -> dict[str, Any]:
    # Index overlays (earlier repos win) under Dusk's facts from the base layer.
    return base_check.networks(catalog)


def write(catalog: Catalog, home: Repo) -> list[Path]:
    changed: list[Path] = []
    for path, text in render(catalog, home).items():
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            jsonio.write_atomic(path, text)
            changed.append(path)
    return changed


def stale(catalog: Catalog, home: Repo) -> list[Path]:
    return [
        path
        for path, text in render(catalog, home).items()
        if not path.exists() or path.read_text(encoding="utf-8") != text
    ]
