"""Truth-Lint — zentrales Invariant-Registry über KAIs eigene Ledger (Operator-Direktive 07-11).

Eine Wahrheitsplattform muss ihrer eigenen Buchhaltung automatisch misstrauen:
Die bekannten Selbstlügen-Klassen (Mock-Preise als Fills #584, Cross-Path-
Episoden-Inflation #579, kumulatives PnL als Trade-PnL, …) werden hier als
benannte, versionierte Invarianten kodifiziert statt als Einmal-Forensik.

Design (bindend, Operator 07-11):

* **Registry, keine Prüfungs-Sammlung** — jede Invariante trägt ID,
  Beschreibung, betroffene Daten, Severity, Owner, Status; auch noch nicht
  implementierte Invarianten sind registriert (``status="planned"``), damit
  die Abdeckungslücke sichtbar bleibt statt zu verschwinden.
* **Severity-Semantik:** INFO → Digest-Zeile · WARNING → Truth-Status
  degraded · ERROR → Dataset/Report-Quarantäne-Marker · CRITICAL →
  Freigabe-/Evidence-Claim-Block (``--gate`` Exit 2). **Ehrliche Grenze:**
  das Gate ist VERFÜGBAR, aber noch nicht systemweit an Publikations-/
  Evidence-Pfade verdrahtet — bis der (bewusst kleine) Consumer-PR existiert
  gilt: „Blocker verfügbar, nicht enforced". Analyse/Forensik laufen bei
  einem Block weiter; gesperrt wird nur der belastbare Evidence-Claim.
* **NIE stillschweigend korrigieren.** Lint erkennt, kennzeichnet und
  quarantänisiert (append-only Marker) — es verändert keine Quelldaten.

Baselines: append-only Ledger tragen dokumentierte Alt-Vorfälle für immer
(z. B. die drei Mock-Fills vor Gate #584). Damit historische, bereits
aufgearbeitete Incidents nicht dauerhaft CRITICAL schreien, prüfen einzelne
Invarianten nur ab einer im Code dokumentierten Baseline — der Zweck ist
Regressions-Erkennung, nicht Dauer-Alarm über erledigte Forensik.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from typing import Any

from app.storage.jsonl_io import iter_jsonl_tolerant

DEFAULT_LINT_REPORT_PATH = Path("artifacts/truth_lint_report.jsonl")
DEFAULT_QUARANTINE_PATH = Path("artifacts/truth_quarantine.jsonl")

# Gate #584 live auf Pi (Mock-Fills unmöglich, refused-Marker existiert) —
# TL-001/TL-002 prüfen ab hier; die drei Alt-Vorfälle (SUMR/MIM/USDT-USDT)
# sind aufgearbeitet und dokumentiert (manual_void…, kai_mock_priced_fills_gate).
BASELINE_MOCK_GATE_UTC = "2026-07-11T00:00:00+00:00"
# Provenance-Felder flächig erst seit Anfang Juli — ältere Rows ohne
# signal_path_id sind Schema-Historie, keine Verletzung.
BASELINE_PROVENANCE_UTC = "2026-07-01T00:00:00+00:00"

_EVIDENCE_CAP = 10  # pro Invariante; total_count trägt die volle Zahl


class Severity(IntEnum):
    INFO = 10
    WARNING = 20
    ERROR = 30
    CRITICAL = 40

    @property
    def label(self) -> str:
        return self.name


@dataclass(frozen=True)
class Violation:
    invariant_id: str
    severity: Severity
    dataset: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "invariant_id": self.invariant_id,
            "severity": self.severity.label,
            "dataset": self.dataset,
            "message": self.message,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class LintContext:
    """Pfad-Kontext eines Laufs — Checks lesen NUR hierüber (testbar)."""

    artifacts_dir: Path

    @property
    def loop_audit(self) -> Path:
        return self.artifacts_dir / "trading_loop_audit.jsonl"

    @property
    def paper_audit(self) -> Path:
        return self.artifacts_dir / "paper_execution_audit.jsonl"

    @property
    def alert_outcomes(self) -> Path:
        return self.artifacts_dir / "alert_outcomes.jsonl"

    @property
    def verdicts_dir(self) -> Path:
        return self.artifacts_dir / "research" / "verdicts"


@dataclass(frozen=True)
class Invariant:
    invariant_id: str
    beschreibung: str
    betroffene_daten: tuple[str, ...]
    severity: Severity
    owner: str
    status: str  # "active" | "planned"
    check: Callable[[LintContext], list[Violation]] | None = None
    quarantaene: str = "marker"  # append-only Marker; NIE Quelldaten anfassen


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _after_baseline(raw_ts: object, baseline_iso: str) -> bool:
    ts = _parse_ts(raw_ts)
    if ts is None:
        # Fail-closed Richtung Sichtbarkeit: unparsebare Zeit zählt als "neu".
        return True
    return ts >= datetime.fromisoformat(baseline_iso)


# ── Aktive Checks ─────────────────────────────────────────────────────────────


def _check_mock_in_fills(ctx: LintContext) -> list[Violation]:
    """TL-001: Mock-/Synthetic-Marktdaten dürfen nach Gate #584 nie wieder in
    einem Loop-Zyklus auftauchen, ohne als refused markiert zu sein."""
    hits: list[dict[str, Any]] = []
    total = 0
    for rec in iter_jsonl_tolerant(ctx.loop_audit):
        blob = json.dumps(rec, ensure_ascii=False)
        if (
            '"market_data_source": "mock"' not in blob
            and "'market_data_source': 'mock'" not in blob
        ):
            continue
        if "synthetic_last_resort_refused" in blob:
            continue  # Gate hat gegriffen — gewolltes Verhalten
        if not _after_baseline(
            rec.get("completed_at") or rec.get("started_at"), BASELINE_MOCK_GATE_UTC
        ):
            continue  # dokumentierte Alt-Vorfälle vor dem Gate
        total += 1
        if len(hits) < _EVIDENCE_CAP:
            hits.append(
                {
                    "symbol": rec.get("symbol"),
                    "ts": rec.get("completed_at") or rec.get("started_at"),
                    "cycle_id": rec.get("cycle_id"),
                }
            )
    if not total:
        return []
    return [
        Violation(
            invariant_id="TL-001",
            severity=Severity.CRITICAL,
            dataset="trading_loop_audit.jsonl",
            message=(
                f"{total} Loop-Zyklen mit market_data_source=mock OHNE refused-Marker "
                f"nach Gate-Baseline {BASELINE_MOCK_GATE_UTC[:10]} — Regression von #584"
            ),
            evidence={"count": total, "samples": hits},
        )
    ]


def _check_mock_price_band(ctx: LintContext) -> list[Violation]:
    """TL-002: Fills im verdächtigen Mock-Preisband (~100 $) nach Gate-Baseline.

    Der Mock-Adapter preist um ~100 (SUMR 100,76 · SKYAI 101,94). Reale Assets
    KÖNNEN dort handeln — darum WARNING (Hinweis auf Sichtprüfung), nie ERROR.
    """
    band_lo, band_hi = 95.0, 105.0
    per_symbol: dict[str, int] = {}
    total = 0
    for rec in iter_jsonl_tolerant(ctx.paper_audit):
        if rec.get("event_type") != "order_filled":
            continue
        if not _after_baseline(rec.get("timestamp_utc"), BASELINE_MOCK_GATE_UTC):
            continue
        try:
            price = float(rec.get("fill_price") or 0.0)
        except (TypeError, ValueError):
            continue
        if band_lo <= price <= band_hi:
            total += 1
            sym = str(rec.get("symbol"))
            per_symbol[sym] = per_symbol.get(sym, 0) + 1
    if not total:
        return []
    top = sorted(per_symbol.items(), key=lambda kv: -kv[1])[:_EVIDENCE_CAP]
    return [
        Violation(
            invariant_id="TL-002",
            severity=Severity.WARNING,
            dataset="paper_execution_audit.jsonl",
            message=(
                f"{total} Fills im Mock-Preisband [{band_lo:.0f},{band_hi:.0f}] seit "
                f"{BASELINE_MOCK_GATE_UTC[:10]} — gegen reale Venue-Preise sichtprüfen"
            ),
            evidence={"count": total, "per_symbol": dict(top)},
        )
    ]


def _tl004_metrics(ctx: LintContext) -> dict[str, Any]:
    """Klassifikations-Metriken für TL-004 (Operator-Nachtrag 07-11).

    Rohe Zeilenzahl darf NIE als unabhängige Episodenanzahl gelesen werden —
    darum trägt jede TL-004-Verletzung die Dimensionen, die Transparenz
    (dieselbe Episode über legitime, DISTINKTE Beobachtungspfade) von echter
    Inflation (dieselben Ereignisse mehrfach als unabhängige Evidenz)
    unterscheidbar machen."""
    raw_rows = 0
    doc_ids: set[str] = set()
    path_ids: set[str] = set()
    null_path_rows = 0
    for rec in iter_jsonl_tolerant(ctx.alert_outcomes):
        if rec.get("outcome") not in ("hit", "miss"):
            continue
        raw_rows += 1
        doc = rec.get("document_id")
        if isinstance(doc, str) and doc:
            doc_ids.add(doc)
        prov = rec.get("provenance")
        pid = prov.get("signal_path_id") if isinstance(prov, dict) else None
        if isinstance(pid, str) and pid:
            path_ids.add(pid)
        else:
            null_path_rows += 1
    duplicate_ratio = round(1.0 - (len(doc_ids) / raw_rows), 4) if raw_rows else 0.0
    return {
        "raw_rows": raw_rows,
        "distinct_document_ids": len(doc_ids),
        "duplicate_ratio": duplicate_ratio,
        "distinct_signal_path_ids": len(path_ids),
        "null_path_rows": null_path_rows,
    }


def _tl004_previous_largest(ctx: LintContext) -> int | None:
    """largest_episode_size des VORHERIGEN Lint-Laufs (read-only) für
    growth_since_last_run; None wenn kein früherer TL-004-Eintrag existiert."""
    prev: int | None = None
    for rec in iter_jsonl_tolerant(ctx.artifacts_dir / "truth_lint_report.jsonl"):
        for v in rec.get("violations") or []:
            if v.get("invariant_id") != "TL-004":
                continue
            size = (v.get("evidence") or {}).get("largest_episode_size")
            if isinstance(size, int):
                prev = size
    return prev


def _check_cross_path_episode_inflation(ctx: LintContext) -> list[Violation]:
    """TL-004: Ein einzelner Markt-Move darf die Outcome-Zählung nicht über
    parallele Signalpfade aufblähen (#579-Klasse). Reuse der kanonischen
    Episoden-Clusterung; Schwelle = größte Episode > 40 Rows."""
    if not ctx.alert_outcomes.exists():
        return []
    try:
        from app.observability.outcome_dedupe_report import build_episode_dedupe_report

        report = build_episode_dedupe_report(
            audit_path=ctx.alert_outcomes,
            alert_audit_path=ctx.artifacts_dir / "alert_audit.jsonl",
        )
    except Exception as exc:  # noqa: BLE001 — Lint darf nie am Helfer sterben
        return [
            Violation(
                invariant_id="TL-004",
                severity=Severity.WARNING,
                dataset="alert_outcomes.jsonl",
                message=f"Episoden-Clusterung nicht berechenbar ({exc}) — Abdeckungslücke",
                evidence={},
            )
        ]
    largest = int(getattr(report, "largest_episode_size", 0) or 0)
    if largest <= 40:
        return []
    metrics = _tl004_metrics(ctx)
    prev_largest = _tl004_previous_largest(ctx)
    episode_total = int(getattr(report, "episode_total", 0) or 0)
    return [
        Violation(
            invariant_id="TL-004",
            severity=Severity.WARNING,
            dataset="alert_outcomes.jsonl",
            message=(
                f"größte Markt-Episode umfasst {largest} resolved Rows "
                f"({episode_total} kanonische Episoden) — rohe Zeilenzahl NIE als "
                "unabhängige Episodenanzahl verwenden; nur episoden-dedupliziert zitieren"
            ),
            evidence={
                "largest_episode_size": largest,
                "canonical_episode_count": episode_total,
                "growth_since_last_run": (
                    largest - prev_largest if prev_largest is not None else None
                ),
                **metrics,
            },
        )
    ]


def _check_missing_provenance(ctx: LintContext) -> list[Violation]:
    """TL-008: resolved Outcome-Rows ohne provenance.signal_path_id (post-Baseline)."""
    total = 0
    samples: list[dict[str, Any]] = []
    for rec in iter_jsonl_tolerant(ctx.alert_outcomes):
        if rec.get("outcome") not in ("hit", "miss"):
            continue
        if not _after_baseline(rec.get("annotated_at"), BASELINE_PROVENANCE_UTC):
            continue
        prov = rec.get("provenance")
        if isinstance(prov, dict) and prov.get("signal_path_id"):
            continue
        total += 1
        if len(samples) < _EVIDENCE_CAP:
            samples.append({"asset": rec.get("asset"), "annotated_at": rec.get("annotated_at")})
    if not total:
        return []
    return [
        Violation(
            invariant_id="TL-008",
            severity=Severity.WARNING,
            dataset="alert_outcomes.jsonl",
            message=(
                f"{total} resolved Rows ohne provenance.signal_path_id seit "
                f"{BASELINE_PROVENANCE_UTC[:10]} — Episoden-/Pfad-Forensik verliert Anker"
            ),
            evidence={"count": total, "samples": samples},
        )
    ]


def _check_report_integrity(ctx: LintContext) -> list[Violation]:
    """TL-011: Jeder attestierte Verdict-Report muss sich selbst beweisen —
    Payload-Rehash == Attestation-Hash; genau EIN code_version pro Report."""
    out: list[Violation] = []
    vdir = ctx.verdicts_dir
    if not vdir.is_dir():
        return []
    from app.truth.attestation import verify_attestation

    for path in sorted(vdir.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            payload = report["payload"]
            attestation = report["attestation"]
        except (OSError, ValueError, KeyError, TypeError):
            out.append(
                Violation(
                    invariant_id="TL-011",
                    severity=Severity.CRITICAL,
                    dataset=f"research/verdicts/{path.name}",
                    message="Verdict-Report unlesbar/ohne payload+attestation",
                    evidence={"file": path.name},
                )
            )
            continue
        try:
            ok = verify_attestation(payload, attestation)
        except Exception:  # noqa: BLE001 — defekte Attestation = Verletzung, kein Crash
            ok = False
        if not ok:
            out.append(
                Violation(
                    invariant_id="TL-011",
                    severity=Severity.CRITICAL,
                    dataset=f"research/verdicts/{path.name}",
                    message="Attestation-Hash stimmt nicht mit Payload überein (Tamper/Korruption)",
                    evidence={"file": path.name},
                )
            )
    return out


# ── Registry (Operator-Liste 07-11, Reihenfolge beibehalten) ─────────────────

REGISTRY: tuple[Invariant, ...] = (
    Invariant(
        "TL-001",
        "Mock-/Synthetic-Marktdaten in echten Fill-/Loop-Ledgern (unrefused)",
        ("trading_loop_audit.jsonl", "paper_execution_audit.jsonl"),
        Severity.CRITICAL,
        "truth",
        "active",
        _check_mock_in_fills,
    ),
    Invariant(
        "TL-002",
        "Fills im verdächtigen Mock-Preisband (~100 $)",
        ("paper_execution_audit.jsonl",),
        Severity.WARNING,
        "truth",
        "active",
        _check_mock_price_band,
    ),
    Invariant(
        "TL-003",
        "Kumulatives realized_pnl_usd als Trade-PnL konsumiert",
        ("paper_execution_audit.jsonl",),
        Severity.ERROR,
        "truth",
        "planned",
    ),
    Invariant(
        "TL-004",
        "Doppelte Ereignisse über parallele Pfade (Episoden-Inflation)",
        ("alert_outcomes.jsonl", "alert_audit.jsonl"),
        Severity.WARNING,
        "truth",
        "active",
        _check_cross_path_episode_inflation,
    ),
    Invariant(
        "TL-005",
        "Inkonsistente Episoden-/Trade-Zählung zwischen Reports",
        ("alert_outcomes.jsonl", "daily_strategy"),
        Severity.WARNING,
        "truth",
        "planned",
    ),
    Invariant(
        "TL-006",
        "Stale Snapshots als aktuell präsentiert",
        ("open_positions_risk_snapshot.json",),
        Severity.WARNING,
        "truth",
        "planned",
    ),
    Invariant(
        "TL-007",
        "Offene Position ohne aktuellen Marktpreis",
        ("paper_execution_audit.jsonl",),
        Severity.ERROR,
        "truth",
        "planned",
    ),
    Invariant(
        "TL-008",
        "Fehlende Provenance (signal_path_id) auf resolved Outcomes",
        ("alert_outcomes.jsonl",),
        Severity.WARNING,
        "truth",
        "active",
        _check_missing_provenance,
    ),
    Invariant(
        "TL-009",
        "Historische Evidenz als aktuell markiert/zitiert",
        ("daily_strategy", "reports"),
        Severity.ERROR,
        "truth",
        "planned",
    ),
    Invariant(
        "TL-010",
        "Read-only-Ergebnis mit Execution-Einfluss",
        ("trading_loop_audit.jsonl",),
        Severity.CRITICAL,
        "truth",
        "planned",
    ),
    Invariant(
        "TL-011",
        "Report-Integrität: Attestation-Rehash + ein Code-SHA pro Report",
        ("research/verdicts",),
        Severity.CRITICAL,
        "truth",
        "active",
        _check_report_integrity,
    ),
)


def run_lint(artifacts_dir: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Alle aktiven Invarianten ausführen; deterministisches Ergebnis-Dict."""
    ctx = LintContext(artifacts_dir=artifacts_dir)
    violations: list[Violation] = []
    per_invariant: list[dict[str, Any]] = []
    for inv in REGISTRY:
        if inv.status != "active" or inv.check is None:
            per_invariant.append({"id": inv.invariant_id, "status": inv.status, "violations": 0})
            continue
        found = inv.check(ctx)
        violations.extend(found)
        per_invariant.append({"id": inv.invariant_id, "status": "active", "violations": len(found)})
    max_sev = max((v.severity for v in violations), default=None)
    return {
        "schema": "truth_lint/v1",
        "ts_utc": (now or datetime.now(UTC)).isoformat(),
        "artifacts_dir": str(artifacts_dir),
        "registry_total": len(REGISTRY),
        "registry_active": sum(1 for i in REGISTRY if i.status == "active"),
        "registry_planned": sum(1 for i in REGISTRY if i.status == "planned"),
        "invariants": per_invariant,
        "violations": [v.to_dict() for v in violations],
        "max_severity": max_sev.label if max_sev else None,
    }


def write_lint_report(
    result: dict[str, Any],
    *,
    report_path: Path = DEFAULT_LINT_REPORT_PATH,
    quarantine_path: Path = DEFAULT_QUARANTINE_PATH,
) -> int:
    """Ergebnis append-only persistieren; ERROR/CRITICAL zusätzlich als
    Quarantäne-Marker (Kennzeichnung — Quelldaten bleiben unangetastet).
    Returns: Anzahl geschriebener Quarantäne-Marker."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    markers = 0
    quarantine_rows = [
        v for v in result.get("violations", []) if Severity[v["severity"]] >= Severity.ERROR
    ]
    if quarantine_rows:
        quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        with quarantine_path.open("a", encoding="utf-8") as fh:
            for v in quarantine_rows:
                fh.write(
                    json.dumps(
                        {
                            "ts_utc": result["ts_utc"],
                            "invariant_id": v["invariant_id"],
                            "severity": v["severity"],
                            "dataset": v["dataset"],
                            "message": v["message"],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
                markers += 1
    return markers


__all__ = [
    "BASELINE_MOCK_GATE_UTC",
    "BASELINE_PROVENANCE_UTC",
    "DEFAULT_LINT_REPORT_PATH",
    "DEFAULT_QUARANTINE_PATH",
    "Invariant",
    "LintContext",
    "REGISTRY",
    "Severity",
    "Violation",
    "run_lint",
    "write_lint_report",
]
