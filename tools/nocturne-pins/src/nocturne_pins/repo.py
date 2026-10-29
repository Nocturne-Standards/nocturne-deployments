"""Pin repos (local checkout or URL) and the catalog that reads several of them."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import jsonio
from .names import EntryKey, FullName, reader_tiers
from .schema import validate

PUBLIC_INDEX_URL = "https://raw.githubusercontent.com/Nocturne-Standards/nocturne-deployments/main/index.json"
# Checkout names the walk-up looks for, private first so it shadows the public repo.
SIBLING_NAMES: tuple[str, ...] = ("nocturne-deployments-internal", "nocturne-deployments")
ENV_INDEXES = "NOCTURNE_PINS"
ENV_TOKEN = "NOCTURNE_PINS_TOKEN"


class RepoError(ValueError):
    pass


TOKEN_HOSTS: tuple[str, ...] = ("raw.githubusercontent.com", "api.github.com")


def _fetch(url: str) -> str:
    request = urllib.request.Request(url)
    token = os.environ.get(ENV_TOKEN)
    host = urllib.parse.urlparse(url).hostname or ""
    # Never hand the token to a host named by repo content or a typo.
    if token and url.startswith("https://") and host in TOKEN_HOSTS:
        request.add_header("Authorization", f"token {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


@dataclass
class Repo:
    """One pin repo. ``root`` for a checkout, ``base_url`` for a remote read-only copy."""

    index: dict[str, Any]
    root: Path | None = None
    base_url: str | None = None
    _cache: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def open(cls, location: str | Path) -> "Repo":
        text = str(location)
        if text.startswith("http://") or text.startswith("https://"):
            base = text[: -len("index.json")] if text.endswith("index.json") else text.rstrip("/") + "/"
            index = json.loads(_fetch(base + "index.json"))
            repo = cls(index=index, base_url=base)
        else:
            path = Path(text).expanduser()
            root = path.parent if path.name == "index.json" else path
            index_path = root / "index.json"
            if not index_path.exists():
                raise RepoError(f"no index.json in {root}")
            repo = cls(index=jsonio.load(index_path), root=root.resolve())
        if repo.index.get("schema") != "nocturne.index.v1":
            raise RepoError(f"{repo.where}: index schema is {repo.index.get('schema')!r}, want nocturne.index.v1")
        return repo

    @property
    def where(self) -> str:
        return str(self.root) if self.root is not None else str(self.base_url)

    @property
    def name(self) -> str:
        return self.index["repo"]

    @property
    def is_public(self) -> bool:
        return self.index.get("visibility") == "public"

    def index_errors(self) -> list[str]:
        return validate(self.index, "nocturne.index.v1", f"{self.where}/index.json")

    def entries(self) -> list[dict[str, Any]]:
        return list(self.index.get("entries", []))

    def entry(self, key: EntryKey) -> dict[str, Any] | None:
        for item in self.entries():
            if (item["product"], item["network"], item["tier"]) == (key.product, key.network, key.tier):
                return item
        return None

    def read_text(self, rel: str) -> str | None:
        if self.root is not None:
            path = self.root / rel
            return path.read_text(encoding="utf-8") if path.exists() else None
        try:
            return _fetch(str(self.base_url) + rel)
        except Exception:
            return None

    def current(self, key: EntryKey) -> dict[str, Any] | None:
        item = self.entry(key)
        if item is None:
            return None
        cache_key = "current:" + item["current"]
        if cache_key not in self._cache:
            text = self.read_text(item["current"])
            self._cache[cache_key] = json.loads(text) if text is not None else None
        return self._cache[cache_key]

    def history_lines(self, key: EntryKey) -> list[str]:
        item = self.entry(key)
        if item is None:
            return []
        text = self.read_text(item["history"])
        if text is None:
            return []
        return [line for line in text.split("\n") if line.strip() != ""]

    def calls_path(self, key: EntryKey) -> str | None:
        for item in self.index.get("calls", []):
            if (item["product"], item["network"], item["tier"]) == (key.product, key.network, key.tier):
                return item["path"]
        return None

    def calls_lines(self, key: EntryKey) -> list[str]:
        """Demo traffic for an entry, which may itself live in another repo."""
        rel = self.calls_path(key)
        if rel is None:
            return []
        text = self.read_text(rel)
        if text is None:
            return []
        return [line for line in text.split("\n") if line.strip() != ""]


def entry_key(item: dict[str, Any]) -> EntryKey:
    return EntryKey(item["product"], item["network"], item["tier"])


def discover(start: Path | None = None) -> list[str]:
    """Index locations: ``$NOCTURNE_PINS`` (os.pathsep list), else checkouts found walking up."""
    env = os.environ.get(ENV_INDEXES)
    if env:
        return [part for part in env.split(os.pathsep) if part.strip()]
    found: list[str] = []
    here = (start or Path.cwd()).resolve()
    for directory in [here, *here.parents]:
        own = directory / "index.json"
        if own.exists() and _is_v1_index(own) and str(directory.resolve()) not in found:
            found.append(str(directory.resolve()))
        # Stop at the first level holding pin checkouts side by side (the usual workspace layout).
        sibling_found = False
        for name in SIBLING_NAMES:
            candidate = (directory / name).resolve()
            if (candidate / "index.json").exists() and _is_v1_index(candidate / "index.json"):
                sibling_found = True
                if str(candidate) not in found:
                    found.append(str(candidate))
        if sibling_found:
            break
    return _private_first(found)


def _is_v1_index(path: Path) -> bool:
    try:
        return jsonio.load(path).get("schema") == "nocturne.index.v1"
    except (OSError, ValueError):
        return False


def _private_first(locations: list[str]) -> list[str]:
    def rank(location: str) -> int:
        try:
            visibility = jsonio.load(Path(location) / "index.json").get("visibility")
        except (OSError, ValueError):
            return 1
        return 0 if visibility == "private" else 1

    return sorted(locations, key=rank)


@dataclass
class Catalog:
    """Several repos in precedence order; the first repo holding an entry wins (private shadows public)."""

    repos: list[Repo]

    @classmethod
    def open(cls, locations: list[str] | None = None, allow_remote_default: bool = True) -> "Catalog":
        locations = list(locations) if locations else discover()
        if not locations and allow_remote_default:
            locations = [PUBLIC_INDEX_URL]
        if not locations:
            raise RepoError("no pin repo found (set NOCTURNE_PINS or pass --index)")
        repos = [Repo.open(loc) for loc in locations]
        # Private shadows public whatever order the locations were given in.
        return cls(sorted(repos, key=lambda r: r.is_public))

    def holder(self, key: EntryKey) -> Repo | None:
        for repo in self.repos:
            if repo.entry(key) is not None:
                return repo
        return None

    def entries(self) -> list[tuple[Repo, EntryKey]]:
        seen: set[EntryKey] = set()
        out: list[tuple[Repo, EntryKey]] = []
        for repo in self.repos:
            for item in repo.entries():
                key = entry_key(item)
                if key in seen:
                    continue
                seen.add(key)
                out.append((repo, key))
        return out

    def find(self, name: FullName, reader: str = "own") -> tuple[Repo, EntryKey, dict[str, Any]]:
        tiers = (name.tier,) if name.tier is not None else reader_tiers(reader)
        if name.tier is not None and name.tier not in reader_tiers(reader):
            raise RepoError(f"{name}: a {reader} reader does not read tier {name.tier}")
        for tier in tiers:
            key = EntryKey(name.product, name.network, tier)
            repo = self.holder(key)
            if repo is None:
                continue
            current = repo.current(key)
            if current is None:
                continue
            contract = current["contracts"].get(name.name)
            if contract is not None:
                return repo, key, contract
        raise RepoError(f"{name}: not found (tiers tried: {', '.join(tiers)})")

    def resolve(self, name: FullName, reader: str = "own") -> str:
        return self.find(name, reader)[2]["id"]

    def state(self, key: EntryKey) -> dict[str, Any]:
        repo = self.holder(key)
        if repo is None:
            raise RepoError(f"{key}: no such entry")
        current = repo.current(key)
        if current is None:
            raise RepoError(f"{key}: current file missing in {repo.where}")
        return current["state"]

    def current_record(self, key: str, network: str = "testnet") -> dict[str, Any] | None:
        """Latest v1-shaped row for a contract name or a wiring key (``contract.fn[.label]``)."""
        for repo, entry in self.entries():
            if entry.network != network:
                continue
            current = repo.current(entry)
            if current is not None and key in current["contracts"]:
                return current["contracts"][key]["record"]
        found: dict[str, Any] | None = None
        for repo, entry in self.entries():
            if entry.network != network:
                continue
            lines = repo.history_lines(entry) + [l for r in self.repos for l in r.calls_lines(entry)]
            for line in lines:
                event = json.loads(line)
                if event.get("key") == key and "record" in event:
                    if found is None or event["at"] >= found["at"]:
                        found = event
        return found["record"] if found else None

