"""L2-Rueckschau: vorregistrierter Test der L2-Merkmale gegen den kanonischen Pool.

**Warum (Operator 2026-10-01).** Die Live-Messung liefert rund 70 L2-Paare am Tag;
ein belastbares Urteil ueber die Frage, ob Fee-/Mempool-Lage etwas ueber den Ausgang
eines Signals sagt, braucht so Wochen. Der eigene L1-Strom
(``artifacts/onchain_fee_shadow.jsonl``) reicht aber bis zum 22.06.2026 zurueck, und
der kanonische Ergebnis-Pool (``shadow_candidate_resolved.jsonl``) hat zehntausende
aufgeloeste Kandidaten. Fuer jeden laesst sich das L2-Merkmal genau so berechnen, wie
es live berechnet worden waere — nur aus L1-Daten VOR seinem Einstieg.

**VORREGISTRIERUNG** — festgeschrieben und gemergt, bevor diese Auswertung zum ersten
Mal auf echte Daten lief. Wer eine dieser Regeln nach dem Lauf aendert, macht das
Ergebnis wertlos (Datenschnueffelei); eine Aenderung braucht einen neuen, frischen
Datenzeitraum.

1. **Merkmal** — exakt die Live-Rechnung: :func:`compute_l2_features` mit Fenster,
   Mindesthistorie und TTL aus den Defaults von ``L2OnChainEvidenceSettings``; nur
   L1-Saetze mit ``ts <= Einstieg``; der letzte davon ist der aktuelle Wert, ist er
   aelter als die TTL, gibt es kein Merkmal (wie live).
2. **Population** — kanonischer Pool, Horizont 3600 s, ohne Canary-/Probe-Zeilen
   (``is_canary`` oder vom Resolver per Default ausgeschlossen); nur Kandidaten mit
   Merkmal.
3. **Einheit** — das Merkmal ist marktweit: alle Kandidaten derselben L1-Beobachtung
   und Richtung sehen denselben Wert und sind EINE Beobachtung (Cluster
   ``(l1_observed_ts, side)``, Ergebnis = Mittel der ``net_bps``).
4. **Test** — :func:`evaluate_feature_direction` unveraendert (Schnitt bei 0,5; beide
   Haelften per Block-Bootstrap > 0,95 bzw. < 0,05), Seed 1337.
5. **Urteil** je Merkmal ``bestaetigt: <richtung>`` nur, wenn (a) Kandidaten-Ebene und
   (b) Cluster-Ebene dieselbe Richtung liefern und (c) in beiden zeitlichen Haelften
   der Cluster-Reihe das Vorzeichen von ``mean_high - mean_low`` zur Richtung passt.
   Sonst ``nicht bestaetigt`` mit Grund.
6. Nur dieser Horizont, diese Schwellen, diese Einheit. Teilgruppen (Quelle, Seite)
   und die Basisrate werden NUR beschreibend ausgegeben, nie als Urteil.

Nur lesend; kein Einfluss auf Handel, Sizing, Gates oder Live-L2. Ein bestaetigtes
Urteil ist die Grundlage fuer die Operator-Entscheidung ueber ein L2-Gewicht, keine
Aktivierung.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.evidence_settings import L2OnChainEvidenceSettings
from app.observability.l2_evidence_eval import evaluate_feature_direction
from app.observability.shadow_candidate_ledger import _is_resolvable_candidate
from app.research.shadow_outcomes import (
    DEFAULT_LEDGER_PATH,
    DEFAULT_RESOLVED_PATH,
    build_outcomes,
    load_entry_times,
    parse_ts,
    read_jsonl,
    to_feature_outcomes,
)
from app.signals.l2_features import compute_l2_features

# --- Vorregistrierte Konstanten (siehe Modul-Docstring) ---------------------------
HORIZON_S = 3600
SEED = 1337
FEATURES: tuple[str, ...] = ("fee_percentile", "mempool_percentile")
_LIVE = L2OnChainEvidenceSettings.model_fields
WINDOW: int = int(_LIVE["window"].default)
MIN_WINDOW: int = int(_LIVE["min_window"].default)
TTL_S: float = float(_LIVE["ttl_seconds"].default)
L1_PATH = Path("artifacts/onchain_fee_shadow.jsonl")

_EXPECTED_SIGN = {"pro_trend": 1.0, "contrarian": -1.0}


class L1Index:
    """Zeitlich sortierter L1-Strom mit Punkt-in-Zeit-Abfrage (nur ``ts <= t``)."""

    def __init__(self, rows: Iterable[dict[str, Any]]) -> None:
        dated = [(ts, r) for r in rows if (ts := parse_ts(r.get("ts"))) is not None]
        dated.sort(key=lambda item: item[0])
        self._ts = [ts for ts, _ in dated]
        self._rows = [r for _, r in dated]

    def __len__(self) -> int:
        return len(self._rows)

    def features_at(self, at: datetime) -> dict[str, Any] | None:
        """L2-Merkmale, wie der Live-Provider sie zum Zeitpunkt ``at`` berechnet haette."""
        end = bisect.bisect_right(self._ts, at)  # nur Saetze mit ts <= at
        if end == 0:
            return None
        current_ts = self._ts[end - 1]
        if (at - current_ts).total_seconds() > TTL_S:
            return None  # L1 zu alt: live gaebe es kein Merkmal
        usable = self._rows[max(0, end - (WINDOW + 1)) : end]
        current, history = usable[-1], usable[:-1]
        if len(history) < MIN_WINDOW:
            return None
        fee_raw = current.get("fee_sat_vb")
        feats = compute_l2_features(
            history,
            fee_sat_vb=float(fee_raw) if fee_raw is not None else None,
            mempool_tx=int(current.get("mempool_tx", 0) or 0),
        )
        return {
            "fee_percentile": feats.fee_percentile,
            "mempool_percentile": feats.mempool_percentile,
            "l1_observed_ts": current_ts.isoformat(),
        }


def in_population(row: dict[str, Any]) -> bool:
    """Regel 2: ohne Canary-/Probe-Zeilen, wie der Resolver per Default."""
    return not bool(row.get("is_canary")) and _is_resolvable_candidate(row, include_canary=False)


def load_population(
    *, resolved_path: Path = DEFAULT_RESOLVED_PATH, ledger_path: Path = DEFAULT_LEDGER_PATH
) -> list[dict[str, Any]]:
    """Kanonischer Pool (Regel 2) in der flachen Form ``{..., entry_ts, net_bps}``."""
    resolved = [r for r in read_jsonl(Path(resolved_path)) if in_population(r)]
    outcomes = build_outcomes(resolved, load_entry_times(read_jsonl(Path(ledger_path))))
    return to_feature_outcomes(outcomes, horizon=HORIZON_S)


def build_pairs(
    outcomes: Sequence[dict[str, Any]], l1: L1Index
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(Messung, Ergebnis) je Kandidat mit Merkmal, zeitlich sortiert (Regel 1)."""
    pairs = []
    for o in outcomes:
        at = parse_ts(o.get("entry_ts"))
        if at is None:
            continue
        feats = l1.features_at(at)
        if feats is None:
            continue
        m = {
            "candidate_id": o["candidate_id"],
            "symbol": o["symbol"],
            "direction": o["side"],
            "ts": at.isoformat(),
            **feats,
        }
        pairs.append((m, o))
    pairs.sort(key=lambda p: p[1]["entry_ts"])
    return pairs


def cluster_pairs(
    pairs: Sequence[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Regel 3: eine Einheit je ``(l1_observed_ts, side)``, Ergebnis = Mittel."""
    groups: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for m, o in pairs:
        groups.setdefault((m["l1_observed_ts"], o["side"]), []).append((m, o))
    out = []
    for (l1_ts, side), members in groups.items():
        values = [float(o["net_bps"]) for _m, o in members]
        first_m, first_o = members[0]
        out.append(
            (
                {**first_m, "direction": side},
                {
                    "candidate_id": f"cluster:{l1_ts}:{side}",
                    "side": side,
                    "entry_ts": first_o["entry_ts"],
                    "net_bps": sum(values) / len(values),
                    "population": len(values),  # Zerlegung des Cluster-Mittels
                },
            )
        )
    out.sort(key=lambda p: p[1]["entry_ts"])
    return out


def spread(pairs: Sequence[tuple[dict[str, Any], dict[str, Any]]], feature: str) -> float | None:
    """``mean_high - mean_low`` (Schnitt 0,5 wie der Test); ``None`` bei leerer Haelfte."""
    high = [float(o["net_bps"]) for m, o in pairs if (f := m.get(feature)) is not None and f > 0.5]
    low = [float(o["net_bps"]) for m, o in pairs if (f := m.get(feature)) is not None and f <= 0.5]
    if not high or not low:
        return None
    return sum(high) / len(high) - sum(low) / len(low)


def verdict(
    pairs: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    clusters: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    feature: str,
) -> dict[str, Any]:
    """Regeln 4 und 5 fuer ein Merkmal; liefert Urteil samt Zerlegung."""
    cand = evaluate_feature_direction(pairs, feature_key=feature, seed=SEED)
    clus = evaluate_feature_direction(clusters, feature_key=feature, seed=SEED)
    half = len(clusters) // 2
    by_half = [spread(clusters[:half], feature), spread(clusters[half:], feature)]
    direction = cand["direction"]
    if direction not in _EXPECTED_SIGN:
        reason = f"Kandidaten-Ebene {direction}"
    elif clus["direction"] != direction:
        reason = f"Cluster-Ebene {clus['direction']} statt {direction}"
    elif any(s is None or s * _EXPECTED_SIGN[direction] <= 0 for s in by_half):
        reason = "zeitliche Haelften uneinig"
    else:
        reason = ""
    return {
        "feature": feature,
        "verdict": f"bestaetigt: {direction}" if not reason else f"nicht bestaetigt ({reason})",
        "candidate_assessment": cand,
        "cluster_assessment": clus,
        "by_half_spread_bps": by_half,
    }


def base_rate(pairs: Sequence[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    """Basisrate der Population (Pflicht neben jeder Richtungsaussage)."""
    values = [float(o["net_bps"]) for _m, o in pairs]
    n_pos = sum(v > 0 for v in values)
    return {
        "population": len(values),
        "mean_bps": sum(values) / len(values) if values else None,
        "numerator": n_pos,
        "denominator": len(values),
        "share_positive": n_pos / len(values) if values else None,
    }


def describe(
    pairs: Sequence[tuple[dict[str, Any], dict[str, Any]]], feature: str
) -> dict[str, Any]:
    """Regel 6: Teilgruppen NUR beschreibend (Quelle nach ID-Praefix, Seite)."""

    def source(cid: str) -> str:
        return "screener" if cid.startswith("tech-") else "loop"

    by_source: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    by_side: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for m, o in pairs:
        by_source.setdefault(source(str(o["candidate_id"])), []).append((m, o))
        by_side.setdefault(str(o["side"]), []).append((m, o))
    return {
        "feature": feature,
        "by_source_spread_bps": {k: (len(v), spread(v, feature)) for k, v in by_source.items()},
        "by_side_spread_bps": {k: (len(v), spread(v, feature)) for k, v in by_side.items()},
    }


def run(
    *,
    l1_path: Path = L1_PATH,
    resolved_path: Path = DEFAULT_RESOLVED_PATH,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> dict[str, Any]:
    """Vollstaendige vorregistrierte Auswertung (nur lesend)."""
    l1 = L1Index(read_jsonl(Path(l1_path)))
    population = load_population(resolved_path=resolved_path, ledger_path=ledger_path)
    pairs = build_pairs(population, l1)
    clusters = cluster_pairs(pairs)
    return {
        "preregistration": {
            "horizon_s": HORIZON_S,
            "seed": SEED,
            "window": WINDOW,
            "min_window": MIN_WINDOW,
            "ttl_s": TTL_S,
        },
        "l1_rows": len(l1),
        "population_with_outcome": len(population),
        "pairs": len(pairs),
        "clusters": len(clusters),
        "base_rate_candidates": base_rate(pairs),
        "base_rate_clusters": base_rate(clusters),
        "verdicts": [verdict(pairs, clusters, f) for f in FEATURES],
        "descriptive": [describe(pairs, f) for f in FEATURES],
    }


__all__ = [
    "FEATURES",
    "HORIZON_S",
    "L1Index",
    "MIN_WINDOW",
    "SEED",
    "TTL_S",
    "WINDOW",
    "base_rate",
    "build_pairs",
    "cluster_pairs",
    "describe",
    "in_population",
    "load_population",
    "run",
    "spread",
    "verdict",
]
