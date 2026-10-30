"""Base layer: Dusk's network files vendored under ``upstream/dusk-networks/``.

A repo declares the base in ``index.json`` ``base.dusk``. The files are read through the
repo, so a checkout reads them from disk and a remote repo from the same base URL as its
``index.json``. Base facts never join catalog entries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .upstream.specs import DUSK_NETWORKS

if TYPE_CHECKING:
    from .repo import Catalog, Repo

BASE_KEY = DUSK_NETWORKS.base_key
# Network files of the snapshot: top-level ``<network>.json`` files other than the schema.
NETWORKS: tuple[str, ...] = tuple(
    name[: -len(".json")] for name in DUSK_NETWORKS.files if name.endswith(".json") and "/" not in name and name != "schema.json"
)
LAYERS: tuple[str, ...] = ("duskds", "duskevm")


class BaseError(ValueError):
    pass


@dataclass
class BaseLayer:
    """Dusk's network files as one repo serves them."""

    repo: "Repo"
    version: str
    _networks: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def where(self) -> str:
        return f"{self.repo.where}/{DUSK_NETWORKS.snapshot_dir}"

    def network(self, name: str) -> dict[str, Any]:
        if name not in NETWORKS:
            raise BaseError(f"unknown Dusk network {name!r} (known: {', '.join(NETWORKS)})")
        if name not in self._networks:
            rel = f"{DUSK_NETWORKS.snapshot_dir}/{name}.json"
            text = self.repo.read_text(rel)
            if text is None:
                raise BaseError(f"{self.repo.where}: {rel} missing")
            doc = json.loads(text)
            if doc.get("name") != name:
                raise BaseError(f"{rel}: name is {doc.get('name')!r}, want {name!r}")
            self._networks[name] = doc
        return self._networks[name]

    def networks(self) -> list[dict[str, Any]]:
        return [self.network(name) for name in NETWORKS]

    def by_chain_id(self, chain_id: int) -> dict[str, Any]:
        for name in NETWORKS:
            doc = self.network(name)
            if doc.get("chainId") == chain_id:
                return doc
        known = ", ".join(f"{d['name']} {d['chainId']}" for d in self.networks())
        raise BaseError(f"no Dusk network with chain id {chain_id} (known: {known})")


def of_repo(repo: "Repo") -> BaseLayer | None:
    declared = repo.index.get("base", {}).get(BASE_KEY)
    if not declared:
        return None
    # One layer per repo, so a remote repo fetches each network file once.
    cache_key = f"base:{BASE_KEY}"
    if cache_key not in repo._cache:
        repo._cache[cache_key] = BaseLayer(repo=repo, version=str(declared.get("version")))
    return repo._cache[cache_key]


def of_catalog(catalog: "Catalog", reader: str = "own") -> BaseLayer:
    """The base layer of the first public repo declaring one, else (own readers) a private one.

    Dusk's facts are public data, so the public repo is their home; a private repo that
    happens to declare a base only serves readers that may read it.
    """
    ordered = [r for r in catalog.repos if r.is_public] + [r for r in catalog.repos if not r.is_public]
    for repo in ordered:
        if reader == "public" and not repo.is_public:
            continue
        layer = of_repo(repo)
        if layer is not None:
            return layer
    raise BaseError(f"no repo in view declares index.json base.{BASE_KEY}")


def layer_view(doc: dict[str, Any], layer: str | None) -> dict[str, Any]:
    """``network`` output: the whole file, or the DuskDS part, or the DuskEVM part."""
    if layer is None:
        return doc
    if layer == "duskevm":
        if doc.get("duskEvm") is None:
            raise BaseError(f"{doc['name']}: Dusk publishes no DuskEVM for this network")
        return doc["duskEvm"]
    if layer == "duskds":
        return {k: v for k, v in doc.items() if k != "duskEvm"}
    raise BaseError(f"unknown layer {layer!r} (duskds or duskevm)")


def field_value(doc: dict[str, Any], dotted: str) -> Any:
    """``urls.node`` style lookup; list items by index."""
    value: Any = doc
    for part in dotted.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise BaseError(f"{doc.get('name', '?')}: no field {dotted!r}")
    return value
