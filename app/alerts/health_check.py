"""System Health Check — flags anomalies in pipeline operation.

Checks:
- Data freshness (artifacts mtime + last-record age — catches stale-data probes)
- Alert volume anomaly (zero alerts in lookback window)
- Actionable-alert volume (P1: structural pipeline health, not just heartbeat)
- Trading loop stale (no cycles in lookback window)
- Trading loop priority_rejected saturation (P1: detects gate-induced silence)
- Trading loop open-deadlock (V5: loop spins but opens no positions)
- High error rate in trading cycles
- Precision degradation below threshold
- Outcome annotation backlog (unannotated directional alerts)

Usage:
    issues = run_health_check(artifacts_dir)
    for issue in issues:
        print(issue)
"""

from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.alerts.audit import load_alert_audits, load_outcome_annotations
from app.alerts.ingress_audit import last_accepted_ingress_event
from app.alerts.youtube_transcript_coverage import (
    COVERAGE_WINDOW_HOURS,
    TRANSCRIPT_MIN_CHARS,
    ChannelCoverage,
    classify_coverage,
    render_message,
)
from app.audit.stream_validation import AuditStreamName, load_audit_stream
from app.core.runtime_identity import (
    checkout_stable_for_s,
    drift_report,
    evaluate_runtime_drift,
    read_runtime_identity_artifact,
)
from app.orchestrator.trading_loop import load_trading_loop_cycles

_ARTIFACTS = Path("artifacts")

# Data-freshness thresholds (P0). A probe-run that reads files older than these
# is almost certainly a sync-lag false-positive (Pi is source-of-truth — see
# memory feedback_pi_branch_pointer_staleness + V4-forensik 2026-05-23).
#
# Per-file thresholds (see feedback_health_probe_design_lessons.md Lehre 1):
# - alert_audit.jsonl is event-driven: it only writes when the Telegram channel
#   dispatches. Quiet hours / weekends commonly produce 4-8h gaps in low-vol
#   phases. Use a wide window (8h) so legitimate quiet is not flagged.
# - trading_loop_audit.jsonl is timer-driven (~5min cycles). A multi-hour gap
#   indicates a real broken scheduler. Use a tight window.
_FRESHNESS_DEFAULT_MIN = 120
_FRESHNESS_PER_FILE_MIN: dict[str, int] = {
    # 1440min = 24h. Die alte 8h-Schwelle war gegen die REALE Verteilung
    # unterdimensioniert: ueber 30 Tage (n=1370 Dispatch-Luecken) liegt der
    # Median bei 1.3min, p99 bei 404min — der groesste LEGITIME Abstand aber
    # bei 883min. 8 Luecken/30d rissen die 480er-Marke, ohne dass irgendetwas
    # defekt war (ruhiger Markt: 6-12 Dispatches/24h). Ein ganzer Tag ohne
    # einen einzigen Dispatch ist dagegen ein echter Befund — und der wird
    # ohnehin praeziser vom Volumen-Check (recent_alerts) getragen.
    "alert_audit.jsonl": 1440,  # 24h — event-driven channel
    "trading_loop_audit.jsonl": 30,  # 5min cycle → 30min is 6 missed runs
    # Recalc-cycle outputs (kai-recalc-cycle.timer, daily 04:00 Pi-local).
    # Threshold 1500min = 25h covers next-day-run + RandomizedDelaySec=120s
    # + longest recalc runtime (~5min ph5_feature) + 1h grace.
    "bayes_posterior_state.json": 1500,
    "source_confluence_audit.jsonl": 1500,
    "ph5_feature_analysis.json": 1500,
    "source_reliability.json": 1500,  # lives in monitor/, not artifacts/
    # Truth-Kette (Voll-Audit 2026-08-06, Blindstelle #2): kai-truth-anchor
    # (04:35 UTC) + kai-canonical-edge-attest (06:20 UTC) schreiben mindestens
    # täglich. 2880 min = 2× legitime Stille — ein still scheiternder Anchor
    # fiel bisher NIEMANDEM auf (kein Digest-/Probe-Konsument).
    "attestation_ledger.jsonl": 2880,
    # Outcome-/Shadow-Streams (Blindstelle #6): ereignisgetrieben — genau die
    # Streams, auf denen TL-004/TL-008/TL-012 rechnen. Ein toter Writer sah
    # für den Lint wie ein sauberes System aus. 3 Tage Toleranz.
    "alert_outcomes.jsonl": 4320,
    "shadow_candidate_ledger.jsonl": 4320,
    # Asset-Rotation (Plan 08-08, PR-5): Shadow-Timer läuft täglich (24 h +
    # OnBootSec/RandomizedDelay). 1560 min = 26 h — stirbt der Writer, wird die
    # Rotation sonst wieder unsichtbar leer laufen wie vor dem Voll-Audit.
    "asset_rotation_shadow.jsonl": 1560,
    "asset_rotation_state.json": 1560,
    # Lightning-Reconciliation (PR-D/T6b, live seit 2026-08-08): der Timer
    # laeuft alle 15 min und schreibt JEDEN Lauf eine Reportzeile — auch den
    # Leerlauf ohne offene Intents. 45 min = 3 verpasste Laeufe, also klar
    # ueber der legitimen Stille (15 min + RandomizedDelaySec 2 min), aber
    # eng genug, um einen toten Timer binnen einer Stunde aufzudecken.
    "ln_reconciliation.jsonl": 45,
    # EINGANGSSTROM (Audit 09.08.). Jede andere Schwelle hier bewacht einen
    # Ausgang; diese bewacht, ob ueberhaupt noch etwas hereinkommt.
    # 720 min = 12 h: TradingView-Alerts feuern unregelmaessig, aber ein ganzer
    # halber Tag ohne einen einzigen eingehenden Webhook ist kein ruhiger Markt
    # mehr, sondern ein toter Eingang. Die reale Stille vom 02.–08.08. betrug
    # 6 TAGE und blieb unbemerkt, weil dieser Strom in keiner Liste stand.
    # Bewusst grosszuegig: lieber spaet und verlaesslich als flatternd.
    "tradingview_webhook_audit.jsonl": 720,
    # EINGANGSSTROM #2: Binance-Liquidations-Websocket. Der Stream schreibt den
    # Heartbeat alle <=15 s, und zwar AUSDRUECKLICH um "ruhiger Markt" von
    # "Feed tot" zu trennen (``binance_stream.write_heartbeat``) — nur schaute
    # bis 2026-08-18 niemand hin. 30 min = 120 verpasste Ticks: bei einem
    # Dauer-Websocket ist jede Stille jenseits weniger Minuten bereits ein
    # Verbindungsabbruch, die Schwelle deckt Reconnect-Backoff (max 60 s) und
    # einen Service-Restart bequem ab.
    "liquidation_stream_heartbeat.txt": 30,
}

# Der Dokumenten-Eingang (RSS/OKX/NewsData) schreibt in KEINE Datei, sondern
# nach ``canonical_documents``. Die Datei-Wachliste oben konnte ihn deshalb nie
# sehen — ein stillgelegter Ingest sah aus wie ein ruhiger Nachrichtentag.
# Kadenz live gemessen (Pi, 18.08., 2 Tage, 337 Fetch-Minuten): groesster
# Abstand 31 min. 240 min ist rund das Achtfache — spaet, aber nicht flatternd.
DOCUMENT_INGEST_MAX_AGE_MIN = 240

_INGRESS_COMPONENTS: frozenset[str] = frozenset(
    {"tradingview_ingress", "liquidation_ingress", "document_ingest"}
)

# Komponenten, deren Veralterung NICHTS ueber die Verlaesslichkeit der Probe
# aussagt und darum `data_sources_stale` (und damit --exit-on-stale) nicht
# ausloesen darf. Zwei Faelle, ein Prinzip:
#   * Eingangsstroeme  — die Quelle schweigt (Systembefund).
#   * Ereignisgetriebene Ausgaenge — der Kanal hat nichts zu sagen gehabt.
# `alert_audit` gehoerte bis 2026-08-18 faelschlich in die Abbruch-Kategorie.
# Folge (Pi-Journal): 66 Abbrueche in 14 Tagen mit "stale data", WAEHREND im
# selben Lauf cycles=1111..1117 standen und `trading_loop_audit` (30-min-
# Schwelle, taktgetrieben) still blieb — die Probe las beweisbar Live-Daten.
# Jeder Abbruch verschluckte den ganzen Report und loeste 5-Minuten-Watchdog-
# Spam aus: der Waechter verstummte genau dann, wenn er melden sollte.
#
# Was den Abbruch WEITERHIN ausloest, ist der taktgetriebene Beweis: schreibt
# `trading_loop_audit` (~1200 Zyklen/Tag) nicht mehr, liest die Probe wirklich
# gespiegelte/veraltete Artefakte. Das ist die Frage, die das Flag beantwortet.
_PROBE_RELIABILITY_EXEMPT: frozenset[str] = _INGRESS_COMPONENTS | frozenset({"alerts"})
_FRESHNESS_LAST_RECORD_WARN_HOURS = 4

# Wieviel vom Ende der Audit-Datei gelesen wird, um den letzten ANGENOMMENEN
# Request zu finden. Die Datei ist auf dem Pi ~2,5 MB und der Waechter laeuft
# alle 15 min -- ein Vollscan waere Verschwendung. 256 KB decken auf dem
# realen Stream mehrere hundert Records ab.
_INGRESS_TAIL_BYTES = 256 * 1024


# V5 loop-deadlock watchdog (DS-20260531-V5). The 2026-05-31 incident: the loop
# ran ~24h of cycles (trading_loop_audit fresh, so the freshness + min-cycles
# checks stayed green) while EVERY cycle was rejected at the diversification /
# sizing gate — zero orders opened, paper_execution_audit frozen for ~24h. No
# existing check fired: the priority_rejected-saturation check only looks at
# `priority_rejected` AND is disabled under RE_ENTRY_MODE. This watchdog catches
# the general "loop spins but opens nothing" failure, RE_ENTRY_MODE-independent.
#
# Discriminator against a legitimately FULL book (also 0 completed): a full book
# rejects new entries with `risk_rejected` (max_open_positions), NOT
# diversification/size. So we only fire when the OPEN-blocking gates dominate.
_OPEN_BLOCKING_STATUSES: frozenset[str] = frozenset(
    {"diversification_rejected", "size_rejected", "sizing_anomaly_rejected"}
)
# paper_execution_audit is event-driven (writes only on a fill/close), so it is
# deliberately NOT in the timer-driven freshness list. Its staleness is only
# meaningful as a SECONDARY signal alongside an active-but-unproductive loop.
_PAPER_EXECUTION_SILENCE_MIN = 180  # 3h — informative threshold for the message

# Hostname substrings that identify the Pi-side authoritative host. Override
# via env KAI_PI_HOSTNAME_MARKER for non-default deployments.
_PI_HOSTNAME_MARKERS = ("kai-pi", "kai-pi5", "pi5", "kai_pi")
_AUDIT_STREAM_SCHEMA_FILES: tuple[tuple[AuditStreamName, str], ...] = (
    ("alert_audit", "alert_audit.jsonl"),
    ("blocked_alerts", "blocked_alerts.jsonl"),
    ("paper_execution_audit", "paper_execution_audit.jsonl"),
    ("decision_journal", "decision_journal.jsonl"),
    ("bayes_confidence_audit", "bayes_confidence_audit.jsonl"),
)


@dataclass(frozen=True)
class HealthIssue:
    """A detected system health issue."""

    severity: str  # "warning" | "critical"
    component: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.component}: {self.message}"


@dataclass
class HealthReport:
    """Structured health-check output (P1: not just issues, but breakdown).

    Backwards-compatible: `run_health_check` still returns the issues list
    when callers don't need the breakdown. Use `run_health_check_report` for
    the structured view.
    """

    issues: list[HealthIssue] = field(default_factory=list)
    recent_alerts: int = 0
    recent_actionable_alerts: int = 0
    recent_cycles: int = 0
    cycle_status_breakdown: dict[str, int] = field(default_factory=dict)
    data_sources_stale: bool = False
    re_entry_mode_active: bool = False
    hostname: str = ""  # P2: lets operator see at a glance where probe ran
    runs_on_pi: bool = False  # P2: True when hostname matches Pi signature


def _check_data_freshness(adir: Path, now: datetime) -> tuple[list[HealthIssue], bool]:
    """P0 — flag stale artifact files so probe doesn't false-positive on sync lag.

    Per-file mtime thresholds (see ``_FRESHNESS_PER_FILE_MIN``): event-driven
    streams (alert_audit) get a wide window; timer-driven streams
    (trading_loop_audit) get a tight one. Returns (issues, is_stale).
    """
    issues: list[HealthIssue] = []
    stale = False
    # (path, fname, component, required). source_reliability.json sits in
    # monitor/, the others in artifacts/. Recalc-cycle outputs are flagged
    # required=False so a fresh-checkout (no recalc-run yet) does not trip the
    # probe; once they exist they are subject to the 1500min staleness
    # threshold, which catches a silent kai-recalc-cycle.timer (e.g. the
    # 2026-05-16..24 8-day stall that motivated this patch).
    monitor_dir = adir.parent / "monitor"
    files_to_check = [
        (adir / "alert_audit.jsonl", "alert_audit.jsonl", "alerts", True),
        (adir / "trading_loop_audit.jsonl", "trading_loop_audit.jsonl", "trading_loop", True),
        (adir / "bayes_posterior_state.json", "bayes_posterior_state.json", "bayes_recalc", False),
        (
            adir / "source_confluence_audit.jsonl",
            "source_confluence_audit.jsonl",
            "confluence_recalc",
            False,
        ),
        (adir / "ph5_feature_analysis.json", "ph5_feature_analysis.json", "ph5_recalc", False),
        (
            monitor_dir / "source_reliability.json",
            "source_reliability.json",
            "source_reliability_recalc",
            False,
        ),
        # Truth-Observability (Voll-Audit 2026-08-06, WP7): required=False —
        # Staleness greift erst, wenn die Datei existiert (fresh checkout
        # stolpert nicht); auf dem Pi existieren alle drei.
        (
            adir / "truth" / "attestation_ledger.jsonl",
            "attestation_ledger.jsonl",
            "truth_anchor",
            False,
        ),
        (adir / "alert_outcomes.jsonl", "alert_outcomes.jsonl", "outcome_writer", False),
        (
            adir / "shadow_candidate_ledger.jsonl",
            "shadow_candidate_ledger.jsonl",
            "shadow_writer",
            False,
        ),
        # Asset-Rotation (Plan 08-08, PR-5): required=False — fresh checkout
        # stolpert nicht; auf dem Pi existieren beide seit G1.
        (
            adir / "asset_rotation_shadow.jsonl",
            "asset_rotation_shadow.jsonl",
            "asset_rotation",
            False,
        ),
        (
            adir / "asset_rotation_state.json",
            "asset_rotation_state.json",
            "asset_rotation_state",
            False,
        ),
        # Geldpfad-Integritaet (PR-D/T6b): der Reconcile-Timer war der erste
        # neue Timer nach der TV-Ingest-Lehre vom 2026-08-08 — ein Ausgang
        # OHNE Waechter faellt sechs Tage lang niemandem auf. required=False,
        # weil ein frischer Checkout die Datei legitim noch nicht hat.
        (
            adir / "lightning" / "ln_reconciliation.jsonl",
            "ln_reconciliation.jsonl",
            "ln_reconcile",
            False,
        ),
        # EINGANGSSTROM, kein Ausgang (Audit 09.08.). Bis hier wachte diese
        # Liste ausschliesslich ueber Ergebnisse — und ein gesunder Ausgang
        # beweist keinen lebenden Eingang: der TV-Webhook war vom 02.08. bis
        # 08.08. tot, waehrend der Promotion-Timer 1728-mal gruen lief, weil
        # "0 offene Ereignisse" als Erfolg zaehlt. Diese Datei ist der einzige
        # Beleg dafuer, dass ueberhaupt noch etwas HEREINKOMMT.
        # required=False: ein frischer Checkout hat sie legitim nicht.
        (
            adir / "tradingview_webhook_audit.jsonl",
            "tradingview_webhook_audit.jsonl",
            "tradingview_ingress",
            False,
        ),
        # EINGANGSSTROM #2 (2026-08-18): Binance-Liquidations-Websocket.
        # required=False, weil ein frischer Checkout ihn legitim noch nicht hat;
        # auf dem Pi laeuft kai-liquidation-stream.service dauerhaft.
        (
            adir / "liquidation_stream_heartbeat.txt",
            "liquidation_stream_heartbeat.txt",
            "liquidation_ingress",
            False,
        ),
    ]
    # Prä-Reg-Ledger (Blindstelle #5): NUR Existenz, keine mtime-Schwelle —
    # Prä-Regs dürfen Wochen legitim ruhen (Stille ≠ Defekt), aber ein
    # VERSCHWUNDENES Ledger ist ein Wahrheitsverlust. Armiert nur, wenn
    # artifacts/research/ existiert (Pi-Realität; Tests/fresh checkout nicht).
    prereg_path = adir / "research" / "prereg_ledger.jsonl"
    if (adir / "research").is_dir() and not prereg_path.exists():
        issues.append(
            HealthIssue(
                severity="critical",
                component="prereg_ledger_presence",
                message=f"prereg_ledger.jsonl does not exist at {prereg_path}",
            )
        )
        stale = True
    for path, fname, component, required in files_to_check:
        if not path.exists():
            if not required:
                continue
            issues.append(
                HealthIssue(
                    severity="critical",
                    component=f"{component}_freshness",
                    message=f"{fname} does not exist at {path}",
                )
            )
            stale = True
            continue
        threshold_min = _FRESHNESS_PER_FILE_MIN.get(fname, _FRESHNESS_DEFAULT_MIN)
        mtime_cutoff = now - timedelta(minutes=threshold_min)
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if component == "tradingview_ingress":
            # Nicht die Datei-mtime, sondern der letzte ANGENOMMENE Request:
            # eine Abweisung schreibt ebenfalls in dieses Audit und wuerde den
            # Waechter sonst von aussen beruhigen (belegt am 2026-08-18 durch
            # drei eigene Diagnose-Requests). Ohne angenommenes Event im
            # gelesenen Ende gilt der Strom als nicht liefernd -- konservativ,
            # und genau der Fall, den die Wache abdecken soll.
            accepted_at = last_accepted_ingress_event(path)
            mtime = accepted_at if accepted_at is not None else datetime.fromtimestamp(0, tz=UTC)
        if mtime < mtime_cutoff:
            age_min = int((now - mtime).total_seconds() / 60)
            is_ingress = component in _INGRESS_COMPONENTS
            if is_ingress:
                hint = (
                    "kein eingehender Verkehr — Quelle pruefen (z. B. "
                    "abgelaufene TradingView-Alerts), NICHT die "
                    "Pi-Synchronisation"
                )
            elif component in _PROBE_RELIABILITY_EXEMPT:
                # Ereignisgetriebener Ausgang: Stille ist eine Aussage ueber
                # den Kanal, nicht ueber die Probe. Den Operator hier auf
                # Sync-Suche zu schicken, ist eine Fehldiagnose am gesunden
                # Teil des Systems.
                hint = (
                    "keine Dispatches in diesem Fenster — ruhiger Kanal oder "
                    "toter Dispatcher, NICHT die Pi-Synchronisation"
                )
            else:
                hint = "probe may be running against stale data, check Pi sync"
            issues.append(
                HealthIssue(
                    severity="warning",
                    component=f"{component}_freshness",
                    message=(
                        f"{fname} mtime is {age_min}min old "
                        f"(threshold: {threshold_min}min) — {hint}"
                    ),
                )
            )
            # Eingangsstroeme setzen `stale` NICHT: das Flag sagt aus, dass die
            # PROBE unzuverlaessig ist (gespiegelte Workstation-Artefakte), und
            # steuert ueber --exit-on-stale den Abbruch. Ein toter Eingang auf
            # dem Pi ist dagegen ein echter SYSTEMBEFUND bei voll verlaesslicher
            # Probe. Ohne diese Trennung waere die Unit dauerhaft `failed`,
            # solange die Quelle schweigt — mit der irrefuehrenden Begruendung
            # "check Pi sync" (beobachtet 09.08., TV-Ingress 10146 min alt).
            if component not in _PROBE_RELIABILITY_EXEMPT:
                stale = True
    return issues, stale


def _check_audit_stream_schemas(adir: Path) -> list[HealthIssue]:
    issues: list[HealthIssue] = []
    for stream, filename in _AUDIT_STREAM_SCHEMA_FILES:
        result = load_audit_stream(adir / filename, stream)
        if not result.issues:
            continue
        first = result.issues[0]
        issues.append(
            HealthIssue(
                severity="warning",
                component=f"{stream}_schema",
                message=(
                    f"{result.issue_count} invalid row(s) in {filename}; "
                    f"first at line {first.line_number}: {first.message.splitlines()[0]}"
                ),
            )
        )
    return issues


def _paper_execution_silence_hint(adir: Path, now: datetime) -> str:
    """Append-able hint about paper_execution_audit staleness (V5 secondary signal).

    Returns ``""`` when the file is missing or fresh, otherwise a short
    `; paper_execution_audit silent for Nh` suffix. Purely informative — the
    deadlock trigger itself is the completed==0 + open-blocking-ratio condition.
    """
    path = adir / "paper_execution_audit.jsonl"
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except (OSError, ValueError):
        return ""
    age_min = (now - mtime).total_seconds() / 60
    if age_min < _PAPER_EXECUTION_SILENCE_MIN:
        return ""
    return f"; paper_execution_audit silent for {age_min / 60:.1f}h"


def _re_entry_mode_active() -> bool:
    """P1 — respect RE_ENTRY_MODE env-flag so probe relaxes during gated window.

    Accepts two key variants because the codebase has both in circulation:
    - ``RE_ENTRY_MODE`` (legacy, accepts "active"/"true"/"1")
    - ``RE_ENTRY_MODE_ENABLED`` (current Pi `.env` form, boolean-like)
    """
    truthy = {"1", "true", "active", "yes", "on"}
    for key in ("RE_ENTRY_MODE", "RE_ENTRY_MODE_ENABLED"):
        if os.environ.get(key, "").strip().lower() in truthy:
            return True
    return False


def _detect_hostname() -> tuple[str, bool]:
    """P2 — detect if probe runs on the Pi or somewhere else (workstation, CI)."""
    try:
        host = socket.gethostname() or ""
    except OSError:
        host = ""
    override = os.environ.get("KAI_PI_HOSTNAME_MARKER", "").strip().lower()
    markers = (override,) if override else _PI_HOSTNAME_MARKERS
    host_lower = host.lower()
    runs_on_pi = any(m and m in host_lower for m in markers)
    return host, runs_on_pi


def run_health_check(
    artifacts_dir: Path | None = None,
    lookback_hours: int = 24,
    min_expected_alerts: int = 1,
    min_expected_cycles: int = 10,
    min_precision_pct: float = 15.0,
) -> list[HealthIssue]:
    """Run all health checks and return list of issues (empty = healthy).

    Backwards-compatible wrapper around `run_health_check_report`.
    """
    return run_health_check_report(
        artifacts_dir=artifacts_dir,
        lookback_hours=lookback_hours,
        min_expected_alerts=min_expected_alerts,
        min_expected_cycles=min_expected_cycles,
        min_precision_pct=min_precision_pct,
    ).issues


# Erwarteter passwortfreier Pfad. Die Validierung liegt im Broker, nicht in
# sudoers — siehe deploy/sudoers.d/kai-deploy.
_EXPECTED_NOPASSWD_CMD = "/usr/local/sbin/kai-service-control"


def _check_sudo_policy(*, runs_on_pi: bool) -> list[HealthIssue]:
    """Die LIVE passwortfreie sudo-Policy gegen die Erwartung des Repos halten.

    Ohne diese Probe meldet niemand, wenn ``/etc/sudoers.d`` und
    ``deploy/sudoers.d`` auseinanderlaufen — dieselbe Klasse wie die Unit-Drift
    aus #717, nur mit hoeherem Einsatz.

    Der scharfe Teil ist die Wildcard-Pruefung. Ein Argument-Glob wie
    ``systemctl restart kai-*`` sieht eng aus, ist es aber nicht: sudoers matcht
    Argumente als EINEN String, und ``*`` matcht auch Leerzeichen. Live
    verifiziert (2026-08-19): ``systemctl restart kai-x.service zzz.service``
    wurde autorisiert. Jede NOPASSWD-Regel mit ``*`` in den Argumenten ist
    deshalb ein Befund, kein Schoenheitsfehler.

    Fail-soft: laesst sich die Policy nicht lesen, gibt es KEINEN Befund und
    keinen Abbruch (Lehre #718 — die Probe ist kein Abbruchgrund).
    """
    if not runs_on_pi:
        return []
    # Ausschliesslich fuer Testumgebungen: die Probe ruft einen externen Prozess,
    # und die autouse-Fixture in tests/conftest.py laesst `runs_on_pi` ueberall
    # wahr werden. In Produktion ist die Variable nicht gesetzt -> Probe aktiv.
    if os.environ.get("KAI_SUDO_POLICY_PROBE", "").strip().lower() == "off":
        return []
    try:
        proc = subprocess.run(  # noqa: S603 - feste Argumentliste, kein shell
            ["sudo", "-n", "-l"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []

    nopasswd_lines = [line.strip() for line in proc.stdout.splitlines() if "NOPASSWD:" in line]
    issues: list[HealthIssue] = []
    if not nopasswd_lines:
        return issues

    for line in nopasswd_lines:
        _, _, cmds = line.partition("NOPASSWD:")
        cmds = cmds.strip()
        if "*" in cmds:
            issues.append(
                HealthIssue(
                    severity="critical",
                    component="sudo_policy",
                    message=(
                        "passwortfreie sudo-Regel enthaelt ein Argument-Wildcard und ist "
                        f"damit umgehbar (sudoers matcht Argumente als EINEN String): {cmds}"
                    ),
                )
            )
        elif _EXPECTED_NOPASSWD_CMD not in cmds:
            issues.append(
                HealthIssue(
                    severity="warning",
                    component="sudo_policy",
                    message=(
                        f"unerwartete passwortfreie sudo-Regel (erwartet nur "
                        f"{_EXPECTED_NOPASSWD_CMD}): {cmds}"
                    ),
                )
            )
    return issues


def _check_privilege_broker(*, runs_on_pi: bool) -> list[HealthIssue]:
    """Existiert das Ziel der NOPASSWD-Policy, gehoert es root und ist es unveraendert?

    Am 2026-08-20 verwies die Policy auf einen Broker, den niemand installiert
    hatte. Der Fehler blieb unbemerkt, weil er sich erst zeigt, wenn Recovery
    GEBRAUCHT wird — bis dahin sieht ein toter Privilegienpfad aus wie ein
    ruhiges System.

    Fail-soft: laesst sich der Zustand nicht ermitteln, gibt es keinen Befund.
    """
    if not runs_on_pi:
        return []
    if os.environ.get("KAI_BROKER_PROBE", "").strip().lower() == "off":
        return []

    from app.services.timer_health import BrokerState, evaluate_privilege_broker

    repo_artifact = Path(__file__).resolve().parents[2] / "deploy" / "bin" / "kai-service-control"
    target = Path("/usr/local/sbin/kai-service-control")
    if not target.exists():
        state = BrokerState(path=str(target), exists=False)
    else:
        # `stat -c` statt pwd/grp: dieselbe Konvention wie die Nachbedingung im
        # Installer, und ohne POSIX-only Importe, die den Type-Check auf einer
        # Nicht-Linux-Workstation brechen.
        try:
            proc = subprocess.run(  # noqa: S603 - feste Argumentliste, kein shell
                ["stat", "-c", "%U:%G:%a", str(target)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        parts = proc.stdout.strip().split(":") if proc.returncode == 0 else []
        if len(parts) != 3:
            return []
        try:
            same = repo_artifact.is_file() and repo_artifact.read_bytes() == target.read_bytes()
        except OSError:
            return []
        state = BrokerState(
            path=str(target),
            exists=True,
            owner=parts[0],
            group=parts[1],
            mode=parts[2],
            matches_repo_artifact=same,
        )

    finding = evaluate_privilege_broker(state)
    if finding is None:
        return []
    return [HealthIssue(severity="critical", component="privilege_broker", message=finding)]


def _check_runtime_identity(adir: Path, now: datetime, *, runs_on_pi: bool) -> list[HealthIssue]:
    """Laeuft der Server auf dem Code, der im Checkout liegt? (STAB-02)

    25.08.2026: kai-server lief seit 7 Tagen 23 Commits hinter seinem eigenen
    Checkout — vier Fast-Forwards ohne Restart. Kein Waechter sah es, weil
    /health nur 'ok' kannte. Monitoring-Lehre 18.08.: ein gesunder Ausgang
    beweist keinen aktuellen Code — der Abstand selbst ist der Befund.

    Quelle ist das Artefakt, das der Server beim Start schreibt. Fehlt es auf
    der Pi, ist das ein Befund (Server vor STAB-02 oder nie gestartet); auf
    einer Workstation ohne Server bleibt der Check still.
    """
    # Gleiche Klasse wie Sudo-/Broker-Probe: tests/conftest.py setzt runs_on_pi
    # in JEDEM Test auf wahr; ohne Kill-Switch meldete das fehlende Artefakt in
    # fremden Fixtures (test_daily_briefing, test_notify) einen Befund.
    if os.environ.get("KAI_RUNTIME_IDENTITY_PROBE", "").strip().lower() == "off":
        return []
    artifact = read_runtime_identity_artifact(adir / "runtime" / "runtime_identity.json")
    if artifact is None:
        if not runs_on_pi:
            return []
        return [
            HealthIssue(
                severity="warning",
                component="runtime_identity",
                message=(
                    "kein runtime_identity-Artefakt — der laufende kai-server ist entweder "
                    "aelter als STAB-02 oder nie gestartet; welcher Code laeuft, ist unbelegt."
                ),
            )
        ]
    repo_dir = adir.resolve().parent
    report = drift_report(artifact, repo_dir, now=now)
    stable = checkout_stable_for_s(repo_dir, now=now)
    return [
        HealthIssue(severity=f.severity, component="runtime_identity", message=f.message)
        for f in evaluate_runtime_drift(report, checkout_stable_for_s=stable)
    ]


def _check_timer_scheduleability(*, runs_on_pi: bool) -> list[HealthIssue]:
    """Wiederkehrende Timer, die laufen und trotzdem keinen Termin haben.

    Vorfall 2026-08-19: ``kai-tv-auto-promote.timer`` stand auf ``enabled`` +
    ``active`` mit ``NextElapseUSecMonotonic=infinity`` und hatte zuletzt am
    2026-07-12 gefeuert — fuenf Wochen tot. Er fiel durch BEIDE bestehenden
    Netze: ``systemctl --failed`` zeigt nichts (nichts ist gescheitert), und
    ``pi_timer_health_probe.sh`` sammelt ``NON_ACTIVE`` (er war aktiv).

    Die Deutung liegt in reinen, getesteten Funktionen
    (``app/services/timer_health``); hier steht nur das Einsammeln.

    Fail-soft: laesst sich systemd nicht befragen, gibt es KEINEN Befund — die
    Probe ist kein Abbruchgrund (Lehre #718).
    """
    if not runs_on_pi:
        return []
    if os.environ.get("KAI_TIMER_SCHEDULE_PROBE", "").strip().lower() == "off":
        return []

    from app.services.timer_health import (
        find_unscheduled_recurring_timers,
        parse_active_units,
        parse_systemctl_show,
    )

    timer_dir = Path(__file__).resolve().parents[2] / "deploy" / "systemd"
    units = sorted(f.name for f in timer_dir.glob("kai-*.timer"))
    if not units:
        return []
    try:
        proc = subprocess.run(  # noqa: S603 - feste Argumentliste, kein shell
            [
                "systemctl",
                "show",
                *units,
                "-p",
                "Id",
                "-p",
                "UnitFileState",
                "-p",
                "ActiveState",
                "-p",
                "NextElapseUSecRealtime",
                "-p",
                "NextElapseUSecMonotonic",
                "-p",
                "LastTriggerUSec",
                "-p",
                "Unit",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            # systemctl rendert Zeitstempel in der Zone des Aufrufers; ``CEST``
            # ist nicht zurueckparsbar und wuerde jeden LastTrigger als "nie
            # gelaufen" erscheinen lassen.
            env={**os.environ, "TZ": "UTC"},
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        return []

    facts = parse_systemctl_show(proc.stdout)

    # Zweite Frage: laeuft der ausgeloeste Service gerade? Waehrend ein
    # ``Type=oneshot`` laeuft, hat ``OnUnitActiveSec`` nichts zum Ankern und
    # systemd meldet ``infinity`` — ohne diese Runde wuerde jeder laufende
    # Timer als tot gemeldet (kai-shadow-resolver: 13-14 min von je 30).
    # Fail-soft wie oben: laesst sich das nicht klaeren, gibt es KEINEN Befund
    # statt eines geratenen.
    services = sorted({f.triggered_unit for f in facts if f.triggered_unit})
    if not services:
        return []
    try:
        svc_proc = subprocess.run(  # noqa: S603 - feste Argumentliste, kein shell
            ["systemctl", "show", *services, "-p", "Id", "-p", "ActiveState"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={**os.environ, "TZ": "UTC"},
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if svc_proc.returncode != 0 or not svc_proc.stdout.strip():
        return []
    running = parse_active_units(svc_proc.stdout)
    facts = [f.with_triggered_state(running) for f in facts]

    stuck = find_unscheduled_recurring_timers(facts)
    if not stuck:
        return []
    return [
        HealthIssue(
            severity="critical",
            component="timer_scheduleability",
            message=(
                f"{len(stuck)} wiederkehrende Timer laufen ohne naechsten Termin "
                f"(enabled+active, aber kein NextElapse): {', '.join(sorted(stuck))} "
                "— sie feuern nie wieder. Reparatur: Unit neu starten, nachdem der "
                "zugehoerige Service einmal gelaufen ist, und auf einen "
                "restart-sicheren Trigger umstellen (OnCalendar oder OnActiveSec)."
            ),
        )
    ]


def _check_rejected_closes(adir: Path, now: datetime, *, lookback_hours: int) -> list[HealthIssue]:
    """Abgewiesene Phantom-Closes sichtbar machen.

    ``close_price_sanity_rejected`` wird seit DS-20260529-V1 geschrieben und
    hatte bis 2026-08-18 **keinen einzigen** operativen Konsumenten — das
    Ereignis lag im Stream und niemand schaute hin.

    Es ist doppeldeutig, und beide Lesarten brauchen Augen: entweder liefert
    der Preis-Feed Muell (der Fall, fuer den der Breaker gebaut ist), oder der
    Breaker liegt falsch und eine echte Position kommt nicht mehr zu — sie
    bleibt offen und wird bei jedem Tick erneut abgewiesen. Seit die Schwelle
    von 200 % auf 20 % gesenkt wurde, ist die zweite Lesart nicht mehr
    theoretisch.

    Gelesen wird ueber den Port (#716), nicht mit einem eigenen ``open()``.
    """
    from app.execution.paper_audit_stream import iter_audit_events

    path = adir / "paper_execution_audit.jsonl"
    if not path.exists():
        return []
    cutoff = now - timedelta(hours=max(1, lookback_hours))
    recent: list[dict[str, Any]] = []
    for event in iter_audit_events(path):
        if event.get("event_type") != "close_price_sanity_rejected":
            continue
        raw_ts = event.get("timestamp_utc")
        try:
            stamp = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
        except ValueError:
            # Fail-closed: eine unlesbare Zeitangabe darf einen Befund nicht
            # verschwinden lassen.
            recent.append(event)
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        if stamp >= cutoff:
            recent.append(event)
    if not recent:
        return []

    worst = max(recent, key=lambda e: abs(float(e.get("implied_return_pct") or 0.0)))
    symbols = sorted({str(e.get("symbol") or "?") for e in recent})
    return [
        HealthIssue(
            severity="warning",
            component="close_price_sanity",
            message=(
                f"{len(recent)} Close(s) in {lookback_hours}h als Phantom abgewiesen "
                f"({', '.join(symbols)}); groesste implizite Rendite "
                f"{float(worst.get('implied_return_pct') or 0.0):.1f}% "
                f"bei Kappe {float(worst.get('max_close_return_pct') or 0.0):.1f}% — "
                "entweder liefert der Preis-Feed Muell ODER eine echte Position "
                "kommt nicht mehr zu und bleibt offen"
            ),
        )
    ]


def _sqlite_path_or_none(db_url: str) -> Path | None:
    """Der Dateipfad hinter einer SQLite-URL, sonst ``None``.

    Bewusst geteilt von allen DB-Sonden hier: waere die Ableitung zweimal
    geschrieben, wuerde eine Haertung nur eine Kopie erreichen und die andere
    stillschweigend zurueckbleiben.
    """
    prefix_pos = db_url.find(":///")
    if "sqlite" not in db_url.split("://", 1)[0] or prefix_pos == -1:
        return None
    db_path = Path(db_url[prefix_pos + 4 :].split("?", 1)[0])
    return db_path if db_path.exists() else None


def _check_document_ingest(db_url: str, now: datetime) -> list[HealthIssue]:
    """EINGANGSSTROM #3: schreibt ueberhaupt noch jemand nach ``canonical_documents``?

    RSS, OKX-Announcements und NewsData landen nicht in einer Datei, sondern in
    der Tabelle — fuer die mtime-basierte Wachliste existierten sie nicht. Ein
    toter Ingest war von einem ruhigen Nachrichtentag nicht zu unterscheiden.

    Bewusst read-only und ohne Engine: ein ``sqlite3``-Zugriff im
    ``mode=ro``-URI kostet nichts und kann den laufenden Schreiber nicht
    stoeren. Nicht-SQLite-Deployments werden NICHT geraten — die Sonde
    schweigt dort, statt etwas zu behaupten (dokumentierte Abdeckungsgrenze,
    kein stiller Ausfall: auf dem Pi ist ``DB_URL`` sqlite).
    """
    db_path = _sqlite_path_or_none(db_url)
    if db_path is None:
        # Nicht-SQLite oder frischer Checkout ohne DB: kein Systembefund.
        return []

    def _issue(message: str) -> list[HealthIssue]:
        return [HealthIssue(severity="warning", component="document_ingest", message=message)]

    try:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT MAX(fetched_at) FROM canonical_documents").fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return _issue(
            f"canonical_documents nicht lesbar ({db_path}): {exc} — Dokumenten-Eingang unbelegbar"
        )

    newest_raw = row[0] if row else None
    if not newest_raw:
        return _issue(
            "canonical_documents ist leer — kein einziges Dokument eingegangen; "
            "Quelle pruefen (RSS/OKX/NewsData), NICHT die Pi-Synchronisation"
        )
    try:
        newest = datetime.fromisoformat(str(newest_raw).replace("Z", "+00:00"))
    except ValueError:
        return _issue(f"canonical_documents.fetched_at unlesbar: {newest_raw!r}")
    if newest.tzinfo is None:
        # Der Writer stempelt naiv in UTC (SQLite-Textspalte).
        newest = newest.replace(tzinfo=UTC)
    age_min = int((now - newest).total_seconds() // 60)
    if age_min <= DOCUMENT_INGEST_MAX_AGE_MIN:
        return []
    return _issue(
        f"juengstes Dokument ist {age_min}min alt "
        f"(Schwelle {DOCUMENT_INGEST_MAX_AGE_MIN}min) — kein eingehender Verkehr, "
        "Quelle pruefen (RSS/OKX/NewsData), NICHT die Pi-Synchronisation"
    )


# Zwei VOLLSTAENDIGE Abfragen statt einer zusammengesetzten: SQL per f-String
# aufzubauen ist auch dann ein schlechtes Muster, wenn die Bausteine konstant
# sind — der Static-Scan (bandit B608) hat recht, und ein Literal kostet nichts.
#
# Vorrangig das explizite Herkunfts-Feld ``youtube_meta.text_source``, das der
# Adapter seit der Kosten-Leiter schreibt. Die Laengen-Heuristik bleibt NUR fuer
# Altzeilen ohne dieses Feld: Beschreibungen aus dem Feed sind ~1400 statt 143
# Zeichen lang und von einem Transkript nicht mehr an der Laenge zu unterscheiden.
_YT_COVERAGE_SQL_BY_SOURCE = (
    "SELECT coalesce(nullif(author, ''), '(ohne Kanal)'), COUNT(*), "
    "SUM(CASE "
    "WHEN json_extract(youtube_meta, '$.text_source') = 'transcript' THEN 1 "
    "WHEN json_extract(youtube_meta, '$.text_source') IS NULL "
    "AND length(coalesce(raw_text, '')) >= ? THEN 1 "
    "ELSE 0 END) "
    "FROM canonical_documents "
    "WHERE source_type = ? AND fetched_at >= ? "
    "GROUP BY 1"
)

#: Rueckfall fuer SQLite-Builds ohne JSON1 — schwaechere Messung statt gar keiner.
_YT_COVERAGE_SQL_BY_LENGTH = (
    "SELECT coalesce(nullif(author, ''), '(ohne Kanal)'), COUNT(*), "
    "SUM(CASE WHEN length(coalesce(raw_text, '')) >= ? THEN 1 ELSE 0 END) "
    "FROM canonical_documents "
    "WHERE source_type = ? AND fetched_at >= ? "
    "GROUP BY 1"
)


def _check_youtube_transcript_coverage(db_url: str, now: datetime) -> list[HealthIssue]:
    """EINGANGSSTROM #4: kommen YouTube-Videos MIT Inhalt an, oder nur mit Titel?

    ``_check_document_ingest`` fragt, ob ueberhaupt jemand schreibt. Diese Sonde
    fragt das Naechste, was vier Monate lang niemand gefragt hat: ob in dem, was
    geschrieben wird, auch etwas drinsteht. Der Transkript-Abruf war seit einem
    Bibliotheks-Upgrade tot, fing seinen eigenen Fehler ab und lieferte ``None``;
    die Pipeline schrieb daraufhin die Video-Beschreibung. Ankunft gruen, Inhalt
    leer, kein Log, kein Alarm.

    Read-only ueber ``mode=ro`` wie die Nachbarsonde; Nicht-SQLite wird nicht
    geraten, sondern geschwiegen (dokumentierte Abdeckungsgrenze).
    """
    db_path = _sqlite_path_or_none(db_url)
    if db_path is None:
        return []

    window_start = (
        (now.astimezone(UTC) - timedelta(hours=COVERAGE_WINDOW_HOURS))
        .replace(tzinfo=None)
        .isoformat(sep=" ")
    )

    def _query(sql: str) -> list[Any]:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            return con.execute(
                sql, (TRANSCRIPT_MIN_CHARS, "youtube_channel", window_start)
            ).fetchall()
        finally:
            con.close()

    try:
        try:
            rows = _query(_YT_COVERAGE_SQL_BY_SOURCE)
        except sqlite3.OperationalError:
            # SQLite ohne JSON1 (aeltere Builds): lieber die schwaechere Messung
            # als gar keine — und sichtbar hier dokumentiert, nicht still.
            rows = _query(_YT_COVERAGE_SQL_BY_LENGTH)
    except sqlite3.Error as exc:
        return [
            HealthIssue(
                severity="warning",
                component="youtube_transcript_coverage",
                message=f"canonical_documents nicht lesbar ({db_path}): {exc} — "
                "Transkript-Abdeckung unbelegbar",
            )
        ]

    verdict = classify_coverage(
        [ChannelCoverage(str(name), int(total), int(hits or 0)) for name, total, hits in rows]
    )
    if verdict.is_healthy:
        return []
    return [
        HealthIssue(
            severity="warning",
            component="youtube_transcript_coverage",
            message=render_message(verdict, window_hours=COVERAGE_WINDOW_HOURS),
        )
    ]


def _check_prereg_reconciliation(adir: Path, *, specs: Any = None) -> list[HealthIssue]:
    """Ledger ↔ Aufsicht: jeder versiegelte Claim ist entschieden, beobachtet oder Befund.

    Befund 2026-08-26: 19 versiegelte Claims, 14 im Reifeblick, einer davon mit
    einem Off-Chain-Verdikt als "kein Verdikt" gefuehrt. Drei Wachlisten am
    selben Tag stimmten nicht mit ihrer Quelle ueberein
    (``feedback_watchlists_must_reconcile_against_source``) — deshalb prueft
    dieser Befund gegen das Ledger, nicht gegen die Wachliste.

    Zerlegung nach Zustand ist Pflicht: eine Summe "7 offen" sagt nicht, ob
    attestiert oder ausgewertet werden muss. Nur die Existenz des Ledgers wacht
    ``prereg_ledger_presence`` — hier kein Doppelbefund.
    """
    from app.research.prereg_maturity import MATURITY_SPECS
    from app.research.prereg_reconciliation import (
        DEFAULT_SUPERVISION_REGISTER,
        RECON_STATE_RESOLVED,
        RECON_STATE_SUPERVISED,
        RECON_STATE_UNWATCHED,
        RECON_STATE_VERDICT_UNATTESTED,
        RECON_STATE_WATCHED,
        classify_ledger_entries,
        load_supervision_register,
    )

    if not (adir / "research" / "prereg_ledger.jsonl").exists():
        return []
    active_specs = MATURITY_SPECS if specs is None else specs
    rows = classify_ledger_entries(adir, specs=active_specs)
    issues: list[HealthIssue] = []

    errors = [r["resolution_error"] for r in rows if r.get("resolution_error")]
    if errors:
        status = str((errors[0] or {}).get("status") or "unknown")
        issues.append(
            HealthIssue(
                severity="critical",
                component="prereg_reconciliation",
                message=(
                    f"truth ledger unusable ({status}); no claim can be RESOLVED "
                    f"until the chain verifies — ledger={len(rows)}"
                ),
            )
        )
        return issues

    sealed = {r["prereg_id"] for r in rows}
    ghost_specs = sorted(
        str(s.get("prereg_id"))
        for s in active_specs
        if isinstance(s.get("prereg_id"), str) and s.get("prereg_id") not in sealed
    )
    if ghost_specs:
        issues.append(
            HealthIssue(
                severity="critical",
                component="prereg_reconciliation",
                message=(
                    "watchlist drift: MATURITY_SPECS references prereg_id(s) not in the "
                    f"sealed ledger: {', '.join(ghost_specs)}"
                ),
            )
        )

    counts = {
        state: sum(1 for r in rows if r["state"] == state)
        for state in (
            RECON_STATE_RESOLVED,
            RECON_STATE_WATCHED,
            RECON_STATE_SUPERVISED,
            RECON_STATE_VERDICT_UNATTESTED,
            RECON_STATE_UNWATCHED,
        )
    }
    # Spiegelbild zu ``ghost_specs``: auch das Aufsichtsregister kann auf eine
    # nie versiegelte ID zeigen. Ein solcher Eintrag sieht wie Aufsicht aus,
    # beaufsichtigt aber nichts — er gehoert gemeldet, nicht geglaubt.
    ghost_supervision = sorted(
        pid for pid in load_supervision_register(DEFAULT_SUPERVISION_REGISTER) if pid not in sealed
    )
    if ghost_supervision:
        issues.append(
            HealthIssue(
                severity="critical",
                component="prereg_reconciliation",
                message=(
                    "supervision drift: prereg_supervision.json references prereg_id(s) "
                    f"not in the sealed ledger: {', '.join(ghost_supervision)}"
                ),
            )
        )
    unattested = [r["prereg_id"] for r in rows if r["state"] == RECON_STATE_VERDICT_UNATTESTED]
    unwatched = [r["prereg_id"] for r in rows if r["state"] == RECON_STATE_UNWATCHED]
    # Beaufsichtigt UND faellig ist eine offene Entscheidung des Eigentuemers,
    # keine Aufsichtsluecke. Beaufsichtigt und noch nicht faellig ist gar kein
    # Befund — sonst meldete der Waechter einen Termin taeglich vor, den der
    # Operator bewusst in die Zukunft gelegt hat.
    supervised_due = [
        r["prereg_id"]
        for r in rows
        if r["state"] == RECON_STATE_SUPERVISED and (r.get("supervision") or {}).get("due")
    ]
    if unattested or unwatched or supervised_due:
        breakdown = " ".join(f"{k}={v}" for k, v in counts.items())
        parts = [f"ledger={len(rows)} {breakdown}"]
        if unattested:
            parts.append("attestieren (Verdikt nur in Seitenablage): " + ", ".join(unattested))
        if unwatched:
            parts.append("Aufsichtsluecke (weder Spec noch Verdikt): " + ", ".join(unwatched))
        if supervised_due:
            parts.append(
                "Aufsichtstermin faellig (Operator-Register, KEINE Luecke): "
                + ", ".join(supervised_due)
            )
        issues.append(
            HealthIssue(
                severity="warning",
                component="prereg_reconciliation",
                message="; ".join(parts),
            )
        )
    return issues


def run_health_check_report(
    artifacts_dir: Path | None = None,
    lookback_hours: int = 24,
    min_expected_alerts: int = 1,
    min_expected_cycles: int = 10,
    min_precision_pct: float = 15.0,
    min_expected_actionable: int = 0,
    max_priority_rejected_ratio: float = 0.95,
    max_open_blocking_ratio: float = 0.5,
    now: datetime | None = None,
) -> HealthReport:
    """Run all health checks and return a structured report (P0+P1+V5).

    Adds data-freshness check (P0), actionable + priority_rejected_ratio
    checks (P1), and the loop open-deadlock watchdog (V5). Respects
    RE_ENTRY_MODE env-flag to relax thresholds — except V5, which fires
    regardless because a self-inflicted open-deadlock is never intended.
    """
    adir = artifacts_dir or _ARTIFACTS
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=lookback_hours)
    report = HealthReport()
    report.re_entry_mode_active = _re_entry_mode_active()
    report.hostname, report.runs_on_pi = _detect_hostname()

    # ── P0: data freshness ───────────────────────────────────────────
    freshness_issues, stale = _check_data_freshness(adir, now)
    report.issues.extend(freshness_issues)
    report.data_sources_stale = stale
    report.issues.extend(_check_audit_stream_schemas(adir))
    # Eingangsstrom #3 — bewusst NACH der Datei-Freshness und ohne Einfluss auf
    # ``data_sources_stale``: ein toter Eingang sagt nichts ueber die
    # Verlaesslichkeit der Probe (Lehre #701).
    from app.core.settings import DBSettings

    try:
        db_url = DBSettings().url
        report.issues.extend(_check_document_ingest(db_url, now))
        # Eingangsstrom #4 — Inhalt statt blosser Ankunft.
        report.issues.extend(_check_youtube_transcript_coverage(db_url, now))
    except Exception:  # pragma: no cover - Konfigurationsfehler darf die Probe nicht toeten
        pass
    report.issues.extend(_check_rejected_closes(adir, now, lookback_hours=lookback_hours))
    report.issues.extend(_check_sudo_policy(runs_on_pi=report.runs_on_pi))
    report.issues.extend(_check_privilege_broker(runs_on_pi=report.runs_on_pi))
    report.issues.extend(_check_timer_scheduleability(runs_on_pi=report.runs_on_pi))
    report.issues.extend(_check_runtime_identity(adir, now, runs_on_pi=report.runs_on_pi))
    report.issues.extend(_check_prereg_reconciliation(adir))

    # ── P2: workstation-redirect — off-Pi probe runs read mirror/sync data
    # that may be selectively truncated (mtime-fresh but content-incomplete).
    # The 2026-05-23 false-positive had fresh mtime but only 6/16 of Pi's
    # alerts in window. Surface this as an explicit `probe_location` issue
    # so operator + `--exit-on-stale` can react; we do NOT touch
    # `data_sources_stale` here, so other check semantics remain stable.
    if not report.runs_on_pi:
        report.issues.append(
            HealthIssue(
                severity="warning",
                component="probe_location",
                message=(
                    f"Probe running on {report.hostname or 'unknown host'} "
                    f"(off-Pi) — counts may be partial-mirror, not authoritative. "
                    f"Re-run on Pi or pass --allow-stale to override."
                ),
            )
        )

    # ── Alert volume ─────────────────────────────────────────────────
    try:
        audits = load_alert_audits(adir)
    except Exception:
        report.issues.append(
            HealthIssue(
                severity="critical",
                component="alerts",
                message="Cannot read alert audit trail",
            )
        )
        audits = []

    recent_alerts = 0
    recent_actionable = 0
    for rec in audits:
        try:
            ts = datetime.fromisoformat(
                rec.dispatched_at.replace("Z", "+00:00"),
            )
        except (ValueError, AttributeError):
            continue
        if ts >= cutoff:
            recent_alerts += 1
            if getattr(rec, "actionable", None) is True:
                recent_actionable += 1
    report.recent_alerts = recent_alerts
    report.recent_actionable_alerts = recent_actionable

    # Suppress base alert-volume warning when data is stale (P0): the count is
    # not authoritative — the freshness warning already tells the operator.
    if recent_alerts < min_expected_alerts and not stale:
        report.issues.append(
            HealthIssue(
                severity="warning",
                component="alerts",
                message=(
                    f"Only {recent_alerts} alerts in last {lookback_hours}h "
                    f"(expected >= {min_expected_alerts})"
                ),
            )
        )

    # P1: actionable-alert floor. Relaxed during RE_ENTRY_MODE (ADR-1 gate=10
    # is expected to produce very few actionable alerts).
    if (
        not stale
        and not report.re_entry_mode_active
        and min_expected_actionable > 0
        and recent_actionable < min_expected_actionable
    ):
        report.issues.append(
            HealthIssue(
                severity="warning",
                component="alerts_actionable",
                message=(
                    f"Only {recent_actionable} actionable alerts in last "
                    f"{lookback_hours}h (expected >= {min_expected_actionable})"
                ),
            )
        )

    # ── Trading loop freshness (+ P1 status breakdown) ───────────────
    try:
        cycles = load_trading_loop_cycles(
            adir / "trading_loop_audit.jsonl",
        )
    except Exception:
        report.issues.append(
            HealthIssue(
                severity="critical",
                component="trading_loop",
                message="Cannot read trading loop audit trail",
            )
        )
        cycles = []

    recent_cycles = 0
    error_cycles = 0
    status_breakdown: Counter[str] = Counter()
    for c in cycles:
        ts_str = c.get("started_at", "")
        try:
            ts = datetime.fromisoformat(
                str(ts_str).replace("Z", "+00:00"),
            )
        except (ValueError, TypeError):
            continue
        if ts < cutoff:
            continue
        recent_cycles += 1
        status = str(c.get("status", "unknown")) or "unknown"
        status_breakdown[status] += 1
        if status in ("error", "no_market_data"):
            error_cycles += 1
    report.recent_cycles = recent_cycles
    report.cycle_status_breakdown = dict(status_breakdown)

    if recent_cycles < min_expected_cycles and not stale:
        report.issues.append(
            HealthIssue(
                severity="warning",
                component="trading_loop",
                message=(
                    f"Only {recent_cycles} cycles in last {lookback_hours}h "
                    f"(expected >= {min_expected_cycles})"
                ),
            )
        )

    if recent_cycles > 0 and error_cycles / recent_cycles > 0.5:
        report.issues.append(
            HealthIssue(
                severity="critical",
                component="trading_loop",
                message=(
                    f"{error_cycles}/{recent_cycles} cycles errored "
                    f"({error_cycles / recent_cycles:.0%})"
                ),
            )
        )

    # P1: priority_rejected saturation — Cron-Liveness without Wertschöpfung.
    # Relaxed during RE_ENTRY_MODE (ADR-1 paper_min_priority=10 expects
    # near-total rejection by design).
    if recent_cycles > 0 and not report.re_entry_mode_active:
        rejected = status_breakdown.get("priority_rejected", 0)
        ratio = rejected / recent_cycles
        if ratio > max_priority_rejected_ratio:
            report.issues.append(
                HealthIssue(
                    severity="warning",
                    component="trading_loop_signal_health",
                    message=(
                        f"{rejected}/{recent_cycles} cycles priority_rejected "
                        f"({ratio:.0%}) — pipeline runs but produces no signals; "
                        f"check priority gate / sentiment scoring"
                    ),
                )
            )

    # ── V5: loop open-deadlock watchdog (DS-20260531-V5) ─────────────
    # "Loop spins but opens nothing." Fires when the loop is demonstrably
    # active (>= min_expected_cycles) yet produced ZERO completed cycles AND
    # the open-blocking gates (diversification / sizing) dominate. This is the
    # exact 2026-05-31 deadlock signature; it is intentionally
    # RE_ENTRY_MODE-INDEPENDENT because a self-inflicted open-deadlock is never
    # a designed state (unlike priority_rejected saturation, which RE_ENTRY_MODE
    # expects). A legitimately full book is excluded: it rejects with
    # `risk_rejected` (max_open_positions), so the open-blocking ratio stays low.
    if recent_cycles > 0 and recent_cycles >= min_expected_cycles and not stale:
        completed = status_breakdown.get("completed", 0)
        open_blocked = sum(status_breakdown.get(s, 0) for s in _OPEN_BLOCKING_STATUSES)
        open_blocked_ratio = open_blocked / recent_cycles
        if completed == 0 and open_blocked_ratio >= max_open_blocking_ratio:
            dominant = max(
                _OPEN_BLOCKING_STATUSES,
                key=lambda s: status_breakdown.get(s, 0),
            )
            paper_hint = _paper_execution_silence_hint(adir, now)
            report.issues.append(
                HealthIssue(
                    severity="critical",
                    component="trading_loop_open_deadlock",
                    message=(
                        f"{open_blocked}/{recent_cycles} cycles "
                        f"{dominant} ({open_blocked_ratio:.0%}), 0 completed "
                        f"— loop spins but opens no positions (self-deadlock at "
                        f"the {dominant.replace('_rejected', '')} gate)"
                        f"{paper_hint}"
                    ),
                )
            )

    # ── Precision ────────────────────────────────────────────────────
    try:
        annotations = load_outcome_annotations(adir)
    except Exception:
        annotations = []

    hits = sum(1 for a in annotations if a.outcome == "hit")
    misses = sum(1 for a in annotations if a.outcome == "miss")
    resolved = hits + misses
    if resolved >= 20:
        precision = hits / resolved * 100
        if precision < min_precision_pct:
            report.issues.append(
                HealthIssue(
                    severity="warning",
                    component="precision",
                    message=(
                        f"Precision {precision:.1f}% is below threshold {min_precision_pct:.0f}%"
                    ),
                )
            )

    # ── Annotation backlog ───────────────────────────────────────────
    annotated_ids = {a.document_id for a in annotations}
    unique_unannotated = len(
        {
            rec.document_id
            for rec in audits
            if rec.directional_eligible is True and rec.document_id not in annotated_ids
        }
    )
    if unique_unannotated > 20:
        report.issues.append(
            HealthIssue(
                severity="warning",
                component="annotations",
                message=(f"{unique_unannotated} directional alerts unannotated"),
            )
        )

    return report
