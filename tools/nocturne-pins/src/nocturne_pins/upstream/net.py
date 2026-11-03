"""Network access for the upstream watcher, behind small interfaces so tests run offline."""

from __future__ import annotations

import os
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
            raise UpstreamError(f"{url}: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise UpstreamError(f"{url}: {exc.reason}") from exc
        if len(body) > MAX_BYTES:
            raise UpstreamError(f"{url}: larger than {MAX_BYTES} bytes")
        return body


class SubprocessGit:
    def ls_remote(self, url: str, refs: list[str]) -> dict[str, str]:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        result = subprocess.run(
            ["git", "ls-remote", url, *refs], capture_output=True, text=True, env=env, timeout=TIMEOUT,
        )
        if result.returncode != 0:
            raise UpstreamError(f"git ls-remote {url}: {result.stderr.strip()}")
        out: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            sha, ref = line.split("\t", 1)
            out[ref] = sha
        return out
