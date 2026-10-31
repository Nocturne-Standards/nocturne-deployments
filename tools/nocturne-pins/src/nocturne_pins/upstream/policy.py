"""Adoption policy: the floor, its grace, the lag limit, and what a consumer's pin means.

The lock publishes four things consumers are measured against:

- ``latest``: the newest verified release, the one consumers should be on. Always the locked
  version: a release we have not verified is never a target.
- ``latest_at``: when ``latest`` was first locked; the lag clock starts there.
- ``floor``: the lowest version with no outstanding safety (S) change. It moves only when a
  release with an S change is synced; D and A changes never move it.
- ``grace_until``: ``policy: immediate`` consumers below the floor fail after this time.

``policy`` in the lock records the numbers: S grace 48 h; lag at most 30 days after
``latest`` was locked or one minor version behind it. Past that lag is a failure for every
policy; below the floor after the grace fails ``immediate`` consumers and warns
``next-release`` ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .npm import VERSION, version_key

GRACE_HOURS = 48
MAX_LAG_DAYS = 30
MAX_LAG_MINORS = 1
POLICY: dict[str, int] = {"s_grace_hours": GRACE_HOURS, "max_lag_days": MAX_LAG_DAYS, "max_lag_minors": MAX_LAG_MINORS}
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
FLOOR_KEYS = ("latest", "latest_at", "floor", "grace_until", "policy")

# Statuses, worst first. The two failures fail the sweep (exit 1).
FAIL = "below-floor-past-grace"
LAG_FAIL = "lag-past-max"
FAILURES = (FAIL, LAG_FAIL)
STATUSES = (FAIL, LAG_FAIL, "not-exact-pin", "unreadable", "no-pin", "ahead", "lag-warn", "planned", "ok")
SEVERITY = {FAIL: "fail", LAG_FAIL: "fail", "not-exact-pin": "warn", "unreadable": "warn", "no-pin": "warn",
            "ahead": "warn", "lag-warn": "warn", "planned": "info", "ok": "ok"}


def parse_time(text: str) -> datetime:
    return datetime.strptime(text, TIME_FORMAT).replace(tzinfo=timezone.utc)


def format_time(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(TIME_FORMAT)


def floor_fields(old: dict[str, Any] | None, version: str, classes: tuple[str, ...], now: str) -> dict[str, Any]:
    """``latest``, ``latest_at``, ``floor``, ``grace_until`` and ``policy`` for a lock of ``version``.

    - First lock, or an old lock without a floor: nothing is known about what came before, so
      the floor is this version (the safe side) with a fresh grace.
    - A newer version with an S change: the floor moves to it, grace starts now.
    - Anything else (same version, D or A only): floor and grace carry over. A downgrade
      below the floor lowers the floor to the locked version, since the floor may not be
      above ``latest``.
    """
    grace = format_time(parse_time(now) + timedelta(hours=GRACE_HOURS))
    if old is None or "floor" not in old:
        # A lock from before these fields: the same release keeps the time it was first locked.
        since = old["verified_at"] if old is not None and old["version"] == version else now
        grace = format_time(parse_time(since) + timedelta(hours=GRACE_HOURS))
        return {"latest": version, "latest_at": since, "floor": version, "grace_until": grace, "policy": dict(POLICY)}
    same = old["version"] == version
    latest_at = old.get("latest_at", now) if same else now
    floor, grace_until = old["floor"], old["grace_until"]
    if not same and "S" in classes and version_key(version) > version_key(old["version"]):
        floor, grace_until = version, grace
    elif version_key(floor) > version_key(version):
        floor = version
    return {"latest": version, "latest_at": latest_at, "floor": floor, "grace_until": grace_until,
            "policy": dict(POLICY)}


@dataclass(frozen=True)
class Assessment:
    status: str
    detail: str


def minor_distance(pinned: str, latest: str) -> int:
    """How many minor versions ``pinned`` is behind ``latest`` (a major counts as many)."""
    have, want = version_key(pinned), version_key(latest)
    if have[0] != want[0]:
        return 1000 * (want[0] - have[0])
    return want[1] - have[1]


def lag_over(pinned: str, lock: dict[str, Any], now: str) -> list[str]:
    """Why ``pinned`` is past ``max_lag`` behind ``latest`` (empty: within it)."""
    latest = lock.get("latest", lock["version"])
    policy = lock.get("policy") or POLICY
    max_minors, max_days = policy.get("max_lag_minors", MAX_LAG_MINORS), policy.get("max_lag_days", MAX_LAG_DAYS)
    if version_key(pinned) >= version_key(latest):
        return []
    over = []
    if minor_distance(pinned, latest) > max_minors:
        over.append(f"more than {max_minors} minor behind")
    latest_at = lock.get("latest_at")
    if latest_at and parse_time(now) - parse_time(latest_at) > timedelta(days=max_days):
        over.append(f"{latest} locked more than {max_days} days ago ({latest_at})")
    return over


def assess(pinned: str, policy: str, lock: dict[str, Any], now: str) -> Assessment:
    """Status of an exact pin against the lock. The pin form is checked by the caller."""
    if VERSION.match(pinned) is None:
        return Assessment("not-exact-pin", f"{pinned!r} is not an exact X.Y.Z version")
    latest = lock.get("latest", lock["version"])
    floor = lock.get("floor")
    grace_until = lock.get("grace_until")
    if version_key(pinned) > version_key(latest):
        return Assessment("ahead", f"{pinned} is newer than the locked {latest} (not verified by us)")
    if version_key(pinned) == version_key(latest):
        return Assessment("ok", f"{pinned} is the locked version")
    below = floor is not None and grace_until is not None and version_key(pinned) < version_key(floor)
    past = below and parse_time(now) >= parse_time(grace_until)
    if past and policy == "immediate":
        return Assessment(FAIL, f"{pinned} is below the safety floor {floor}; grace ended {grace_until}")
    over = lag_over(pinned, lock, now)
    if over:
        return Assessment(LAG_FAIL, f"{pinned} is behind {latest}, past max_lag: {'; '.join(over)}")
    if past:
        return Assessment("lag-warn", f"{pinned} is below the safety floor {floor} (grace ended {grace_until}); "
                                      f"policy next-release: take it in the next release")
    if below:
        return Assessment("lag-warn", f"{pinned} is below the safety floor {floor}; update before {grace_until}")
    return Assessment("lag-warn", f"{pinned} is behind {latest} (within max_lag)")


def worst(statuses: list[str]) -> str:
    return min(statuses, key=STATUSES.index) if statuses else "ok"
