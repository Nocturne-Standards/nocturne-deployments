"""Replay a history log into its current file.

The current file is never written by hand: it is exactly what its history replays to.
CI and every writer use this same function.
"""

from __future__ import annotations

import json
from typing import Any

from . import jsonio
from .names import EntryKey
from .schema import validate

CONFIG_EVENTS: tuple[str, ...] = ("wired", "admin", "proposal")


class ReplayError(ValueError):
    pass


def parse_lines(lines: list[str], where: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    last_at = ""
    for n, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReplayError(f"{where}:{n}: not JSON: {exc}") from exc
        if jsonio.dump_line(event) != line:
            raise ReplayError(f"{where}:{n}: line is not in canonical form (compact, key order kept)")
        errors = validate(event, "nocturne.history.v1", f"{where}:{n}")
        if errors:
            raise ReplayError("; ".join(errors))
        if event["at"] < last_at:
            raise ReplayError(f"{where}:{n}: at {event['at']} is older than the line before ({last_at})")
        last_at = event["at"]
        events.append(event)
    return events


def since_of(event: dict[str, Any]) -> str | None:
    if event.get("tx"):
        return event["tx"]
    if event.get("block") is not None:
        return f"block:{event['block']}"
    return None


def apply_sets(state: dict[str, Any], event: dict[str, Any]) -> None:
    for key, value in event.get("sets", {}).items():
        if value is None:
            state.pop(key, None)
            continue
        state[key] = {"value": value, "since": since_of(event), "at": event["at"]}


def replay(entry: EntryKey, lines: list[str], where: str = "history") -> dict[str, Any]:
    events = parse_lines(lines, where)
    contracts: dict[str, Any] = {}
    state: dict[str, Any] = {}
    last_replaced: dict[str, str] = {}

    for n, event in enumerate(events, start=1):
        kind = event["event"]
        at = f"{where}:{n}"
        if kind == "deployed":
            name = event["name"]
            if name in contracts:
                last_replaced[name] = contracts[name]["id"]
            contract: dict[str, Any] = {
                "layer": event["layer"],
                "id": event["id"],
                "version": event.get("version"),
                "tx": since_of(event),
                "at": event["at"],
            }
            if event.get("refs"):
                contract["refs"] = event["refs"]
            contract["record"] = event["record"]
            contracts[name] = contract
            apply_sets(state, event)
        elif kind == "superseded":
            name = event["name"]
            live = contracts.get(name)
            if live is None or live["id"] != event["by"] or last_replaced.get(name) != event["id"]:
                raise ReplayError(f"{at}: superseded {name} {event['id'][:12]} by {event['by'][:12]} does not follow its deploy")
            del last_replaced[name]
        elif kind == "retired":
            name = event["name"]
            live = contracts.get(name)
            if live is None or live["id"] != event["id"]:
                raise ReplayError(f"{at}: retired {name} {event['id'][:12]} is not live")
            del contracts[name]
            apply_sets(state, event)
        elif kind in CONFIG_EVENTS or kind == "note":
            apply_sets(state, event)
        elif kind == "call":
            raise ReplayError(f"{at}: demo calls do not belong in a history log")
        else:
            raise ReplayError(f"{at}: unknown event {kind!r}")

    return {
        "schema": "nocturne.pins.v2",
        "product": entry.product,
        "network": entry.network,
        "tier": entry.tier,
        "contracts": contracts,
        "state": state,
        "events": len(events),
        "last_event": jsonio.line_hash(lines[-1]) if lines else None,
    }
