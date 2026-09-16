#!/usr/bin/env bash
# .env-Sicherung anlegen und alte Sicherungen auf die neuesten N kuerzen.
#
# WARUM: Am 16.09.2026 lagen auf dem Pi 79 Klartext-Kopien der .env (Mai bis
# September), die meisten mit noch gueltigen Secrets. Jede Sitzung sicherte vor
# einem Eingriff, keine raeumte auf. Dieser Helfer macht beides in einem Schritt.
#
# REGEL: Vor jeder .env-Aenderung NUR ueber diesen Helfer sichern, nie per `cp`.
#
# Aufruf:
#   scripts/env_backup.sh <label>      Sicherung .env.bak-<UTC>-<label>, dann kuerzen
#   scripts/env_backup.sh --prune-only nur kuerzen
#
# Umgebung:
#   KAI_ENV_BACKUP_ROOT  Verzeichnis mit der .env (Default: Repo-Wurzel)
#   KAI_ENV_BACKUP_KEEP  Anzahl behaltener Sicherungen (Default 3, mindestens 1)
#
# Exit: 0 ok · 2 Aufruf/Konfiguration falsch · 3 keine .env · 4 Kopie gescheitert
# Gibt nie Datei-Inhalte aus.

set -uo pipefail

ROOT="${KAI_ENV_BACKUP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
KEEP="${KAI_ENV_BACKUP_KEEP:-3}"
ARG="${1:-}"

if [[ ! "$KEEP" =~ ^[0-9]+$ ]] || (( KEEP < 1 )); then
    echo "env_backup: KAI_ENV_BACKUP_KEEP muss >= 1 sein" >&2
    exit 2
fi

if [[ -z "$ARG" ]]; then
    echo "Aufruf: env_backup.sh <label> | --prune-only" >&2
    exit 2
fi

if [[ "$ARG" != "--prune-only" ]]; then
    if [[ ! "$ARG" =~ ^[a-z0-9][a-z0-9-]{0,39}$ ]]; then
        echo "env_backup: Label nur [a-z0-9-], max. 40 Zeichen" >&2
        exit 2
    fi
    if [[ ! -f "$ROOT/.env" ]]; then
        echo "env_backup: keine .env unter $ROOT" >&2
        exit 3
    fi
    target="$ROOT/.env.bak-$(date -u +%Y%m%dT%H%M%SZ)-$ARG"
    if ! ( umask 077 && cp "$ROOT/.env" "$target" ); then
        echo "env_backup: Kopie gescheitert" >&2
        exit 4
    fi
    chmod 600 "$target"
    echo "env_backup: angelegt $(basename "$target")"
fi

# Neueste zuerst (mtime). Nur regulaere Dateien direkt in ROOT; .env und
# .env.example passen nicht auf das Muster.
mapfile -t backups < <(
    find "$ROOT" -maxdepth 1 -type f \( -name '.env.bak*' -o -name '.env.backup*' \) \
        -printf '%T@\t%p\n' | sort -rn | cut -f2-
)

removed=0
for (( i = KEEP; i < ${#backups[@]}; i++ )); do
    f="${backups[$i]}"
    if command -v shred >/dev/null 2>&1; then
        shred -u "$f" || rm -f "$f"
    else
        rm -f "$f"
    fi
    removed=$((removed + 1))
done

kept=$(( ${#backups[@]} < KEEP ? ${#backups[@]} : KEEP ))
echo "env_backup: behalten=$kept entfernt=$removed"
exit 0
