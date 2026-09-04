#!/usr/bin/env bash
# Installiert die GEPRUEFTE Repo-Fassung nach /usr/local/bin/standby_to_usb.sh.
#
# WARUM HIER ALLES FESTVERDRAHTET IST
#
# Dieses Skript ist dafuer gebaut, spaeter unter einer NOPASSWD-sudo-Regel zu
# stehen. Damit ist es kein gewoehnliches Hilfsskript mehr, sondern eine
# Rechteerweiterung: alles, was es tun KANN, kann jeder tun, der es aufrufen
# darf. Die Vorgaengerfassung nahm Quelle, Ziel, Owner und Modus aus der
# Umgebung entgegen --
#
#     KAI_INSTALL_SRC=/tmp/meins.sh KAI_INSTALL_DST=/usr/local/bin/sudo \
#     KAI_INSTALL_MODE=4755 sudo ./install_standby_backup.sh
#
# -- und waere damit ein generischer Root-Executor gewesen, nur umstaendlicher
# aufgeschrieben. Eine sudo-Regel darauf haette root verschenkt.
#
# Deshalb: KEINE Umgebungsvariable veraendert Quelle, Ziel, Owner oder Modus,
# und es werden keine Argumente angenommen. Genau eine Datei, genau ein Pfad,
# genau ein Modus.
#
# Die Quelle wird ausschliesslich relativ zu DIESEM Skript aufgeloest -- wer sie
# austauschen will, muss den Repo-Baum aendern, und der ist versioniert und
# reviewt. Der erwartete SHA-256 steht daneben in `standby_to_usb.sha256` und
# ist PFLICHT: fehlt die Datei oder passt der Hash nicht, wird nichts
# installiert. Ein Installer ohne Hashpruefung installiert, was gerade dasteht.
#
# Heute wird keine sudo-Regel eingerichtet. Dies ist der kontrollierte Pfad, den
# es dafuer braeuchte -- nicht seine Aktivierung.
set -euo pipefail

# Keine Argumente. Ein Installer, der Argumente nimmt, ist eine Schnittstelle;
# eine Schnittstelle unter sudo ist eine Angriffsflaeche.
if [ "$#" -ne 0 ]; then
    echo "INSTALL_FAIL: dieses Kommando nimmt keine Argumente (bekam $#)" >&2
    exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- festverdrahtet, NICHT ueberschreibbar -----------------------------------
readonly SRC="$HERE/standby_to_usb.sh"
readonly SHA_FILE="$HERE/standby_to_usb.sha256"
readonly DST="/usr/local/bin/standby_to_usb.sh"
readonly OWNER="root:root"
readonly MODE="0755"

die() { echo "INSTALL_FAIL: $*" >&2; exit 1; }

[ -f "$SRC" ] || die "Quelle fehlt: $SRC"
[ -f "$SHA_FILE" ] || die "Erwarteter Hash fehlt: $SHA_FILE"

# Eine kaputte Quelle darf gar nicht erst an den Zielpfad. Ein Backup-Skript,
# das mitten im Satz aufhoert, faellt erst beim Restore auf.
bash -n "$SRC" || die "Quelle ist syntaktisch kaputt: $SRC"

EXPECT_SHA="$(tr -d '[:space:]' < "$SHA_FILE" | cut -c1-64)"
[ ${#EXPECT_SHA} -eq 64 ] || die "Erwarteter Hash unbrauchbar in $SHA_FILE"
ACTUAL_SHA="$(sha256sum "$SRC" | cut -d' ' -f1)"
[ "$ACTUAL_SHA" = "$EXPECT_SHA" ] \
    || die "SHA_MISMATCH erwartet=$EXPECT_SHA tatsaechlich=$ACTUAL_SHA"

# Atomar: erst daneben schreiben, dann umbenennen. Ein abgebrochener Lauf darf
# keine halbe Datei am Zielpfad hinterlassen.
TMP="$DST.installing.$$"
cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

install -m "$MODE" "$SRC" "$TMP" || die "install nach $TMP fehlgeschlagen"
chown "$OWNER" "$TMP" || die "chown $OWNER fehlgeschlagen (root noetig)"
mv -f "$TMP" "$DST" || die "mv nach $DST fehlgeschlagen"
trap - EXIT

echo "INSTALL_OK $DST"
echo "  sha256 $ACTUAL_SHA"
echo "  mode   $MODE"
echo "  owner  $OWNER"
