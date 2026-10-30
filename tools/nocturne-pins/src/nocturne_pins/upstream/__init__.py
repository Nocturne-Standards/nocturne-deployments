"""Upstream base layers: fetch a release, verify it, keep a snapshot and a lock.

Plug-ins (see ``model``): a source (``npm``), verifiers (``provenance``, ``content``)
and a classifier (``classify``). ``specs`` declares each upstream;
``sync`` wires them together.
"""

from .model import UpstreamError, VerificationError

__all__ = ["UpstreamError", "VerificationError"]
