"""NEO-P-002-r3 — Real-analysis provider for the shadow path (read-only).

Phase-1 inventory (docs/design/neo_p002_r3_source_inventory_20260605.md) showed:
the canonical analyzed documents already live in the DocumentRepository
(``get_recent_analyzed``, "for shadow run input I-51"), and the loop already
exposes a clean injection seam (``run_trading_loop_once(analysis_result=...)``).
This module is the missing *delta*: map a stored ``CanonicalDocument`` back to a
schema-valid ``AnalysisResult`` and select eligible, deduplicated candidates so
the REAL ``SignalGenerator`` runs in the existing shadow path.

Strictly read-only and side-effect free except an append-only *fed-ledger* used
for idempotency (so a document is replayed into the shadow path at most once).
No execution, no orders, no entry_mode change — that invariant is owned by the
loop's shadow path; this module never touches it.

Honest mapping note: ``AnalysisResult.confidence_score`` is required (0..1) but
is not persisted as a distinct column; we map it from the stored
``credibility_score`` (= 1 - spam_probability), the closest persisted proxy, and
record that choice here rather than fabricating a constant. All other required
scores are persisted 1:1 by ``DocumentRepository.update_analysis``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app.core.domain.document import AnalysisResult, CanonicalDocument
from app.core.enums import SentimentLabel

logger = logging.getLogger(__name__)

FED_LEDGER_PATH = Path("artifacts/shadow_real_fed.jsonl")


def _clamp01(v: float | None) -> float:
    if v is None:
        return 0.0
    return max(0.0, min(1.0, float(v)))


def _clamp_signed(v: float | None) -> float:
    if v is None:
        return 0.0
    return max(-1.0, min(1.0, float(v)))


def _has_confidence_signal(doc: CanonicalDocument) -> bool:
    """True iff the document carries ANY persisted confidence axis.

    ``credibility_score`` and ``spam_probability`` both default to ``None``. When
    neither is set, confidence is *not calibratable* — such a document must never
    be treated as high-confidence (see ``_confidence_proxy`` / ``is_eligible``).
    """
    return doc.credibility_score is not None or doc.spam_probability is not None


def _confidence_proxy(doc: CanonicalDocument) -> float:
    """confidence_score proxy from the closest persisted axis.

    Order: persisted ``credibility_score`` (= 1 - spam_prob), else derived from
    ``spam_probability``. When NEITHER is persisted, confidence is unknown and we
    return a conservative ``0.0`` — NEVER ``1.0``: an unknown score must not read
    as maximal confidence, which would optimistically bias the very edge
    measurement r3 exists to keep honest (CLAUDE.md: no fabricated optimistic
    defaults). ``is_eligible`` additionally excludes such docs, so this 0.0 is a
    defensive floor, not a signal that should reach the generator.
    """
    if doc.credibility_score is not None:
        return _clamp01(doc.credibility_score)
    if doc.spam_probability is not None:
        return _clamp01(1.0 - _clamp01(doc.spam_probability))
    return 0.0


def canonical_to_analysis_result(doc: CanonicalDocument) -> AnalysisResult:
    """Reconstruct a schema-valid ``AnalysisResult`` from a stored document.

    Pure / no IO. Ranges are clamped defensively so a slightly out-of-range
    persisted value never raises inside the strict pydantic model. ``document_id``
    traces straight back to the canonical document, keeping the shadow candidate
    auditable (``source=autonomous_generator`` is then derived downstream by the
    loop from this real, non-``loop_control_*`` document_id).
    """
    affected = list(doc.tickers or []) or list(getattr(doc, "crypto_assets", []) or [])
    short = (doc.title or "").strip() or f"analyzed:{doc.id}"
    long = (getattr(doc, "subtitle", None) or doc.title or "").strip() or short
    return AnalysisResult(
        document_id=str(doc.id),
        sentiment_label=doc.sentiment_label or SentimentLabel.NEUTRAL,
        sentiment_score=_clamp_signed(doc.sentiment_score),
        relevance_score=_clamp01(doc.relevance_score),
        impact_score=_clamp01(doc.impact_score),
        # confidence_score proxy: persisted credibility/spam axis, conservative
        # 0.0 when neither is known (never 1.0). See _confidence_proxy.
        confidence_score=_confidence_proxy(doc),
        novelty_score=_clamp01(doc.novelty_score),
        market_scope=doc.market_scope,
        affected_assets=affected,
        explanation_short=short,
        explanation_long=long,
        actionable=bool(doc.priority_score and doc.directional_confidence),
        tags=list(doc.tags or []),
        spam_probability=_clamp01(doc.spam_probability),
        recommended_priority=doc.priority_score,
        directional_confidence=_clamp01(doc.directional_confidence),
    )


def _symbol_for(doc: CanonicalDocument, *, quote: str = "USDT") -> str | None:
    """First affected asset → trading symbol (BTC → BTC/USDT). None if absent."""
    assets = list(doc.tickers or []) or list(getattr(doc, "crypto_assets", []) or [])
    for a in assets:
        a = str(a).strip().upper()
        if not a:
            continue
        return a if "/" in a else f"{a}/{quote}"
    return None


@dataclass(frozen=True)
class FeedCandidate:
    symbol: str
    analysis: AnalysisResult


def is_eligible(
    doc: CanonicalDocument, *, min_directional_confidence: float = 0.0
) -> tuple[bool, str]:
    """Read-only eligibility verdict. Returns (eligible, reject_reason).

    Eligible = has a tradable symbol AND a calibratable confidence signal AND a
    directional signal. A document with no persisted confidence axis is excluded
    (``no_confidence_signal``) so unknown-confidence docs never reach the
    generator as edge candidates. Priority gating is intentionally left to the
    loop's existing D-182 gate (we do not bypass or re-implement it).
    ``min_directional_confidence`` lets the driver tighten the directional filter
    without touching the loop.
    """
    if _symbol_for(doc) is None:
        return False, "no_symbol"
    if not _has_confidence_signal(doc):
        return False, "no_confidence_signal"
    dc = _clamp01(doc.directional_confidence)
    if dc <= min_directional_confidence:
        return False, "non_directional"
    return True, "eligible"


def _read_fed_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            did = rec.get("document_id")
            if isinstance(did, str) and did:
                out.add(did)
    except OSError as exc:  # noqa: BLE001
        logger.warning("[r3-provider] fed-ledger read failed: %s", exc)
    return out


def mark_fed(document_id: str, *, path: Path = FED_LEDGER_PATH) -> None:
    """Append-only idempotency marker. Fail-soft."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"document_id": str(document_id)}, ensure_ascii=False) + "\n")
    except OSError as exc:  # noqa: BLE001
        logger.warning("[r3-provider] fed-ledger write failed: %s", exc)


def select_pending(
    docs: Iterable[CanonicalDocument],
    *,
    fed_ledger_path: Path = FED_LEDGER_PATH,
    min_directional_confidence: float = 0.0,
) -> tuple[list[FeedCandidate], dict[str, int]]:
    """Pure selection over already-fetched analyzed docs (no DB, no IO except the
    fed-ledger read). Returns (eligible candidates, funnel counters).

    Funnel axes: seen, already_fed, no_symbol, non_directional, eligible.
    Idempotent: documents whose id is in the fed-ledger are skipped (``already_fed``).
    The caller marks each injected candidate via ``mark_fed`` after the loop ran.
    """
    fed = _read_fed_ids(fed_ledger_path)
    funnel = {
        "seen": 0,
        "already_fed": 0,
        "no_symbol": 0,
        "no_confidence_signal": 0,
        "non_directional": 0,
        "eligible": 0,
    }
    out: list[FeedCandidate] = []
    for doc in docs:
        funnel["seen"] += 1
        if str(doc.id) in fed:
            funnel["already_fed"] += 1
            continue
        ok, reason = is_eligible(doc, min_directional_confidence=min_directional_confidence)
        if not ok:
            funnel[reason] = funnel.get(reason, 0) + 1
            continue
        symbol = _symbol_for(doc)
        assert symbol is not None  # is_eligible guaranteed it
        funnel["eligible"] += 1
        out.append(FeedCandidate(symbol=symbol, analysis=canonical_to_analysis_result(doc)))
    return out, funnel


__all__ = [
    "FED_LEDGER_PATH",
    "FeedCandidate",
    "canonical_to_analysis_result",
    "is_eligible",
    "mark_fed",
    "select_pending",
]
