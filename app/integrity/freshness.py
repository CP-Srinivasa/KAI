"""L3 audit-integrity freshness + replay probe (KAI L3, P0.5 hardening).

Once the dashboard shows L3 as ``ok · enabled``, KAI must also notice if that
state goes silently stale or non-reproducible — otherwise a green KPI hides a
dead timer or a tampered audit log. This probe is that watchdog.

It is DELIBERATELY narrow: it does NOT depend on OpenTimestamps and treats
``stamper=null`` / ``proof_available=false`` as a normal state, never an error
(that's the expected digest-only mode). It checks four things, worst-first:

  * replay FAILED   — the audit file is missing / unreadable (critical)
  * replay MISMATCH — the append-only prefix changed or the file shrank vs the
    sizes recorded at anchor time (critical; tamper/truncation, not growth)
  * anchor STALE    — the last anchor is older than the freshness window
    (warning >26h, critical >48h)
  * anchor MISSING  — enabled but nothing anchored yet (warning, not critical —
    avoids a false alarm right after activation)

Read-only, never raises. No capital path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.integrity_settings import IntegritySettings
from app.core.settings import get_settings
from app.integrity.digest import sha256_prefix

STALE_WARN_HOURS = 26.0
STALE_CRIT_HOURS = 48.0

#: Anker-Familien und ihre GEMESSENE Kadenz (Pi, 31.08.2026, 150 Saetze).
#:
#: Bis hierher glob'te diese Sonde ausschliesslich ``audit-*.json`` und sah
#: damit **75 von 150** Anker-Saetzen (A7-051/072). Ein seit zehn Tagen toter
#: ``truthledger``-Anker haette exakt so ausgesehen wie ein lebender — die
#: Familie kam in keiner Wachliste vor.
#:
#: Gemessen, nicht geraten:
#:   audit             n=75  Median-Abstand 24,00 h  groesster 48,00 h
#:   truthledger       n=62  Median-Abstand 24,00 h  groesster 24,01 h
#:   newsverdict       n=9   Median-Abstand  0,11 h  groesster 819,43 h
#:   verdict           n=3   Median-Abstand  9,99 h  Alter 1.490 h
#:   analystprobe-rule n=1   einmalig, Alter 628 h
#:
#: Nur die ersten beiden sind taktgetrieben und duerfen ueber ihr Alter
#: beurteilt werden. Die drei anderen sind ereignisgetrieben bzw. einmalig —
#: eine Altersschwelle waere dort ein Dauer-Fehlalarm, nicht eine Wache
#: (dieselbe Falle wie beim H2-Zombie).
CADENCED_ANCHOR_FAMILIES: dict[str, float] = {
    "audit": STALE_CRIT_HOURS,
    # 48 h = das Doppelte des groessten je gemessenen Abstands (24,01 h) und
    # damit dieselbe Grosszuegigkeit, die audit- seit jeher zugestanden wird.
    "truthledger": 48.0,
}
EVENT_DRIVEN_ANCHOR_FAMILIES = ("newsverdict", "verdict", "analystprobe-rule")


@dataclass(frozen=True)
class L3FreshnessProbe:
    """Watchdog verdict for the live L3 digest anchor."""

    status: str  # "ok" | "warning" | "critical"
    reason_code: str
    enabled: bool
    anchor_count: int
    last_anchor_age_hours: float | None
    proof_available: bool
    stamper: str
    digest_prefix: str
    # Kein Aggregat ohne Zerlegung: welche Anker-Familien es gibt, wie viele
    # Saetze und wie alt der juengste je Familie ist. ``anchor_count`` bleibt
    # die Zahl der audit-Saetze (Rueckwaertskompatibilitaet des Feldes);
    # ``anchor_total`` ist die ehrliche Gesamtzahl.
    families: dict[str, dict[str, float | int | None]] = field(default_factory=dict)
    anchor_total: int = 0
    stale_families: tuple[str, ...] = ()


def _parse_iso(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _latest_record(out_dir: Path) -> tuple[dict[str, Any], int]:
    """Return (latest_record_by_ts, total_record_count). Empty dict if none."""
    records = sorted(out_dir.glob("audit-*.json")) if out_dir.exists() else []
    parsed: list[dict[str, Any]] = []
    for path in records:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            parsed.append(data)
    if not parsed:
        return {}, 0
    return max(parsed, key=lambda d: str(d.get("ts", ""))), len(parsed)


def _family_of(name: str) -> str:
    """``truthledger-b8b2....json`` -> ``truthledger``; der Hash ist kein Name."""
    stem = name.rsplit(".", 1)[0]
    parts = stem.split("-")
    while len(parts) > 1 and _looks_like_hash(parts[-1]):
        parts.pop()
    return "-".join(parts)


def _looks_like_hash(token: str) -> bool:
    return len(token) >= 8 and all(c in "0123456789abcdef" for c in token.lower())


def anchor_families(out_dir: Path, *, now: datetime) -> dict[str, dict[str, float | int | None]]:
    """Bestandsaufnahme ALLER Anker-Familien, nicht nur der geglob'ten einen.

    Liefert je Familie ``count`` und ``age_hours`` des juengsten Satzes.
    Unlesbare Dateien werden uebersprungen, nie geraten.
    """
    out: dict[str, dict[str, float | int | None]] = {}
    if not out_dir.exists():
        return out
    for path in sorted(out_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        family = _family_of(path.name)
        entry = out.setdefault(family, {"count": 0, "age_hours": None})
        entry["count"] = int(entry["count"] or 0) + 1
        ts = _parse_iso(data.get("ts"))
        if ts is None:
            continue
        age = (now - ts).total_seconds() / 3600.0
        current = entry["age_hours"]
        if current is None or age < float(current):
            entry["age_hours"] = round(age, 2)
    return out


def stale_anchor_families(
    families: dict[str, dict[str, float | int | None]],
) -> tuple[str, ...]:
    """Welche TAKTGETRIEBENE Familie ihr gemessenes Fenster gerissen hat.

    Ereignisgetriebene Familien (``EVENT_DRIVEN_ANCHOR_FAMILIES``) werden
    bewusst nicht ueber ihr Alter beurteilt — ``verdict`` ist 1.490 h alt und
    voellig in Ordnung.
    """
    stale: list[str] = []
    for family, limit in CADENCED_ANCHOR_FAMILIES.items():
        entry = families.get(family)
        if not entry:
            continue
        age = entry.get("age_hours")
        if isinstance(age, (int, float)) and float(age) > limit:
            stale.append(family)
    return tuple(sorted(stale))


def check_l3_integrity_freshness(
    cfg: IntegritySettings | None = None, *, now: datetime | None = None
) -> L3FreshnessProbe:
    """Return the L3 freshness/replay verdict, never raising."""
    cfg = cfg or get_settings().integrity
    current = now or datetime.now(UTC)

    if not cfg.enabled:
        return L3FreshnessProbe(
            status="ok",
            reason_code="L3_DISABLED",
            enabled=False,
            anchor_count=0,
            last_anchor_age_hours=None,
            proof_available=False,
            stamper=cfg.stamper,
            digest_prefix="",
        )

    latest, count = _latest_record(Path(cfg.proofs_dir))
    if not latest:
        # Enabled but nothing anchored yet — warning, not critical (a just-
        # activated system has not had its first scheduled run).
        return L3FreshnessProbe(
            status="warning",
            reason_code="L3_ANCHOR_MISSING",
            enabled=True,
            anchor_count=0,
            last_anchor_age_hours=None,
            proof_available=False,
            stamper=cfg.stamper,
            digest_prefix="",
        )

    digest = str(latest.get("digest", ""))
    files: dict[str, Any] = latest.get("files", {}) if isinstance(latest.get("files"), dict) else {}
    sizes: dict[str, Any] = latest.get("sizes", {}) if isinstance(latest.get("sizes"), dict) else {}
    last_ts = _parse_iso(latest.get("ts"))
    age_hours = (current - last_ts).total_seconds() / 3600.0 if last_ts else None
    out_dir = Path(cfg.proofs_dir)
    proof_available = bool(digest) and (out_dir / f"audit-{digest[:16]}.ots").exists()

    families = anchor_families(out_dir, now=current)
    stale_families = stale_anchor_families(families)
    anchor_total = sum(int(e.get("count") or 0) for e in families.values())

    def verdict(status: str, code: str) -> L3FreshnessProbe:
        return L3FreshnessProbe(
            status=status,
            reason_code=code,
            enabled=True,
            anchor_count=count,
            last_anchor_age_hours=round(age_hours, 2) if age_hours is not None else None,
            proof_available=proof_available,
            stamper=cfg.stamper,
            digest_prefix=digest[:8],
            families=families,
            anchor_total=anchor_total,
            stale_families=stale_families,
        )

    # Worst-first: replay integrity beats freshness.
    for path, recorded_sha in files.items():
        p = Path(path)
        if not p.exists():
            return verdict("critical", "L3_DIGEST_REPLAY_FAILED")  # source file gone
        recorded_size = sizes.get(path)
        if not isinstance(recorded_size, int):
            continue  # legacy record without sizes → cannot prefix-verify, skip
        try:
            current_size = p.stat().st_size
            if current_size < recorded_size:
                return verdict("critical", "L3_DIGEST_REPLAY_MISMATCH")  # truncated
            if sha256_prefix(p, recorded_size) != recorded_sha:
                return verdict("critical", "L3_DIGEST_REPLAY_MISMATCH")  # prefix changed
        except OSError:
            return verdict("critical", "L3_DIGEST_REPLAY_FAILED")  # unreadable

    if age_hours is not None and age_hours > STALE_CRIT_HOURS:
        return verdict("critical", "L3_ANCHOR_CRITICAL_STALE")
    if age_hours is not None and age_hours > STALE_WARN_HOURS:
        return verdict("warning", "L3_ANCHOR_STALE")
    # Eine taktgetriebene Familie, die ihr gemessenes Fenster reisst, ist ein
    # Befund — auch wenn die audit-Familie gerade frisch ist. Vorher konnte
    # genau das nicht auffallen: die Sonde kannte nur eine der fuenf Familien.
    if stale_families:
        return verdict("warning", "L3_ANCHOR_FAMILY_STALE")
    return verdict("ok", "L3_ANCHOR_OK")
