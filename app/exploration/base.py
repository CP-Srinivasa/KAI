"""Exploration-layer contracts.

These dataclasses + the ExplorationProbe ABC are the ONLY interface between a
source probe and the rest of the sandbox (runner, capture, report).

Rules enforced here:
- A probe MUST NOT raise. Any failure → ExplorationResult(success=False, error=...).
- ``records`` is always a list (never None). Each record is a flat dict so the
  report can compute per-field coverage across probes uniformly.
- ``raw`` holds the unmodified payload (audit / re-analysis); it may be any
  JSON-serialisable value or None.
- A probe declares whether it needs a key (``requires_key``) and its access mode
  (``api`` | ``scrape``) so the report can compare API vs scrape per source.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

AccessMode = Literal["api", "scrape"]


@dataclass
class ProbeMeta:
    """Per-fetch observability metadata. All fields optional / best-effort."""

    http_status: int | None = None
    latency_ms: float | None = None
    bytes: int | None = None
    rate_limit_remaining: str | None = None
    field_count: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "http_status": self.http_status,
            "latency_ms": self.latency_ms,
            "bytes": self.bytes,
            "rate_limit_remaining": self.rate_limit_remaining,
            "field_count": self.field_count,
            "extra": dict(self.extra),
        }


@dataclass
class ExplorationResult:
    """Output of one probe run.

    Contract (probes MUST respect this):
    - On success: success=True, records=[...] (possibly empty), error=None
    - On failure: success=False, error=<non-empty message>, records=[]
    - records is NEVER None
    - probes MUST NOT raise — catch everything and reflect it here
    """

    source_name: str
    access_mode: AccessMode
    fetched_at: datetime
    success: bool
    error: str | None = None
    raw: Any = None
    records: list[dict[str, Any]] = field(default_factory=list)
    meta: ProbeMeta = field(default_factory=ProbeMeta)

    @property
    def probe_id(self) -> str:
        return f"{self.source_name}:{self.access_mode}"

    @property
    def record_count(self) -> int:
        return len(self.records)

    def to_envelope(self) -> dict[str, Any]:
        """Serialisable envelope WITHOUT the (potentially huge) raw payload."""
        return {
            "source_name": self.source_name,
            "access_mode": self.access_mode,
            "probe_id": self.probe_id,
            "fetched_at": self.fetched_at.isoformat(),
            "success": self.success,
            "error": self.error,
            "record_count": self.record_count,
            "meta": self.meta.to_dict(),
        }


class ExplorationProbe(ABC):
    """Abstract base for one source probe (one source, one access mode).

    Subclasses set the class attributes and implement ``probe()``.
    """

    #: stable lowercase source key, e.g. "coinglass"
    source_name: str = "unknown"
    #: "api" or "scrape"
    access_mode: AccessMode = "api"
    #: whether a configured API key is required to run
    requires_key: bool = False

    @property
    def probe_id(self) -> str:
        return f"{self.source_name}:{self.access_mode}"

    @abstractmethod
    async def probe(self) -> ExplorationResult:
        """Fetch one sample. MUST NOT raise — return success=False on any failure."""

    # -- helpers for subclasses -------------------------------------------------

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def ok(
        self,
        records: list[dict[str, Any]],
        *,
        raw: Any = None,
        meta: ProbeMeta | None = None,
    ) -> ExplorationResult:
        m = meta or ProbeMeta()
        if m.field_count is None:
            keys: set[str] = set()
            for rec in records:
                keys.update(rec.keys())
            m.field_count = len(keys)
        return ExplorationResult(
            source_name=self.source_name,
            access_mode=self.access_mode,
            fetched_at=self._now(),
            success=True,
            records=records,
            raw=raw,
            meta=m,
        )

    def fail(self, error: str, *, meta: ProbeMeta | None = None) -> ExplorationResult:
        return ExplorationResult(
            source_name=self.source_name,
            access_mode=self.access_mode,
            fetched_at=self._now(),
            success=False,
            error=error,
            records=[],
            meta=meta or ProbeMeta(),
        )
