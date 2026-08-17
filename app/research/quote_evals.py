"""Evaluatoren für die versiegelten Quoten-Prä-Registrierungen.

H1 ``technical_paper_precision_fwd_v1`` (``fd6f5f7842f49244``) und
H2 ``execution_translation_hit_to_win_v1`` (``0c7ead764621dd17``) werden von
``trading prereg-check`` mechanisch geurteilt; dieses Modul erzeugt das
Evaluator-JSON mit dem ``overall``-Block, den die Gates lesen. Die Konstruktion
folgt den ``success_criteria`` der Ledger-Einträge WÖRTLICH: ±1-Kodierung
(hit/win=+1, miss/loss=-1), ``p_positive`` = Normal-Approximation von
P(mean>0) — äquivalent P(Quote>0,5). Read-only über die Artefakt-JSONL,
kein Netzwerk, kein Zustand.

H2 ist am 2026-08-08 als CLOSED_UNMEASURABLE geschlossen (n=14/50, kein
Sachverdikt): nur ~26 % der geschlossenen Trades konnten die Population je
erreichen. Nachfolger ist ``signal_hit_to_win_conversion_v2``
(``26d3e0eb29f553f3``) — gleiche Frage, reparierte Messung: die miss-Seite
wird als **nicht gatende** Diagnostik mitgemessen (erst die Zellmatrix trennt
ein Signal- von einem Execution-Problem), die Populationslücke wird nach
``signal_source`` gezählt statt verschwiegen, und der Claim trägt eine Frist.
Der H2-Evaluator bleibt erhalten: ein geschlossener Claim muss reproduzierbar
bleiben.

Fail-closed-Verhalten: fehlende Dateien zählen als leere Population;
``p_positive`` ist ``None`` bei n<2 (das Gate wertet das als nicht bestanden);
Close-Dokumente mit fehlendem ``trade_pnl_usd`` werden AUSGESCHLOSSEN und
gezählt statt still über das kumulative ``realized_pnl_usd`` geschätzt
(TL-003-Falle).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from math import comb, erf, fsum, sqrt
from pathlib import Path
from typing import Any

from app.research.decomposition import decompose_rate
from app.storage.jsonl_io import iter_jsonl_tolerant

# Versiegelte Claim-IDs (Pi-Ledger artifacts/research/prereg_ledger.jsonl).
TECH_PRECISION_PREREG_ID = "fd6f5f7842f49244"
EXEC_TRANSLATION_PREREG_ID = "0c7ead764621dd17"  # 2026-08-08 CLOSED_UNMEASURABLE
# Nachfolger von H2, registriert 2026-08-08T10:41:26Z. Gate n>=30/p>=0.90,
# Frist 2026-09-22 ⇒ INCONCLUSIVE_BY_TIMEOUT (der Vorgänger hatte keine).
HIT_TO_WIN_V2_PREREG_ID = "26d3e0eb29f553f3"
# Versiegelte Gate-Schwelle des v2-Claims. Steht hier NUR, damit die
# nicht-gatende Robustheits-Diagnostik weiß, an welcher Kante sie vergleicht —
# das gatende Urteil fällt unverändert in ``prereg_gate``.
_HIT_TO_WIN_V2_P_MIN = 0.90

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


def p_positive_robustness(xs: list[float]) -> dict[str, Any]:
    """Nicht-gatende Gegenrechnung zur versiegelten Normal-Approximation.

    Die Approximation IST das versiegelte Kriterium (``26d3e0eb29f553f3``:
    "p_positive = normal approximation of P(mean>0)" … "no criterion change").
    Sie wird hier deshalb NICHT ersetzt — sie wird nur eingeordnet.

    Bei ±1-Kodierung ist die Größe eine Binomialrate, und die Normal-Näherung
    ist bei n≈30 leicht optimistisch. Durchgerechnet über k=15..25 bei n=30
    weichen die Verfahren an der Gate-Schwelle 0,90 an **genau einem** Ausgang
    voneinander ab: k=19 (Wald 0,9319 · Posterior 0,9252 · exakter einseitiger
    Binomialtest 0,8998). Genau dort — und nur dort — hängt das Verdikt an der
    Wahl des Verfahrens statt an den Daten. Das Flag macht diesen Grenzfall im
    Report sichtbar, ohne das Gate zu berühren.
    """
    n = len(xs)
    gating = normal_p_positive(xs)
    out: dict[str, Any] = {
        "gating_wald": round(gating, 6) if gating is not None else None,
        "gating_method": "normal approximation of P(mean>0) — sealed, authoritative",
        "bayes_posterior_uniform": None,
        "binomial_exact_onesided": None,
        "gate_p_min": _HIT_TO_WIN_V2_P_MIN,
        "methods_disagree_at_gate": False,
        "note": (
            "Nicht gatend. Weicht ein Verfahren am Gate ab, ist das Verdikt ein "
            "Grenzfall der Methode — so und nicht als klares Ergebnis zitieren."
        ),
    }
    # Exakte Verfahren setzen eine saubere 0/1-Zählung voraus. Alles andere als
    # eine ±1-Kodierung wird nicht zurechtgebogen, sondern ausgelassen.
    if gating is None or any(x not in (1.0, -1.0) for x in xs):
        return out

    k = sum(1 for x in xs if x > 0)
    # P(theta>0.5) unter Beta(1,1)-Prior => Beta(k+1, n-k+1). Für ganzzahlige
    # Parameter gilt P = sum_{j=0}^{k} C(n+1, j) * 0.5^(n+1).
    posterior = fsum(comb(n + 1, j) for j in range(0, k + 1)) * 0.5 ** (n + 1)
    # Einseitiger exakter Binomialtest gegen H0: theta=0.5; als 1-p berichtet,
    # damit alle drei Zahlen dieselbe Richtung haben ("höher = stärker").
    p_value = fsum(comb(n, j) for j in range(k, n + 1)) * 0.5**n
    exact_test = 1.0 - p_value

    out["bayes_posterior_uniform"] = round(posterior, 6)
    out["binomial_exact_onesided"] = round(exact_test, 6)
    bar = _HIT_TO_WIN_V2_P_MIN
    out["methods_disagree_at_gate"] = len({gating >= bar, posterior >= bar, exact_test >= bar}) > 1
    return out


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
    # Zerlegungs-Achse: Preisquelle. Eine Precision, die nur unter dem
    # CoinGecko-Fallback hält, ist eine andere Aussage als eine, die unter
    # Binance hält — das Aggregat allein zeigt den Unterschied nicht.
    units_for_decomposition: list[tuple[str, bool]] = []
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
        units_for_decomposition.append(
            (src if isinstance(src, str) and src else "unknown", outcome == "hit")
        )

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
        # PFLICHT (Direktive 2026-08-08): kein Aggregat ohne Zerlegung.
        "decomposition": decompose_rate(
            units_for_decomposition,
            group_of=lambda u: u[0],
            is_positive=lambda u: u[1],
        ),
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
    # Zerlegungs-Achse: Join-Art. Ein Ergebnis, das nur an rekonstruierten
    # TV-IDs hängt, ist schwächer belegt als eines aus direkten Joins.
    units_for_decomposition: list[tuple[str, bool]] = []
    for doc in sorted(set(pnl_by_doc) - docs_missing_pnl):
        if doc in hit_direct:
            joined_direct += 1
            join_kind = "direct"
        elif doc in hit_reconstructed:
            joined_reconstructed += 1
            join_kind = "reconstructed"
        else:
            continue
        won = fsum(pnl_by_doc[doc]) > 0
        xs.append(1 if won else -1)
        units_for_decomposition.append((join_kind, won))

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
        # PFLICHT (Direktive 2026-08-08): kein Aggregat ohne Zerlegung.
        # Auch für den GESCHLOSSENEN Claim — ein archiviertes Ergebnis muss
        # genauso prüfbar bleiben wie ein laufendes.
        "decomposition": decompose_rate(
            units_for_decomposition,
            group_of=lambda u: u[0],
            is_positive=lambda u: u[1],
        ),
        "overall": _overall_block(xs, horizon_s),
    }


def evaluate_hit_to_win_conversion(
    *,
    outcomes_path: Path,
    exec_audit_path: Path,
    registered_at_utc: str,
    horizon_s: int = 86400,
) -> dict[str, Any]:
    """H2-Nachfolger: wird ein bestätigt richtiges Signal zu einem Gewinn-Trade?

    Nachfolger von ``execution_translation_hit_to_win_v1`` (2026-08-08 als
    CLOSED_UNMEASURABLE geschlossen: nur ~26 % der Closes konnten die
    Population je erreichen). Die **Frage** bleibt, die **Messung** ist
    repariert — und zwar in drei Punkten:

    1. **Gatend** ist weiterhin die Konversion über ``outcome == "hit"``
       (±1 auf die ``trade_pnl_usd``-Summe je Dokument). Das ist die
       ökonomisch entscheidende Größe und wird NICHT verwässert.
    2. **Diagnostisch** (nicht gatend) wird die ``miss``-Seite mitgemessen.
       Erst die Zellmatrix trennt ein Signal- von einem Execution-Problem:
       diskriminiert das Signal (hohe Trennschärfe), scheitert aber die
       Umsetzung, liegt es an der Stop-/Ziel-Geometrie — nicht am Signal.
    3. Die **Populationslücke** wird gezählt statt verschwiegen: Closes ohne
       Outcome-Eintrag erscheinen nach ``signal_source`` aufgeschlüsselt.
       Genau diese Blindstelle ließ den Vorgänger verhungern.

    Ausreißerfest per Konstruktion: ±1 statt PnL-Mittelwert (die Lehre aus
    „ohne Best-Trade 3,50 %" — ein einzelner Trade darf kein Verdikt tragen).

    Fail-closed wie H1/H2: fehlende Datei ⇒ leere Population; Dokumente ohne
    ``trade_pnl_usd`` werden ausgeschlossen und gezählt (TL-003-Falle);
    ``p_positive`` ist ``None`` bei n<2.
    """
    reg = _parse_ts(registered_at_utc)
    if reg is None:
        raise ValueError(f"registered_at_utc nicht parsebar: {registered_at_utc!r}")

    latest = _last_outcome_rows(outcomes_path)

    pnl_by_doc: dict[str, list[float]] = {}
    docs_missing_pnl: set[str] = set()
    source_by_doc: dict[str, str] = {}
    for rec in iter_jsonl_tolerant(exec_audit_path):
        if rec.get("event_type") not in _CLOSE_EVENTS:
            continue
        ts = _parse_ts(rec.get("timestamp_utc"))
        if ts is None or ts <= reg:
            continue
        doc = str(rec.get("document_id") or "")
        if not doc:
            continue
        source_by_doc[doc] = str(rec.get("signal_source") or "unknown")
        pnl = rec.get("trade_pnl_usd")
        if not isinstance(pnl, int | float):
            docs_missing_pnl.add(doc)
            continue
        pnl_by_doc.setdefault(doc, []).append(float(pnl))

    xs: list[int] = []  # GATEND: nur hit-Dokumente
    cells = {"hit_win": 0, "hit_loss": 0, "miss_win": 0, "miss_loss": 0}
    concordant = 0
    concordance_n = 0
    absent_by_source: dict[str, int] = {}
    excluded_outcomes: dict[str, int] = {}
    units_for_decomposition: list[tuple[str, bool]] = []

    for doc in sorted(set(pnl_by_doc) - docs_missing_pnl):
        row = latest.get(doc)
        if row is None:
            src = source_by_doc.get(doc, "unknown")
            absent_by_source[src] = absent_by_source.get(src, 0) + 1
            continue
        outcome = str(row.get("outcome") or "")
        if outcome not in ("hit", "miss"):
            key = outcome or "missing"
            excluded_outcomes[key] = excluded_outcomes.get(key, 0) + 1
            continue
        won = fsum(pnl_by_doc[doc]) > 0
        cells[f"{outcome}_{'win' if won else 'loss'}"] += 1
        concordance_n += 1
        # Einheit für die Pflicht-Zerlegung: Gruppe = Signal-Outcome,
        # positiv = konkordant. Genau diese Achse deckte am 2026-08-08 auf,
        # dass die Konkordanz fast vollständig von der miss-Seite kam.
        units_for_decomposition.append((outcome, (outcome == "hit") == won))
        if (outcome == "hit") == won:
            concordant += 1
        if outcome == "hit":
            xs.append(1 if won else -1)

    n_hit = cells["hit_win"] + cells["hit_loss"]
    n_miss = cells["miss_win"] + cells["miss_loss"]
    win_rate_hit = (cells["hit_win"] / n_hit) if n_hit else None
    win_rate_miss = (cells["miss_win"] / n_miss) if n_miss else None
    # ANTEILS-Differenz, nicht Prozentpunkte: 0.3778 sind 37,78 pp. Der
    # Feldname steht so in den versiegelten ``success_criteria`` und wird
    # deshalb NICHT umbenannt — wer ihn rendert, multipliziert mit 100.
    discrimination = (
        round(win_rate_hit - win_rate_miss, 4)
        if win_rate_hit is not None and win_rate_miss is not None
        else None
    )

    return {
        "schema": "quote_eval/hit_to_win_conversion/v2",
        "hypothesis": "signal_hit_to_win_conversion_v2",
        "registered_at_utc": registered_at_utc,
        "horizon_s": int(horizon_s),
        "population": {
            "closed_docs_since_reg": len(set(pnl_by_doc) | docs_missing_pnl),
            "annotated_hit_or_miss": concordance_n,
            "docs_excluded_missing_trade_pnl": len(docs_missing_pnl),
            "absent_from_outcome_ledger": sum(absent_by_source.values()),
            "absent_by_signal_source": dict(sorted(absent_by_source.items())),
            "excluded_by_outcome": dict(sorted(excluded_outcomes.items())),
        },
        # PFLICHT (Direktive 2026-08-08): kein Aggregat ohne Zerlegung.
        "decomposition": decompose_rate(
            [(o, w) for o, w in units_for_decomposition],
            group_of=lambda u: u[0],
            is_positive=lambda u: u[1],
        ),
        # NICHT gatend — Diagnose, die aus einem FAIL die nächste Handlung macht.
        "diagnostics": {
            "cells": dict(cells),
            "n_hit": n_hit,
            "n_miss": n_miss,
            "win_rate_hit": round(win_rate_hit, 4) if win_rate_hit is not None else None,
            "win_rate_miss": round(win_rate_miss, 4) if win_rate_miss is not None else None,
            "discrimination_pp": discrimination,
            "concordance_n": concordance_n,
            "concordance_rate": (round(concordant / concordance_n, 4) if concordance_n else None),
            # Ordnet die versiegelte Approximation ein, ersetzt sie nicht.
            "p_positive_robustness": p_positive_robustness([float(x) for x in xs]),
        },
        "overall": _overall_block(xs, horizon_s),
    }


__all__ = [
    "EXEC_TRANSLATION_PREREG_ID",
    "HIT_TO_WIN_V2_PREREG_ID",
    "TECH_PRECISION_PREREG_ID",
    "evaluate_execution_translation",
    "evaluate_hit_to_win_conversion",
    "evaluate_technical_paper_precision",
    "normal_p_positive",
    "p_positive_robustness",
    "reconstruct_tv_signal_id",
]
