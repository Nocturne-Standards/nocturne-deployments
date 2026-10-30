"""Source plug-in: a package on the npm registry."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import posixpath
import re
import tarfile
import urllib.parse
from typing import Any, Callable

from .model import Artifact, Evidence, Release, UpstreamError, VerificationError
from .net import Http

REGISTRY = "https://registry.npmjs.org"
# Plain releases only: a pre-release tag (0.0.0-stage) is never a base layer.
VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
MAX_UNPACKED = 50 * 1024 * 1024


def check_version(version: str) -> str:
    if VERSION.match(version) is None:
        raise UpstreamError(f"version {version!r} is not a plain X.Y.Z release")
    return version


def version_key(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in check_version(version).split("."))
    return major, minor, patch


def check_integrity(tarball: bytes, integrity: str) -> str:
    """Compare against every sha512 entry of an SRI string; return the matching entry."""
    entries = [item for item in integrity.split() if item.startswith("sha512-")]
    if not entries:
        raise VerificationError(f"dist.integrity {integrity!r} has no sha512 entry")
    actual = "sha512-" + base64.b64encode(hashlib.sha512(tarball).digest()).decode("ascii")
    for entry in entries:
        if entry.split("?", 1)[0] != actual:
            raise VerificationError(f"tarball sha512 {actual} != dist.integrity {entry}")
    return actual


def read_selected(tarball: bytes, select: Callable[[str], bool], prefix: str = "package/") -> dict[str, bytes]:
    """Read the regular files under ``prefix`` that ``select`` picks from an npm tarball, in memory.

    Nothing is written to disk and nothing from the tarball is run. Every member path is
    checked; a picked member that is a link or device file, or a name present twice, is refused.
    """
    out: dict[str, bytes] = {}
    total = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise UpstreamError(f"tarball does not open: {exc}") from exc
    with archive:
        try:
            members = archive.getmembers()
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise UpstreamError(f"tarball does not read: {exc}") from exc
        for member in members:
            name = member.name
            if not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
            if posixpath.normpath(rel) != rel or rel.startswith("/") or rel.startswith(".."):
                raise UpstreamError(f"tarball member {name!r} has an unsafe path")
            if not select(rel):
                continue
            if not member.isreg():
                raise UpstreamError(f"tarball member {name!r} is not a regular file")
            if rel in out:
                raise UpstreamError(f"tarball has {name!r} twice")
            total += member.size
            if total > MAX_UNPACKED:
                raise UpstreamError("tarball unpacks to more than the size limit")
            handle = archive.extractfile(member)
            if handle is None:
                raise UpstreamError(f"tarball member {name!r} has no data")
            out[rel] = handle.read()
    return out


def read_members(tarball: bytes, wanted: list[str], prefix: str = "package/") -> dict[str, bytes]:
    """Read exactly the regular files named in ``wanted`` (see ``read_selected``); all must be there."""
    want = set(wanted)
    out = read_selected(tarball, want.__contains__, prefix)
    missing = sorted(want - out.keys())
    if missing:
        raise UpstreamError(f"tarball lacks {', '.join(missing)}")
    return out


class NpmSource:
    def __init__(self, package: str, members: list[str], tag_prefix: str, http: Http, registry: str = REGISTRY) -> None:
        self.package = package
        self.members = members
        self.tag_prefix = tag_prefix
        self.http = http
        self.registry = registry

    def _quoted(self) -> str:
        # Scoped names keep "@" and encode the slash: @scope%2fname.
        return urllib.parse.quote(self.package, safe="@")

    def _metadata(self) -> dict[str, Any]:
        value = json.loads(self.http.get(f"{self.registry}/{self._quoted()}"))
        if not isinstance(value, dict) or value.get("name") != self.package:
            raise UpstreamError(f"registry metadata is not for {self.package}")
        return value

    def latest(self) -> str:
        latest = self._metadata().get("dist-tags", {}).get("latest")
        if not isinstance(latest, str):
            raise UpstreamError(f"{self.package}: no dist-tags.latest")
        return latest

    def release(self, version: str) -> Release:
        check_version(version)
        meta = self._metadata().get("versions", {}).get(version)
        if not isinstance(meta, dict):
            raise UpstreamError(f"{self.package}@{version} is not on {self.registry}")
        dist = meta.get("dist", {})
        tarball = dist.get("tarball")
        integrity = dist.get("integrity")
        if not isinstance(tarball, str) or not isinstance(integrity, str):
            raise UpstreamError(f"{self.package}@{version}: registry gives no dist.tarball or dist.integrity")
        registry_host = urllib.parse.urlparse(self.registry).hostname
        parsed = urllib.parse.urlparse(tarball)
        if parsed.scheme != "https" or parsed.hostname != registry_host:
            raise UpstreamError(f"{self.package}@{version}: tarball URL {tarball} is not on {self.registry}")
        # Built from the name, not taken from the metadata, so the registry cannot point elsewhere.
        attestations = f"{self.registry}/-/npm/v1/attestations/{self._quoted()}@{version}"
        return Release(
            package=self.package, version=version, tag=f"{self.tag_prefix}{version}", tarball_url=tarball,
            integrity=integrity, attestations_url=attestations, registry=self.registry,
        )

    def fetch(self, release: Release, evidence: Evidence) -> Artifact:
        """Download, check the bytes against ``dist.integrity``, and only then open the tarball."""
        tarball = self.http.get(release.tarball_url)
        check_integrity(tarball, release.integrity)
        evidence.verified.append("tarball sha512 equals npm dist.integrity")
        return Artifact(release=release, tarball=tarball, files=read_members(tarball, self.members))
