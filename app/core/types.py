"""Core Type Aliases."""

from __future__ import annotations

from typing import Any
from uuid import UUID

DocumentId = UUID
SourceId = str
ContentHash = str
JsonDict = dict[str, Any]
ScoreFloat = float  # 0.0 to 1.0
SentimentScore = float  # -1.0 to 1.0
