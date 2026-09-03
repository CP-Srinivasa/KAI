"""Der Gesundheitszustand des Geldpfads (ADR 0018 §10).

Die Leitfrage ist nicht *"laeuft der Dienst?"* — das beantwortet ``/health``
— sondern **"ist die Aussage ueber das Geld gedeckt?"**. Deshalb gibt es hier
kein gruenes Licht ohne Beleg: eine gebrochene Journal-Kette oder ein
Reconciler mit ``attention`` machen den Schnappschuss ``degraded``, ganz gleich
wie gesund der Node sich meldet. Ein Health-Endpunkt, der bei kaputter
Beweiskette ``ok`` sagt, ist schlimmer als keiner.

**Alles kommt aus dem Journal, nichts aus einer Probe.** Wie ``app/ai/health``
loest dieser Aufruf keine Zahlung, keinen Decode und keine Quote aus. In
SIMULATION wird nicht einmal der Rail nach seiner Gesundheit gefragt — dort
gibt es keinen Node, und ein erfundenes ``reachable=true`` waere die
gefaehrlichste Zeile in dieser Datei.

**Verschwiegenheit.** Ein Health-Body ist die Stelle, an der Betriebsdaten am
ehesten in ein Ticket kopiert werden. Hier steht deshalb kein Pfad, kein
Macaroon, kein Proof-Hash und kein Ziel. ``live_gate`` traegt ausschliesslich
Booleans: WELCHE Datei fehlt, ist eine Frage fuer den Operator am Geraet, nicht
fuer eine HTTP-Antwort.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.lightning_settings import LightningSettings
from app.core.payment_settings import PaymentSettings
from app.payments.health_metrics import JournalMetrics, collect
from app.payments.journal import PaymentJournal
from app.payments.journal_chain import JournalIntegrityError
from app.payments.rail import PaymentRail
from app.payments.reconcile_types import STATE_FILENAME, load_state

DEFAULT_WINDOW_HOURS = 24.0


async def payment_health_snapshot(
    *,
    journal: PaymentJournal,
    rail: PaymentRail,
    settings: PaymentSettings,
    lightning: LightningSettings,
    app_env: str,
    now: datetime | None = None,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Der vollstaendige Zustand des Geldpfads — ohne Node-Probe, ohne Geheimnis."""
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(hours=window_hours)

    chain = _chain(journal)
    metrics = _metrics(journal, cutoff=cutoff)
    reconciliation = _reconciliation(journal, state_path)
    rail_state = await _rail(rail, settings)

    degraded = chain["chain"] != "ok" or reconciliation["status"] not in ("ok", "unknown")
    return {
        "status": "degraded" if degraded else "ok",
        "mode": settings.mode,
        "window_hours": window_hours,
        "rail": rail_state,
        "journal": chain,
        "reconciliation": reconciliation,
        "live_gate": _live_gate(settings, lightning, app_env=app_env),
        "in_flight": metrics.in_flight,
        "reconciliation_required": metrics.reconciliation_required,
        "policy_reject_count": metrics.policy_rejects,
        "fees_minor_units": metrics.fees,
        "settlement_latency_p50_ms": metrics.latency_p50_ms,
        "settlement_latency_p95_ms": metrics.latency_p95_ms,
        "last_settlement": metrics.last_settlement,
        "last_failure": metrics.last_failure,
    }


# --------------------------------------------------------------------------- #
# Bausteine
# --------------------------------------------------------------------------- #


def _chain(journal: PaymentJournal) -> dict[str, Any]:
    """Kettenzustand: ``ok`` / ``torn`` / ``broken`` / ``unreadable``.

    ``torn`` und ``broken`` sind zwei verschiedene Vorfaelle: ein zerrissener
    Tail ist ein abgebrochener Schreibvorgang (Stromausfall), ein Kettenbruch
    eine nachtraegliche Aenderung. Der Operator repariert sie verschieden.
    """
    if not journal.path.is_file():
        return {"chain": "ok", "seq": 0, "reason": "no journal yet"}
    try:
        status = journal.verify_chain()
    except JournalIntegrityError as exc:  # pragma: no cover - verify_chain berichtet
        return {"chain": "unreadable", "seq": 0, "reason": type(exc).__name__}
    if status.ok:
        return {"chain": "ok", "seq": status.records, "reason": ""}
    torn = "torn tail" in status.reason
    return {
        "chain": "torn" if torn else "broken",
        "seq": status.records,
        "reason": "incomplete final record" if torn else "chain link mismatch",
    }


def _metrics(journal: PaymentJournal, *, cutoff: datetime) -> JournalMetrics:
    try:
        events = journal.events()
    except JournalIntegrityError:
        # Eine kaputte Kette liefert keine Kennzahlen. Das ist kein Grund, den
        # Endpunkt zu verlieren — der Kettenzustand sagt es bereits deutlich.
        return JournalMetrics()
    return collect(events, cutoff=cutoff)


def _reconciliation(journal: PaymentJournal, state_path: Path | None) -> dict[str, Any]:
    path = state_path or (journal.path.parent / STATE_FILENAME)
    if not path.is_file():
        return {"status": "unknown", "last_run": None, "orphans": 0, "clock_anomaly": False}
    state = load_state(path)
    if not state.last_run_utc:
        return {"status": "attention", "last_run": None, "orphans": 0, "clock_anomaly": False}
    return {
        "status": state.last_status or "unknown",
        "last_run": state.last_run_utc,
        "orphans": state.last_orphans,
        "clock_anomaly": state.last_clock_anomaly,
    }


async def _rail(rail: PaymentRail, settings: PaymentSettings) -> dict[str, Any]:
    """Node-Zustand — in SIMULATION ausdruecklich ``simulated``, nie ``ok``."""
    base: dict[str, Any] = {"name": rail.name, "reachable": False}
    if settings.mode == "simulation":
        return {**base, "state": "simulated", "reachable": False}
    try:
        health = await rail.health()
    except Exception:  # noqa: BLE001 - ein stummer Node ist ein Befund, kein 500
        return {**base, "state": "unknown"}
    return {
        "name": rail.name,
        "state": "ok" if health.healthy else "degraded",
        "reachable": health.reachable,
        "synced_to_chain": health.synced_to_chain,
        "synced_to_graph": health.synced_to_graph,
        "wallet_locked": health.wallet_locked,
    }


def _live_gate(
    settings: PaymentSettings, lightning: LightningSettings, *, app_env: str
) -> dict[str, bool]:
    """Die vier LIVE-Vorbedingungen als Booleans (ADR §11).

    Bewusst nur Booleans: der Wert eines Pfads waere eine Wegbeschreibung zum
    Schluessel, und ``validate_payment_boot`` nennt dem Operator den fehlenden
    Namen ohnehin — beim Start, wo er hingehoert.
    """
    seed = lightning.hotp_seed_path.strip()
    return {
        "app_env_production": app_env.lower() == "production",
        "pay_enabled": bool(lightning.pay_enabled),
        "hotp_seed_present": bool(seed) and Path(seed).is_file(),
        "fee_limit_ok": settings.fee_limit_default_ppm > 0 and settings.fee_limit_max_sat > 0,
    }


__all__ = ["DEFAULT_WINDOW_HOURS", "payment_health_snapshot"]
