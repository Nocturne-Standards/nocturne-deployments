"""nocturne-pins: per-product pin files, append-only history, one resolver.

Library entry points: ``Catalog.open`` / ``Catalog.resolve`` for readers,
``write.record_contract`` / ``write.record_call`` for the deploy engine.
"""

from .names import FullName, EntryKey, parse, parse_entry
from .repo import Catalog, Repo, RepoError

__version__ = "0.1.0"

__all__ = ["Catalog", "Repo", "RepoError", "FullName", "EntryKey", "parse", "parse_entry", "__version__"]
