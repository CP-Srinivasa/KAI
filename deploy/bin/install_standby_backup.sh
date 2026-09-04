#!/usr/bin/env bash
# Installiert die GEPRUEFTE Repo-Fassung nach /usr/local/bin/standby_to_usb.sh.
#
# Warum es diesen Wrapper gibt: der Backup-Vertrag darf nicht per Editor auf dem
# Pi gepflegt werden. Eine root-Datei, die niemand testet und die von keiner
# Quelle abgeleitet ist, wird beim naechsten Umbau vergessen — und ein
# vergessener Backup-Vertrag faellt erst beim Restore auf, also genau dann, wenn
# es zu spaet ist.
#
# Er ist bewusst ENG: er installiert genau eine Datei an genau einen Pfad, mit
# festem Owner und Modus, und nur wenn ihr SHA-256 dem erwarteten entspricht. Er
# nimmt keine Argumente an, die den Zielpfad oder die Quelle veraendern.
#
# Damit laesst sich spaeter — falls gewuenscht — eine sudo-Regel auf GENAU diesen
# Wrapper legen, statt auf einen Editor, eine Shell, `cp` oder `tee`. Eine
# NOPASSWD-Regel fuer eines dieser vier waere gleichbedeutend mit root, nur
# umstaendlicher aufgeschrieben.
#
#   sudo /usr/local/sbin/kai-install-standby-backup
#
# Heute wird nichts davon eingerichtet. Dieses Skript ist der kontrollierte Pfad,
# den es dafuer braeuchte — nicht seine Aktivierung.
set -euo pipefail

SRC_DEFAULT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/standby_to_usb.sh"
SRC="${KAI_INSTALL_SRC:-$SRC_DEFAULT}"
DST="${KAI_INSTALL_DST:-/usr/local/bin/standby_to_usb.sh}"
OWNER="${KAI_INSTALL_OWNER:-root:root}"
MODE="${KAI_INSTALL_MODE:-0755}"
#: Erwarteter SHA-256 der Quelle. Leer = nur ausweisen, nicht erzwingen.
EXPECT_SHA="${KAI_INSTALL_EXPECT_SHA:-}"

die() { echo "INSTALL_FAIL: $*" >&2; exit 1; }

[ -f "$SRC" ] || die "Quelle fehlt: $SRC"
bash -n "$SRC" || die "Quelle ist syntaktisch kaputt: $SRC"

ACTUAL_SHA="$(sha256sum "$SRC" | cut -d' ' -f1)"
if [ -n "$EXPECT_SHA" ] && [ "$ACTUAL_SHA" != "$EXPECT_SHA" ]; then
    die "SHA_MISMATCH erwartet=$EXPECT_SHA tatsaechlich=$ACTUAL_SHA"
fi

# Atomar: erst daneben schreiben, dann umbenennen. Ein abgebrochener Lauf darf
# keine halbe Datei am Zielpfad hinterlassen — das waere ein Backup-Skript, das
# mitten im Satz aufhoert.
TMP="$DST.installing.$$"
install -m "$MODE" "$SRC" "$TMP" || die "install nach $TMP fehlgeschlagen"
if command -v chown >/dev/null 2>&1; then
    chown "$OWNER" "$TMP" 2>/dev/null || echo "note: chown $OWNER uebersprungen (kein root?)" >&2
fi
mv -f "$TMP" "$DST" || { rm -f "$TMP"; die "mv nach $DST fehlgeschlagen"; }

echo "INSTALL_OK $DST"
echo "  sha256 $ACTUAL_SHA"
echo "  mode   $MODE"
echo "  owner  $OWNER"
