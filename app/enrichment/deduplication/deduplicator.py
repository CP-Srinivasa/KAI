"""Compatibility shim for moved deduplication module.

Canonical implementation now lives in `app.normalization.deduplication`.
"""

from __future__ import annotations

from app.normalization.deduplication import Deduplicator, DuplicateScore

__all__ = ["Deduplicator", "DuplicateScore"]

