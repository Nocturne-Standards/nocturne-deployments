"""Plug-in interfaces of the upstream watcher: source, verifier, classifier.

A source finds and fetches one release. Verifiers each check one trust anchor and
record what they checked, and what they could not. A classifier says what a change
means for consumers. ``sync`` only wires them together, so a second upstream adds
its own source and verifiers without touching the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class UpstreamError(ValueError):
    """Any refusal: unknown version, failed verification, bad tarball."""


class VerificationError(UpstreamError):
    pass


@dataclass(frozen=True)
class Release:
    """What the registry says about one version, before anything is downloaded."""

    package: str
    version: str
    tag: str
    tarball_url: str
    integrity: str
    attestations_url: str
    registry: str


@dataclass(frozen=True)
class Artifact:
    """A fetched release. ``files`` maps package-relative paths to bytes (read in memory, never run)."""

    release: Release
    tarball: bytes
    files: dict[str, bytes]


@dataclass
class Evidence:
    """What the verifiers established, in order. Ends up in the lock."""

    commit: str | None = None
    provenance: dict[str, object] = field(default_factory=dict)
    verified: list[str] = field(default_factory=list)
    not_verified: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Classification:
    classes: tuple[str, ...]
    note: str


class Source(Protocol):
    def latest(self) -> str: ...

    def release(self, version: str) -> Release: ...

    def fetch(self, release: Release, evidence: Evidence) -> Artifact:
        """Download and check transport integrity before opening anything."""
        ...


class Verifier(Protocol):
    def verify(self, artifact: Artifact, evidence: Evidence) -> None:
        """Raise ``VerificationError`` on failure; append to ``evidence`` on success."""
        ...


class Classifier(Protocol):
    def classify(self, old: dict[str, bytes] | None, new: dict[str, bytes]) -> Classification: ...
