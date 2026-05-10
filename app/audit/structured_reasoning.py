"""Structured reasoning journal — auditable, reproducible, no chain-of-thought.

What this stream stores
-----------------------

Per ``decision_id`` we append a sequence of ``ReasoningStep`` rows that
together form the *trail* of how a decision was constructed:

  • **evidence**          — which inputs the model saw (refs, not bodies)
  • **scoring**           — what numbers it produced from those inputs
  • **risk_adjustment**   — how risk-engine/active calibrator changed things
  • **confidence_change** — pre/post posterior, version_id of the calibrator
  • **trigger**           — what fired the decision (news, regime change, …)
  • **invalidation**      — why a downstream gate rejected / aborted

What this stream **must not** store
------------------------------------

  • full LLM raw chain-of-thought (use ``rationale_summary`` ≤ 500 chars),
  • sensitive prompts (every string passes the secrets-redactor),
  • any value carrying API keys, tokens, OAuth, etc.

Properties enforced by the writer
---------------------------------

- **Auditable** — append-only JSONL, frozen Pydantic schema, `extra="forbid"`.
- **Reproducible** — every step references the parameter_versions in effect
  at the time (calibrator version_id, threshold version_id, …).
- **Compressible** — strings go through the truncator; payload is ASCII-safe
  canonical JSON (sort_keys, no spaces) → high gzip ratio.
- **Referenceable** — every step has its own ``step_id`` plus a
  ``decision_id`` pointer; cross-stream refs are explicit (e.g.
  ``bayes_audit:dec_xxx``, ``manipulation_flag:src_yyy``).
- **Tamper-evident** — every row carries ``prev_chain_hash`` (sha256 of
  the canonical JSON of the prior row, genesis ``0×64``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.audit.sanitization import SanitizationConfig, sanitize_string, sanitize_value

logger = logging.getLogger(__name__)

DEFAULT_REASONING_JOURNAL_PATH: Final[Path] = Path(
    "artifacts/structured_reasoning.jsonl"
)
SCHEMA_VERSION: Final[int] = 1
GENESIS_PREV_HASH: Final[str] = "0" * 64

# Phase enum — stable JSON keys, exhaustive list. Anything not in this list
# must NOT be loggable here. New phases require a code change + schema bump.
PHASE_EVIDENCE: Final[Literal["evidence"]] = "evidence"
PHASE_SCORING: Final[Literal["scoring"]] = "scoring"
PHASE_RISK_ADJUSTMENT: Final[Literal["risk_adjustment"]] = "risk_adjustment"
PHASE_CONFIDENCE_CHANGE: Final[Literal["confidence_change"]] = "confidence_change"
PHASE_TRIGGER: Final[Literal["trigger"]] = "trigger"
PHASE_INVALIDATION: Final[Literal["invalidation"]] = "invalidation"

PhaseLiteral = Literal[
    "evidence",
    "scoring",
    "risk_adjustment",
    "confidence_change",
    "trigger",
    "invalidation",
]


class ReasoningStep(BaseModel):
    """A single audit row in the structured reasoning trail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    step_id: str = Field(min_length=8, max_length=64)
    decision_id: str = Field(min_length=1)
    timestamp_utc: str
    phase: PhaseLiteral
    actor: str = Field(min_length=1)
    rationale_summary: str  # max length enforced by sanitizer

    # Sanitized payloads (callers may pass raw inputs; the writer redacts).
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)

    # Cross-stream refs in the form "<stream>:<id>" (e.g. "bayes_audit:dec_xxx",
    # "manipulation_flag:src_yyy", "param_version:pv_xxx"). Strings only — we
    # don't dereference at write time.
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)

    # Reproducibility anchors: which parameter_version was active for each
    # parameter_path that influenced this step.
    parameter_versions: dict[str, str] = Field(default_factory=dict)

    # Optional: numeric confidence change, for the confidence_change phase.
    confidence_before: float | None = None
    confidence_after: float | None = None

    # Hash chain
    prev_chain_hash: str = Field(min_length=64, max_length=64)


def _canonical_json(record: ReasoningStep) -> str:
    return json.dumps(
        record.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _hash_record(record: ReasoningStep) -> str:
    return hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


def _new_step_id() -> str:
    return f"rs_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class ReasoningJournalConfig:
    """Sanitization knobs the journal applies to every write."""

    sanitization: SanitizationConfig = SanitizationConfig()


class ReasoningJournal:
    """Append-only writer + reader for the structured reasoning stream."""

    def __init__(
        self,
        path: Path | str = DEFAULT_REASONING_JOURNAL_PATH,
        *,
        config: ReasoningJournalConfig | None = None,
    ) -> None:
        self._path = Path(path)
        self._config = config or ReasoningJournalConfig()
        # Cache: (file_size, last_chain_hash) — see Neo-F-001.
        self._cached_size: int = -1
        self._cached_last_hash: str = GENESIS_PREV_HASH

    @property
    def path(self) -> Path:
        return self._path

    # ----- read ----------------------------------------------------------

    def iter_steps(self) -> Iterator[ReasoningStep]:
        """Stream all steps in write order. Malformed rows are logged + skipped.

        Use ``verify_chain()`` afterwards to catch tampering — skipped rows
        will break the chain at that point.
        """
        if not self._path.exists():
            return
        for line_no, raw in enumerate(
            self._path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
                yield ReasoningStep.model_validate(payload)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning(
                    "[reasoning-journal] skipped malformed row %s:%d (%s)",
                    self._path,
                    line_no,
                    exc,
                )

    def steps_for_decision(self, decision_id: str) -> list[ReasoningStep]:
        return [s for s in self.iter_steps() if s.decision_id == decision_id]

    def verify_chain(self) -> tuple[bool, str | None]:
        expected_prev = GENESIS_PREV_HASH
        for line_no, step in enumerate(self.iter_steps(), start=1):
            if step.prev_chain_hash != expected_prev:
                return (
                    False,
                    (
                        f"chain broken at step #{line_no} "
                        f"(step_id={step.step_id}): expected prev="
                        f"{expected_prev[:12]}…, got prev="
                        f"{step.prev_chain_hash[:12]}…"
                    ),
                )
            expected_prev = _hash_record(step)
        return True, None

    # ----- write ---------------------------------------------------------

    def _last_chain_hash(self) -> str:
        """Last step's hash, cached by file size (Neo-F-001 fix).

        Cache hit: O(1). Cache miss (first call, or file size changed
        externally): full re-walk via iter_steps. Collapses the previous
        O(n²) append behaviour to O(1) amortised.
        """
        try:
            current_size = self._path.stat().st_size if self._path.exists() else 0
        except OSError:
            current_size = -1
        if current_size == self._cached_size:
            return self._cached_last_hash

        last: ReasoningStep | None = None
        for step in self.iter_steps():
            last = step
        new_hash = GENESIS_PREV_HASH if last is None else _hash_record(last)
        self._cached_last_hash = new_hash
        self._cached_size = current_size
        return new_hash

    def _append(self, step: ReasoningStep) -> ReasoningStep:
        """Append step with portalocker file lock (Neo-F-002 fix).

        Prevents half-written rows visible to a concurrent CLI verify.
        """
        import portalocker  # local import — only at write time

        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            step.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._path.open("a", encoding="utf-8") as fh:
            portalocker.lock(fh, portalocker.LOCK_EX)
            try:
                fh.write(line + "\n")
                fh.flush()
            finally:
                portalocker.unlock(fh)

        # Refresh cache to the just-written step's hash → next append O(1).
        self._cached_last_hash = _hash_record(step)
        try:
            self._cached_size = self._path.stat().st_size
        except OSError:
            self._cached_size = -1
        return step

    def log_step(
        self,
        *,
        decision_id: str,
        phase: PhaseLiteral,
        actor: str,
        rationale_summary: str,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        evidence_refs: tuple[str, ...] = (),
        parameter_versions: dict[str, str] | None = None,
        confidence_before: float | None = None,
        confidence_after: float | None = None,
    ) -> ReasoningStep:
        """Append one structured reasoning step.

        All string-bearing inputs are sanitized (secrets redacted, length
        capped). The hash chain is extended atomically per write (single-
        process journal — caller serializes).
        """
        san_cfg = self._config.sanitization
        step = ReasoningStep(
            schema_version=SCHEMA_VERSION,
            step_id=_new_step_id(),
            decision_id=decision_id,
            timestamp_utc=_now_utc(),
            phase=phase,
            actor=sanitize_string(actor, config=san_cfg),
            rationale_summary=sanitize_string(
                rationale_summary, config=san_cfg
            ),
            inputs=sanitize_value(inputs or {}, config=san_cfg),
            outputs=sanitize_value(outputs or {}, config=san_cfg),
            evidence_refs=tuple(
                sanitize_string(r, config=san_cfg) for r in evidence_refs
            ),
            parameter_versions=dict(parameter_versions or {}),
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            prev_chain_hash=self._last_chain_hash(),
        )
        return self._append(step)


__all__ = [
    "DEFAULT_REASONING_JOURNAL_PATH",
    "GENESIS_PREV_HASH",
    "PHASE_CONFIDENCE_CHANGE",
    "PHASE_EVIDENCE",
    "PHASE_INVALIDATION",
    "PHASE_RISK_ADJUSTMENT",
    "PHASE_SCORING",
    "PHASE_TRIGGER",
    "PhaseLiteral",
    "ReasoningJournal",
    "ReasoningJournalConfig",
    "ReasoningStep",
    "SCHEMA_VERSION",
]
