# shellcheck shell=bash
# scripts/lib/pi_install_guard.sh — trennt Provisionierung von Live-Aenderung.
#
# BEFUND 2026-08-21 (Operator, am Code bestaetigt): `pi_install_systemd.sh`
# kopiert in einer Schleife JEDE Unit per `install -m 0644` nach
# `/etc/systemd/system` — und zwar VOR dem Broker-Block. Wer das Skript benutzt,
# um den Privilegien-Broker zu installieren, wendet damit als Nebenwirkung 24
# divergente Unit-Dateien an:
#
#   * ohne Sicherung           (kein Rueckweg)
#   * ohne Freeze-Guard        (ein eingefrorener Writer wird ueberschrieben)
#   * ohne Beweise             (kein cmp, kein NextElapse-Check)
#   * ohne Rollback            (halb angewendet bleibt halb angewendet)
#
# Genau dafuer existiert `scripts/pi_apply_systemd_units.sh`. Der Installer
# wuerde ihn stillschweigend umgehen.
#
# DIE UNTERSCHEIDUNG, die dieses Modul zieht:
#
#   frischer Host       ->  im Ziel liegt nichts oder nichts Abweichendes
#                           => Massenkopie ist genau richtig, das ist der
#                              eigentliche Zweck des Installers
#
#   laufendes System    ->  im Ziel liegen abweichende Units
#                           => Massenkopie ist eine LIVE-AENDERUNG und gehoert
#                              in den Operator-Pfad mit Backup und Beweis
#
# Der Installer wird also nicht entwertet — er darf weiterhin provisionieren.
# Er darf nur nicht mehr unbemerkt zum Deployment-Werkzeug werden.

# 0 = Massenkopie zulaessig (frisch oder deckungsgleich)
# 10 = Drift vorhanden -> Operator-Pfad benutzen
pi_install_units_allowed() {
    local src="${1:?src}" dst="${2:?dst}"
    local drift
    drift="$(pi_install_units_drift "$src" "$dst")"
    [[ -z "$drift" ]] && return 0
    return 10
}

# Basenames der Units, die im Ziel EXISTIEREN und ABWEICHEN. Fehlende Units
# zaehlen NICHT als Drift: sie neu anzulegen ist Provisionierung, nicht
# Ueberschreiben — es gibt nichts zu sichern und nichts zu verlieren.
pi_install_units_drift() {
    local src="${1:?src}" dst="${2:?dst}"
    local path base

    [[ -d "$src" ]] || return 0

    for path in "$src"/*; do
        [[ -f "$path" ]] || continue
        base="$(basename "$path")"
        case "$base" in
            *.service | *.timer | *.socket | *.target | *.path) ;;
            *) continue ;;
        esac
        [[ -e "$dst/$base" ]] || continue
        cmp -s "$path" "$dst/$base" || echo "$base"
    done
}

# Die Meldung, die den Operator auf den richtigen Pfad schickt. Ein Gate, das
# kein Mittel nennt, ist nur eine Blockade.
pi_install_units_refusal() {
    local count="${1:-0}"
    cat >&2 <<'REFUSAL'
ABGELEHNT: im Ziel liegen abweichende Unit-Dateien.

Eine Massenkopie waere hier keine Provisionierung, sondern eine LIVE-AENDERUNG
— ohne Sicherung, ohne Freeze-Guard, ohne Beweise, ohne Rueckweg.

Der dafuer gebaute Weg:

    bash scripts/pi_apply_systemd_units.sh --dry-run
    bash scripts/pi_apply_systemd_units.sh

Nur den Privilegien-Broker installieren (beruehrt keine Unit):

    sudo bash scripts/pi_install_systemd.sh --broker-only

Wenn du wirklich provisionieren willst und weisst, dass es keinen Rueckweg gibt:

    --force-units
REFUSAL
    echo "abweichende Units: $count" >&2
}
