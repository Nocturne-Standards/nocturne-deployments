"""Verifier plug-ins on the package content: manifest fields, and bytes equal to the tagged commit."""

from __future__ import annotations

import json

from .model import Artifact, Evidence, VerificationError
from .net import Http, NotFound


class PackageJsonVerifier:
    """``package.json`` names the package and version that was asked for."""

    def verify(self, artifact: Artifact, evidence: Evidence) -> None:
        release = artifact.release
        try:
            manifest = json.loads(artifact.files["package.json"])
        except (KeyError, ValueError) as exc:
            raise VerificationError(f"package.json missing or unreadable: {exc}") from exc
        if manifest.get("name") != release.package:
            raise VerificationError(f"package.json name {manifest.get('name')!r} is not {release.package}")
        if manifest.get("version") != release.version:
            raise VerificationError(f"package.json version {manifest.get('version')!r} is not {release.version}")
        evidence.verified.append("package.json name and version match")


class GitHubContentVerifier:
    """Each listed file in the tarball is byte-equal to the same path at the provenance commit."""

    def __init__(self, owner_repo: str, paths: list[str], http: Http) -> None:
        self.owner_repo = owner_repo
        self.paths = paths
        self.http = http

    def verify(self, artifact: Artifact, evidence: Evidence) -> None:
        if evidence.commit is None:
            raise VerificationError("no commit established before the content check")
        for path in self.paths:
            url = f"https://raw.githubusercontent.com/{self.owner_repo}/{evidence.commit}/{path}"
            try:
                upstream = self.http.get(url)
            except NotFound as exc:
                raise VerificationError(f"{path} is not in {self.owner_repo} at {evidence.commit}") from exc
            if artifact.files.get(path) != upstream:
                raise VerificationError(f"{path} in the tarball differs from {self.owner_repo} at {evidence.commit}")
        evidence.verified.append(f"byte-equal to GitHub at the commit: {', '.join(self.paths)}")
