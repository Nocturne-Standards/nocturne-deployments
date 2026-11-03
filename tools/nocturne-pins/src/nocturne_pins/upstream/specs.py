"""Known upstreams: where each comes from, how it is verified, what is kept."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .classify import UnclassifiedClassifier
from .content import GitHubContentVerifier, PackageJsonVerifier
from .model import Classifier, Source, UpstreamError, Verifier
from .net import Git, Http
from .npm import NpmSource
from .provenance import NpmProvenanceVerifier


@dataclass(frozen=True)
class UpstreamSpec:
    name: str  # key under upstream/ and in the lock
    base_key: str  # key under index.json "base"
    package: str
    repository: str  # https://github.com/<owner>/<repo>
    workflow: str
    tag_prefix: str
    # Snapshot file (relative to upstream/<name>/) -> path inside the package.
    files: dict[str, str]
    # Package paths that must be byte-equal to the tagged commit.
    compare: tuple[str, ...]
    source: Callable[["UpstreamSpec", Http], Source]
    verifiers: Callable[["UpstreamSpec", Http, Git], list[Verifier]]
    classifier: Callable[[], Classifier]
    # Checks the design asks for that this tool does not do yet; recorded in the lock.
    unchecked: tuple[str, ...] = ()

    @property
    def owner_repo(self) -> str:
        return self.repository.removeprefix("https://github.com/")

    @property
    def snapshot_dir(self) -> str:
        return f"upstream/{self.name}"

    @property
    def lock_path(self) -> str:
        return f"upstream/{self.name}.lock.json"


def _npm_source(spec: UpstreamSpec, http: Http) -> Source:
    members = sorted({*spec.files.values(), *spec.compare, "package.json"})
    return NpmSource(spec.package, members, spec.tag_prefix, http)


def _npm_verifiers(spec: UpstreamSpec, http: Http, git: Git) -> list[Verifier]:
    # Order matters: the content check needs the commit the provenance check establishes.
    return [
        PackageJsonVerifier(),
        NpmProvenanceVerifier(spec.repository, spec.workflow, http, git),
        GitHubContentVerifier(spec.owner_repo, list(spec.compare), http),
    ]


DUSK_NETWORKS = UpstreamSpec(
    name="dusk-networks",
    base_key="dusk",
    package="@dusk-network/networks",
    repository="https://github.com/dusk-network/networks",
    workflow=".github/workflows/publish.yml",
    tag_prefix="v",
    files={
        "mainnet.json": "networks/mainnet.json",
        "testnet.json": "networks/testnet.json",
        "devnet.json": "networks/devnet.json",
        "schema.json": "schema.json",
        "fixtures/sozu-vectors.json": "fixtures/sozu-vectors.json",
        "LICENSE": "LICENSE",
    },
    compare=(
        "networks/mainnet.json",
        "networks/testnet.json",
        "networks/devnet.json",
        "schema.json",
        "fixtures/sozu-vectors.json",
        "LICENSE",
        "package.json",
    ),
    source=_npm_source,
    verifiers=_npm_verifiers,
    classifier=UnclassifiedClassifier,
    unchecked=("JSR files equal npm (JSR is not read)",),
)

UPSTREAMS: dict[str, UpstreamSpec] = {DUSK_NETWORKS.name: DUSK_NETWORKS}


def get(name: str) -> UpstreamSpec:
    if name not in UPSTREAMS:
        raise UpstreamError(f"unknown upstream {name!r} (known: {', '.join(sorted(UPSTREAMS))})")
    return UPSTREAMS[name]
