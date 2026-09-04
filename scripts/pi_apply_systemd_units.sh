#!/usr/bin/env bash
# scripts/pi_apply_systemd_units.sh — Unit-Dateien anwenden. Vom OPERATOR, auf
# der Pi, mit Passwort.
#
# WARUM ES DIESEN WEG BRAUCHT: der Deploy misst den Unit-Drift nur noch
# (`pi_deploy_step.sh`, read-only) und meldet DEPLOY_HOLD. Anwenden kann er
# nicht — Unit-Dateien sind bewusst operator-privilegiert. Der Broker
# `kai-service-control` startet Dienste; er kopiert keine Dateien nach /etc, und
# das soll so bleiben: ein kompromittierter `ubuntu`-Prozess koennte sonst eine
# Unit im Arbeitsbaum aendern und sie als root installieren lassen.
# `ExecStart=/bin/bash /home/ubuntu/evil.sh` waere wieder ein passwortfreier
# Root-Codepfad. Der Dateiinhalt IST das Privileg.
#
# Was dieses Skript dem blossen `sudo cp ...` voraus hat:
#
#   * Sicherung JEDER ueberschriebenen Datei, bevor die erste geschrieben wird.
#     Laesst sich nichts sichern, wird nichts angefasst.
#   * Rueckweg, wenn das Anwenden oder ein Beweis scheitert.
#   * BEWEISE statt Behauptungen: Byte-Gleichheit je Datei, `active` je Timer und
#     ein endlicher naechster Termin je Timer. Das Letzte ist der Vorfall vom
#     19.08.: `kai-tv-auto-promote.timer` stand fuenf Wochen auf enabled+active
#     mit `NextElapseUSecMonotonic=infinity`. Ein Timer, der laeuft und trotzdem
#     keinen Termin hat, sieht in jeder anderen Pruefung gesund aus.
#   * KEINE release-gebundene Unit in ein Release, das es hier nicht gibt.
#     VORFALL 2026-09-04: die fuenf Units mit WorkingDirectory=/home/kai/current
#     wurden nach /etc kopiert, `current` existierte nicht, der naechste
#     `restart kai-server` scheiterte mit 200/CHDIR (~10 min Ausfall, Restore
#     aus /var/backups/kai-units). Der Installer-Guard aus #855 sass nur vor
#     `enable --now`. Dieser Weg fragt jetzt dieselbe Bibliothek
#     (`lib/pi_release_guard.sh`), ueberspringt die Unit LAUT
#     (SKIPPED_RELEASE_NOT_ACTIVE <unit>) und endet mit 10 — die uebrigen Units
#     laufen durch, und das Ergebnis nennt die Uebersprungenen beim Namen.
#
# EHRLICHE GRENZE: das ist kein transaktionaler Vorgang. Zwischen erster Kopie
# und letztem Beweis existiert ein Zustand, in dem manche Units neu und manche
# alt sind. Der Rollback ist ein Rueckweg, keine Atomaritaet.
#
# Usage (auf der Pi, im Checkout):
#   bash scripts/pi_apply_systemd_units.sh --dry-run
#   bash scripts/pi_apply_systemd_units.sh
#   bash scripts/pi_apply_systemd_units.sh --yes      # ohne Rueckfrage
#
# Exit: 0 = angewendet und bewiesen · 10 = HOLD: teils wegen Writer-Freeze
#       zurueckgestellt oder als release-gebundene Unit ohne aktives Release
#       uebersprungen (auch im --dry-run) · 1 = gescheitert (Rollback versucht)
set -uo pipefail

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/pi_unit_sync.sh
. "$_here/lib/pi_unit_sync.sh"
# shellcheck source=scripts/lib/paper_writer_freeze.sh
. "$_here/lib/paper_writer_freeze.sh" 2>/dev/null || true

SRC="${PI_UNIT_APPLY_SRC:-deploy/systemd}"
DST="${PI_UNIT_APPLY_DST:-/etc/systemd/system}"
SUDO="${PI_UNIT_APPLY_SUDO-sudo}"
SYSTEMCTL="${PI_UNIT_SYNC_SYSTEMCTL:-systemctl}"
BACKUP_ROOT="${KAI_UNIT_BACKUP_DIR:-/var/backups/kai-units}"
dry=0
assume_yes=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) dry=1; shift ;;
        --yes | -y) assume_yes=1; shift ;;
        --src) SRC="${2:-}"; shift 2 ;;
        --dst) DST="${2:-}"; shift 2 ;;
        *) shift ;;
    esac
done

[ -d "$SRC" ] || { echo "FATAL: $SRC nicht gefunden — im Checkout ausfuehren." >&2; exit 1; }

# ── 1. Was steht an? ────────────────────────────────────────────────────────
diff_out="$(pi_unit_sync_diff "$SRC" "$DST")"
mapfile -t changed < <(printf '%s' "$diff_out" | awk '/^(DIFF|NEW) /{print $2}')

orphans="$(printf '%s' "$diff_out" | sed -n 's/^ORPHAN /  ORPHAN (nur im Ziel, wird nie entfernt): /p')"
[ -n "$orphans" ] && printf '%s\n' "$orphans"

if [ "${#changed[@]}" -eq 0 ]; then
    echo "Nichts zu tun: $SRC == $DST"
    exit 0
fi

# Geschuetzte Writer, die der Freeze-Guard ablehnt, werden von
# `pi_unit_sync_apply` bewusst NICHT kopiert. Wer sie trotzdem in den Beweis
# nimmt, laesst einen absichtlich zurueckgestellten Zustand wie einen Fehlschlag
# aussehen — und wuerde deswegen alles andere zurueckrollen. Deshalb wird
# dieselbe Funktion gefragt, die auch der Sync fragt: eine Kopie des AUFRUFS,
# keine Kopie der LOGIK.
deferred=()
release_skipped=()
apply_set=()
for base in "${changed[@]}"; do
    if [ "${base##*.}" = "timer" ] && declare -F paper_writer_freeze_guard_restart >/dev/null 2>&1; then
        if ! paper_writer_freeze_guard_restart "$base" >/dev/null 2>&1; then
            deferred+=("$base")
            continue
        fi
    fi
    # Release-gebunden, aber das Release gibt es auf diesem Host nicht: nicht
    # kopieren, nicht sichern, nicht beweisen — und laut sagen. Dieselbe
    # Funktion wie im Sync und im Installer, eine Kopie des AUFRUFS.
    target="$(pi_release_unit_target "$SRC/$base")"
    if [ -n "$target" ] && ! reason="$(pi_release_active_reason "$target")"; then
        release_skipped+=("$base")
        echo "SKIPPED_RELEASE_NOT_ACTIVE $base ($reason)"
        continue
    fi
    apply_set+=("$base")
done

echo "Anzuwenden (${#apply_set[@]} von ${#changed[@]}):"
for base in ${apply_set[@]+"${apply_set[@]}"}; do
    printf '%s\n' "$diff_out" | grep -E "^(DIFF|NEW) $base\$" | sed 's/^/  /'
done
if [ "${#deferred[@]}" -gt 0 ]; then
    echo "Zurueckgestellt (Writer-Freeze aktiv): ${deferred[*]}"
fi
if [ "${#release_skipped[@]}" -gt 0 ]; then
    echo "Uebersprungen (kein aktives Release auf diesem Host): ${release_skipped[*]}"
    pi_release_hint
fi

# HOLD ist ein eigener Ausgang: "fertig" waere gelogen, "gescheitert" falsch.
hold_rc=0
if [ "${#deferred[@]}" -gt 0 ] || [ "${#release_skipped[@]}" -gt 0 ]; then
    hold_rc=10
fi

if [ "${#apply_set[@]}" -eq 0 ]; then
    echo "Alles zurueckgestellt/uebersprungen — nichts geschrieben."
    exit 10
fi

if [ "$dry" = "1" ]; then
    echo "--dry-run: nichts geschrieben."
    exit "$hold_rc"
fi

if [ "$assume_yes" != "1" ]; then
    printf 'Anwenden? [j/N] '
    read -r answer
    case "$answer" in
        j | J | y | Y) ;;
        *) echo "Abgebrochen — nichts geschrieben."; exit 1 ;;
    esac
fi

# ── 2. Sichern, BEVOR irgendetwas geschrieben wird ──────────────────────────
stamp="${KAI_UNIT_BACKUP_STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
backup="$BACKUP_ROOT/$stamp"
if ! $SUDO mkdir -p "$backup"; then
    echo "FATAL: $backup nicht anlegbar — ohne Rueckweg wird nichts geschrieben." >&2
    exit 1
fi

restorable=()
created=()
for base in "${apply_set[@]}"; do
    if [ -e "$DST/$base" ]; then
        if $SUDO cp -p "$DST/$base" "$backup/$base"; then
            restorable+=("$base")
        else
            echo "FATAL: $base nicht sicherbar — Abbruch vor der ersten Aenderung." >&2
            exit 1
        fi
    else
        created+=("$base")
    fi
done
echo "Sicherung: $backup (${#restorable[@]} ueberschrieben, ${#created[@]} neu)"

# ── 3. Anwenden ─────────────────────────────────────────────────────────────
PI_UNIT_SYNC_SUDO="$SUDO" PI_UNIT_SYNC_SYSTEMCTL="$SYSTEMCTL" \
    pi_unit_sync_apply "$SRC" "$DST"
apply_rc=$?

# ── 4. Beweise ──────────────────────────────────────────────────────────────
# Beide Next-Elapse-Felder muessen beurteilt werden: systemd fuehrt fuer
# Kalender- und monotone Timer GETRENNTE Werte, und wer nur eines liest, haelt
# die jeweils andere Haelfte des Bestands fuer tot.
_has_future_trigger() {
    local out realtime monotonic value
    out="$("$SYSTEMCTL" show "$1" -p NextElapseUSecRealtime -p NextElapseUSecMonotonic 2>/dev/null)"
    realtime="$(printf '%s' "$out" | sed -n 's/^NextElapseUSecRealtime=//p')"
    monotonic="$(printf '%s' "$out" | sed -n 's/^NextElapseUSecMonotonic=//p')"
    for value in "$realtime" "$monotonic"; do
        case "$value" in
            "" | 0 | infinity | n/a) ;;
            *) return 0 ;;
        esac
    done
    return 1
}

# Ein Timer ohne Termin ist NICHT automatisch kaputt. Laeuft der von ihm
# ausgeloeste Service gerade, hat `OnUnitActiveSec` nichts zum Ankern, und ein
# per `Persistent=` nachgeholter Lauf haelt den Timer ebenso kurz terminlos.
#
# VORFALL 2026-08-21: `kai-tv-auto-promote.timer` bekam mit diesem Apply seine
# Reparatur (`OnCalendar=*:0/5` + `Persistent=true`). 11 ms nach dem
# Timer-Restart holte systemd den seit dem 12.07. verpassten Lauf nach; der
# Beweis mass 3,2 s spaeter mitten im Lauf, las "kein naechster Termin" und
# rollte ALLE 30 Units zurueck — obwohl der Lauf nachweislich durchlief
# (2093 Events geprueft). Der Beweis wartet das Ende des Laufs jetzt ab.
#
# 90 s deckt jeden kurzen Nachholer ab (der obige brauchte 4,0 s). Laeuft der
# Service laenger (kai-shadow-resolver: p50 12,9 min), wird das ausgewiesen
# statt zurueckgerollt — ein aufgeschobener Beweis ist keine Fehlfunktion.
PROOF_WAIT_S="${KAI_UNIT_PROOF_WAIT_S:-90}"

_triggered_unit() {
    "$SYSTEMCTL" show "$1" -p Unit 2>/dev/null | sed -n 's/^Unit=//p'
}

_unit_running() {
    local state
    state="$("$SYSTEMCTL" show "$1" -p ActiveState 2>/dev/null | sed -n 's/^ActiveState=//p')"
    case "$state" in
        active | activating | reloading | deactivating) return 0 ;;
        *) return 1 ;;
    esac
}

# 0 = Termin vorhanden · 2 = kein Termin, aber der Lauf dauert an (kein
# Fehlschlag) · 1 = wirklich terminlos, also der Vorfall vom 19.08.
_proof_future_trigger() {
    local timer="$1" svc waited=0
    svc="$(_triggered_unit "$timer")"
    while :; do
        _has_future_trigger "$timer" && return 0
        { [ -n "$svc" ] && _unit_running "$svc"; } || return 1
        [ "$waited" -lt "$PROOF_WAIT_S" ] || return 2
        sleep 1
        waited=$((waited + 1))
    done
}

proof_failures=()
for base in "${apply_set[@]}"; do
    if ! cmp -s "$SRC/$base" "$DST/$base"; then
        proof_failures+=("$base: Bytes im Ziel weichen weiterhin ab")
        continue
    fi
    echo "beweis: $base byte-gleich"
    [ "${base##*.}" = "timer" ] || continue
    if [ "$("$SYSTEMCTL" is-active "$base" 2>/dev/null)" != "active" ]; then
        proof_failures+=("$base: nicht active")
    else
        _proof_future_trigger "$base"
        case $? in
            0) echo "beweis: $base active mit endlichem naechsten Termin" ;;
            2) echo "beweis: $base active, ausgeloester Lauf dauert nach ${PROOF_WAIT_S}s an — Termin entsteht nach dem Lauf (kein Rollback)" ;;
            *) proof_failures+=("$base: aktiv, aber KEIN naechster Termin (NextElapse leer/infinity)") ;;
        esac
    fi
done

# ── 5. Rueckweg nur, wenn wirklich etwas schiefging ─────────────────────────
# rc=10 heisst "wegen Writer-Freeze zurueckgestellt" — bewusst nicht angewendet
# ist kein Fehlschlag und darf keinen Rollback ausloesen.
if { [ "$apply_rc" != "0" ] && [ "$apply_rc" != "10" ]; } || [ "${#proof_failures[@]}" -gt 0 ]; then
    echo "FEHLGESCHLAGEN (apply_rc=$apply_rc):" >&2
    if [ "${#proof_failures[@]}" -gt 0 ]; then
        for line in "${proof_failures[@]}"; do echo "  $line" >&2; done
    fi
    echo "Rollback aus $backup ..." >&2
    for base in ${restorable[@]+"${restorable[@]}"}; do
        if $SUDO cp -p "$backup/$base" "$DST/$base"; then
            echo "  zurueckgesetzt: $base" >&2
        else
            echo "  ROLLBACK GESCHEITERT: $base — Sicherung liegt in $backup" >&2
        fi
    done
    for base in ${created[@]+"${created[@]}"}; do
        if $SUDO rm -f "$DST/$base"; then
            echo "  entfernt (war neu): $base" >&2
        else
            echo "  ROLLBACK GESCHEITERT: $base nicht entfernbar" >&2
        fi
    done
    $SUDO "$SYSTEMCTL" daemon-reload
    for base in ${restorable[@]+"${restorable[@]}"}; do
        [ "${base##*.}" = "timer" ] && $SUDO "$SYSTEMCTL" restart "$base"
    done
    echo "Rollback beendet. Die Sicherung bleibt unter $backup liegen." >&2
    exit 1
fi

echo "OK: ${#apply_set[@]} Unit(s) angewendet und bewiesen. Sicherung: $backup"
# Das Ergebnis nennt, was NICHT angewendet wurde — kein stilles `continue`
# (FALSE_GREEN_ON_MISSING_ACTIVE_RELEASE = IMPOSSIBLE, Runbook).
if [ "$apply_rc" = "10" ]; then
    hold_rc=10
fi
if [ "${#deferred[@]}" -gt 0 ]; then
    echo "HOLD: ${#deferred[@]} Unit(s) zurueckgestellt (Writer-Freeze aktiv): ${deferred[*]}"
fi
if [ "${#release_skipped[@]}" -gt 0 ]; then
    echo "HOLD: ${#release_skipped[@]} release-gebundene Unit(s) NICHT angewendet (kein aktives Release auf diesem Host): ${release_skipped[*]}"
fi
exit "$hold_rc"
