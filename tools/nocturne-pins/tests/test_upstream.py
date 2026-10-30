"""``upstream sync`` / ``upstream status`` against a fake registry, GitHub and git remote (no network)."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from nocturne_pins import cli, jsonio
from nocturne_pins.check import check_repo
from nocturne_pins.repo import Catalog, Repo
from nocturne_pins.schema import SCHEMA_FILES, schema_text
from nocturne_pins.upstream import UpstreamError, VerificationError, provenance
from nocturne_pins.upstream.net import NotFound
from nocturne_pins.upstream.npm import read_members
from nocturne_pins.upstream.specs import DUSK_NETWORKS as SPEC
from nocturne_pins.upstream.sync import status, sync

FIXTURES = Path(__file__).parent / "fixtures" / "upstream"
REGISTRY = "https://registry.npmjs.org"
PACKAGE = "@dusk-network/networks"
REPO = "https://github.com/dusk-network/networks"
WORKFLOW = ".github/workflows/publish.yml"
COMMIT = "c0" * 20
TAG_OBJECT = "7a" * 20
CLOCK = "2026-10-30T12:00:00Z"


def package_files(version: str) -> dict[str, bytes]:
    return {
        "package.json": json.dumps({"name": PACKAGE, "version": version}).encode(),
        "networks/mainnet.json": b'{"chainId": 1, "name": "mainnet"}\n',
        "networks/testnet.json": b'{"chainId": 2, "name": "testnet"}\n',
        "networks/devnet.json": b'{"chainId": 3, "name": "devnet"}\n',
        "schema.json": b'{"type": "object"}\n',
        "fixtures/sozu-vectors.json": b'{"vectors": []}\n',
        "LICENSE": b"Mozilla Public License Version 2.0\n",
        "src/index.js": b"export const never = 'run';\n",
    }


def make_tarball(files: dict[str, bytes], extra: list[tarfile.TarInfo] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(f"package/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        for info in extra or []:
            archive.addfile(info, io.BytesIO(b"x" * info.size) if info.isreg() else None)
    return buffer.getvalue()


def sri(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


def statement(version: str, tarball: bytes, **over: Any) -> dict[str, Any]:
    ref = over.get("ref", f"refs/tags/v{version}")
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": provenance.purl(PACKAGE, version),
                     "digest": {"sha512": over.get("sha512", hashlib.sha512(tarball).hexdigest())}}],
        "predicateType": provenance.SLSA_V1,
        "predicate": {
            "buildDefinition": {
                "buildType": provenance.GITHUB_BUILD_TYPE,
                "externalParameters": {"workflow": {
                    "ref": ref, "repository": over.get("repository", REPO), "path": over.get("workflow", WORKFLOW),
                }},
                "resolvedDependencies": [{"uri": f"git+{over.get('repository', REPO)}@{ref}",
                                          "digest": {"gitCommit": over.get("commit", COMMIT)}}],
            },
            "runDetails": {
                "builder": {"id": over.get("builder", provenance.GITHUB_HOSTED)},
                "metadata": {"invocationId": f"{REPO}/actions/runs/1/attempts/1"},
            },
        },
    }


def attestations(version: str, tarball: bytes, cert_identity: str | None = None, **over: Any) -> bytes:
    body = statement(version, tarball, **over)
    identity = cert_identity or f"{REPO}/{WORKFLOW}@refs/tags/v{version}"
    cert = b"\x30\x82junk" + identity.encode() + b"\x06more"
    entry = {
        "predicateType": provenance.SLSA_V1,
        "bundle": {
            "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
            "verificationMaterial": {
                "certificate": {"rawBytes": base64.b64encode(cert).decode()},
                "tlogEntries": [{"logIndex": "42", "integratedTime": "1793311799"}],
            },
            "dsseEnvelope": {"payloadType": provenance.IN_TOTO,
                             "payload": base64.b64encode(json.dumps(body).encode()).decode(), "signatures": []},
        },
    }
    return json.dumps({"attestations": [entry]}).encode()


@dataclass
class World:
    """A fake npm registry, GitHub raw host and git remote for one or more versions."""

    responses: dict[str, bytes] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=dict)
    versions: dict[str, Any] = field(default_factory=dict)
    latest: str = ""
    fetched: list[str] = field(default_factory=list)

    def get(self, url: str) -> bytes:
        self.fetched.append(url)
        if url == f"{REGISTRY}/@dusk-network%2Fnetworks":
            return json.dumps({"name": PACKAGE, "dist-tags": {"latest": self.latest}, "versions": self.versions}).encode()
        if url not in self.responses:
            raise NotFound(url)
        return self.responses[url]

    def ls_remote(self, url: str, refs: list[str]) -> dict[str, str]:
        assert url == REPO
        return {ref: sha for ref, sha in self.refs.items() if ref in refs}

    def publish(self, version: str, files: dict[str, bytes] | None = None, tarball: bytes | None = None,
                integrity: str | None = None, attest: bytes | None = None, github: dict[str, bytes] | None = None,
                commit: str = COMMIT) -> bytes:
        files = files or package_files(version)
        tarball = tarball if tarball is not None else make_tarball(files)
        url = f"{REGISTRY}/@dusk-network/networks/-/networks-{version}.tgz"
        self.versions[version] = {"dist": {"tarball": url, "integrity": integrity or sri(tarball)}}
        self.responses[url] = tarball
        self.responses[f"{REGISTRY}/-/npm/v1/attestations/@dusk-network%2Fnetworks@{version}"] = (
            attest if attest is not None else attestations(version, tarball, commit=commit)
        )
        for path, data in (github or files).items():
            self.responses[f"https://raw.githubusercontent.com/dusk-network/networks/{commit}/{path}"] = data
        self.refs[f"refs/tags/v{version}"] = commit
        self.latest = version
        return tarball


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "schema").mkdir(parents=True)
    for schema_id, file_name in SCHEMA_FILES.items():
        (root / "schema" / file_name).write_text(schema_text(schema_id))
    index = {
        "schema": "nocturne.index.v1",
        "repo": "Nocturne-Standards/nocturne-deployments",
        "visibility": "public",
        "networks": {"testnet": {"duskds": {"chain_id": 2, "rpc": "https://node", "explorer": None}}},
        "entries": [],
    }
    (root / "index.json").write_text(jsonio.dump_file(index))
    return root


def run(root: Path, world: World, version: str, **kw: Any):  # noqa: ANN201
    return sync(root, SPEC, version, world, world, clock=lambda: CLOCK, **kw)


@pytest.fixture
def world() -> World:
    w = World()
    w.publish("0.3.0")
    return w


def test_sync_writes_snapshot_lock_and_index(tmp_path: Path, world: World) -> None:
    root = make_root(tmp_path)
    result = run(root, world, "0.3.0")
    assert result.changed
    files = package_files("0.3.0")
    for rel, src in SPEC.files.items():
        assert (root / "upstream/dusk-networks" / rel).read_bytes() == files[src]
    assert not (root / "upstream/dusk-networks/src").exists()
    lock = jsonio.load(root / "upstream/dusk-networks.lock.json")
    assert lock["version"] == "0.3.0" and lock["tag"] == "v0.3.0" and lock["commit"] == COMMIT
    assert lock["tarball"]["integrity"] == world.versions["0.3.0"]["dist"]["integrity"]
    tarball = world.responses[world.versions["0.3.0"]["dist"]["tarball"]]
    assert lock["tarball"]["sha256"] == hashlib.sha256(tarball).hexdigest()
    assert lock["files"]["upstream/dusk-networks/testnet.json"] == {
        "source": "networks/testnet.json", "sha256": hashlib.sha256(files["networks/testnet.json"]).hexdigest(),
    }
    assert lock["provenance"]["workflow"] == WORKFLOW
    assert lock["provenance"]["ref"] == "refs/tags/v0.3.0"
    assert lock["provenance"]["rekor_log_index"] == 42
    assert lock["verified_at"] == CLOCK
    assert any("Sigstore" in item for item in lock["not_verified"])
    index = jsonio.load(root / "index.json")
    assert index["base"] == {"dusk": {"version": "0.3.0", "lock": "upstream/dusk-networks.lock.json"}}
    assert list(index) == ["schema", "repo", "visibility", "networks", "base", "entries"]
    home = Repo.open(root)
    assert check_repo(Catalog([home]), home) == []


def test_resync_same_version_writes_nothing(tmp_path: Path, world: World) -> None:
    root = make_root(tmp_path)
    run(root, world, "0.3.0")
    before = (root / "upstream/dusk-networks.lock.json").read_text()
    again = sync(root, SPEC, "0.3.0", world, world, clock=lambda: "2027-01-01T00:00:00Z")
    assert not again.changed
    assert (root / "upstream/dusk-networks.lock.json").read_text() == before


def test_upgrade_and_downgrade(tmp_path: Path, world: World) -> None:
    root = make_root(tmp_path)
    run(root, world, "0.3.0")
    world.publish("0.4.0", commit="d1" * 20)
    run(root, world, "0.4.0")
    assert jsonio.load(root / "index.json")["base"]["dusk"]["version"] == "0.4.0"
    with pytest.raises(UpstreamError, match="older than the locked"):
        run(root, world, "0.3.0")
    run(root, world, "0.3.0", allow_downgrade=True)
    assert jsonio.load(root / "upstream/dusk-networks.lock.json")["commit"] == COMMIT


def assert_refused(tmp_path: Path, world: World, version: str, match: str) -> None:
    root = make_root(tmp_path)
    index_before = (root / "index.json").read_bytes()
    with pytest.raises(UpstreamError, match=match):
        run(root, world, version)
    assert not (root / "upstream").exists()
    assert (root / "index.json").read_bytes() == index_before


def test_tampered_tarball(tmp_path: Path) -> None:
    world = World()
    tarball = world.publish("0.3.0")
    files = package_files("0.3.0")
    files["networks/testnet.json"] = b'{"chainId": 2, "name": "evil"}\n'
    world.responses[world.versions["0.3.0"]["dist"]["tarball"]] = make_tarball(files)
    assert tarball != make_tarball(files)
    assert_refused(tmp_path, world, "0.3.0", "tarball sha512")


def test_wrong_integrity(tmp_path: Path) -> None:
    world = World()
    world.publish("0.3.0", integrity=sri(b"something else"))
    assert_refused(tmp_path, world, "0.3.0", "dist.integrity")


def test_integrity_without_sha512(tmp_path: Path) -> None:
    world = World()
    world.publish("0.3.0", integrity="sha1-AAAA")
    assert_refused(tmp_path, world, "0.3.0", "no sha512 entry")


def test_missing_attestation(tmp_path: Path) -> None:
    world = World()
    world.publish("0.3.0")
    del world.responses[f"{REGISTRY}/-/npm/v1/attestations/@dusk-network%2Fnetworks@0.3.0"]
    assert_refused(tmp_path, world, "0.3.0", "no provenance attestation")


def test_attestation_without_slsa(tmp_path: Path) -> None:
    world = World()
    world.publish("0.3.0", attest=json.dumps({"attestations": []}).encode())
    assert_refused(tmp_path, world, "0.3.0", "no SLSA provenance")


@pytest.mark.parametrize(
    ("over", "match"),
    [
        ({"workflow": ".github/workflows/other.yml"}, "provenance workflow"),
        ({"ref": "refs/heads/main"}, "provenance ref"),
        ({"ref": "refs/tags/v0.2.0"}, "provenance ref"),
        ({"repository": "https://github.com/someone/networks"}, "provenance repository"),
        ({"builder": "https://example.org/self-hosted"}, "GitHub-hosted"),
        ({"sha512": "00" * 64}, "subject sha512"),
        ({"commit": "not-a-commit"}, "no single commit"),
    ],
)
def test_wrong_provenance(tmp_path: Path, over: dict[str, str], match: str) -> None:
    world = World()
    tarball = make_tarball(package_files("0.3.0"))
    world.publish("0.3.0", tarball=tarball, attest=attestations("0.3.0", tarball, **over))
    assert_refused(tmp_path, world, "0.3.0", match)


def test_certificate_names_other_workflow(tmp_path: Path) -> None:
    world = World()
    tarball = make_tarball(package_files("0.3.0"))
    other = f"{REPO}/.github/workflows/other.yml@refs/tags/v0.3.0"
    world.publish("0.3.0", tarball=tarball, attest=attestations("0.3.0", tarball, cert_identity=other))
    assert_refused(tmp_path, world, "0.3.0", "signing certificate")


def test_tag_points_elsewhere(tmp_path: Path, world: World) -> None:
    world.refs["refs/tags/v0.3.0"] = "ee" * 20
    assert_refused(tmp_path, world, "0.3.0", "provenance says")


def test_tag_missing(tmp_path: Path, world: World) -> None:
    del world.refs["refs/tags/v0.3.0"]
    assert_refused(tmp_path, world, "0.3.0", "has no refs/tags/v0.3.0")


def test_annotated_tag_uses_peeled_commit(tmp_path: Path, world: World) -> None:
    world.refs["refs/tags/v0.3.0"] = TAG_OBJECT
    world.refs["refs/tags/v0.3.0^{}"] = COMMIT
    assert run(make_root(tmp_path), world, "0.3.0").commit == COMMIT


def test_content_mismatch_with_github(tmp_path: Path) -> None:
    world = World()
    github = dict(package_files("0.3.0"))
    github["schema.json"] = b'{"type": "array"}\n'
    world.publish("0.3.0", github=github)
    assert_refused(tmp_path, world, "0.3.0", "schema.json in the tarball differs")


def test_file_missing_on_github(tmp_path: Path) -> None:
    world = World()
    github = dict(package_files("0.3.0"))
    del github["LICENSE"]
    world.publish("0.3.0", github=github)
    assert_refused(tmp_path, world, "0.3.0", "LICENSE is not in")


def test_package_json_version_mismatch(tmp_path: Path) -> None:
    world = World()
    files = package_files("0.3.0")
    files["package.json"] = json.dumps({"name": PACKAGE, "version": "0.3.1"}).encode()
    world.publish("0.3.0", files=files)
    assert_refused(tmp_path, world, "0.3.0", "package.json version")


def test_unknown_and_prerelease_versions(tmp_path: Path, world: World) -> None:
    assert_refused(tmp_path, world, "0.9.9", "is not on")
    with pytest.raises(UpstreamError, match="plain X.Y.Z"):
        run(make_root(tmp_path / "b"), world, "0.0.0-stage")
    with pytest.raises(UpstreamError, match="plain X.Y.Z"):
        run(make_root(tmp_path / "c"), world, "../../x")


def test_tarball_url_off_registry(tmp_path: Path, world: World) -> None:
    world.versions["0.3.0"]["dist"]["tarball"] = "https://example.org/networks-0.3.0.tgz"
    assert_refused(tmp_path, world, "0.3.0", "not on https://registry.npmjs.org")


def test_unsafe_tarball_members() -> None:
    files = package_files("0.3.0")
    link = tarfile.TarInfo("package/schema.json")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    del files["schema.json"]
    with pytest.raises(UpstreamError, match="not a regular file"):
        read_members(make_tarball(files, [link]), ["schema.json"])
    escape = tarfile.TarInfo("package/../outside.json")
    escape.size = 1
    with pytest.raises(UpstreamError, match="unsafe path"):
        read_members(make_tarball(package_files("0.3.0"), [escape]), ["schema.json"])
    twice = tarfile.TarInfo("package/schema.json")
    twice.size = 3
    with pytest.raises(UpstreamError, match="twice"):
        read_members(make_tarball(package_files("0.3.0"), [twice]), ["schema.json"])
    with pytest.raises(UpstreamError, match="lacks"):
        read_members(make_tarball({"README.md": b"x"}), ["schema.json"])
    with pytest.raises(UpstreamError, match="does not open"):
        read_members(b"not a tarball", ["schema.json"])


def test_status(tmp_path: Path, world: World) -> None:
    root = make_root(tmp_path)
    assert status(root, SPEC, None).code == 1
    run(root, world, "0.3.0")
    assert status(root, SPEC, None).code == 0
    assert status(root, SPEC, world).code == 0
    world.publish("0.4.0")
    newer = status(root, SPEC, world)
    assert newer.code == 2 and any("0.4.0" in line for line in newer.lines)
    (root / "upstream/dusk-networks/testnet.json").write_bytes(b"{}")
    broken = status(root, SPEC, None)
    assert broken.code == 1 and any("testnet.json" in line for line in broken.lines)


def test_cli_status_offline(tmp_path: Path, world: World, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_root(tmp_path)
    run(root, world, "0.3.0")
    assert cli.main(["upstream", "status", "--root", str(root), "--offline"]) == 0
    assert "locked 0.3.0" in capsys.readouterr().out


def test_recorded_0_3_0_attestation() -> None:
    """The real 0.3.0 statement and certificate (recorded) pass the build and identity checks."""
    listing = json.loads((FIXTURES / "dusk-networks-0.3.0.attestations.json").read_text())
    slsa = next(e for e in listing["attestations"] if e["predicateType"] == provenance.SLSA_V1)
    verifier = provenance.NpmProvenanceVerifier(REPO, WORKFLOW, World(), World())
    body = provenance._statement(slsa)
    assert verifier._check_build(body, "refs/tags/v0.3.0") == "0bebc7799b99bba1f32e9dc6c24b4e4994bc864a"
    with pytest.raises(VerificationError, match="provenance ref"):
        verifier._check_build(body, "refs/tags/v0.3.1")
    cert = provenance._certificate(slsa)
    assert cert is not None and f"{REPO}/{WORKFLOW}@refs/tags/v0.3.0".encode() in cert
    assert body["subject"][0]["name"] == provenance.purl(PACKAGE, "0.3.0")
