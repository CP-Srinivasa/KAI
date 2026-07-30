"""Evaluatoren für die versiegelten Quoten-Prä-Registrierungen vom 2026-07-29.

H1 ``technical_paper_precision_fwd_v1`` (``fd6f5f7842f49244``) und
H2 ``execution_translation_hit_to_win_v1`` (``0c7ead764621dd17``) werden von
``trading prereg-check`` mechanisch geurteilt; dieses Modul erzeugt das
Evaluator-JSON mit dem ``overall``-Block, den die Gates lesen. Die Konstruktion
folgt den ``success_criteria`` der Ledger-Einträge WÖRTLICH: ±1-Kodierung
(hit/win=+1, miss/loss=-1), ``p_positive`` = Normal-Approximation von
P(mean>0) — äquivalent P(Quote>0,5). Read-only über die Artefakt-JSONL,
kein Netzwerk, kein Zustand.

Fail-closed-Verhalten: fehlende Dateien zählen als leere Population;
``p_positive`` ist ``None`` bei n<2 (das Gate wertet das als nicht bestanden);
Close-Dokumente mit fehlendem ``trade_pnl_usd`` werden AUSGESCHLOSSEN und
gezählt statt still über das kumulative ``realized_pnl_usd`` geschätzt
(TL-003-Falle).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from math import erf, fsum, sqrt
from pathlib import Path
from typing import Any

from app.storage.jsonl_io import iter_jsonl_tolerant

# Versiegelte Claim-IDs (Pi-Ledger artifacts/research/prereg_ledger.jsonl).
TECH_PRECISION_PREREG_ID = "fd6f5f7842f49244"
EXEC_TRANSLATION_PREREG_ID = "0c7ead764621dd17"

_TECHNICAL_PAPER_PREFIX = "technical_paper"
_CLOSE_EVENTS = ("position_closed", "position_partial_closed")


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def normal_p_positive(xs: list[float]) -> float | None:
    """P(mean>0) per Normal-Approximation (einseitig) über ±1-kodierte Werte.

    ``None`` bei n<2 (keine Varianzschätzung möglich — fail-closed im Gate).
    Degenerierte Stichproben (Varianz 0) sind bei ±1-Kodierung „alle gleich":
    P=1 wenn alles positiv, P=0 wenn alles negativ.
    """
    n = len(xs)
    if n < 2:
        return None
    mean = fsum(xs) / n
    var = fsum((x - mean) ** 2 for x in xs) / (n - 1)
    if var <= 0.0:
        return 1.0 if mean > 0 else (0.5 if mean == 0 else 0.0)
    z = mean / sqrt(var / n)
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


def _overall_block(xs: list[int], horizon_s: int) -> dict[str, Any]:
    """``overall``-Block im Schema, das ``prereg_gate._resolve_row`` liest."""
    n = len(xs)
    mean = (fsum(xs) / n) if n else 0.0
    p = normal_p_positive([float(x) for x in xs])
    row: dict[str, Any] = {
        "n": n,
        "mean_x": round(mean, 4),
        "positive_rate": round((sum(1 for x in xs if x > 0) / n), 4) if n else None,
        "p_positive": round(p, 6) if p is not None else None,
    }
    return {"n": n, "horizons": {str(int(horizon_s)): row}}


def _last_outcome_rows(outcomes_path: Path) -> dict[str, dict[str, Any]]:
    """Letzte Zeile je ``document_id`` (dokument-dedupliziert, W1-Doktrin)."""
    latest: dict[str, dict[str, Any]] = {}
    for rec in iter_jsonl_tolerant(outcomes_path):
        doc = rec.get("document_id")
        if isinstance(doc, str) and doc:
            latest[doc] = rec
    return latest


def _first_fill_by_document(exec_audit_path: Path, *, prefix: str) -> dict[str, datetime]:
    firsts: dict[str, datetime] = {}
    for rec in iter_jsonl_tolerant(exec_audit_path):
        if rec.get("event_type") != "order_filled":
            continue
        doc = str(rec.get("document_id") or "")
        if not doc.startswith(prefix):
            continue
        ts = _parse_ts(rec.get("filled_at") or rec.get("timestamp_utc"))
        if ts is None:
            continue
        prev = firsts.get(doc)
        if prev is None or ts < prev:
            firsts[doc] = ts
    return firsts


def evaluate_technical_paper_precision(
    *,
    outcomes_path: Path,
    exec_audit_path: Path,
    registered_at_utc: str,
    horizon_s: int = 604800,
) -> dict[str, Any]:
    """H1: FORWARD-Precision eigener technischer Signale (±1 auf hit/miss).

    Population laut versiegeltem Kriterium: ``document_id``-Präfix
    ``technical_paper`` mit ERSTEM ``order_filled`` NACH ``registered_at_utc``.
    Outcome: letzte Zeile je Dokument, nur hit/miss; inconclusive exkludiert,
    Anteil berichtspflichtig. CoinGecko-Fallback-Anteil wird über das (seit
    diesem PR persistierte) ``price_source``-Feld berichtet; Zeilen ohne Feld
    fließen in ``price_source_coverage`` ein statt still als binance zu gelten.
    """
    reg = _parse_ts(registered_at_utc)
    if reg is None:
        raise ValueError(f"registered_at_utc nicht parsebar: {registered_at_utc!r}")

    firsts = _first_fill_by_document(exec_audit_path, prefix=_TECHNICAL_PAPER_PREFIX)
    population = {doc for doc, ts in firsts.items() if ts > reg}

    latest = _last_outcome_rows(outcomes_path)
    xs: list[int] = []
    inconclusive = 0
    pending = 0
    ps_rows = 0
    ps_fallback = 0
    for doc in sorted(population):
        row = latest.get(doc)
        outcome = (row or {}).get("outcome")
        if row is None or outcome not in ("hit", "miss", "inconclusive"):
            pending += 1
            continue
        if outcome == "inconclusive":
            inconclusive += 1
            continue
        xs.append(1 if outcome == "hit" else -1)
        src = row.get("price_source")
        if isinstance(src, str) and src:
            ps_rows += 1
            if src != "binance":
                ps_fallback += 1

    n_outcomed = len(xs) + inconclusive
    return {
        "schema": "quote_eval/tech_precision/v1",
        "hypothesis": "technical_paper_precision_fwd_v1",
        "registered_at_utc": registered_at_utc,
        "horizon_s": int(horizon_s),
        "population": {
            "fills_docs_total": len(firsts),
            "docs_first_fill_after_reg": len(population),
            "docs_resolved": len(xs),
            "docs_inconclusive": inconclusive,
            "inconclusive_share": round(inconclusive / n_outcomed, 4) if n_outcomed else None,
            "docs_pending_no_outcome": pending,
            "price_source_coverage": round(ps_rows / len(xs), 4) if xs else None,
            "price_source_fallback_share": round(ps_fallback / ps_rows, 4) if ps_rows else None,
        },
        "overall": _overall_block(xs, horizon_s),
    }


def reconstruct_tv_signal_id(event_id: str, symbol_compact: str) -> str:
    """``SIG-TVP``-Rekonstruktion nach ``tradingview_paper_feeder.build_envelope``.

    ``env_id = "ENV-TVP-" + sha256(event_id)[:16]``; die Signal-ID trägt die
    letzten 8 Hex davon: ``SIG-TVP-{SYMBOL}-{env_id[-8:]}``.
    """
    digest16 = hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:16]
    return f"SIG-TVP-{symbol_compact}-{digest16[-8:]}"


def evaluate_execution_translation(
    *,
    outcomes_path: Path,
    exec_audit_path: Path,
    registered_at_utc: str,
    horizon_s: int = 86400,
) -> dict[str, Any]:
    """H2: Übersetzen richtige Signale in Gewinn-Trades? (±1 auf win/loss).

    Population laut versiegeltem Kriterium: ``position_closed``/
    ``position_partial_closed`` NACH ``registered_at_utc``, deren
    ``document_id`` auf einen Outcome-hit joinbar ist — direkt ODER per
    sha256-Rekonstruktion (``tv:{event_id}`` → ``SIG-TVP-…``). x=+1 wenn
    ``sum(trade_pnl_usd)`` je Dokument > 0, sonst -1.
    """
    reg = _parse_ts(registered_at_utc)
    if reg is None:
        raise ValueError(f"registered_at_utc nicht parsebar: {registered_at_utc!r}")

    latest = _last_outcome_rows(outcomes_path)
    hit_direct: set[str] = set()
    hit_reconstructed: dict[str, str] = {}  # SIG-TVP-Id -> Outcome-doc
    tv_without_asset = 0
    for doc, row in latest.items():
        if row.get("outcome") != "hit":
            continue
        hit_direct.add(doc)
        if doc.startswith("tv:"):
            asset = row.get("asset")
            if not isinstance(asset, str) or not asset:
                tv_without_asset += 1
                continue
            sig = reconstruct_tv_signal_id(doc[len("tv:") :], asset.replace("/", ""))
            hit_reconstructed[sig] = doc

    pnl_by_doc: dict[str, list[float]] = {}
    docs_missing_pnl: set[str] = set()
    for rec in iter_jsonl_tolerant(exec_audit_path):
        if rec.get("event_type") not in _CLOSE_EVENTS:
            continue
        ts = _parse_ts(rec.get("timestamp_utc"))
        if ts is None or ts <= reg:
            continue
        doc = str(rec.get("document_id") or "")
        if not doc:
            continue
        pnl = rec.get("trade_pnl_usd")
        if not isinstance(pnl, int | float):
            # Kumulatives realized_pnl_usd ist KEIN Ersatz (TL-003) —
            # Dokument fail-closed ausschließen und sichtbar zählen.
            docs_missing_pnl.add(doc)
            continue
        pnl_by_doc.setdefault(doc, []).append(float(pnl))

    xs: list[int] = []
    joined_direct = 0
    joined_reconstructed = 0
    for doc in sorted(set(pnl_by_doc) - docs_missing_pnl):
        if doc in hit_direct:
            joined_direct += 1
        elif doc in hit_reconstructed:
            joined_reconstructed += 1
        else:
            continue
        xs.append(1 if fsum(pnl_by_doc[doc]) > 0 else -1)

    return {
        "schema": "quote_eval/exec_translation/v1",
        "hypothesis": "execution_translation_hit_to_win_v1",
        "registered_at_utc": registered_at_utc,
        "horizon_s": int(horizon_s),
        "population": {
            "closed_docs_since_reg": len(set(pnl_by_doc) | docs_missing_pnl),
            "docs_joined_to_hit": len(xs),
            "joined_direct": joined_direct,
            "joined_reconstructed": joined_reconstructed,
            "docs_excluded_missing_trade_pnl": len(docs_missing_pnl),
            "hit_outcomes_available": len(hit_direct),
            "tv_hits_without_asset": tv_without_asset,
        },
        "overall": _overall_block(xs, horizon_s),
    }


__all__ = [
    "EXEC_TRANSLATION_PREREG_ID",
    "TECH_PRECISION_PREREG_ID",
    "evaluate_execution_translation",
    "evaluate_technical_paper_precision",
    "normal_p_positive",
    "reconstruct_tv_signal_id",
]
