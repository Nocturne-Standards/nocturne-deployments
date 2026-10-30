"""Network access for the upstream watcher, behind small interfaces so tests run offline."""

from __future__ import annotations

import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

from .model import UpstreamError

MAX_BYTES = 20 * 1024 * 1024
TIMEOUT = 60


class NotFound(UpstreamError):
    pass


class Unreachable(UpstreamError):
    """The registry or host did not answer usefully (network error, timeout, 429, 5xx).

    Separate from a wrong answer, so a CI check can warn on an outage and still
    fail on a mismatch.
    """


class Http(Protocol):
    def get(self, url: str) -> bytes:
        """Body of an HTTPS GET; ``NotFound`` on 404."""
        ...


class Git(Protocol):
    def ls_remote(self, url: str, refs: list[str]) -> dict[str, str]:
        """``ref -> object id`` for the refs the remote has."""
        ...


class UrllibHttp:
    def get(self, url: str) -> bytes:
        if urllib.parse.urlparse(url).scheme != "https":
            raise UpstreamError(f"refusing non-https URL {url}")
        request = urllib.request.Request(url, headers={"User-Agent": "nocturne-pins", "Accept": "*/*"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                body = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotFound(f"{url}: 404") from exc
            if exc.code == 429 or exc.code >= 500:
                raise Unreachable(f"{url}: HTTP {exc.code}") from exc
            raise UpstreamError(f"{url}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise Unreachable(f"{url}: {getattr(exc, 'reason', exc)}") from exc
        if len(body) > MAX_BYTES:
            raise UpstreamError(f"{url}: larger than {MAX_BYTES} bytes")
        return body


# git's words for "the host did not answer", as opposed to a wrong answer.
GIT_OUTAGE = re.compile(
    r"Could not resolve host|unable to access|Failed to connect|Connection (timed out|refused|reset)"
    r"|Operation timed out|early EOF|returned error: (429|5\d\d)",
)


class SubprocessGit:
    def ls_remote(self, url: str, refs: list[str]) -> dict[str, str]:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            result = subprocess.run(
                ["git", "ls-remote", url, *refs], capture_output=True, text=True, env=env, timeout=TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise Unreachable(f"git ls-remote {url}: timed out") from exc
        if result.returncode != 0:
            message = f"git ls-remote {url}: {result.stderr.strip()}"
            raise Unreachable(message) if GIT_OUTAGE.search(result.stderr) else UpstreamError(message)
        out: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            sha, ref = line.split("\t", 1)
            out[ref] = sha
        return out
