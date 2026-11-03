"""Classifier plug-in seam. The data-diff classifier (S safety, D data, A API) is not built yet."""

from __future__ import annotations

from .model import Classification


class UnclassifiedClassifier:
    """Placeholder: says nothing about the change. Never treat its result as "no impact"."""

    def classify(self, old: dict[str, bytes] | None, new: dict[str, bytes]) -> Classification:
        return Classification(classes=(), note="not classified (classifier not built yet)")
