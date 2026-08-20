# shellcheck shell=bash
# scripts/lib/pi_deploy_verdict.sh — aus Deploy-Fakten ein Urteil machen.
# Zum Sourcen gedacht (definiert nur Funktionen, keine Seiteneffekte).
#
# WARUM (2026-08-20, real passiert). `kai_deploy.sh` rief den Unit-Sync so auf:
#
#     pi_unit_sync_apply || echo "unit-sync: rc=$? (10=zurueckgestellt, 1=Fehler)"
#
# Der Sync scheiterte an `sudo: a password is required` und gab rc=1 zurueck.
# `|| echo` machte daraus einen Text — und einen Erfolg. Danach lief der
# /health-Smoke, fand erwartungsgemaess 200 (der Server war nie angefasst worden)
# und der Deploy meldete Gruen. Alle 24 Unit-Dateien blieben divergent, und
# niemand hatte einen Grund nachzusehen.
#
# Zwei getrennte Krankheiten in dieser einen Zeile:
#
#   1. `|| echo` verwandelt jeden Fehlschlag in eine Notiz. Der Exit-Code, das
#      einzige Signal, das ein Aufrufer maschinell lesen kann, geht verloren.
#   2. `/health=200` wurde als URTEIL gelesen, obwohl es nur eine NACHBEDINGUNG
#      ist. Es beweist, dass der Server laeuft — nicht, dass das Deploy ankam.
#      Beim Unit-Drift beweist es sogar nachweislich nichts: Unit-Dateien
#      beruehren den laufenden uvicorn ueberhaupt nicht.
#
# Die Trennung, die daraus folgt und die dieses Modul haelt:
#
#     Fakten  ->  pi_deploy_reasons  ->  Tokens  ->  pi_deploy_verdict  ->  rc
#
# Beide Schritte sind rein (keine ssh-, sudo- oder Dateizugriffe) und damit in CI
# pruefbar. `kai_deploy.sh` sammelt nur noch ein und gibt weiter.

# Kanonische Exit-Codes. Ein Aufrufer darf sich darauf verlassen:
#   0  DEPLOY_SUCCESS — gemerged, keine Abweichung, Nachbedingungen erfuellt
#   10 DEPLOY_HOLD    — technisch heil, aber ein Teil braucht den Operator
#   1  DEPLOY_FAILED  — etwas ist wirklich kaputt
PI_DEPLOY_RC_SUCCESS=0
PI_DEPLOY_RC_HOLD=10
PI_DEPLOY_RC_FAILED=1

# Schweregrad eines Grund-Tokens. Bewusst mit `*) failed` als Default:
# ein unbekanntes Token darf NIE stillschweigend zu HOLD oder SUCCESS
# abrutschen. Wer einen neuen Grund einfuehrt, muss ihn hier einordnen —
# sonst faellt er auf die strengste Stufe.
_pi_deploy_severity() {
    case "${1:-}" in
        SYSTEMD_CHANGE_REQUIRES_OPERATOR | SYSTEMD_DRIFT_PREEXISTING | \
        SYSTEMD_DRIFT_UNKNOWN | WRITER_FREEZE_DEFERRED)
            echo hold
            ;;
        *)
            echo failed
            ;;
    esac
}

# Fakten -> Grund-Tokens, ein Token je Zeile. Keine Ausgabe = nichts zu melden.
#
#   $1 health_code   HTTP-Code von /health ("000" wenn unerreichbar)
#   $2 unit_drift    Anzahl abweichender Unit-Dateien, oder "unknown"
#   $3 drift_caused_by_merge  1 = dieser ff-Merge hat deploy/systemd/ angefasst
#   $4.. weitere Tokens, die der Aufrufer schon kennt (z.B. RESTART_FAILED)
pi_deploy_reasons() {
    local health="${1:-000}"
    local drift="${2:-unknown}"
    local caused="${3:-0}"
    # Nicht `shift 3 || true`: bei weniger als drei Argumenten blieben die
    # Fakten stehen und wuerden als Zusatz-Tokens fehlgedeutet.
    if (( $# > 3 )); then shift 3; else set --; fi

    local token
    for token in "$@"; do
        [[ -n "$token" ]] && echo "$token"
    done

    # Nachbedingung, nicht Urteil: ein toter /health ist ein echter Fehlschlag,
    # ein lebender beweist fuer sich genommen gar nichts.
    [[ "$health" == "200" ]] || echo "HEALTH_NOT_200:${health:-unset}"

    case "$drift" in
        0) ;;
        '' | *[!0-9]*)
            # Nicht messbar ist NICHT dasselbe wie null. Der Deploy ist deshalb
            # nicht kaputt — aber "Units sind synchron" darf niemand behaupten.
            echo "SYSTEMD_DRIFT_UNKNOWN"
            ;;
        *)
            if [[ "$caused" == "1" ]]; then
                echo "SYSTEMD_CHANGE_REQUIRES_OPERATOR:$drift"
            else
                echo "SYSTEMD_DRIFT_PREEXISTING:$drift"
            fi
            ;;
    esac
}

# Grund-Tokens -> Urteil. Echo = Verdikt-Token, Rueckgabe = kanonischer Exit-Code.
# Strengster Grund gewinnt: ein echter Fehlschlag darf sich nie hinter einem
# weicheren HOLD verstecken.
pi_deploy_verdict() {
    local token bare worst=success

    for token in "$@"; do
        [[ -n "$token" ]] || continue
        bare="${token%%:*}"
        case "$(_pi_deploy_severity "$bare")" in
            failed)
                worst=failed
                break
                ;;
            hold) [[ "$worst" == success ]] && worst=hold ;;
        esac
    done

    case "$worst" in
        success)
            echo DEPLOY_SUCCESS
            return "$PI_DEPLOY_RC_SUCCESS"
            ;;
        hold)
            echo DEPLOY_HOLD
            return "$PI_DEPLOY_RC_HOLD"
            ;;
        *)
            echo DEPLOY_FAILED
            return "$PI_DEPLOY_RC_FAILED"
            ;;
    esac
}

# Menschenlesbare Zeile je Grund. Ein Gate, das kein Mittel nennt, ist nur eine
# Blockade — deshalb steht beim Unit-Drift der konkrete naechste Handgriff dabei.
pi_deploy_explain() {
    local token bare value
    for token in "$@"; do
        [[ -n "$token" ]] || continue
        bare="${token%%:*}"
        value="${token#*:}"
        [[ "$value" == "$token" ]] && value=""
        case "$bare" in
            HEALTH_NOT_200)
                echo "FEHLER: /health liefert '$value' statt 200 — der Dienst ist nach dem Deploy nicht gesund."
                ;;
            SYSTEMD_CHANGE_REQUIRES_OPERATOR)
                echo "HOLD: dieser Merge aendert $value Unit-Datei(en). Unit-Dateien sind bewusst"
                echo "      operator-privilegiert (der Broker kopiert keine Dateien nach /etc) —"
                echo "      der Deploy KANN sie nicht anwenden und behauptet es deshalb auch nicht."
                ;;
            SYSTEMD_DRIFT_PREEXISTING)
                echo "HOLD: $value Unit-Datei(en) weichen von /etc/systemd/system ab (nicht durch"
                echo "      diesen Merge verursacht — der Drift lag schon vorher an)."
                ;;
            SYSTEMD_DRIFT_UNKNOWN)
                echo "HOLD: Unit-Abgleich nicht messbar — 'synchron' waere eine unbelegte Behauptung."
                ;;
            WRITER_FREEZE_DEFERRED)
                echo "HOLD: Writer-Freeze aktiv, Unit(s) bewusst zurueckgestellt statt halb angewendet."
                ;;
            *)
                echo "FEHLER: $bare${value:+ ($value)}"
                ;;
        esac
    done
}
