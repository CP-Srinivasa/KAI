"""LIVE-Sektion des Operator-Boards — offene Prä-Regs statt Handpflege.

Operator-Befund 2026-07-30: das Board meldete „Kuratierter Snapshot · Stand
2026-07-12 · 18 Tage alt — veraltet, bitte pflegen", obwohl ``docs/
operator_board.json`` ausschliesslich **abgeschlossene** Phasen enthielt. Zwei
Defekte in einem:

1. Es gab **keine** live-berechnete Sektion — der laufende Prozess (welche
   Prä-Reg ist offen, welche ist fällig) stand nur in der Erinnerung des
   Operators bzw. in einer Datei, die jemand pflegen musste.
2. Der Stale-Alarm feuerte auf einem **Chronik-Log**. Ein Log erledigter Phasen
   kann per Definition nicht veralten; nur OFFENE kuratierte Punkte können
   ungepflegt sein.

Dieses Modul liefert die Sektion, die nicht gepflegt werden muss: sie fällt aus
den Ledgern ab, die die Falsifikations-Doktrin (ADR 0012) ohnehin schreibt —
``prereg_ledger.jsonl`` (registriert) minus ``prereg_verdicts.jsonl``
(aufgelöst), angereichert um die Reife-Zahlen aus
:mod:`app.research.prereg_maturity`.

**Rein (kein I/O, keine DB)** wie :func:`app.observability.n_overview.
build_n_overview`: der Endpoint liest die Artefakte und reicht die Rohwerte
hinein. Damit sind Zuordnung, Zustände und Handlungstexte an EINER testbaren
Stelle gepflegt.

Sprachregel (Lehre ``kai_news_direction_v2_immature``): Reife ist ein
**Upper-Bound-Proxy**. ``due`` heisst „Eval jetzt fahren", NIE „Claim
bestanden"; fehlende Reife-Info ist weder PASS noch FAIL, sondern
„nicht gezählt".
"""

from __future__ import annotations

from typing import Any

# INSUFFICIENT_N schliesst einen Claim NICHT ab — er reift weiter und muss
# darum als offener Punkt sichtbar bleiben. Nur MET/NOT_MET sind terminal.
TERMINAL_VERDICTS = frozenset({"MET", "NOT_MET"})

# Ab wann ein OFFENER kuratierter Punkt als ungepflegt gilt. Bewusst identisch
# zur bisherigen Backend-Schwelle — geändert wird nur, WORAUF sie zählt.
CURATED_STALE_DAYS = 7


def _sort_key(row: dict[str, Any]) -> tuple[int, float, str]:
    """Handlungsreihenfolge: fällig zuerst, dann am weitesten gereift.

    Claims ohne Zähler sortieren zuletzt — sie tragen keine Dringlichkeit, die
    man belegen könnte.
    """
    state = row["state"]
    rank = {"due": 0, "maturing": 1, "no_counter": 2}.get(state, 3)
    progress = row["progress_pct"] if isinstance(row["progress_pct"], (int, float)) else -1.0
    return (rank, -float(progress), str(row["name"]))


def open_preregs(
    ledger: list[dict[str, Any]],
    verdicts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Registrierte Claims minus terminal aufgelöste — read-only-safe.

    Kaputte/fremde Zeilen werden übersprungen (nie eine Exception), doppelte
    Registrierungen derselben ``prereg_id`` kollabieren auf eine Zeile.
    """
    last_verdict: dict[str, str] = {}
    terminal: set[str] = set()
    for raw in verdicts:
        if not isinstance(raw, dict):
            continue
        pid = raw.get("prereg_id")
        verdict = raw.get("verdict")
        if not isinstance(pid, str) or not isinstance(verdict, str):
            continue
        last_verdict[pid] = verdict
        if verdict in TERMINAL_VERDICTS:
            terminal.add(pid)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in ledger:
        if not isinstance(raw, dict):
            continue
        pid = raw.get("prereg_id")
        name = raw.get("name")
        if not isinstance(pid, str) or not isinstance(name, str) or not pid or not name:
            continue
        if pid in terminal or pid in seen:
            continue
        seen.add(pid)
        try:
            target = int(raw.get("sample_size_target") or 0)
        except (TypeError, ValueError):
            target = 0
        out.append(
            {
                "prereg_id": pid,
                "name": name,
                "sample_size_target": target,
                "created_at_utc": str(raw.get("created_at_utc") or ""),
                "last_verdict": last_verdict.get(pid),
            }
        )
    return out


def build_live_board(
    *,
    ledger: list[dict[str, Any]],
    verdicts: list[dict[str, Any]],
    maturity_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble die Live-Sektion: offene Prä-Regs mit Reife-Zustand + Aktion.

    ``maturity_rows`` sind die Rohzeilen aus
    :func:`app.research.prereg_maturity.compute_maturity`. Zugeordnet wird
    **vorrangig über ``prereg_id``** — die versiegelte Identität — und nur als
    Rückfall über ``name``: Spec-Namen driften von Ledger-Namen ab (der
    hedged-drift-Spec heisst ``…_drift``, die Prä-Reg ``…_drift_v2``), ein
    reiner Namens-Join hängt die Reife dann an den falschen Claim. Fehlt eine
    Zeile, bleibt der Claim ehrlich ungezählt.
    """
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for raw in maturity_rows:
        if not isinstance(raw, dict):
            continue
        pid = raw.get("prereg_id")
        if isinstance(pid, str) and pid:
            by_id[pid] = raw
        if isinstance(raw.get("name"), str):
            by_name.setdefault(raw["name"], raw)

    # Ein Spec mit prereg_id gehört AUSSCHLIESSLICH diesem Claim — sein Name darf
    # keinen namensgleichen anderen Claim mehr treffen.
    claimed_names = {r["name"] for r in by_id.values() if isinstance(r.get("name"), str)}

    rows: list[dict[str, Any]] = []
    for claim in open_preregs(ledger, verdicts):
        mat = by_id.get(claim["prereg_id"])
        if mat is None and claim["name"] not in claimed_names:
            mat = by_name.get(claim["name"])
        n_proxy: int | None = None
        n_target: int | None = None
        progress: float | None = None
        state = "no_counter"

        if mat is not None:
            try:
                n_proxy = int(mat.get("n_proxy"))  # type: ignore[arg-type]
                n_target = int(mat.get("n_target"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                n_proxy = n_target = None
            if n_proxy is not None and n_target and n_target > 0:
                progress = round(min(100.0, 100.0 * n_proxy / n_target), 1)
            state = "due" if bool(mat.get("due")) else "maturing"

        if state == "due":
            action = "Eval jetzt fahren — Reife ist Upper-Bound-Proxy, kein PASS."
        elif state == "maturing":
            action = f"reift ({n_proxy}/{n_target}) — kein Attest vor Ziel-n."
        else:
            action = "kein Reife-Zähler registriert — Fortschritt nicht gezählt."

        rows.append(
            {
                **claim,
                "state": state,
                "n_proxy": n_proxy,
                "n_target": n_target if n_target else claim["sample_size_target"] or None,
                "per_source": dict(mat.get("per_source") or {}) if mat else {},
                "progress_pct": progress,
                "action": action,
            }
        )

    rows.sort(key=_sort_key)
    due_count = sum(1 for r in rows if r["state"] == "due")
    return {
        "open_preregs": rows,
        "open_count": len(rows),
        "due_count": due_count,
        "has_content": bool(rows),
        # Sprachregel: die Sektion ist live-berechnet, die Reife ist ein Proxy.
        "note": (
            "Live aus prereg_ledger − prereg_verdicts (ADR 0012). Reife-n ist "
            "Upper-Bound-Proxy: FÄLLIG = Eval fahren, nie „bestanden“."
        ),
    }


def has_open_curated_items(curated: dict[str, Any]) -> bool:
    """True wenn die kuratierte Datei überhaupt einen OFFENEN Punkt trägt.

    Erledigte Phasen zählen nicht — sie sind Chronik. Genau hier lag der
    Fehlalarm: 10× ``status: done`` + 0 Todos wurde als „veraltet" gemeldet.
    """
    for key in ("todos", "improvements"):
        items = curated.get(key)
        if isinstance(items, list) and any(isinstance(i, dict) for i in items):
            return True
    phases = curated.get("phases")
    if isinstance(phases, list):
        for phase in phases:
            if isinstance(phase, dict) and str(phase.get("status", "")).lower() != "done":
                return True
    return False


def curated_is_stale(curated: dict[str, Any], *, age_days: int | None = None) -> bool:
    """Ungepflegt-Alarm NUR für offene kuratierte Punkte jenseits der Schwelle.

    Reine Chronik (alles ``done``) veraltet nie; unbekanntes Alter erfindet
    keine Alterung.
    """
    if age_days is None or age_days <= CURATED_STALE_DAYS:
        return False
    return has_open_curated_items(curated)


__all__ = [
    "CURATED_STALE_DAYS",
    "TERMINAL_VERDICTS",
    "build_live_board",
    "curated_is_stale",
    "has_open_curated_items",
    "open_preregs",
]
