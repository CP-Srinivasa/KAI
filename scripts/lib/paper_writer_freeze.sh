# shellcheck shell=bash
# scripts/lib/paper_writer_freeze.sh — gemeinsame Paper-Writer-Freeze-Semantik
# für Deploy-/Reactivate-Pfade. Zum Sourcen gedacht (definiert nur Funktionen +
# eine Konstante, keine Seiteneffekte).
#
# Hintergrund (2026-07-12): `pi_install_systemd.sh --reactivate` hat den
# versiegelten Weg-B+-Writer-Freeze blind aufgehoben, weil er nur den Aktiv-
# zustand prüfte. Dieser Helper zentralisiert die Marker-Auswertung, damit
# Installer, Deploy-Wrapper und spätere OPS-Skripte dieselbe Semantik nutzen.
#
# WICHTIG — Fail-Richtung: fail-CLOSED. Anders als der Monitor
# (app/observability/premium_pipeline_health.read_paper_writer_freeze, der bei
# Zweifel NICHT unterdrückt, damit er sich nie stummschaltet), darf ein Deploy
# bei Zweifel KEINEN möglicherweise absichtlich eingefrorenen Writer anfassen.
#
# `paper_writer_freeze_state` — Return-Code (NICHT stdout):
#     0  = nicht eingefroren  (Marker fehlt ODER valides Objekt mit frozen=false)
#     10 = eingefroren         (valides JSON-Objekt mit frozen === true)
#     20 = Marker INVALID      (unlesbar / kein Objekt / frozen fehlt|falscher Typ)
# Aufrufer MUSS 20 als HOLD behandeln: vor jeder Mutation abbrechen, Exit != 0.

# Zentrale Schutzliste — ALLE vier Weg-B+-Writer, auch wenn aktuell nur zwei
# (kai-paper-trading.timer, kai-entry-watch.service) in CRITICAL_REACTIVATE
# stehen. Single source of truth für jeden Guard.
PAPER_WRITER_PROTECTED_UNITS=(
    "kai-paper-trading.timer"
    "kai-real-analysis-paper-feed.timer"
    "kai-tv-auto-promote.timer"
    "kai-entry-watch.service"
)

# Repo-Root: relativ zur eigenen Datei (scripts/lib/ → ../..), NICHT zum CWD.
# `REPO_ROOT` (falls vom Aufrufer gesetzt) hat Vorrang.
_paper_writer_freeze_repo_root() {
    if [[ -n "${REPO_ROOT:-}" ]]; then
        printf '%s' "$REPO_ROOT"
        return 0
    fi
    ( cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd )
}

# Marker-Pfad. `PAPER_WRITER_FREEZE_MARKER` (Test-/OPS-Override) hat Vorrang.
paper_writer_freeze_marker_path() {
    if [[ -n "${PAPER_WRITER_FREEZE_MARKER:-}" ]]; then
        printf '%s' "$PAPER_WRITER_FREEZE_MARKER"
        return 0
    fi
    printf '%s/artifacts/paper_writer_freeze.json' "$(_paper_writer_freeze_repo_root)"
}

# 0 wenn eine Marker-Datei existiert (unabhängig von frozen-Wert). Für die
# Stale-Warnung: Marker vorhanden + state=0 ⇒ valides frozen=false ⇒ veraltet.
paper_writer_freeze_marker_present() {
    [[ -e "$(paper_writer_freeze_marker_path)" ]]
}

# 0 wenn $1 eine geschützte Writer-Unit ist, sonst 1.
paper_writer_is_protected() {
    local unit="$1" u
    for u in "${PAPER_WRITER_PROTECTED_UNITS[@]}"; do
        [[ "$u" == "$unit" ]] && return 0
    done
    return 1
}

paper_writer_freeze_state() {
    local marker py verdict
    marker="$(paper_writer_freeze_marker_path)"
    [[ -e "$marker" ]] || return 0  # kein Marker → nicht eingefroren

    py="$(command -v python3 || command -v python || true)"
    [[ -n "$py" ]] || return 20  # kein Interpreter → fail-closed

    verdict="$("$py" - "$marker" <<'PY' 2>/dev/null
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
except Exception:
    print("INVALID")
    raise SystemExit(0)

if not isinstance(data, dict):
    print("INVALID")
elif data.get("frozen") is True:
    print("FROZEN")
elif data.get("frozen") is False:
    print("NOT_FROZEN")
else:
    # frozen fehlt oder hat falschen Typ → fail-closed
    print("INVALID")
PY
)"

    case "$verdict" in
        FROZEN) return 10 ;;
        NOT_FROZEN) return 0 ;;
        *) return 20 ;;  # INVALID oder leer (python-Fehler) → fail-closed
    esac
}

# Wrapper-Guard für Restart-Pfade (z. B. kai_deploy.sh --restart <units...>).
# Versionierte, getestete Entscheidung — jeder Restart-Wrapper ruft NUR diese
# Funktion, statt die Logik neu zu bauen. Rückgabe:
#   0  = erlaubt (kein Freeze ODER keine geschützten Writer in der Liste)
#   10 = VERWEIGERT (Freeze aktiv UND >=1 geschützter Writer angefragt)
#   20 = Marker INVALID (fail-closed → verweigern)
# Bei 10/20 wird eine erklärende Zeile auf stderr ausgegeben.
paper_writer_freeze_guard_restart() {
    paper_writer_freeze_state
    local st=$?
    if (( st == 20 )); then
        echo "PAPER_WRITER_FREEZE_MARKER_INVALID — Restart fail-closed verweigert." >&2
        return 20
    fi
    (( st == 10 )) || return 0  # nicht eingefroren → erlaubt

    local blocked=() u
    for u in "$@"; do
        paper_writer_is_protected "$u" && blocked+=("$u")
    done
    if (( ${#blocked[@]} > 0 )); then
        echo "PAPER_WRITER_FREEZE_ACTIVE — Restart geschützter Writer verweigert: ${blocked[*]}" >&2
        echo "  Writer-Reaktivierung ist ein eigener, geclaimter OPS-Vorgang (Unfreeze)." >&2
        return 10
    fi
    return 0
}
