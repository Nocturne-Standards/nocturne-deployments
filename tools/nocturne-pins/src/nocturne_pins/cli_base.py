"""``network`` and ``labels``: Dusk's network facts from the base layer."""

from __future__ import annotations

import argparse
import json
from typing import Any, Callable

from . import dusk_names
from .base import LAYERS, NETWORKS, BaseError, BaseLayer, field_value, layer_view, of_catalog
from .repo import Catalog, RepoError


def _layer(args: argparse.Namespace) -> BaseLayer:
    catalog = Catalog.open(args.index or None)
    try:
        return of_catalog(catalog, args.reader)
    except BaseError as exc:
        raise RepoError(str(exc)) from exc


def _print_value(value: Any) -> None:
    print(value if isinstance(value, str) else json.dumps(value, indent=2))


def cmd_network(args: argparse.Namespace) -> int:
    if (args.network is None) == (args.chain_id is None):
        raise RepoError("network: give a network name or --chain-id, not both")
    layer = _layer(args)
    try:
        doc = layer.network(args.network) if args.network is not None else layer.by_chain_id(args.chain_id)
        view = layer_view(doc, args.layer)
        _print_value(field_value(view, args.field) if args.field else view)
    except BaseError as exc:
        raise RepoError(str(exc)) from exc
    return 0


def cmd_labels(args: argparse.Namespace) -> int:
    """One line per Dusk name: full name, layer, id, label, ``fallback`` when read live."""
    layer = _layer(args)
    try:
        docs = [layer.network(args.network)] if args.network else layer.networks()
    except BaseError as exc:
        raise RepoError(str(exc)) from exc
    for doc in docs:
        for name, item in dusk_names.entries(doc).items():
            full = f"{dusk_names.PRODUCT}/{name}/{doc['name']}/{dusk_names.TIER}"
            flag = "fallback" if item["fallback"] else ""
            print(f"{full}\t{item['layer']}\t{item['id']}\t{item['label'] or '-'}\t{flag}".rstrip("\t"))
    return 0


def add_parsers(sub: Any, readers: Callable[[argparse.ArgumentParser], None]) -> None:
    sp = sub.add_parser("network", help="Dusk's facts for one network (base layer)")
    sp.add_argument("network", nargs="?", choices=NETWORKS)
    sp.add_argument("--chain-id", type=int, help="pick the network by DuskDS chain id instead")
    sp.add_argument("--layer", choices=LAYERS, help="duskds: all but duskEvm; duskevm: the duskEvm part")
    sp.add_argument("--field", help="dotted path, e.g. urls.node; with --layer duskevm, e.g. rpcUrl")
    readers(sp)
    sp.set_defaults(func=cmd_network)

    sp = sub.add_parser("labels", help="Dusk's contracts and accounts by name, with their labels")
    sp.add_argument("network", nargs="?", choices=NETWORKS)
    readers(sp)
    sp.set_defaults(func=cmd_labels)
