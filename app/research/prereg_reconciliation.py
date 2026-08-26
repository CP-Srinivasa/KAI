"""Abgleich versiegeltes Ledger ↔ Reifeblick — EINE Zustandsfunktion, drei Leser.

Befund 2026-08-26 (live, Pi): 19 versiegelte Prä-Regs, 14 Zeilen im
Reifeblick. Die fünf fehlenden waren alle terminal entschieden — und genau
deshalb unsichtbar: gerendert wurden nur Wachlisten-Specs und unbeobachtete
Claims, „entschieden ohne Spec" fiel durch. Gleichzeitig stand ``0879a65c``
(LN) als ``UNWATCHED`` ohne Verdikt, obwohl ``ln_reconciliation_verdict.jsonl``
ein ``PASS`` trägt: die Seitenablage-Suche kannte nur eine Datei.

Dieses Modul ist die **einzige** Stelle, die einem Ledger-Eintrag seinen
Abgleichszustand zuweist. ``prereg-list``, ``compute_maturity`` und der
Health-Check lesen alle hier — eine zweite Implementierung würde driften
(#723/#748/#755: doppelt implementierte Invarianten kassierten die Reparatur
der jeweils anderen Kopie).

Wahrheitsordnung (unverändert, nur konsequent angewandt):

1. Terminal ist allein ein Verdikt in der **verifizierten Truth-Kette**
   (``load_attested_resolutions``). → ``RESOLVED``.
2. Ein Verdikt in einer **Seitenablage** (``prereg_verdicts.jsonl``,
   ``ln_reconciliation_verdict.jsonl``) schließt nicht, ändert aber die
   fällige Handlung: attestieren statt auswerten. → ``VERDICT_UNATTESTED``.
3. Ein Claim mit Reife-Spec ist beobachtet. → ``WATCHED``.
4. Alles andere ist eine Aufsichtslücke. → ``UNWATCHED``.

Beschädigte, widersprüchliche oder unklassifizierbare Resolutionen sind KEIN
Abschluss (fail-closed): der Claim bleibt in seinem Aufsichtszustand, die
Resolution hängt zur Diagnose an der Zeile.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.research.prereg_maturity import (
    MATURITY_SPECS,
    PREREG_LEDGER_RELPATH,
    _terminal_verdict_class,
    load_attested_resolutions,
)

RECON_STATE_RESOLVED = "RESOLVED"
RECON_STATE_VERDICT_UNATTESTED = "VERDICT_UNATTESTED"
RECON_STATE_WATCHED = "WATCHED"
RECON_STATE_UNWATCHED = "UNWATCHED"

# Seitenablagen mit Verdikt-Datensätzen außerhalb der Truth-Kette. Bewusst
# eine explizite Liste, kein Glob: eine Datei, die zufällig „verdict" im
# Namen trägt, darf nicht still zur Verdikt-Quelle werden.
OFFCHAIN_VERDICT_RELPATHS: tuple[Path, ...] = (
    Path("research") / "prereg_verdicts.jsonl",
    Path("research") / "ln_reconciliation_verdict.jsonl",
)

_TERMINAL_CLASSES = frozenset({"MET", "NOT_MET", "CLOSED_NO_VERDICT"})


def _read_jsonl_dicts(path: Path) -> list[dict[str, Any]]:
    """Fail-soft: kaputte Zeilen werden übersprungen, nie der ganze Abgleich."""
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            rows.append(record)
    return rows


def load_offchain_verdicts(artifacts_dir: Path) -> dict[str, list[dict[str, str]]]:
    """Je ``prereg_id`` das JÜNGSTE terminale Verdikt je Seitenablage.

    Ein ``IMMATURE`` gefolgt von ``PASS`` (LN, 0879a65c) zählt als PASS; ein
    ``IMMATURE`` allein zählt nicht — ein Reifevermerk ist kein Verdikt.
    Rein diagnostisch: nichts hiervon schließt einen Claim.
    """
    out: dict[str, list[dict[str, str]]] = {}
    for rel in OFFCHAIN_VERDICT_RELPATHS:
        path = artifacts_dir / rel
        if not path.exists():
            continue
        latest: dict[str, str | None] = {}
        for record in _read_jsonl_dicts(path):
            prereg_id = record.get("prereg_id")
            if not isinstance(prereg_id, str) or not prereg_id:
                continue
            # Letzter Datensatz je Datei gewinnt — auch wenn er nicht terminal
            # ist: ein späteres INSUFFICIENT_N hebt ein früheres PASS auf.
            latest[prereg_id] = _terminal_verdict_class(record.get("verdict"))
        for prereg_id, verdict_class in latest.items():
            if verdict_class in _TERMINAL_CLASSES:
                out.setdefault(prereg_id, []).append(
                    {"source": rel.as_posix(), "verdict_class": str(verdict_class)}
                )
    return out


def load_sealed_entries(artifacts_dir: Path) -> list[dict[str, Any]]:
    """Versiegelte Claims in Ledger-Reihenfolge, Dubletten kollabiert."""
    path = artifacts_dir / PREREG_LEDGER_RELPATH
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in _read_jsonl_dicts(path):
        prereg_id = record.get("prereg_id")
        if not isinstance(prereg_id, str) or not prereg_id or prereg_id in seen:
            continue
        seen.add(prereg_id)
        entries.append(record)
    return entries


def classify_ledger_entries(
    artifacts_dir: Path,
    *,
    specs: Any = MATURITY_SPECS,
    resolutions: dict[str, dict[str, Any]] | None = None,
    resolution_error: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Eine Zeile je versiegeltem Claim, mit Abgleichszustand.

    ``resolutions``/``resolution_error`` können übergeben werden, wenn der
    Aufrufer die Truth-Kette bereits verifiziert hat (``compute_maturity``);
    sonst wird sie hier gelesen. Bei ungültiger Kette ist KEIN Claim
    ``RESOLVED`` — die Zeile trägt dann ``resolution_error``.
    """
    if resolutions is None and resolution_error is None:
        resolutions, resolution_error = load_attested_resolutions(artifacts_dir)
    resolutions = resolutions or {}
    watched = {
        str(spec.get("prereg_id"))
        for spec in specs
        if isinstance(spec.get("prereg_id"), str) and spec.get("prereg_id")
    }
    offchain = load_offchain_verdicts(artifacts_dir)

    rows: list[dict[str, Any]] = []
    for record in load_sealed_entries(artifacts_dir):
        prereg_id = str(record["prereg_id"])
        resolution = None if resolution_error is not None else resolutions.get(prereg_id)
        resolved = isinstance(resolution, dict) and resolution.get("status") == "resolved"
        offchain_rows = offchain.get(prereg_id, [])
        if resolved:
            state = RECON_STATE_RESOLVED
        elif offchain_rows:
            state = RECON_STATE_VERDICT_UNATTESTED
        elif prereg_id in watched:
            state = RECON_STATE_WATCHED
        else:
            state = RECON_STATE_UNWATCHED
        rows.append(
            {
                "prereg_id": prereg_id,
                "name": str(record.get("name") or "?"),
                "created_at_utc": str(record.get("created_at_utc") or ""),
                "horizon": str(record.get("horizon") or ""),
                "n_target": record.get("sample_size_target"),
                "state": state,
                "watched": prereg_id in watched,
                "verdict_class": (
                    str(resolution.get("verdict_class")) if resolved and resolution else None
                ),
                "resolution": resolution,
                "resolution_error": resolution_error,
                "offchain_verdicts": offchain_rows,
            }
        )
    return rows


def reconcile_ledger_view(artifacts_dir: Path, view_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Invariante: jeder versiegelte Claim erscheint im Reifeblick genau einmal.

    Drei Verletzungsarten, getrennt gemeldet — eine Summe würde die Ursache
    verstecken: ``missing_from_view`` (Ledger-Eintrag ohne Zeile — der Befund
    vom 26.08.), ``duplicated_in_view`` (Zeile doppelt — eine Frist würde
    doppelt gemeldet), ``not_in_ledger`` (Zeile ohne Versiegelung —
    Wachlisten-Drift auf eine nie registrierte ID).
    """
    ledger_ids = [str(r["prereg_id"]) for r in load_sealed_entries(artifacts_dir)]
    view_ids = [
        str(r.get("prereg_id"))
        for r in view_rows
        if isinstance(r.get("prereg_id"), str) and r.get("prereg_id")
    ]
    ledger_set = set(ledger_ids)
    seen: set[str] = set()
    duplicated: list[str] = []
    for pid in view_ids:
        if pid in seen and pid not in duplicated:
            duplicated.append(pid)
        seen.add(pid)
    missing = [pid for pid in ledger_ids if pid not in seen]
    foreign = sorted({pid for pid in view_ids if pid not in ledger_set})
    return {
        "ok": not missing and not duplicated and not foreign,
        "ledger_count": len(ledger_ids),
        "view_count": len(view_ids),
        "missing_from_view": missing,
        "duplicated_in_view": duplicated,
        "not_in_ledger": foreign,
    }


__all__ = [
    "OFFCHAIN_VERDICT_RELPATHS",
    "RECON_STATE_RESOLVED",
    "RECON_STATE_UNWATCHED",
    "RECON_STATE_VERDICT_UNATTESTED",
    "RECON_STATE_WATCHED",
    "classify_ledger_entries",
    "load_offchain_verdicts",
    "load_sealed_entries",
    "reconcile_ledger_view",
]
