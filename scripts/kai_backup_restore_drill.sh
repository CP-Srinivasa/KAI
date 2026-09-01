#!/usr/bin/env bash
# KAI restore drill for encrypted artifact backups.
#
# The backup timer proves only that an encrypted blob was written. This drill
# proves the blob can be decrypted, unpacked, and matched against the current
# curated source contract from scripts/kai_backup_artifacts.sh. It never writes
# outside artifacts/ops/backup_drill and its private temporary directory.
#
# Exit codes:
#   0  restore drill passed
#   2  KAI_BACKUP_PASSPHRASE missing
#   3  no archive found
#   4  decrypt/unpack/runtime failure
#   6  restored content differs from expectation

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 4

BACKUP_DIR="$ROOT/artifacts/backups"
PROOF_DIR="$ROOT/artifacts/ops/backup_drill"
AUDIT_FILE="$ROOT/artifacts/backup_audit.jsonl"
BACKUP_SCRIPT="$ROOT/scripts/kai_backup_artifacts.sh"
PYTHON_BIN="${PYTHON_BIN:-python3}"

START_EPOCH="$(date -u +%s)"
TS_UTC="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
PROOF_TS="$(date -u +'%Y-%m-%dT%H-%M-%S.%NZ')"
PROOF_PATH="$PROOF_DIR/$PROOF_TS.json"
HOST="$(hostname 2>/dev/null || printf 'unknown')"

ARCHIVE_ARG=""
ARCHIVE=""
ARCHIVE_SHA256=""
TMP_DIR=""
VALIDATION_JSON=""
STATUS="FAIL"
REASON=""

cleanup() {
    if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT

sha256_of() {
    local path="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$path" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$path" | awk '{print $1}'
    else
        openssl dgst -sha256 "$path" | awk '{print $NF}'
    fi
}

write_proof() {
    local duration_s
    duration_s="$(($(date -u +%s) - START_EPOCH))"
    mkdir -p "$PROOF_DIR"
    if ! "$PYTHON_BIN" - "$PROOF_PATH" "$STATUS" "$REASON" "$ARCHIVE" \
            "$ARCHIVE_SHA256" "$TS_UTC" "$duration_s" "$HOST" \
            "${VALIDATION_JSON:-}" <<'PY'
import json
import os
import sys
from pathlib import Path

proof_path, status, reason, archive, archive_sha256, ts_utc, duration_s, host, validation_path = sys.argv[1:10]
payload = {
    "schema": "backup_restore_drill/v1",
    "ts_utc": ts_utc,
    "status": status,
    "reason": reason,
    "archive": archive,
    "archive_sha256": archive_sha256,
    "expectation_source": "",
    "files_expected": [],
    "files_restored": [],
    "files_missing": [],
    "sha256_mismatch": [],
    # Nicht-Ansprueche: stehen auch dann im Artefakt, wenn die Validierung gar
    # nicht erst lief (fail_push, Passphrase fehlt, openssl fehlt). Ein Beweis,
    # der seine Grenzen nur im Erfolgsfall nennt, nennt sie nicht.
    "global_atomic_point_in_time": "NOT_CLAIMED",
    "off_pi_redundancy": "NOT_CLAIMED",
    "duration_s": int(duration_s),
    "host": host,
}
if validation_path:
    try:
        validation = json.loads(Path(validation_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        validation = {}
    for key in (
        "expectation_source",
        "files_expected",
        "files_restored",
        "files_missing",
        "sha256_mismatch",
        "global_atomic_point_in_time",
        "off_pi_redundancy",
    ):
        if key in validation:
            payload[key] = validation[key]

Path(proof_path).parent.mkdir(parents=True, exist_ok=True)
tmp = proof_path + ".tmp"
Path(tmp).write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
os.replace(tmp, proof_path)
PY
    then
        printf 'ERROR: failed to write proof artifact %s\n' "$PROOF_PATH" >&2
    fi
}

fail() {
    local code="$1"
    REASON="$2"
    STATUS="FAIL"
    write_proof
    exit "$code"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --archive)
            if [[ $# -lt 2 ]]; then
                fail 4 "missing --archive value"
            fi
            ARCHIVE_ARG="$2"
            shift 2
            ;;
        *)
            fail 4 "unknown argument: $1"
            ;;
    esac
done

if [[ -n "$ARCHIVE_ARG" ]]; then
    if [[ "$ARCHIVE_ARG" = /* ]]; then
        ARCHIVE="$ARCHIVE_ARG"
    else
        ARCHIVE="$ROOT/$ARCHIVE_ARG"
    fi
else
    ARCHIVE="$(find "$BACKUP_DIR" -type f -name 'kai_artifacts_*.tar.gz.enc' -print 2>/dev/null | sort | tail -n 1)"
fi

if [[ -z "$ARCHIVE" || ! -f "$ARCHIVE" ]]; then
    ARCHIVE=""
    fail 3 "archive missing"
fi

ARCHIVE_SHA256="$(sha256_of "$ARCHIVE")"

if [[ -z "${KAI_BACKUP_PASSPHRASE:-}" ]]; then
    fail 2 "passphrase missing"
fi

if ! command -v openssl >/dev/null 2>&1; then
    fail 4 "openssl missing"
fi
if ! command -v tar >/dev/null 2>&1; then
    fail 4 "tar missing"
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    fail 4 "python missing"
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kai-backup-restore-drill.XXXXXX")" \
    || fail 4 "tmpdir failed"
TAR_PATH="$TMP_DIR/archive.tar.gz"
EXTRACT_DIR="$TMP_DIR/extracted"
MEMBERS_FILE="$TMP_DIR/members.txt"
VALIDATION_JSON="$TMP_DIR/validation.json"
mkdir -p "$EXTRACT_DIR"

if ! openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
        -in "$ARCHIVE" -out "$TAR_PATH" \
        -pass "env:KAI_BACKUP_PASSPHRASE" >/dev/null 2>&1; then
    fail 4 "decrypt failed"
fi

if ! tar -tzf "$TAR_PATH" >"$MEMBERS_FILE" 2>/dev/null; then
    fail 4 "unpack failed"
fi
if ! "$PYTHON_BIN" - "$MEMBERS_FILE" <<'PY'
import sys
from pathlib import PurePosixPath

for raw in open(sys.argv[1], encoding="utf-8"):
    member = raw.strip()
    path = PurePosixPath(member)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit(1)
PY
then
    fail 4 "unsafe archive paths"
fi

if ! tar -xzf "$TAR_PATH" -C "$EXTRACT_DIR" 2>/dev/null; then
    fail 4 "unpack failed"
fi

if ! "$PYTHON_BIN" - "$ROOT" "$EXTRACT_DIR" "$BACKUP_SCRIPT" "$AUDIT_FILE" \
        "$ARCHIVE" "$VALIDATION_JSON" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
extract_dir = Path(sys.argv[2])
backup_script = Path(sys.argv[3])
audit_file = Path(sys.argv[4])
archive = Path(sys.argv[5])
out = Path(sys.argv[6])


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def array_entries(text: str, name: str) -> list[str]:
    match = re.search(rf"^{name}=\((.*?)^\)", text, re.S | re.M)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def rel(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def _has_historical_expectation(item: dict[str, object]) -> bool:
    """Traegt dieser Ledger-Eintrag den Zustand, der eingepackt WURDE?

    Nur solche Eintraege duerfen einen bereits ausgewaehlten beweisfuehrenden
    Eintrag ersetzen. Die geprueften Schluessel sind exakt die, die
    ``audit_expectation()`` unten auswertet — bewusst dieselbe Liste, damit
    Praedikat und Auswertung nicht auseinanderlaufen koennen.
    """
    for key in ("file_sha256", "files_sha256", "sha256_by_file"):
        value = item.get(key)
        if isinstance(value, dict) and value:
            return True
    files = item.get("files_expected") or item.get("files")
    return isinstance(files, list) and bool(files)


def audit_expectation() -> tuple[list[str], dict[str, str]]:
    archive_name = archive.name
    plain_name = archive_name[:-4] if archive_name.endswith(".enc") else archive_name
    if not audit_file.exists():
        return [], {}
    selected: dict[str, object] | None = None
    for line in audit_file.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("archive") not in {archive_name, plain_name}:
            continue
        # Die spaetere Zeile gewinnt — AUSSER sie wuerde eine beweisfuehrende
        # durch eine beweislose ersetzen (STAB-05D, 2026-08-27).
        #
        # Ohne diese Bedingung genuegt EINE spaetere Zeile mit passendem
        # ``archive`` und ohne Manifest, um die historische Erwartung zu
        # verdraengen. ``audit_expectation()`` liefert dann leer, der Aufrufer
        # faellt auf ``current_expectation()`` zurueck — und der Drill prueft
        # gegen das HEUTIGE Dateisystem statt gegen den Archivinhalt. Kein
        # Crash, kein roter Test, keine Fehlermeldung: die Beweiskraft sinkt
        # still. Genau die Klasse Fehler, die ein Waechter nicht haben darf.
        if (
            selected is not None
            and _has_historical_expectation(selected)
            and not _has_historical_expectation(item)
        ):
            continue
        selected = item
    if selected is None:
        return [], {}

    expected: list[str] = []
    hashes: dict[str, str] = {}
    for key in ("file_sha256", "files_sha256", "sha256_by_file"):
        value = selected.get(key)
        if isinstance(value, dict):
            hashes = {str(k): str(v) for k, v in value.items()}
            expected = sorted(hashes)
            return expected, hashes

    files = selected.get("files_expected") or selected.get("files")
    if isinstance(files, list):
        for item in files:
            if isinstance(item, str):
                expected.append(item)
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                expected.append(item["path"])
                if isinstance(item.get("sha256"), str):
                    hashes[item["path"]] = item["sha256"]
    return sorted(set(expected)), hashes


def current_expectation() -> tuple[list[str], dict[str, str]]:
    text = backup_script.read_text(encoding="utf-8")
    sources = array_entries(text, "DEFAULT_SOURCES")
    source_dirs = array_entries(text, "DEFAULT_SOURCE_DIRS")
    required = set(array_entries(text, "REQUIRED_SOURCES"))
    extras = os.environ.get("KAI_BACKUP_EXTRA_FILES", "").split()

    expected: list[str] = []
    hashes: dict[str, str] = {}
    for name in [*sources, *extras]:
        path = root / name
        if path.is_file():
            expected.append(name)
            hashes[name] = sha256(path)
        elif name in required:
            expected.append(name)

    for name in source_dirs:
        path = root / name
        if not path.is_dir():
            continue
        for child in sorted(path.rglob("*")):
            if child.is_file():
                child_rel = rel(child, root)
                expected.append(child_rel)
                hashes[child_rel] = sha256(child)

    return sorted(set(expected)), hashes


def validate_json_file(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return True


def validate_jsonl_file(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return False
        for line in lines:
            if not line.strip():
                continue
            json.loads(line)
    except json.JSONDecodeError:
        return False
    return True


expected, expected_hashes = audit_expectation()
expectation_source = "audit_manifest"
fail_closed_reason = ""
if not expected:
    # Der Rueckfall auf den Live-Zustand ist noetig fuer Archive aus der Zeit
    # vor dem Manifest (STAB-05c), aber er beantwortet eine SCHWAECHERE Frage:
    # "passt das Archiv zum heutigen Dateisystem?" statt "laesst sich aus
    # diesem Archiv wiederherstellen?".
    #
    # Der Backup-Writer legt das Manifest DOPPELT ab: als Sidecar neben dem
    # Archiv und als ``file_sha256`` in der Ledger-Zeile. Existiert der Sidecar,
    # liefert das Ledger aber keine Erwartung, ist das ein Widerspruch — die
    # Zeile wurde verdraengt oder die beiden Ablagen sind auseinandergelaufen.
    # Ein Waechter darf einen Widerspruch nicht durch eine schwaechere Pruefung
    # ersetzen; hier wird fail-closed abgebrochen (STAB-05D, 2026-08-27).
    sidecar = archive.parent / (archive.name + ".manifest.json")
    if sidecar.is_file():
        expectation_source = "ledger_manifest_missing"
        fail_closed_reason = (
            "manifest sidecar exists but the ledger yielded no historical "
            "expectation for this archive"
        )
    else:
        expected, expected_hashes = current_expectation()
        expectation_source = "current_filesystem"

restored = sorted(rel(path, extract_dir) for path in extract_dir.rglob("*") if path.is_file())
restored_set = set(restored)
missing: list[str] = []
mismatches: list[dict[str, str]] = []

for name in expected:
    restored_path = extract_dir / name
    if name not in restored_set or not restored_path.is_file():
        missing.append(name)
        continue
    if restored_path.stat().st_size == 0:
        missing.append(name)
        continue
    if restored_path.suffix == ".json" and not validate_json_file(restored_path):
        missing.append(name)
        continue
    if restored_path.suffix == ".jsonl" and not validate_jsonl_file(restored_path):
        missing.append(name)
        continue
    expected_hash = expected_hashes.get(name)
    if expected_hash:
        restored_hash = sha256(restored_path)
        if restored_hash != expected_hash:
            mismatches.append(
                {
                    "path": name,
                    "expected": expected_hash,
                    "restored": restored_hash,
                }
            )

payload = {
    "expectation_source": expectation_source,
    "files_expected": expected,
    "files_restored": restored,
    "files_missing": sorted(missing),
    "sha256_mismatch": mismatches,
    # Zwei Dinge, die dieser Drill AUSDRUECKLICH NICHT beweist. Sie stehen im
    # Beweis-Artefakt, damit niemand sie spaeter hineinliest: ein PASS ist ein
    # Beleg fuer genau das, was geprueft wurde, und fuer nichts darueber hinaus.
    #
    # GLOBAL_ATOMIC_POINT_IN_TIME: das Archiv entsteht waehrend das System
    # laeuft. Die enthaltenen Dateien stammen aus einem Zeitfenster, nicht aus
    # einem Augenblick; zwei Stroeme koennen um Sekunden auseinanderliegen.
    # Wer daraus einen konsistenten Systemzustand ableitet, behauptet mehr als
    # gemessen wurde.
    #
    # OFF_PI_REDUNDANCY: geprueft wird ein Archiv AUF dem Pi. Dass eine Kopie
    # ausserhalb existiert und lesbar ist, sagt dieser Lauf nicht — das waere
    # ein eigener Beweis an einem anderen Ort.
    "global_atomic_point_in_time": "NOT_CLAIMED",
    "off_pi_redundancy": "NOT_CLAIMED",
}
out.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")

if fail_closed_reason or missing or mismatches:
    raise SystemExit(1)
PY
then
    fail 6 "content mismatch"
fi

STATUS="PASS"
REASON="ok"
write_proof
exit 0
