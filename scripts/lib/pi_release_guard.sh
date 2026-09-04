# shellcheck shell=bash
# scripts/lib/pi_release_guard.sh — erkennt release-gebundene Units und prueft,
# ob ihr Release-Ziel auf DIESEM Host ein aufgeloestes Release ist. Zum Sourcen
# gedacht (definiert nur Funktionen, keine Seiteneffekte).
#
# VORFALL 2026-09-04: `pi_apply_systemd_units.sh` hat die fuenf release-
# gebundenen Units (WorkingDirectory=/home/kai/current, ExecStart aus
# /home/kai/current/.venv) nach /etc/systemd/system kopiert. Auf der Pi gab es
# `current` nicht — der Cutover war nicht vollzogen. Der naechste
# `restart kai-server` scheiterte mit status=200/CHDIR, agent-worker und
# entry-watch als Abhaengige; ~10 min Ausfall, Restore aus /var/backups/kai-units.
#
# Der Installer-Guard aus #855 (`assert_release_ready`) sass NUR vor
# `enable --now`. Kopieren ist bei diesen Units aber nicht folgenlos: die Datei
# in /etc wird beim naechsten Restart gelesen, egal wer ihn ausloest. Deshalb
# liegt das Kriterium jetzt HIER, und Installer wie Unit-Sync fragen dieselbe
# Funktion — zwei Implementierungen desselben Guards waeren zwei Wahrheiten.
#
# KRITERIUM (aus #855 uebernommen, plus das Binary, das ExecStart startet):
#
#   release-gebunden  = die Unit fuehrt `runtime-exec` und nennt `--repo <ziel>`
#                       (dieselbe Quelle wie `expected_attesting_units`), oder
#                       ihr WorkingDirectory endet auf `/current` — der Vorfall
#                       war ein CHDIR, WorkingDirectory allein reicht dafuer.
#   Release aktiv     = <ziel> ist ein Verzeichnis (Symlink aufgeloest)
#                     + <ziel>/release.json       (pi_make_release.sh, Stufe 5)
#                     + <ziel>/.venv/bin/python   (pi_make_release.sh, Stufe 3)
#
# Was hier NICHT geprueft wird: der Baum-Hash. Das ist `verify_release` im
# Python-Modul und Sache von `pi_activate_release.sh`. Ein Shell-Guard vor dem
# Kopieren einer Unit-Datei muss nur wissen, ob der Start ueberhaupt landen kann.

# Release-Ziel einer Unit-Datei auf stdout; leer, wenn die Unit nicht
# release-gebunden ist. Rueckgabe immer 0 — "nicht gebunden" ist kein Fehler,
# und die Aufrufer im Installer laufen unter `set -e`.
pi_release_unit_target() {
    local file="${1:?unit file}" target=""
    [ -f "$file" ] || return 0
    if grep -q "runtime-exec" "$file" 2>/dev/null; then
        target="$(awk '{for(i=1;i<=NF;i++) if($i=="--repo") {print $(i+1); exit}}' "$file")"
    fi
    if [ -z "$target" ]; then
        target="$(sed -n 's#^WorkingDirectory=\(.*/current\)[[:space:]]*$#\1#p' "$file" | head -n 1)"
    fi
    if [ -n "$target" ]; then
        printf '%s\n' "$target"
    fi
    return 0
}

# Basenames aller release-gebundenen `.service`-Dateien in <dir>.
pi_release_bound_units() {
    local dir="${1:?unit dir}" path
    for path in "$dir"/*.service; do
        [ -f "$path" ] || continue
        if [ -n "$(pi_release_unit_target "$path")" ]; then
            basename "$path"
        fi
    done
    return 0
}

# 0, wenn <ziel> ein aktives, aufgeloestes Release ist. Sonst 1 und der Grund
# auf stdout — ein Guard, der nur "1" sagt, schickt den Operator raten.
pi_release_active_reason() {
    local target="${1:-}"
    if [ -z "$target" ]; then
        echo "kein Release-Ziel in der Unit"
        return 1
    fi
    if [ ! -d "$target" ]; then
        echo "$target existiert nicht"
        return 1
    fi
    if [ ! -f "$target/release.json" ]; then
        echo "$target ohne release.json"
        return 1
    fi
    if [ ! -f "$target/.venv/bin/python" ]; then
        echo "$target ohne .venv/bin/python"
        return 1
    fi
    return 0
}

# Der Ausweg, den jede Verweigerung nennen muss. Ein Gate ohne Mittel ist nur
# eine Blockade.
pi_release_hint() {
    echo "Erst  bash scripts/pi_make_release.sh  und  bash scripts/pi_activate_release.sh,"
    echo "dann erneut anwenden. Ein Start in einen leeren Pfad erzeugt tote Dienste (200/CHDIR)."
}
