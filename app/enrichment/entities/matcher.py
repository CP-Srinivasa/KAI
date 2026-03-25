"""Compatibility shim for moved entity matcher.

Canonical implementation now lives in `app.normalization.entities`.
"""

from __future__ import annotations

from app.normalization.entities import hits_to_entity_mentions

__all__ = ["hits_to_entity_mentions"]

