"""Checks on the upstream base layer (Dusk's network package) and the network data derived from it.

Offline, run by ``check``:

1. the lock validates (``nocturne.upstream-lock.v1``), names the version ``index.json`` ``base``
   names, lists exactly the snapshot files, and each file's sha256 matches; no stray files;
2. each network file validates against the vendored ``schema.json`` (every keyword of which
   the bundled validator must support, or the check fails);
3. ``index.json`` ``networks`` never overrides a Dusk fact. Network data comes from the
   base (``projection``); an index may add keys (our own RPC mirror), nothing else.

Online, ``upstream verify``: the locked tarball is fetched again and its bytes, and the
files in it, compared with the lock and the snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from . import jsonio
from .repo import Catalog, Repo
from .schema import unsupported, validate, validate_with
from .upstream import UpstreamError, specs
from .upstream.net import Http, Unreachable, UrllibHttp
from .upstream.npm import check_integrity, read_members

LOCK_SCHEMA = "nocturne.upstream-lock.v1"
DUSK = specs.DUSK_NETWORKS

# Per upstream: the vendored schema file and the snapshot files it describes.
SCHEMA_CHECKS: dict[str, tuple[str, tuple[str, ...]]] = {
    DUSK.name: ("schema.json", ("mainnet.json", "testnet.json", "devnet.json")),
}

# index.json networks.<net>.<layer>.<key>  <-  path in Dusk's <net>.json.
# DuskEVM keys exist only where Dusk lists DuskEVM (duskEvm not null).
PROJECTION: dict[str, dict[str, tuple[str, ...]]] = {
    "duskds": {
        "chain_id": ("chainId",),
        "rpc": ("urls", "node"),
        "explorer": ("apps", "explorer"),
    },
    "duskevm": {
        "chain_id": ("duskEvm", "chainId"),
        "rpc": ("duskEvm", "rpcUrl"),
        "explorer": ("duskEvm", "explorerUrl"),
    },
}

_MISSING = object()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- projection


def _dig(doc: Any, path: tuple[str, ...]) -> Any:
    for part in path:
        if not isinstance(doc, dict) or part not in doc:
            return _MISSING
        doc = doc[part]
    return doc


def project(network_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``{layer: {key: value}}`` for one Dusk network file. Keys Dusk does not give are left out."""
    out: dict[str, dict[str, Any]] = {}
    for layer, fields in PROJECTION.items():
        if layer == "duskevm" and not isinstance(network_doc.get("duskEvm"), dict):
            continue
        values = {key: _dig(network_doc, path) for key, path in fields.items()}
        values = {key: value for key, value in values.items() if value is not _MISSING}
        if values:
            out[layer] = values
    return out


def base_repo(catalog: Catalog, home: Repo | None = None) -> Repo | None:
    """The repo whose ``base.dusk`` applies: ``home`` if it has one, else the first in the catalog."""
    for repo in ([home] if home is not None else []) + list(catalog.repos):
        if DUSK.base_key in repo.index.get("base", {}):
            return repo
    return None


def projection(repo: Repo | None) -> dict[str, dict[str, dict[str, Any]]]:
    """``{network: {layer: {key: value}}}`` from ``repo``'s Dusk snapshot; empty without a base."""
    if repo is None or DUSK.base_key not in repo.index.get("base", {}):
        return {}
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for rel in SCHEMA_CHECKS[DUSK.name][1]:
        text = repo.read_text(f"{DUSK.snapshot_dir}/{rel}")
        if text is None:
            continue
        try:
            doc = json.loads(text)
        except ValueError:
            continue  # check_snapshot reports the broken file
        name = doc.get("name") if isinstance(doc, dict) else None
        if isinstance(name, str):
            out[name] = project(doc)
    return out


def networks(catalog: Catalog, home: Repo | None = None) -> dict[str, Any]:
    """Network data for readers (legacy files): index overlays, then Dusk's facts on top.

    Earlier repos win among overlays, so a private index can add keys the public one lacks.
    Dusk's facts win over every overlay; ``check`` reports an overlay that disagrees.
    """
    merged: dict[str, Any] = {}
    for repo in reversed(catalog.repos):
        for network, layers in repo.index.get("networks", {}).items():
            for layer, data in layers.items():
                merged.setdefault(network, {}).setdefault(layer, {}).update(data)
    for network, layers in projection(base_repo(catalog, home)).items():
        for layer, data in layers.items():
            merged.setdefault(network, {}).setdefault(layer, {}).update(data)
    return merged


# ---------------------------------------------------------------- offline checks


def check_base(catalog: Catalog, home: Repo) -> list[str]:
    errors: list[str] = []
    for spec in specs.UPSTREAMS.values():
        if spec.base_key in home.index.get("base", {}):
            errors += check_snapshot(home, spec)
    errors += check_networks(catalog, home)
    return errors


def check_snapshot(home: Repo, spec: specs.UpstreamSpec) -> list[str]:
    assert home.root is not None
    root = home.root
    base = home.index["base"][spec.base_key]
    lock_path = root / spec.lock_path
    if not lock_path.exists():
        return [f"{spec.lock_path}: missing (index.json base.{spec.base_key} names it)"]
    lock = jsonio.load(lock_path)
    errors = validate(lock, LOCK_SCHEMA, spec.lock_path)
    if errors:
        return errors
    if lock["upstream"] != spec.name or lock["package"] != spec.package:
        errors.append(f"{spec.lock_path}: is for {lock['upstream']} ({lock['package']}), not {spec.name}")
    if lock["version"] != base.get("version"):
        errors.append(f"{spec.lock_path}: version {lock['version']} != index.json base.{spec.base_key} {base.get('version')}")
    if lock["tag"] != f"{spec.tag_prefix}{lock['version']}" or lock["provenance"]["commit"] != lock["commit"]:
        errors.append(f"{spec.lock_path}: tag or provenance commit does not match version and commit")

    want = {f"{spec.snapshot_dir}/{rel}": source for rel, source in spec.files.items()}
    have = {path: item["source"] for path, item in lock["files"].items()}
    if have != want:
        errors.append(f"{spec.lock_path}: files {sorted(have.items())} != expected {sorted(want.items())}")
    for path, item in lock["files"].items():
        file = root / path
        if not file.is_file():
            errors.append(f"{path}: missing (listed in {spec.lock_path})")
        elif sha256(file.read_bytes()) != item["sha256"]:
            errors.append(f"{path}: sha256 differs from {spec.lock_path} (written only by upstream sync)")
    snapshot = root / spec.snapshot_dir
    if snapshot.exists():
        for file in sorted(p for p in snapshot.rglob("*") if p.is_file()):
            rel = file.relative_to(root).as_posix()
            if rel not in lock["files"]:
                errors.append(f"{rel}: not in {spec.lock_path}")
    if errors:
        return errors
    return check_snapshot_schema(root, spec)


def check_snapshot_schema(root: Path, spec: specs.UpstreamSpec) -> list[str]:
    if spec.name not in SCHEMA_CHECKS:
        return []
    schema_rel, documents = SCHEMA_CHECKS[spec.name]
    schema_path = f"{spec.snapshot_dir}/{schema_rel}"
    try:
        schema = jsonio.load(root / schema_path)
    except ValueError as exc:
        return [f"{schema_path}: not JSON: {exc}"]
    missing = unsupported(schema)
    if missing:
        return [f"{schema_path}: uses keywords nocturne-pins does not check: {', '.join(missing)}"]
    errors: list[str] = []
    for rel in documents:
        path = f"{spec.snapshot_dir}/{rel}"
        try:
            doc = jsonio.load(root / path)
        except ValueError as exc:
            errors.append(f"{path}: not JSON: {exc}")
            continue
        errors += validate_with(doc, schema, path)
        stem = rel.removesuffix(".json")
        if isinstance(doc, dict) and doc.get("name") != stem:
            errors.append(f"{path}: name {doc.get('name')!r} is not {stem!r}")
    return errors


def check_networks(catalog: Catalog, home: Repo) -> list[str]:
    """``index.json`` ``networks`` may add keys; a key Dusk gives must not be there with another value."""
    holder = base_repo(catalog, home)
    facts = projection(holder)
    if not facts:
        return []
    errors: list[str] = []
    version = holder.index["base"][DUSK.base_key]["version"] if holder else "?"
    for network, layers in home.index.get("networks", {}).items():
        for layer, data in layers.items():
            for key, ours in data.items():
                theirs = facts.get(network, {}).get(layer, {}).get(key, _MISSING)
                if theirs is _MISSING or _equal(ours, theirs):
                    continue
                field = ".".join(PROJECTION[layer][key])
                errors.append(
                    f"index.json networks.{network}.{layer}.{key} = {json.dumps(ours)} overrides Dusk "
                    f"{version} {network}.json {field} = {json.dumps(theirs)}; drop it (it comes from the base; "
                    f"an index may only add keys)"
                )
    return errors


def _equal(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------------------------------------------------------------- online check


def verify_remote(root: Path, spec: specs.UpstreamSpec, http: Http) -> list[str]:
    """Fetch the locked tarball again; it, the registry's record of it and the snapshot must all agree."""
    lock_path = root / spec.lock_path
    if not lock_path.exists():
        return [f"{spec.lock_path}: missing"]
    lock = jsonio.load(lock_path)
    errors = validate(lock, LOCK_SCHEMA, spec.lock_path)
    if errors:
        return errors
    release = spec.source(spec, http).release(lock["version"])
    if release.tarball_url != lock["tarball"]["url"]:
        errors.append(f"registry dist.tarball {release.tarball_url} != locked {lock['tarball']['url']}")
    if release.integrity != lock["tarball"]["integrity"]:
        errors.append(f"registry dist.integrity {release.integrity} != locked {lock['tarball']['integrity']}")
    tarball = http.get(lock["tarball"]["url"])
    try:
        check_integrity(tarball, lock["tarball"]["integrity"])
    except UpstreamError as exc:
        return errors + [f"refetched tarball: {exc}"]
    if sha256(tarball) != lock["tarball"]["sha256"]:
        return errors + [f"refetched tarball sha256 {sha256(tarball)} != locked {lock['tarball']['sha256']}"]
    members = read_members(tarball, sorted(set(spec.files.values())))
    for rel, source in spec.files.items():
        path = f"{spec.snapshot_dir}/{rel}"
        file = root / path
        if not file.is_file():
            errors.append(f"{path}: missing")
        elif file.read_bytes() != members[source]:
            errors.append(f"{path}: differs from {source} in the locked tarball")
    return errors


def cmd_verify(args: argparse.Namespace) -> int:
    spec = specs.get(args.upstream)
    try:
        errors = verify_remote(Path(args.root), spec, UrllibHttp())
    except Unreachable as exc:
        if not args.warn_unreachable:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        # An outage is not evidence of tampering; a wrong answer still fails.
        print(f"::warning::{spec.name}: registry unreachable, remote check skipped: {exc}")
        return 0
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"ok: {spec.name}: locked tarball refetched; bytes, registry record and snapshot agree")
    return 0


def add_cli(upstream_sub: Any) -> None:
    """``upstream verify`` (network): registered by ``cli.parser``."""
    sp = upstream_sub.add_parser("verify", help="refetch the locked tarball and compare bytes with lock and snapshot")
    sp.add_argument("--upstream", default=DUSK.name, choices=sorted(specs.UPSTREAMS))
    sp.add_argument("--root", default=".", help="pin repo checkout to read")
    sp.add_argument(
        "--warn-unreachable", action="store_true",
        help="exit 0 with a warning when the registry cannot be reached; mismatches still fail",
    )
    sp.set_defaults(func=cmd_verify)
