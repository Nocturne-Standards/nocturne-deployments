"""Full names: ``<product>/<name>/<network>/<tier>``."""

from __future__ import annotations

from dataclasses import dataclass

TIERS: tuple[str, ...] = ("experiment", "own", "public")
# Tiers that may be pinned at all. Experiments stay in local run records.
PINNED_TIERS: tuple[str, ...] = ("own", "public")
DEFAULT_NETWORK = "testnet"


class NameError_(ValueError):
    pass


@dataclass(frozen=True)
class FullName:
    product: str
    name: str
    network: str
    tier: str | None  # None means "use the reader's tier order"

    def __str__(self) -> str:
        tier = self.tier if self.tier is not None else "*"
        return f"{self.product}/{self.name}/{self.network}/{tier}"

    def with_tier(self, tier: str) -> "FullName":
        return FullName(self.product, self.name, self.network, tier)


def parse(text: str) -> FullName:
    """Parse ``product/name[/network[/tier]]``. A missing tier is left open."""
    parts = text.strip().split("/")
    if len(parts) < 2 or len(parts) > 4 or any(p == "" for p in parts):
        raise NameError_(f"not a full name: {text!r} (want product/name/network/tier)")
    product, name = parts[0], parts[1]
    network = parts[2] if len(parts) >= 3 else DEFAULT_NETWORK
    tier = parts[3] if len(parts) == 4 else None
    if tier == "*":
        tier = None
    if tier is not None and tier not in PINNED_TIERS:
        raise NameError_(f"tier {tier!r} is not pinned (pinned tiers: {', '.join(PINNED_TIERS)})")
    return FullName(product, name, network, tier)


@dataclass(frozen=True)
class EntryKey:
    """One current file: product, network, tier."""

    product: str
    network: str
    tier: str

    def __str__(self) -> str:
        return f"{self.product}/{self.network}/{self.tier}"


def parse_entry(text: str) -> EntryKey:
    parts = text.strip().split("/")
    if len(parts) != 3 or any(p == "" for p in parts):
        raise NameError_(f"not an entry: {text!r} (want product/network/tier)")
    if parts[2] not in PINNED_TIERS:
        raise NameError_(f"tier {parts[2]!r} is not pinned")
    return EntryKey(parts[0], parts[1], parts[2])


def reader_tiers(mode: str) -> tuple[str, ...]:
    """Tier order for a reader. ``own`` readers fall back to ``public``; docs read ``public`` only."""
    if mode == "own":
        return ("own", "public")
    if mode == "public":
        return ("public",)
    raise NameError_(f"unknown reader tier {mode!r} (own or public)")
