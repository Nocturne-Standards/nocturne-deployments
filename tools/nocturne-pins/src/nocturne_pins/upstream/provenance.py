"""Verifier plug-in: npm provenance (SLSA v1 from GitHub Actions) bound to a git tag.

What this checks, with the standard library only:

- the registry has a provenance attestation for the version (none: refused);
- the SLSA statement's subject is this tarball (sha512) and this package@version;
- the statement names the expected repository, workflow file and ``refs/tags/vX``,
  a GitHub-hosted runner, and a commit;
- the signing certificate carries the same workflow identity (byte search, the
  certificate is not parsed);
- ``git ls-remote`` of the tag gives that commit (peeled when annotated).

What it does not check: the Sigstore bundle itself. Verifying the DSSE signature,
the Fulcio certificate chain and the Rekor inclusion proof needs X.509 and ECDSA,
which the standard library lacks. Neither are the npm registry signatures
(``npm audit signatures``). The statement is therefore trusted as served by the
registry over HTTPS; the tag binding and the byte comparison with GitHub are the
independent checks. Both gaps are written to the lock as ``not_verified``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from typing import Any

from .model import Artifact, Evidence, VerificationError
from .net import Git, Http, NotFound

SLSA_V1 = "https://slsa.dev/provenance/v1"
NPM_PUBLISH = "https://github.com/npm/attestation/tree/main/specs/publish/v0.1"
GITHUB_BUILD_TYPE = "https://slsa-framework.github.io/github-actions-buildtypes/workflow/v1"
GITHUB_HOSTED = "https://github.com/actions/runner/github-hosted"
IN_TOTO = "application/vnd.in-toto+json"
COMMIT = re.compile(r"^[0-9a-f]{40}$")

NOT_VERIFIED = [
    "Sigstore bundle: DSSE signature, Fulcio certificate chain and Rekor inclusion proof "
    "(needs X.509 and ECDSA; the tool is standard library only)",
    "npm registry signatures (npm audit signatures)",
]


def purl(package: str, version: str) -> str:
    return f"pkg:npm/{package.replace('@', '%40', 1)}@{version}"


def _statement(entry: dict[str, Any]) -> dict[str, Any]:
    envelope = entry.get("bundle", {}).get("dsseEnvelope", {})
    if envelope.get("payloadType") != IN_TOTO:
        raise VerificationError(f"attestation payload type is {envelope.get('payloadType')!r}, want {IN_TOTO}")
    try:
        statement = json.loads(base64.b64decode(envelope["payload"], validate=True))
    except (KeyError, binascii.Error, ValueError) as exc:
        raise VerificationError(f"attestation payload does not decode: {exc}") from exc
    if not isinstance(statement, dict) or statement.get("predicateType") != entry.get("predicateType"):
        raise VerificationError("attestation statement predicate type differs from the listing")
    return statement


def _check_subject(statement: dict[str, Any], name: str, sha512_hex: str, what: str) -> None:
    subjects = statement.get("subject")
    if not isinstance(subjects, list) or len(subjects) != 1:
        raise VerificationError(f"{what}: want exactly one subject")
    subject = subjects[0]
    if subject.get("name") != name:
        raise VerificationError(f"{what}: subject {subject.get('name')!r} is not {name}")
    if subject.get("digest", {}).get("sha512") != sha512_hex:
        raise VerificationError(f"{what}: subject sha512 is not the tarball's")


def _certificate(entry: dict[str, Any]) -> bytes | None:
    material = entry.get("bundle", {}).get("verificationMaterial", {})
    raw = material.get("certificate", {}).get("rawBytes")
    if raw is None:
        chain = material.get("x509CertificateChain", {}).get("certificates", [])
        raw = chain[0].get("rawBytes") if chain else None
    if not isinstance(raw, str):
        return None
    try:
        return base64.b64decode(raw, validate=True)
    except binascii.Error as exc:
        raise VerificationError(f"certificate does not decode: {exc}") from exc


def _tlog(entry: dict[str, Any]) -> dict[str, Any]:
    entries = entry.get("bundle", {}).get("verificationMaterial", {}).get("tlogEntries", [])
    if not entries:
        return {}
    first = entries[0]
    out: dict[str, Any] = {}
    if "logIndex" in first:
        out["rekor_log_index"] = int(first["logIndex"])
    if "integratedTime" in first:
        out["rekor_integrated_time"] = int(first["integratedTime"])
    return out


class NpmProvenanceVerifier:
    def __init__(self, repository: str, workflow: str, http: Http, git: Git) -> None:
        self.repository = repository  # https://github.com/<owner>/<repo>
        self.workflow = workflow
        self.http = http
        self.git = git

    def _attestations(self, url: str) -> dict[str, dict[str, Any]]:
        try:
            listing = json.loads(self.http.get(url))
        except NotFound as exc:
            raise VerificationError("no provenance attestation on the registry; refusing this version") from exc
        except ValueError as exc:
            raise VerificationError(f"attestations do not parse: {exc}") from exc
        by_type: dict[str, dict[str, Any]] = {}
        for entry in listing.get("attestations", []) if isinstance(listing, dict) else []:
            if isinstance(entry, dict) and isinstance(entry.get("predicateType"), str):
                if entry["predicateType"] in by_type:
                    raise VerificationError(f"two attestations of type {entry['predicateType']}")
                by_type[entry["predicateType"]] = entry
        return by_type

    def verify(self, artifact: Artifact, evidence: Evidence) -> None:
        release = artifact.release
        ref = f"refs/tags/{release.tag}"
        sha512_hex = hashlib.sha512(artifact.tarball).hexdigest()
        name = purl(release.package, release.version)

        attestations = self._attestations(release.attestations_url)
        slsa = attestations.get(SLSA_V1)
        if slsa is None:
            raise VerificationError("no SLSA provenance attestation on the registry; refusing this version")
        statement = _statement(slsa)
        _check_subject(statement, name, sha512_hex, "SLSA provenance")
        evidence.verified.append("SLSA provenance subject is this tarball (sha512) and package@version")

        publish = attestations.get(NPM_PUBLISH)
        if publish is not None:
            _check_subject(_statement(publish), name, sha512_hex, "npm publish attestation")
            evidence.verified.append("npm publish attestation subject is this tarball (sha512)")

        commit = self._check_build(statement, ref)
        evidence.verified.append(
            f"provenance binds {self.repository}, {self.workflow}, {ref}, commit {commit}, GitHub-hosted runner"
        )

        identity = f"{self.repository}/{self.workflow}@{ref}".encode()
        certificate = _certificate(slsa)
        if certificate is None:
            evidence.not_verified.append("signing certificate identity (bundle has no certificate)")
        elif identity not in certificate:
            raise VerificationError(f"signing certificate does not name {identity.decode()}")
        else:
            evidence.verified.append("signing certificate names the same workflow identity (byte search, not parsed)")

        self._check_tag(ref, commit)
        evidence.verified.append(f"git ls-remote {ref} equals the provenance commit")

        evidence.commit = commit
        evidence.provenance = {
            "predicate_type": SLSA_V1,
            "repository": self.repository,
            "workflow": self.workflow,
            "ref": ref,
            "commit": commit,
            "builder": GITHUB_HOSTED,
            "invocation": statement["predicate"].get("runDetails", {}).get("metadata", {}).get("invocationId"),
            **_tlog(slsa),
        }
        evidence.not_verified.extend(NOT_VERIFIED)

    def _check_build(self, statement: dict[str, Any], ref: str) -> str:
        predicate = statement.get("predicate", {})
        definition = predicate.get("buildDefinition", {})
        if definition.get("buildType") != GITHUB_BUILD_TYPE:
            raise VerificationError(f"build type {definition.get('buildType')!r} is not a GitHub Actions workflow")
        workflow = definition.get("externalParameters", {}).get("workflow", {})
        if workflow.get("repository") != self.repository:
            raise VerificationError(f"provenance repository {workflow.get('repository')!r} is not {self.repository}")
        if workflow.get("path") != self.workflow:
            raise VerificationError(f"provenance workflow {workflow.get('path')!r} is not {self.workflow}")
        if workflow.get("ref") != ref:
            raise VerificationError(f"provenance ref {workflow.get('ref')!r} is not {ref}")
        builder = predicate.get("runDetails", {}).get("builder", {}).get("id")
        if builder != GITHUB_HOSTED:
            raise VerificationError(f"builder {builder!r} is not a GitHub-hosted runner")
        source = f"git+{self.repository}@{ref}"
        commits = [
            dep.get("digest", {}).get("gitCommit")
            for dep in definition.get("resolvedDependencies", [])
            if isinstance(dep, dict) and dep.get("uri") == source
        ]
        if len(commits) != 1 or not isinstance(commits[0], str) or COMMIT.match(commits[0]) is None:
            raise VerificationError(f"provenance names no single commit for {source}")
        return commits[0]

    def _check_tag(self, ref: str, commit: str) -> None:
        refs = self.git.ls_remote(self.repository, [ref, f"{ref}^{{}}"])
        if ref not in refs:
            raise VerificationError(f"{self.repository} has no {ref}")
        # Annotated tags list the tag object and the peeled commit; lightweight tags only the commit.
        tagged = refs.get(f"{ref}^{{}}", refs[ref])
        if tagged != commit:
            raise VerificationError(f"{ref} is {tagged} on {self.repository}, provenance says {commit}")
