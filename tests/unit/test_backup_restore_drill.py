from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT / "deploy" / "systemd"
BACKUP_SCRIPT = ROOT / "scripts" / "kai_backup_artifacts.sh"
DRILL_SCRIPT = ROOT / "scripts" / "kai_backup_restore_drill.sh"
PASSPHRASE = "correct horse battery staple for kai backups 2026"


def _directives(path: Path) -> dict[str, dict[str, str]]:
    section = ""
    parsed: dict[str, dict[str, str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            parsed.setdefault(section, {})
            continue
        key, sep, value = line.partition("=")
        assert sep, f"nicht parsebare Unit-Zeile in {path.name}: {raw!r}"
        parsed.setdefault(section, {})[key] = value
    return parsed


def test_backup_artifacts_timer_contract() -> None:
    timer = _directives(SYSTEMD / "kai-backup-artifacts.timer")

    # Der Timer existierte schon (03:47 UTC, RandomizedDelay, kein Requires= — #414);
    # er war auf der Pi nur nie ENABLED. Deshalb bleibt die Datei unveraendert.
    assert timer["Timer"]["OnCalendar"] == "*-*-* 03:47:00"
    assert timer["Timer"]["Persistent"] == "true"
    assert timer["Timer"]["AccuracySec"] == "10min"
    assert timer["Timer"]["RandomizedDelaySec"] == "15min"
    assert "Requires" not in timer.get("Unit", {}), "Timer-Requires-Kaskade (#414)"
    assert timer["Install"]["WantedBy"] == "timers.target"


def test_restore_drill_units_contract() -> None:
    service = _directives(SYSTEMD / "kai-backup-restore-drill.service")
    timer = _directives(SYSTEMD / "kai-backup-restore-drill.timer")

    assert service["Service"]["Type"] == "oneshot"
    assert service["Service"]["User"] == "ubuntu"
    assert service["Service"]["WorkingDirectory"] == "/home/ubuntu/ai_analyst_trading_bot"
    assert service["Unit"]["OnFailure"] == "kai-unit-failure-notify@%n.service"
    assert service["Service"]["TimeoutStartSec"] == "20min"
    assert "scripts/kai_backup_restore_drill.sh" in service["Service"]["ExecStart"]
    read_write = service["Service"]["ReadWritePaths"]
    assert "/home/ubuntu/ai_analyst_trading_bot/artifacts" in read_write
    assert "/tmp" in read_write

    assert timer["Timer"]["OnCalendar"] == "*-*-01 04:10:00"
    assert timer["Timer"]["Persistent"] == "true"
    assert timer["Timer"]["Unit"] == "kai-backup-restore-drill.service"
    assert timer["Install"]["WantedBy"] == "timers.target"


def test_restore_drill_script_static_contract() -> None:
    text = DRILL_SCRIPT.read_text(encoding="utf-8")

    assert "set -uo pipefail" in text
    assert "trap cleanup EXIT" in text
    assert "backup_restore_drill/v1" in text
    assert "KAI_BACKUP_PASSPHRASE" in text
    assert "kai_artifacts_*.tar.gz.enc" in text


def _require_backup_tools() -> None:
    if shutil.which("bash") is None:
        pytest.skip("bash nicht installiert")
    probe = subprocess.run(
        ["bash", "-lc", "command -v openssl >/dev/null && command -v tar >/dev/null"],
        text=True,
        capture_output=True,
        timeout=10,
    )
    if probe.returncode != 0:
        pytest.skip("openssl oder tar nicht in bash-PATH")


def _copy_drill_fixture(root: Path) -> None:
    scripts = root / "scripts"
    scripts.mkdir()
    shutil.copy2(BACKUP_SCRIPT, scripts / "kai_backup_artifacts.sh")
    shutil.copy2(DRILL_SCRIPT, scripts / "kai_backup_restore_drill.sh")


def _write_fixture_sources(root: Path) -> None:
    files = {
        "artifacts/research/prereg_ledger.jsonl": '{"id":"pre","ok":true}\n',
        "artifacts/truth/attestation_ledger.jsonl": '{"id":"att","ok":true}\n',
        "artifacts/research/hypothesis_ledger.jsonl": '{"id":"hyp","n":1}\n',
        "artifacts/research/falsification_verdicts.jsonl": '{"id":"verdict","pass":true}\n',
        "artifacts/research/forecaster_panel/panel.json": '{"panel":"alpha"}\n',
        "DECISION_LOG.md": "# decisions\n\n- fixture\n",
    }
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _run_bash(
    root: Path, command: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.update(env or {})
    return subprocess.run(
        ["bash", "-lc", command],
        cwd=root,
        env=merged,
        text=True,
        capture_output=True,
        timeout=30,
    )


def _make_backup(root: Path) -> Path:
    result = _run_bash(
        root,
        "env "
        f"KAI_BACKUP_PASSPHRASE={shlex.quote(PASSPHRASE)} "
        "KAI_BACKUP_KEEP_DAYS=0 "
        "bash scripts/kai_backup_artifacts.sh",
    )
    assert result.returncode == 0, result.stderr + result.stdout
    archives = sorted((root / "artifacts" / "backups").rglob("kai_artifacts_*.tar.gz.enc"))
    assert len(archives) == 1
    return archives[0]


def _run_drill(
    root: Path, archive: Path | None, passphrase: str | None = PASSPHRASE
) -> subprocess.CompletedProcess[str]:
    (root / "tmp").mkdir(exist_ok=True)
    env_prefix = "env TMPDIR=$PWD/tmp "
    if passphrase is not None:
        env_prefix += f"KAI_BACKUP_PASSPHRASE={shlex.quote(passphrase)} "
    archive_arg = ""
    if archive is not None:
        archive_arg = " --archive " + shlex.quote(archive.relative_to(root).as_posix())
    return _run_bash(root, env_prefix + "bash scripts/kai_backup_restore_drill.sh" + archive_arg)


def _latest_proof(root: Path) -> dict[str, object]:
    proofs = sorted((root / "artifacts" / "ops" / "backup_drill").glob("*.json"))
    assert proofs
    return cast(dict[str, object], json.loads(proofs[-1].read_text(encoding="utf-8")))


def test_restore_drill_passes_and_cleans_tmp(tmp_path: Path) -> None:
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    result = _run_drill(tmp_path, archive)

    assert result.returncode == 0, result.stderr + result.stdout
    proof = _latest_proof(tmp_path)
    assert proof["schema"] == "backup_restore_drill/v1"
    assert proof["status"] == "PASS"
    assert proof["archive_sha256"]
    assert proof["files_restored"] == proof["files_expected"]
    files_expected = cast(list[str], proof["files_expected"])
    assert "artifacts/research/prereg_ledger.jsonl" in files_expected
    assert "artifacts/research/forecaster_panel/panel.json" in files_expected
    assert proof["files_missing"] == []
    assert proof["sha256_mismatch"] == []
    assert list((tmp_path / "tmp").glob("kai-backup-restore-drill.*")) == []


def test_restore_drill_detects_a_corrupted_archive(tmp_path: Path) -> None:
    """Korruption IM ARCHIV ist ein Befund — das ist die Frage eines Restore-Drills.

    Vorher pruefte dieser Test etwas anderes: er aenderte die LEBENDE Quelldatei
    und erwartete FAIL. Das kodierte die falsche Frage („entspricht das Archiv
    dem aktuellen Systemzustand?") statt der richtigen („laesst sich aus diesem
    Archiv wiederherstellen?"). Live auf der Pi (2026-08-27) fuehrte genau das
    zum Fehlalarm: ``trading_loop_audit.jsonl`` war zwischen Backup und Drill um
    5.044 Bytes gewachsen, das Archiv war bit-genau ein Praefix der Live-Datei —
    also einwandfrei — und der Drill meldete trotzdem ``content mismatch``.
    Ein Waechter, der bei jedem Lauf schlaegt, wird abgeschaltet.
    """
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    # Das verschluesselte Archiv beschaedigen (ein Byte in der Mitte kippen).
    blob = bytearray(archive.read_bytes())
    mid = len(blob) // 2
    blob[mid] ^= 0xFF
    archive.write_bytes(bytes(blob))

    result = _run_drill(tmp_path, archive)

    assert result.returncode != 0, "ein beschaedigtes Archiv darf nie PASS melden"
    proof = _latest_proof(tmp_path)
    assert proof["status"] == "FAIL"
    assert list((tmp_path / "tmp").glob("kai-backup-restore-drill.*")) == []


def test_drill_passes_when_a_source_grew_after_the_backup(tmp_path: Path) -> None:
    """Der Regressionstest zum Vorfall: append-only-Wachstum ist kein Befund.

    Der Trading-Loop haengt im Minutentakt an ``trading_loop_audit.jsonl`` an.
    Ein Drill, der gegen die lebende Datei vergleicht, wird dadurch ab dem
    ersten Timer-Lauf JEDEN Monat rot — ohne dass am Backup etwas falsch waere.
    """
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)
    assert archive.exists()

    grown = tmp_path / "artifacts" / "research" / "hypothesis_ledger.jsonl"
    with grown.open("a", encoding="utf-8") as fh:
        fh.write('{"id":"hyp","n":2}\n')

    result = _run_drill(tmp_path, archive)
    proof = _latest_proof(tmp_path)

    assert proof["status"] == "PASS", (
        f"append-only-Wachstum darf kein FAIL sein: {proof.get('reason')} "
        f"/ {proof.get('sha256_mismatch')}"
    )
    assert proof["sha256_mismatch"] == []
    assert result.returncode == 0


def test_backup_manifest_describes_the_archive_content(tmp_path: Path) -> None:
    """Das Manifest beschreibt den ARCHIVINHALT und aendert sich danach nie.

    Genau diese Eigenschaft macht den Drill unabhaengig vom Live-Zustand: er
    prueft, ob das Wiederhergestellte dem entspricht, was eingepackt wurde.
    """
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    _make_backup(tmp_path)

    audit = tmp_path / "artifacts" / "backup_audit.jsonl"
    entry = json.loads(audit.read_text(encoding="utf-8").strip().splitlines()[-1])
    hashes = entry.get("file_sha256")
    assert isinstance(hashes, dict) and hashes, "Audit-Eintrag ohne file_sha256"
    assert "artifacts/research/prereg_ledger.jsonl" in hashes

    for rel, digest in hashes.items():
        source = tmp_path / rel
        if source.is_file():
            assert digest == hashlib.sha256(source.read_bytes()).hexdigest(), rel

    before = dict(hashes)
    ledger = tmp_path / "artifacts" / "research" / "hypothesis_ledger.jsonl"
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write('{"id":"hyp","n":3}\n')
    again = json.loads(audit.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert again["file_sha256"] == before, "Manifest darf sich nachtraeglich nicht aendern"


def test_restore_drill_missing_passphrase_writes_fail_proof(tmp_path: Path) -> None:
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    result = _run_drill(tmp_path, archive, passphrase=None)

    assert result.returncode == 2
    proof = _latest_proof(tmp_path)
    assert proof["status"] == "FAIL"
    assert proof["reason"] == "passphrase missing"
    assert cast(str, proof["archive"]).endswith(".tar.gz.enc")


def test_restore_drill_no_archive_writes_fail_proof(tmp_path: Path) -> None:
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    (tmp_path / "artifacts" / "backups").mkdir(parents=True)

    result = _run_drill(tmp_path, archive=None)

    assert result.returncode == 3
    proof = _latest_proof(tmp_path)
    assert proof["status"] == "FAIL"
    assert proof["reason"] == "archive missing"
    assert proof["archive"] == ""


def test_restore_drill_wrong_passphrase_writes_fail_proof(tmp_path: Path) -> None:
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    result = _run_drill(tmp_path, archive, passphrase="wrong passphrase but still present")

    assert result.returncode == 4
    proof = _latest_proof(tmp_path)
    assert proof["status"] == "FAIL"
    assert proof["reason"] == "decrypt failed"


# ── STAB-05D: Ledger-Identitaet und Shadow-Schutz ────────────────────────────


def _ledger_rows(root: Path) -> list[dict[str, object]]:
    path = root / "artifacts" / "backup_audit.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def test_ledger_ok_row_names_the_final_encrypted_artifact(tmp_path: Path) -> None:
    """Der Ledger muss die Identitaet nennen, die den Lauf UEBERLEBT.

    Bis STAB-05D schrieb er den Pre-Encryption-Staging-Namen und dazu Hash und
    Groesse des verschluesselten Artefakts. Live gemessen 2026-08-27: 0 von 3
    erfolgreichen Laeufen nannten eine existierende Datei, waehrend Hash und
    Bytes 3/3 korrekt waren. Ein Recovery-Prozess, der dem Feld folgt, sucht
    eine Datei, die nach erfolgreichem Backup geloescht ist.
    """
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    ok_rows = [r for r in _ledger_rows(tmp_path) if r.get("status") == "ok"]
    assert ok_rows, "kein ok-Eintrag im Ledger"
    row = ok_rows[-1]

    assert row["archive"] == archive.name
    assert str(row["archive"]).endswith(".tar.gz.enc")
    # Die drei Felder muessen DIESELBE Byte-Identitaet beschreiben.
    named = archive.parent / str(row["archive"])
    assert named.is_file(), "der Ledger nennt eine nicht existierende Datei"
    assert row["sha256"] == hashlib.sha256(named.read_bytes()).hexdigest()
    assert row["bytes"] == named.stat().st_size


def test_failure_rows_keep_the_staging_name(tmp_path: Path) -> None:
    """Gegenprobe gegen Ueber-Korrektur.

    Die sechs uebrigen write_audit-Aufrufe duerfen NICHT mitgeaendert werden:
    zu ihrem Zeitpunkt existiert kein verschluesseltes Artefakt, und sie
    schreiben bewusst leeren Hash und bytes=0. Wer dort ebenfalls auf .enc
    umstellt, laesst den Ledger eine Datei benennen, die nie entstanden ist.
    """
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)

    result = _run_bash(
        tmp_path,
        "env KAI_BACKUP_KEEP_DAYS=0 bash scripts/kai_backup_artifacts.sh",
    )
    assert result.returncode != 0, "ohne Passphrase muss das Backup scheitern"

    rows = [r for r in _ledger_rows(tmp_path) if r.get("status") != "ok"]
    assert rows, "kein Fehl-Eintrag im Ledger"
    for row in rows:
        assert not str(row.get("archive") or "").endswith(".enc"), (
            f"Fehl-Eintrag {row.get('status')} nennt ein .enc-Artefakt, das nie entstand"
        )
        assert row.get("sha256") == ""
        assert row.get("bytes") == 0


def test_a_later_row_without_expectations_cannot_shadow_the_manifest(
    tmp_path: Path,
) -> None:
    """Der eigentliche Silent-Green-Schutz.

    Der Reader nimmt die LETZTE passende Ledger-Zeile. Eine spaetere Zeile ohne
    Manifest wuerde die historische Erwartung verdraengen; ``audit_expectation``
    liefert dann leer und der Drill faellt auf ``current_expectation`` zurueck —
    er prueft gegen das HEUTIGE Dateisystem statt gegen den Archivinhalt.

    Der Test macht das messbar: nach dem Backup entsteht eine NEUE Live-Datei in
    einem Quellverzeichnis. Sie steht nicht im Archiv. Greift die historische
    Erwartung, ist sie kein Teil der Erwartung und der Drill besteht. Faellt der
    Drill auf den Live-Zustand zurueck, erwartet er sie und meldet sie als
    fehlend.
    """
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    ledger = tmp_path / "artifacts" / "backup_audit.jsonl"
    schatten = {
        "ts": "2026-08-27T23:59:59Z",
        "status": "ok",
        "archive": archive.name,
        "sha256": "0" * 64,
        "bytes": 1,
        "remote": "",
        "remote_status": "",
        "note": "spaetere Zeile ohne Manifest",
    }
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(schatten) + "\n")

    neu = tmp_path / "artifacts" / "research" / "forecaster_panel" / "nachtraeglich.json"
    neu.write_text('{"entstanden":"nach dem Backup"}\n', encoding="utf-8")

    result = _run_drill(tmp_path, archive)

    assert result.returncode == 0, result.stderr + result.stdout
    proof = _latest_proof(tmp_path)
    assert proof["status"] == "PASS"
    files_expected = cast(list[str], proof["files_expected"])
    assert "artifacts/research/forecaster_panel/nachtraeglich.json" not in files_expected, (
        "der Drill hat auf den Live-Zustand zurueckgegriffen — die schwaechere "
        "Zeile hat das Manifest verdraengt"
    )
    assert proof["files_missing"] == []


def test_a_later_row_with_expectations_still_wins(tmp_path: Path) -> None:
    """Die Haertung darf den Ledger nicht einfrieren.

    Nur eine BEWEISLOSE Zeile wird abgewiesen. Traegt eine spaetere Zeile selbst
    ein Manifest, muss sie weiterhin gewinnen — sonst waere eine spaetere,
    genauere Korrektur wirkungslos.
    """
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    ledger = tmp_path / "artifacts" / "backup_audit.jsonl"
    spaeter = {
        "ts": "2026-08-27T23:59:59Z",
        "status": "ok",
        "archive": archive.name,
        "sha256": "0" * 64,
        "bytes": 1,
        "file_sha256": {"DECISION_LOG.md": "1" * 64},
        "note": "spaetere Zeile MIT Manifest",
    }
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(spaeter) + "\n")

    result = _run_drill(tmp_path, archive)

    proof = _latest_proof(tmp_path)
    files_expected = cast(list[str], proof["files_expected"])
    assert files_expected == ["DECISION_LOG.md"], (
        "die spaetere Zeile MIT Manifest muss die Erwartung bestimmen"
    )
    assert result.returncode != 0, "der gefaelschte Hash muss als Mismatch auffallen"


def test_ledger_identity_pairing_contract() -> None:
    """Statischer Vertrag ueber ALLE write_audit-Aufrufe.

    Ein Laufzeittest kann nur den Fehlerpfad pruefen, den er ausloesen kann —
    ``fail_tar`` und ``fail_encrypt`` sind ohne kuenstlich zerstoerte Umgebung
    nicht erreichbar. Die Invariante gilt aber fuer jeden Aufruf, deshalb wird
    sie hier am Skripttext geprueft:

        schreibt der Aufruf ENC_SHA/ENC_BYTES  -> muss er ARCHIVE_ENC benennen
        schreibt er leeren Hash und bytes=0    -> darf er KEIN .enc benennen

    Damit faellt sowohl das alte Fehlpaar (Staging-Name + finaler Hash) auf als
    auch die Ueber-Korrektur, die Fehlerpfade auf ein Artefakt umstellt, das zu
    ihrem Zeitpunkt nie entstanden ist.
    """
    # Die geprueften Argumente (status, archive, sha, bytes) stehen alle auf der
    # ERSTEN Zeile eines Aufrufs; Fortsetzungszeilen tragen nur remote und note.
    aufrufe = [
        line.split("write_audit", 1)[1]
        for line in BACKUP_SCRIPT.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("write_audit ")
    ]
    assert len(aufrufe) >= 8, f"unerwartet wenige write_audit-Aufrufe: {len(aufrufe)}"

    mit_enc_hash = 0
    for argumente in aufrufe:
        schreibt_enc_hash = "$ENC_SHA" in argumente
        # Auf die EIGENSCHAFT pruefen, nicht auf den Variablennamen: ein
        # ``${ARCHIVE_NAME}.enc`` benennt ebenfalls das verschluesselte
        # Artefakt, enthaelt aber nie die Zeichenkette ARCHIVE_ENC.
        nennt_enc = "ARCHIVE_ENC" in argumente or ".enc" in argumente
        if schreibt_enc_hash:
            mit_enc_hash += 1
            assert nennt_enc, (
                f"Aufruf schreibt ENC_SHA, benennt aber nicht das finale Artefakt: {argumente!r}"
            )
            assert "$ENC_BYTES" in argumente
        else:
            assert not nennt_enc, (
                f"Aufruf ohne ENC_SHA benennt ein .enc-Artefakt, das zu diesem "
                f"Zeitpunkt nicht existiert: {argumente!r}"
            )
    assert mit_enc_hash == 2, (
        f"erwartet genau 2 Aufrufe mit finalem Hash (fail_push, ok), gefunden {mit_enc_hash}"
    )


def test_proof_names_the_source_of_its_expectation(tmp_path: Path) -> None:
    """Ein PASS aus dem Manifest und ein PASS aus dem Live-Zustand sehen sonst
    identisch aus — obwohl sie verschiedene Fragen beantworten.

    Das Beweis-Artefakt muss deshalb ausweisen, WORAUS die Erwartung stammt.
    Ohne dieses Feld laesst sich `MODERN_RESTORE_CANNOT_SILENTLY_DOWNGRADE`
    nicht belegen, sondern nur behaupten.
    """
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    result = _run_drill(tmp_path, archive)
    assert result.returncode == 0, result.stderr + result.stdout
    assert _latest_proof(tmp_path)["expectation_source"] == "audit_manifest"


def test_proof_marks_the_fallback_when_no_manifest_exists(tmp_path: Path) -> None:
    """Gegenprobe: Archive aus der Zeit vor STAB-05c haben kein Manifest.

    Der Rueckfall bleibt erlaubt — er ist fuer Alt-Archive noetig —, aber er
    muss im Artefakt ERKENNBAR sein. Sonst zaehlt ein alter Drill-Pass wie ein
    neuer, obwohl er nur gegen das heutige Dateisystem geprueft hat.
    """
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    # Das Manifest entfernen und den Ledger auf den Vor-STAB-05c-Zustand
    # zuruecksetzen: Zeile ohne file_sha256, wie sie bis 27.08. geschrieben wurde.
    (archive.parent / (archive.name + ".manifest.json")).unlink(missing_ok=True)
    ledger = tmp_path / "artifacts" / "backup_audit.jsonl"
    alt = [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    for row in alt:
        row.pop("file_sha256", None)
    ledger.write_text(
        "".join(json.dumps(r) + "\n" for r in alt),
        encoding="utf-8",
    )

    result = _run_drill(tmp_path, archive)

    assert result.returncode == 0, result.stderr + result.stdout
    assert _latest_proof(tmp_path)["expectation_source"] == "current_filesystem"


def test_modern_archive_with_orphaned_manifest_fails_closed(tmp_path: Path) -> None:
    """Ein Widerspruch darf nicht durch eine schwaechere Pruefung ersetzt werden.

    Der Writer legt das Manifest doppelt ab: als Sidecar neben dem Archiv UND
    als ``file_sha256`` in der Ledger-Zeile. Existiert der Sidecar, liefert das
    Ledger aber keine Erwartung, ist entweder die Zeile verdraengt worden oder
    die beiden Ablagen sind auseinandergelaufen. Beides ist ein Befund — ein
    Rueckfall auf den Live-Zustand wuerde ihn zudecken.
    """
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    sidecar = archive.parent / (archive.name + ".manifest.json")
    assert sidecar.is_file(), "Vorbedingung: der Writer legt einen Sidecar an"

    # Sidecar bleibt, Ledger-Erwartung verschwindet -> Widerspruch.
    ledger = tmp_path / "artifacts" / "backup_audit.jsonl"
    rows = [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    for row in rows:
        row.pop("file_sha256", None)
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    result = _run_drill(tmp_path, archive)

    assert result.returncode != 0, "der Widerspruch muss FAIL erzeugen, nicht PASS"
    proof = _latest_proof(tmp_path)
    assert proof["expectation_source"] == "ledger_manifest_missing"
    assert proof["status"] != "PASS"


# ---------------------------------------------------------------------------
# Ausdrueckliche Nicht-Ansprueche (G5 Task 3, Operator-Vorgabe 2026-09-01)
# ---------------------------------------------------------------------------


def test_proof_states_what_it_does_not_claim(tmp_path: Path) -> None:
    """Ein PASS belegt genau das Gepruefte — und sagt selbst, was es NICHT belegt.

    Ohne diese beiden Felder liest jemand spaeter einen konsistenten
    Systemzustand oder eine Off-Site-Redundanz in einen Drill hinein, der
    beides nie gemessen hat.
    """
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    assert _run_drill(tmp_path, archive).returncode == 0
    proof = _latest_proof(tmp_path)
    assert proof["status"] == "PASS"
    assert proof["global_atomic_point_in_time"] == "NOT_CLAIMED"
    assert proof["off_pi_redundancy"] == "NOT_CLAIMED"


def test_non_claims_survive_a_failing_run(tmp_path: Path) -> None:
    """Auch im Fehlerfall. Ein Beweis, der seine Grenzen nur im Erfolgsfall
    nennt, nennt sie nicht."""
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    assert _run_drill(tmp_path, archive, passphrase=None).returncode == 2
    proof = _latest_proof(tmp_path)
    assert proof["status"] == "FAIL"
    assert proof["global_atomic_point_in_time"] == "NOT_CLAIMED"
    assert proof["off_pi_redundancy"] == "NOT_CLAIMED"
