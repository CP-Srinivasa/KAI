# shellcheck shell=bash
# scripts/lib/pi_unit_sync.sh — Unit-Dateien vom Checkout nach /etc/systemd/system
# abgleichen. Zum Sourcen gedacht (definiert nur Funktionen, keine Seiteneffekte).
#
# WARUM (2026-08-18, zweimal an einem Tag beobachtet): `kai_deploy.sh` zieht den
# Checkout per ff-merge nach, fasst Unit-Dateien aber NICHT an. Wer eine
# `.timer`/`.service` in einer PR ändert, hat danach die neue Datei im Repo und
# den ALTEN Zustand im laufenden systemd — ohne jeden Hinweis. Beide
# Kadenz-Änderungen des Tages (kai-premium-healthcheck 60→300 s,
# kai-oracle-earnings-booking 10→60 min) waren nach dem Deploy committet, aber
# nicht live; erst ein manuelles `cp` + `daemon-reload` hat sie wirksam gemacht.
# Das ist dieselbe Krankheit wie „Code auf Platte != Code im Prozess", nur eine
# Ebene tiefer.
#
# GRENZEN — bewusst eng, weil das hier mit sudo auf einem laufenden System
# arbeitet:
#
#   * `.service` wird kopiert und `daemon-reload`-t, aber NIE neu gestartet.
#     Ein Deploy-Restart mitten im Tick hat am 17.08. `kai-paper-trading` mit
#     SIGTERM getötet, `Failed with result 'signal'` erzeugt und über OnFailure=
#     einen Fehlalarm ausgelöst. Timer-getriebene Oneshots lesen die neue Datei
#     ohnehin beim nächsten Start.
#   * `.timer` MUSS neu gestartet werden — systemd übernimmt einen geänderten
#     Zeitplan sonst nicht. Das läuft durch `paper_writer_freeze_guard_restart`.
#   * Verweigert der Freeze-Guard, wird die Datei auch NICHT kopiert. Halb
#     angewendet wäre schlimmer als gar nicht: Datei neu, Zeitplan alt, und
#     niemand sieht den Unterschied.
#   * Units, die es nur im Repo gibt, werden kopiert, aber NICHT enabled/gestartet
#     — das ist Sache von `pi_install_systemd.sh`.
#   * Units, die es nur in /etc gibt, werden NUR GEMELDET, nie gelöscht.

_PI_UNIT_SYNC_SRC_DEFAULT="deploy/systemd"
_PI_UNIT_SYNC_DST_DEFAULT="/etc/systemd/system"

# Basenames aller Unit-Dateien, die sich zwischen Quelle und Ziel unterscheiden
# oder im Ziel fehlen. Eine Zeile je Unit, Präfix sagt was los ist:
#     DIFF   <name>   Inhalt weicht ab
#     NEW    <name>   im Ziel nicht vorhanden
#     ORPHAN <name>   nur im Ziel (wird nie automatisch entfernt)
pi_unit_sync_diff() {
    local src="${1:-$_PI_UNIT_SYNC_SRC_DEFAULT}"
    local dst="${2:-$_PI_UNIT_SYNC_DST_DEFAULT}"
    local path base

    [[ -d "$src" ]] || return 0

    for path in "$src"/*; do
        [[ -f "$path" ]] || continue
        base="$(basename "$path")"
        case "$base" in
            *.service | *.timer | *.socket | *.target | *.path) ;;
            *) continue ;;
        esac
        if [[ ! -e "$dst/$base" ]]; then
            echo "NEW $base"
        elif ! cmp -s "$path" "$dst/$base"; then
            echo "DIFF $base"
        fi
    done

    for path in "$dst"/kai-*; do
        [[ -f "$path" ]] || continue
        base="$(basename "$path")"
        # Nur echte Unit-Endungen. In /etc/systemd/system liegen auf der Pi
        # acht `.bak*`-Sicherungen aus frueheren Handeingriffen; systemd
        # ignoriert sie ohnehin. Sie bei JEDEM Deploy zu melden waere genau
        # das Rauschen, gegen das der Rest dieser Session gearbeitet hat.
        case "$base" in
            *.service | *.timer | *.socket | *.target | *.path) ;;
            *) continue ;;
        esac
        [[ -e "$src/$base" ]] || echo "ORPHAN $base"
    done
}

# Abgleich anwenden. Gibt eine Zeile je Aktion aus. Rückgabe:
#     0  alles angewendet (oder nichts zu tun)
#     10 mindestens eine Unit wurde wegen Writer-Freeze zurückgestellt
#     1  ein Kopier-/Reload-Schritt ist fehlgeschlagen
#
# `PI_UNIT_SYNC_DRY_RUN=1` meldet nur, ohne zu schreiben.
# `PI_UNIT_SYNC_SUDO` überschreibt den Privilegien-Präfix (Tests setzen "").
pi_unit_sync_apply() {
    local src="${1:-$_PI_UNIT_SYNC_SRC_DEFAULT}"
    local dst="${2:-$_PI_UNIT_SYNC_DST_DEFAULT}"
    local sudo_cmd="${PI_UNIT_SYNC_SUDO-sudo -n}"
    local dry="${PI_UNIT_SYNC_DRY_RUN:-0}"
    local systemctl_cmd="${PI_UNIT_SYNC_SYSTEMCTL:-systemctl}"

    local changed=() timers=() deferred=() line kind base rc=0
    while read -r kind base; do
        [[ -n "$base" ]] || continue
        case "$kind" in
            DIFF | NEW) changed+=("$base") ;;
            ORPHAN) echo "unit-sync: ORPHAN (nur in $dst, wird NICHT entfernt): $base" ;;
        esac
    done < <(pi_unit_sync_diff "$src" "$dst")

    if (( ${#changed[@]} == 0 )); then
        echo "unit-sync: keine Abweichung ($src == $dst)"
        return 0
    fi

    # Timer erst durch den Freeze-Guard: ein geschützter Writer darf durch einen
    # Deploy nicht neu scharf gestellt werden.
    for base in "${changed[@]}"; do
        [[ "$base" == *.timer ]] || continue
        if declare -F paper_writer_freeze_guard_restart >/dev/null 2>&1; then
            if ! paper_writer_freeze_guard_restart "$base" >/dev/null 2>&1; then
                deferred+=("$base")
                continue
            fi
        fi
        timers+=("$base")
    done

    for base in "${changed[@]}"; do
        for line in "${deferred[@]}"; do
            [[ "$line" == "$base" ]] && continue 2
        done
        if [[ "$dry" == "1" ]]; then
            echo "unit-sync: WUERDE kopieren: $base"
            continue
        fi
        if $sudo_cmd cp "$src/$base" "$dst/$base"; then
            echo "unit-sync: kopiert: $base"
        else
            echo "unit-sync: FEHLER beim Kopieren: $base" >&2
            rc=1
        fi
    done

    for base in "${deferred[@]}"; do
        echo "unit-sync: ZURUECKGESTELLT (Writer-Freeze aktiv, Datei NICHT kopiert): $base" >&2
    done

    if [[ "$dry" != "1" ]]; then
        $sudo_cmd "$systemctl_cmd" daemon-reload || { echo "unit-sync: daemon-reload FEHLGESCHLAGEN" >&2; rc=1; }
        echo "unit-sync: daemon-reload"
        for base in "${timers[@]}"; do
            if $sudo_cmd "$systemctl_cmd" restart "$base"; then
                echo "unit-sync: Timer neu gestartet (Zeitplan uebernommen): $base"
            else
                echo "unit-sync: FEHLER beim Timer-Restart: $base" >&2
                rc=1
            fi
        done
    fi

    # .service-Dateien bewusst ohne Restart — siehe Kopfkommentar.
    for base in "${changed[@]}"; do
        [[ "$base" == *.service ]] || continue
        echo "unit-sync: $base aktualisiert, KEIN Restart (naechster Start liest die neue Datei)"
    done

    (( ${#deferred[@]} > 0 )) && return 10
    return "$rc"
}
