#!/usr/bin/env bash
# Installiert die GEPRUEFTE Repo-Fassung nach /usr/local/bin/standby_to_usb.sh.
#
# WOFUER DIESES SKRIPT GEDACHT IST -- UND WOFUER AUSDRUECKLICH NICHT
#
# Gedacht ist es fuer einen EINMALIGEN, MANUELLEN Operator-Schritt:
#
#     sudo bash deploy/bin/install_standby_backup.sh
#
# Der Operator gibt sein Passwort ein, sieht Quelle, Hash, Ziel, Owner und Modus
# in der Ausgabe und kann sie nachpruefen. Der Backup-Vertrag darf nicht per
# Editor auf dem Pi gepflegt werden: eine root-Datei, die niemand testet und die
# von keiner Quelle abgeleitet ist, wird beim naechsten Umbau vergessen -- und
# ein vergessener Backup-Vertrag faellt erst beim Restore auf, also genau dann,
# wenn es zu spaet ist.
#
# NICHT gedacht ist es als Ziel einer NOPASSWD-sudo-Regel. Diese Datei liegt im
# Checkout und ist fuer den `ubuntu`-Benutzer SCHREIBBAR. Eine sudo-Regel darauf
# gaebe root an jeden, der sie vorher editieren kann -- und "ist in Git
# versioniert" ist keine Unix-Rechtebarriere, sondern eine Aussage ueber die
# Nachvollziehbarkeit HINTERHER.
#
#     NOPASSWD_REPO_SCRIPT = FORBIDDEN
#     NOPASSWD_STANDBY_TO_USB_DIRECT = FORBIDDEN
#
# Auch `standby_to_usb.sh` selbst darf keine passwortfreie sudo-Schnittstelle
# werden: es nimmt bewusst KAI_STANDBY_*-Overrides fuer Repo, Current,
# Release-Root, State, USB und Mount-Guard entgegen. Das ist fuer Tests richtig
# und unter frei aufrufbarem sudo eine ganz andere Sicherheitslage.
#
# Sollte spaeter wirklich ein passwortfreier Broker gewuenscht sein, braucht er
# einen eigenen, festen, root-eigenen und fuer `ubuntu` NICHT beschreibbaren
# Pfad, ohne Shell, ohne cp/tee/env, ohne freie Quelle oder Ziel und mit eng
# definierter Argumentmenge. Ein user-modifizierbarer Wrapper taugt nicht als
# Privilege Boundary. Dieser Sprint richtet so etwas nicht ein.
#
# WARUM ES HIER KEINE EINZIGE STELLSCHRAUBE GIBT
#
# Die Vorgaengerfassung las Quelle, Ziel, Owner, Modus UND den erwarteten Hash
# aus der Umgebung (`KAI_INSTALL_*`). Unter `sudo` ist das kein Installer mehr,
# sondern ein generischer Root-Executor: eine gesetzte Quelle plus ein gesetztes
# Ziel schreiben beliebigen Inhalt an einen beliebigen Ort. Der erwartete Hash
# war zudem per Default LEER und wurde dann "nur ausgewiesen, nicht erzwungen"
# -- eine Pruefung, die man durch Weglassen abschaltet, ist keine.
#
# Deshalb: alles festverdrahtet, keine Argumente, keine Umgebungsvariablen, und
# ein fehlender Hash ist ein Fehler, kein Freibrief.
#
# WARUM ZUERST KOPIERT UND DANN GEPRUEFT WIRD
#
# Die Vorgaengerfassung rechnete den Hash der Quelle und uebergab danach DIESELBE
# Quelle an `install` -- zwei getrennte Oeffnungen einer fuer `ubuntu`
# schreibbaren Datei. Zwischen Pruefung und Benutzung passt ein Austausch
# (TOCTOU): geprueft wird A, installiert wird B.
#
# Deshalb wird die Quelle EINMAL in eine root-erzeugte temporaere Datei
# uebernommen, und ab da arbeitet alles nur noch auf diesem Schnappschuss:
# Syntaxpruefung, Hash, Owner, Modus, Umbenennung. Der Inhalt, der gehasht
# wurde, IST der Inhalt, der installiert wird.
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

EXPECT_SHA="$(tr -d '[:space:]' < "$SHA_FILE" | cut -c1-64)"
# Ein unbrauchbarer Pin ist ein Fehler, kein "dann eben ohne Pruefung".
[ ${#EXPECT_SHA} -eq 64 ] || die "Erwarteter Hash unbrauchbar in $SHA_FILE"
case "$EXPECT_SHA" in
    *[!0-9a-f]*) die "Erwarteter Hash ist kein SHA-256 in $SHA_FILE" ;;
esac

# --- ab hier arbeitet alles auf EINEM Schnappschuss --------------------------
SNAP="$(mktemp)" || die "kein temporaerer Schnappschuss moeglich"
STAGE="$DST.installing.$$"
cleanup() { rm -f "$SNAP" "$STAGE"; }
trap cleanup EXIT

# Der einzige Lesevorgang der Quelle.
cp -- "$SRC" "$SNAP" || die "Uebernahme der Quelle fehlgeschlagen: $SRC"

# Eine kaputte Quelle darf nicht an den Zielpfad. Ein Backup-Skript, das mitten
# im Satz aufhoert, faellt erst beim Restore auf.
bash -n "$SNAP" || die "Quelle ist syntaktisch kaputt: $SRC"

ACTUAL_SHA="$(sha256sum "$SNAP" | cut -d' ' -f1)"
[ "$ACTUAL_SHA" = "$EXPECT_SHA" ] \
    || die "SHA_MISMATCH erwartet=$EXPECT_SHA tatsaechlich=$ACTUAL_SHA"

# Rechte auf dem Schnappschuss setzen, bevor er den Zielpfad erreicht: sonst
# gaebe es ein Fenster, in dem die Datei schon da, aber noch nicht root-eigen
# ist. `install` setzt den Modus beim Anlegen, damit auch dazwischen nichts
# offener steht als vorgesehen.
install -m "$MODE" "$SNAP" "$STAGE" || die "install nach $STAGE fehlgeschlagen"
# Kein `|| true`: wer ohne root installiert, bekaeme sonst eine Datei mit
# falschem Eigentuemer und die Meldung INSTALL_OK.
chown "$OWNER" "$STAGE" || die "chown $OWNER fehlgeschlagen (root noetig)"

# Atomar: ein abgebrochener Lauf darf keine halbe Datei am Zielpfad hinterlassen.
mv -f "$STAGE" "$DST" || die "mv nach $DST fehlgeschlagen"
trap - EXIT
rm -f "$SNAP"

echo "INSTALL_OK $DST"
echo "  sha256 $ACTUAL_SHA"
echo "  mode   $MODE"
echo "  owner  $OWNER"
