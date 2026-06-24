"""Wende das Source-Lifecycle-Ranking autonom auf den DB-Status an (Phase 1).

Schliesst die verwaiste Schreibseite: ``source_lifecycle_recalc`` berechnet das
Ranking nach ``monitor/source_ranking.json``, aber bisher las das niemand für
Produktionslogik. Dieser Step transponiert die Lifecycle-Entscheidung in den
echten Kontrollpunkt — ``sources.status`` in der DB, den der RSS-Scheduler liest
(nur ``ACTIVE`` wird gepollt). Damit hören tote/dauerschwache Quellen autonom
auf, das System zu verschmutzen.

Sicherheit:
* Kill-Switch ``SOURCE_LIFECYCLE_APPLY_ENABLED`` (default OFF → DRY-RUN: loggt +
  auditiert, was passieren WÜRDE, ohne DB-Mutation).
* Entscheidung in ``app.learning.source_rotation_policy`` (pure, getestet): nur
  ``silent`` (sofort) oder echter ``low``-Tier über N Läufe wird stillgelegt;
  ``watch`` bleibt aktiv; ``pinned`` nie angefasst.
* FSM-validiert (``app.learning.source_lifecycle``), jeder Wechsel im
  Lifecycle-Audit-Trail, reversibel (SILENT→ACTIVE recovery).
* Fail-soft: ein Fehler hier killt den Recalc-Cycle nicht (systemd ``-``-Prefix);
  fehlt/kaputt das Ranking → no-op, kein Crash.

Usage:
    python scripts/source_lifecycle_apply.py
    python scripts/source_lifecycle_apply.py --dry-run   # erzwingt Dry-Run
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Projekt-Root auf den Pfad (wie scripts/seed_rss_sources.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.settings import get_settings  # noqa: E402
from app.learning.source_lifecycle import can_transition  # noqa: E402
from app.learning.source_lifecycle_audit import (  # noqa: E402
    LifecycleEvent,
    append_lifecycle_event,
)
from app.learning.source_rotation_policy import decide_rotation  # noqa: E402
from app.storage.db.session import build_session_factory  # noqa: E402
from app.storage.repositories.source_repo import SourceRepository  # noqa: E402
from app.storage.schemas.source import SourceUpdate  # noqa: E402

_RANKING_PATH = Path("monitor/source_ranking.json")
_STATE_PATH = Path("monitor/source_rotation_state.json")
_ARTIFACTS = Path("artifacts")


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_flagged_runs() -> dict[str, int]:
    raw = _load_json(_STATE_PATH).get("flagged_runs", {})
    if not isinstance(raw, dict):
        return {}
    return {str(k): int(v) for k, v in raw.items() if isinstance(v, (int, float))}


def _save_flagged_runs(runs: dict[str, int]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "report_type": "source_rotation_state",
        "updated_at": datetime.now(UTC).isoformat(),
        "flagged_runs": runs,
    }
    _STATE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


async def apply_rotation(*, force_dry_run: bool = False) -> int:
    """Wende das Ranking auf die DB an. Returns Anzahl tatsächlicher Wechsel."""
    settings = get_settings()
    enabled = bool(settings.sources.lifecycle_apply_enabled) and not force_dry_run
    mode = "ENFORCE" if enabled else "DRY-RUN"

    ranking = _load_json(_RANKING_PATH)
    ranked = ranking.get("ranked")
    if not isinstance(ranked, list) or not ranked:
        print(f"source_lifecycle_apply [{mode}]: kein Ranking ({_RANKING_PATH}) — no-op")
        return 0

    prior_runs = _load_flagged_runs()
    new_runs: dict[str, int] = {}
    session_factory = build_session_factory(settings.db)

    applied = 0
    would = 0
    skipped_no_db = 0

    async with session_factory() as session:
        repo = SourceRepository(session)
        for entry in ranked:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("source_name") or "").strip()
            if not name:
                continue

            # Join Ranking(source_name) -> DB(provider). Nicht-RSS-Quellen
            # (YouTube, tradingview_webhook) haben keine DB-Row -> überspringen.
            matches = await repo.list(provider=name)
            if not matches:
                skipped_no_db += 1
                continue
            source = matches[0]
            current = source.status  # SourceStatus

            decision = decide_rotation(
                current,
                silent=bool(entry.get("silent")),
                reliability_tier=str(entry.get("reliability_tier") or ""),
                pinned=bool(entry.get("pinned")),
                prior_flagged_runs=prior_runs.get(name, 0),
            )
            # Zähler fortschreiben (trackt die beobachtete Realität, auch im Dry-Run).
            if decision.flagged_runs:
                new_runs[name] = decision.flagged_runs

            target = decision.target
            if target is None or target == current:
                continue
            if not can_transition(current, target):
                print(
                    f"  SKIP  {name:20s} illegaler Übergang "
                    f"{current.value} -> {target.value} ({decision.reason})"
                )
                continue

            print(
                f"  {'APPLY' if enabled else 'WOULD':5s} {name:20s} "
                f"{current.value} -> {target.value}  ({decision.reason})"
            )
            reason = decision.reason if enabled else f"dry_run:{decision.reason}"
            append_lifecycle_event(
                LifecycleEvent(
                    source=name,
                    from_status=current.value,
                    to_status=target.value,
                    reason=reason,
                    recorded_at_utc=datetime.now(UTC).isoformat(),
                    evidence={
                        "n": entry.get("n"),
                        "reliability_tier": entry.get("reliability_tier"),
                        "silent": entry.get("silent"),
                        "rotation_flagged": entry.get("rotation_flagged"),
                        "wilson_lower_95": entry.get("wilson_lower_95"),
                    },
                ),
                _ARTIFACTS,
            )
            if enabled:
                await repo.update(source.source_id, SourceUpdate(status=target))
                applied += 1
            else:
                would += 1

        if enabled:
            await session.commit()

    _save_flagged_runs(new_runs)
    print(
        f"source_lifecycle_apply [{mode}]: applied={applied} would_change={would} "
        f"skipped_no_db_row={skipped_no_db} tracked_low_runs={len(new_runs)}"
    )
    return applied


def main() -> None:
    force_dry_run = "--dry-run" in sys.argv[1:]
    asyncio.run(apply_rotation(force_dry_run=force_dry_run))


if __name__ == "__main__":
    main()
