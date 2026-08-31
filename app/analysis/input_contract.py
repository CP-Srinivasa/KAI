"""Measured content contract before any primary or shadow classification (G5)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import portalocker

from app.core.domain.document import CanonicalDocument
from app.core.enums import DocumentType, SourceType

ANALYSIS_INPUT_REJECTIONS_FILENAME = "analysis_input_contract_rejections.jsonl"
_DEFAULT_REJECTION_PATH = Path("artifacts") / ANALYSIS_INPUT_REJECTIONS_FILENAME

# Measured 2026-08-28: 2,584 historical YouTube descriptions had max=143
# characters, while the shortest observed real transcript had 315.  Two hundred
# is the already-audited separator between those populations; it is not inferred
# from the single 124-character incident.
YOUTUBE_MIN_CONTENT_CHARS = 200


@dataclass(frozen=True)
class AnalysisInputRejection:
    reason: str
    content_chars: int
    minimum_chars: int


class AnalysisInputRejectionAuditError(RuntimeError):
    """The analysis-input rejection could not be recorded durably."""


def analysis_input_rejection(
    document: CanonicalDocument,
    text: str,
) -> AnalysisInputRejection | None:
    """Return the measured YouTube rejection, or ``None`` for admissible input.

    The threshold is source-specific.  Applying 200 characters to tweets or
    exchange notices would silently turn a YouTube measurement into unrelated
    policy and create false positives.
    """
    is_youtube = (
        document.source_type == SourceType.YOUTUBE_CHANNEL
        or document.document_type == DocumentType.YOUTUBE_VIDEO
        or document.youtube_meta is not None
    )
    if not is_youtube:
        return None

    content_chars = len(text.strip())
    if content_chars >= YOUTUBE_MIN_CONTENT_CHARS:
        return None
    return AnalysisInputRejection(
        reason="youtube_content_below_measured_minimum",
        content_chars=content_chars,
        minimum_chars=YOUTUBE_MIN_CONTENT_CHARS,
    )


def append_analysis_input_rejection(
    document: CanonicalDocument,
    rejection: AnalysisInputRejection,
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Append only identifiers and measurements; never title, URL or content."""
    target = path or _DEFAULT_REJECTION_PATH
    record = {
        "schema": "analysis-input-rejection/v1",
        "ts": (now or datetime.now(UTC)).isoformat(),
        "contract": "analysis_input/v1",
        "document_id": str(document.id),
        "source_type": document.source_type.value if document.source_type else None,
        "reason": rejection.reason,
        "content_chars": rejection.content_chars,
        "minimum_chars": rejection.minimum_chars,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(target, mode="a", encoding="utf-8", timeout=10) as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:  # noqa: BLE001 - caller keeps the input rejected
        raise AnalysisInputRejectionAuditError(
            "analysis input rejection audit write failed"
        ) from exc


__all__ = [
    "ANALYSIS_INPUT_REJECTIONS_FILENAME",
    "YOUTUBE_MIN_CONTENT_CHARS",
    "AnalysisInputRejection",
    "AnalysisInputRejectionAuditError",
    "analysis_input_rejection",
    "append_analysis_input_rejection",
]
