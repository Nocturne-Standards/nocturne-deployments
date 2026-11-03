"""Upstream base layers: fetch a release, verify it, keep a snapshot and a lock.

Plug-ins (see ``model``): a source (``npm``), verifiers (``provenance``, ``content``)
and a classifier (``classify``, not built yet). ``specs`` declares each upstream;
``sync`` wires them together.
"""

from .model import UpstreamError, VerificationError

__all__ = ["UpstreamError", "VerificationError"]
