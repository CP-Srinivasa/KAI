"""P1-C-7 — welche unveraenderlichen Bytes hat dieser Prozess geladen?

Der Vorgaenger band den Marker an die Kernel-Identitaet (PID, Startzeit, Boot)
und las den Commit beim Start. Das schliesst die *Prozess*-Identitaet, nicht die
*Code*-Identitaet: Python importiert Module erst zur Laufzeit, und ein
beweglicher Checkout darf sich zwischen Attestierung und Import weiterbewegen.

    Checkout OLD -> attestiert OLD -> Checkout wandert auf NEW -> exec
    -> importiert NEW -> Marker behauptet OLD, Prozess laeuft NEW

Die Antwort ist ein Baum, der sich nicht bewegt. Diese Datei prueft, dass die
Identitaet am Release haengt und nicht an einem Symlink, der spaeter umgelegt
werden kann.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.observability.process_runtime_marker import (
    STATE_CODE_DRIFT,
    STATE_MATCH,
    STATE_RELEASE_MISMATCH,
    STATE_UNKNOWN,
    VERDICT_HOLD,
    VERDICT_OK,
    ProcessObservation,
    evaluate_process_markers,
    marker_from_release,
)
from app.observability.release_identity import (
    PROBLEM_MANIFEST_MISSING,
    PROBLEM_PATH_MISMATCH,
    PROBLEM_TREE_TAMPERED,
    RELEASE_MANIFEST_SCHEMA,
    ReleaseManifest,
    read_release_manifest,
    release_tree_sha256,
    resolve_current,
    verify_release,
)

BOOT = "0b1d5f2a-0000-4000-8000-000000000001"
LOCK = "65de2c3439f9bc06e77dfa0b41427186476f0d81271784cb796e2c54370b6908"
DEPLOYED = "2026-09-02T04:00:00+00:00"
STARTED = "2026-09-02T05:00:00+00:00"


def _release(root: Path, sha: str, *, code: str = "print('a')") -> Path:
    """Ein minimaler, aber echter Release-Baum samt ``release.json``."""
    rel = root / "releases" / sha
    (rel / "app").mkdir(parents=True)
    (rel / "app" / "main.py").write_text(code, encoding="utf-8")
    (rel / "requirements.lock").write_text("pkg==1.0\n", encoding="utf-8")
    tree = release_tree_sha256(rel)
    (rel / "release.json").write_text(
        json.dumps(
            {
                "schema": RELEASE_MANIFEST_SCHEMA,
                "repo_sha": sha,
                "release_path": str(rel),
                "release_tree_sha256": tree,
                "requirements_lock_sha256": LOCK,
                "python_version": "3.12.0",
                "created_at_utc": "2026-09-02T03:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return rel


def _switch(root: Path, target: Path) -> Path | None:
    """``current`` umlegen — ``None``, wenn die Plattform keine Symlinks erlaubt.

    KEIN ``pytest.skip``: die Aussagen dieser Datei haengen nicht am Symlink.
    Wer ``current`` betrachtet, bekommt hier den aufgeloesten Pfad direkt
    uebergeben; der Symlink ist nur die Produktionsmechanik, und die prueft der
    Linux-Runner. Ein uebersprungener Provenance-Test waere genau die
    Blindstelle, gegen die diese Datei gebaut ist.
    """
    link = root / "current"
    if link.is_symlink() or link.exists():
        link.unlink()
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        return None
    return link


def _marker_for(release: Path, *, pid: int = 4711, ticks: int = 90210) -> dict:
    manifest = read_release_manifest(release)
    assert manifest is not None
    return marker_from_release(
        manifest,
        unit="kai-server.service",
        pid=pid,
        release_path=release,
        started_at_utc=STARTED,
        proc_start_ticks_value=ticks,
        boot_id=BOOT,
        python_executable=str(release / ".venv" / "bin" / "python3"),
    )


def _obs(pid: int = 4711, ticks: int = 90210) -> ProcessObservation:
    return ProcessObservation(
        unit="kai-server.service",
        main_pid=pid,
        proc_start_ticks=ticks,
        boot_id=BOOT,
        started_at_utc=STARTED,
    )


# --------------------------------------------------------------------------
# Der Baum-Hash
# --------------------------------------------------------------------------


def test_zwei_gleiche_baeume_haben_denselben_hash(tmp_path: Path) -> None:
    a = _release(tmp_path / "x", "aaa")
    b = _release(tmp_path / "y", "aaa")
    assert release_tree_sha256(a) == release_tree_sha256(b)


def test_ein_geaenderter_byte_aendert_den_hash(tmp_path: Path) -> None:
    rel = _release(tmp_path, "aaa")
    vorher = release_tree_sha256(rel)
    (rel / "app" / "main.py").write_text("print('b')", encoding="utf-8")
    assert release_tree_sha256(rel) != vorher


def test_umbenennen_aendert_den_hash(tmp_path: Path) -> None:
    """Der Pfad geht mit ein — gleiche Bytes unter anderem Namen sind anderer Code."""
    rel = _release(tmp_path, "aaa")
    vorher = release_tree_sha256(rel)
    (rel / "app" / "main.py").rename(rel / "app" / "other.py")
    assert release_tree_sha256(rel) != vorher


def test_zustand_gehoert_nicht_zur_identitaet(tmp_path: Path) -> None:
    """Sonst aenderte jeder Logeintrag die Identitaet des Releases."""
    rel = _release(tmp_path, "aaa")
    vorher = release_tree_sha256(rel)
    (rel / "logs").mkdir()
    (rel / "logs" / "server.log").write_text("etwas passiert\n", encoding="utf-8")
    (rel / "artifacts").mkdir()
    (rel / "artifacts" / "x.jsonl").write_text("{}\n", encoding="utf-8")
    assert release_tree_sha256(rel) == vorher


def test_pycache_gehoert_nicht_zur_identitaet(tmp_path: Path) -> None:
    rel = _release(tmp_path, "aaa")
    vorher = release_tree_sha256(rel)
    (rel / "app" / "__pycache__").mkdir()
    (rel / "app" / "__pycache__" / "main.pyc").write_bytes(b"\x00\x01")
    assert release_tree_sha256(rel) == vorher


# --------------------------------------------------------------------------
# Das Manifest
# --------------------------------------------------------------------------


def test_ein_versiegelter_release_traegt_seinen_anspruch(tmp_path: Path) -> None:
    assert verify_release(_release(tmp_path, "aaa")) == []


def test_nachtraegliche_aenderung_faellt_auf(tmp_path: Path) -> None:
    rel = _release(tmp_path, "aaa")
    (rel / "app" / "main.py").write_text("print('manipuliert')", encoding="utf-8")
    assert PROBLEM_TREE_TAMPERED in verify_release(rel)


def test_verschobener_release_faellt_auf(tmp_path: Path) -> None:
    rel = _release(tmp_path, "aaa")
    ziel = rel.parent / "woanders"
    rel.rename(ziel)
    assert PROBLEM_PATH_MISMATCH in verify_release(ziel)


def test_fehlendes_manifest_ist_ein_befund(tmp_path: Path) -> None:
    rel = _release(tmp_path, "aaa")
    (rel / "release.json").unlink()
    assert verify_release(rel) == [PROBLEM_MANIFEST_MISSING]


def test_fremdes_schema_zaehlt_als_fehlend(tmp_path: Path) -> None:
    rel = _release(tmp_path, "aaa")
    (rel / "release.json").write_text(json.dumps({"schema": "anderes"}), encoding="utf-8")
    assert read_release_manifest(rel) is None


# --------------------------------------------------------------------------
# current wird AUFGELOEST
# --------------------------------------------------------------------------


def test_current_wird_zum_release_aufgeloest(tmp_path: Path) -> None:
    rel = _release(tmp_path, "aaa")
    link = _switch(tmp_path, rel)
    if link is not None:  # Linux/CI: echter Symlink
        assert resolve_current(link) == rel.resolve()
    # Ueberall: ein Pfad loest auf sich selbst auf, ein fehlender auf None.
    assert resolve_current(rel) == rel.resolve()
    assert resolve_current(rel / "gibtsnicht") is None


def test_fehlendes_current_ist_none(tmp_path: Path) -> None:
    assert resolve_current(tmp_path / "current") is None


# --------------------------------------------------------------------------
# DER Pflicht-Negativtest: der Race, der P1-C-7 begruendet hat
# --------------------------------------------------------------------------


def test_prozess_bleibt_an_seinem_release_wenn_current_weiterschaltet(tmp_path: Path) -> None:
    """Release OLD attestiert, current wechselt auf NEW, Prozess laedt weiter OLD.

    Marker OLD und geladener Baum OLD stimmen ueberein — es gibt keine
    Diskrepanz zwischen Behauptung und Wirklichkeit. Aber der Deploy erwartet
    NEW, also ist der Dienst nicht neu gestartet: RUNTIME_CODE_DRIFT, kein PASS.
    """
    alt = _release(tmp_path, "a" * 40, code="print('alt')")
    marker = _marker_for(alt)  # 1. der Prozess bezeugt ALT

    neu = _release(tmp_path, "b" * 40, code="print('neu')")
    _switch(tmp_path, neu)  # 2. current schaltet weiter (wo moeglich)
    neu_manifest = read_release_manifest(neu)
    assert neu_manifest is not None

    p = evaluate_process_markers(  # 3. der Deploy erwartet NEU
        [_obs()],
        {"kai-server.service": marker},
        expected_sha=neu_manifest.repo_sha,
        checkout_sha=neu_manifest.repo_sha,
        expected_lock_sha256=LOCK,
        deployed_at_utc=DEPLOYED,
        expected_release_tree_sha256=neu_manifest.release_tree_sha256,
        current_release_path=str(neu),
    )
    assert p.verdict == VERDICT_HOLD
    # Der Deploy erwartet NEU, der Prozess bezeugt ALT: die Code-Achse trennt
    # hier zuerst. Das ist der Zustand, den die Vorgabe fuer diesen Fall nennt.
    assert p.findings[0].state == STATE_CODE_DRIFT
    # Der Marker hat NICHT gelogen — er nennt weiterhin den Baum, aus dem der
    # Prozess laedt. Genau das war vorher unmoeglich.
    assert marker["release_tree_sha256"] != neu_manifest.release_tree_sha256


def test_positivfall_release_prozess_und_deploy_stimmen_ueberein(tmp_path: Path) -> None:
    rel = _release(tmp_path, "c" * 40)
    manifest = read_release_manifest(rel)
    assert manifest is not None
    p = evaluate_process_markers(
        [_obs()],
        {"kai-server.service": _marker_for(rel)},
        expected_sha=manifest.repo_sha,
        checkout_sha=manifest.repo_sha,
        expected_lock_sha256=LOCK,
        deployed_at_utc=DEPLOYED,
        expected_release_tree_sha256=manifest.release_tree_sha256,
        current_release_path=str(rel),
    )
    assert p.verdict == VERDICT_OK
    assert p.findings[0].state == STATE_MATCH


def test_marker_ohne_release_identitaet_ist_unbelegt(tmp_path: Path) -> None:
    rel = _release(tmp_path, "d" * 40)
    manifest = read_release_manifest(rel)
    assert manifest is not None
    marker = _marker_for(rel)
    marker["release_tree_sha256"] = ""
    p = evaluate_process_markers(
        [_obs()],
        {"kai-server.service": marker},
        expected_sha=manifest.repo_sha,
        checkout_sha=manifest.repo_sha,
        expected_lock_sha256=LOCK,
        deployed_at_utc=DEPLOYED,
        expected_release_tree_sha256=manifest.release_tree_sha256,
    )
    assert p.findings[0].state == STATE_UNKNOWN


def test_prozess_aus_einem_anderen_pfad_faellt_auf(tmp_path: Path) -> None:
    """Gleicher Baum-Hash, anderer Ort — der Pfad ist Teil der Aussage."""
    rel = _release(tmp_path, "e" * 40)
    manifest = read_release_manifest(rel)
    assert manifest is not None
    marker = _marker_for(rel)
    marker["release_path"] = str(tmp_path / "releases" / "woanders")
    p = evaluate_process_markers(
        [_obs()],
        {"kai-server.service": marker},
        expected_sha=manifest.repo_sha,
        checkout_sha=manifest.repo_sha,
        expected_lock_sha256=LOCK,
        deployed_at_utc=DEPLOYED,
        expected_release_tree_sha256=manifest.release_tree_sha256,
        current_release_path=str(rel),
    )
    assert p.findings[0].state == STATE_RELEASE_MISMATCH


def test_der_marker_traegt_keinen_git_aufruf(tmp_path: Path) -> None:
    """Struktur-Ratchet: die Release-Identitaet kommt aus release.json."""
    rel = _release(tmp_path, "f" * 40)
    marker = _marker_for(rel)
    assert marker["runtime_code_sha"] == "f" * 40
    assert marker["release_path"] == str(rel)
    assert marker["release_tree_sha256"] == release_tree_sha256(rel)


def test_manifest_dataclass_bleibt_vollstaendig() -> None:
    """Eine Feldumbenennung soll hier auffallen, nicht still degradieren."""
    m = ReleaseManifest(
        schema=RELEASE_MANIFEST_SCHEMA,
        repo_sha="a",
        release_path="/x",
        release_tree_sha256="b",
        requirements_lock_sha256="c",
        python_version="3.12.0",
        created_at_utc="2026-09-02T00:00:00+00:00",
    )
    d = m.to_dict()
    for feld in (
        "schema",
        "repo_sha",
        "release_path",
        "release_tree_sha256",
        "requirements_lock_sha256",
        "python_version",
        "created_at_utc",
        "venv_python_path",
        "dependency_manifest_sha256",
        "builder_version",
    ):
        assert feld in d


# --------------------------------------------------------------------------
# Die Bau- und Aktivierungsskripte
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_das_bauskript_kopiert_keinen_vorhandenen_venv() -> None:
    """Ein hineinkopierter venv truege vorhandenen Drift in einen 'unveraenderlichen' Stand."""
    src = (REPO_ROOT / "scripts" / "pi_make_release.sh").read_text(encoding="utf-8")
    assert "python3 -m venv" in src
    assert "pip check" in src
    assert "cp -a" in src and "$REPO/.venv" not in src


def test_das_bauskript_schaltet_current_nicht_um() -> None:
    """Bauen und Aktivieren sind getrennt — sonst behauptet ein Marker zu frueh."""
    src = (REPO_ROOT / "scripts" / "pi_make_release.sh").read_text(encoding="utf-8")
    assert "deployment_marker" not in src
    assert "mv -T" not in src


def test_das_aktivierungsskript_schaltet_atomar_und_markiert_danach() -> None:
    src = (REPO_ROOT / "scripts" / "pi_activate_release.sh").read_text(encoding="utf-8")
    assert "mv -T" in src, "ln -sfn allein ist nicht atomar"
    schalten = src.index("mv -T")
    markieren = src.index("deployment_marker")
    assert schalten < markieren, "der Deploy-Marker darf nicht vor dem Switch entstehen"


def test_das_aufraeumen_schont_current_und_lebende_prozesse() -> None:
    src = (REPO_ROOT / "scripts" / "pi_activate_release.sh").read_text(encoding="utf-8")
    assert '[ "$d" = "$RESOLVED" ] && continue' in src
    assert "release_path" in src, "lebende Prozessmarker muessen geschont werden"


def test_die_fuenf_langlebigen_units_laufen_aus_dem_release() -> None:
    units = [
        "kai-server.service",
        "kai-agent-worker.service",
        "kai-tg-listener.service",
        "kai-liquidation-stream.service",
        "kai-entry-watch.service",
    ]
    for name in units:
        text = (REPO_ROOT / "deploy" / "systemd" / name).read_text(encoding="utf-8")
        assert "WorkingDirectory=/home/kai/current" in text, name
        assert "--repo /home/kai/current" in text, name
        assert "PYTHONDONTWRITEBYTECODE=1" in text, name
        # Zustand bleibt draussen: WO ``ReadWritePaths`` gesetzt ist, zeigt es
        # ausschliesslich auf den Zustandspfad, nie auf den Release-Baum. Nicht
        # alle fuenf Units sind so gehaertet — das zu behaupten waere falsch.
        rw = [ln for ln in text.splitlines() if ln.startswith("ReadWritePaths=")]
        assert rw in ([], ["ReadWritePaths=/home/kai/ai_analyst_trading_bot"]), name
        assert "ReadWritePaths=/home/kai/current" not in text, name


def test_die_uebrigen_units_bleiben_bewusst_am_alten_pfad() -> None:
    """Dokumentierte Entscheidung, kein Versehen — die Population ist benannt."""
    alle = sorted((REPO_ROOT / "deploy" / "systemd").glob("*.service"))
    release_gebunden = [
        u for u in alle if "WorkingDirectory=/home/kai/current" in u.read_text(encoding="utf-8")
    ]
    assert len(release_gebunden) == 5, [u.name for u in release_gebunden]
    assert len(alle) > 5, "die uebrigen Units existieren weiterhin"


@pytest.mark.parametrize("script", ["pi_make_release.sh", "pi_activate_release.sh"])
def test_die_skripte_sind_ausfuehrbare_shell(script: str) -> None:
    src = (REPO_ROOT / "scripts" / script).read_text(encoding="utf-8")
    assert src.startswith("#!/usr/bin/env bash")
    assert "set -uo pipefail" in src


def test_os_getpid_bleibt_die_quelle_der_marker_pid(tmp_path: Path) -> None:
    """Keine extern injizierte PID — der attestierende Prozess ist der Dienst."""
    rel = _release(tmp_path, "0" * 40)
    manifest = read_release_manifest(rel)
    assert manifest is not None
    marker = marker_from_release(
        manifest,
        unit="kai-server.service",
        pid=os.getpid(),
        release_path=rel,
        started_at_utc=STARTED,
        proc_start_ticks_value=1,
        boot_id=BOOT,
    )
    assert marker["pid"] == os.getpid()


def test_gleicher_commit_anderer_baum_trennt_erst_die_release_achse(tmp_path: Path) -> None:
    """Zwei Baeume unter demselben Commit — nur die Release-Achse sieht das.

    Ein neu gebauter Release derselben Revision kann sich unterscheiden (anderer
    venv, andere Datei). Die Code-SHA ist dann identisch und beweist nichts; erst
    ``release_tree_sha256`` trennt.
    """
    sha = "1" * 40
    alt = _release(tmp_path / "a", sha, code="print('alt')")
    neu = _release(tmp_path / "b", sha, code="print('neu')")
    neu_manifest = read_release_manifest(neu)
    assert neu_manifest is not None
    assert read_release_manifest(alt).repo_sha == neu_manifest.repo_sha  # type: ignore[union-attr]

    p = evaluate_process_markers(
        [_obs()],
        {"kai-server.service": _marker_for(alt)},
        expected_sha=sha,
        checkout_sha=sha,
        expected_lock_sha256=LOCK,
        deployed_at_utc=DEPLOYED,
        expected_release_tree_sha256=neu_manifest.release_tree_sha256,
        current_release_path=str(neu),
    )
    assert p.findings[0].state == STATE_RELEASE_MISMATCH
    assert p.verdict == VERDICT_HOLD


# --------------------------------------------------------------------------
# Rollback — derselbe Vorgang, anderes Ziel.
#
# Ein Rollback-Pfad, der den Deploy-Marker NICHT mitschreibt, ist per
# Konstruktion dauerhaft rot: die Prozesse laufen korrekt aus ALT, der Marker
# behauptet NEU. Der Operator gewoehnte sich an, ein rotes Provenance-Signal zu
# ignorieren — genau der Schaden, den dieser Umbau verhindern soll.
# --------------------------------------------------------------------------


def _bewerten(prozess_release: Path, marker_release: Path):
    """Prozess laeuft aus ``prozess_release``, der Deploy-Marker nennt ``marker_release``."""
    soll = read_release_manifest(marker_release)
    assert soll is not None
    return evaluate_process_markers(
        [_obs()],
        {"kai-server.service": _marker_for(prozess_release)},
        expected_sha=soll.repo_sha,
        checkout_sha=soll.repo_sha,
        expected_lock_sha256=LOCK,
        deployed_at_utc=DEPLOYED,
        expected_release_tree_sha256=soll.release_tree_sha256,
        current_release_path=str(marker_release),
    )


def test_rollback_mit_marker_aktualisierung_ist_wieder_gruen(tmp_path: Path) -> None:
    alt = _release(tmp_path, "a" * 40, code="print('alt')")
    neu = _release(tmp_path, "b" * 40, code="print('neu')")

    # Vorwaerts: NEU aktiv, Prozess laeuft NEU.
    assert _bewerten(neu, neu).verdict == VERDICT_OK

    # Rollback auf ALT, Marker MITgeschrieben: der Soll-Stand ist jetzt ALT.
    zurueck = _bewerten(alt, alt)
    assert zurueck.verdict == VERDICT_OK, "ein gewollter Rollback darf nicht rot sein"
    assert zurueck.findings[0].state == STATE_MATCH


def test_rollback_ohne_marker_aktualisierung_ist_hold(tmp_path: Path) -> None:
    """Der Gegenfall — sonst waere der Test oben nur eine Behauptung."""
    alt = _release(tmp_path, "a" * 40, code="print('alt')")
    neu = _release(tmp_path, "b" * 40, code="print('neu')")
    p = _bewerten(alt, neu)  # Prozess aus ALT, Marker sagt weiterhin NEU
    assert p.verdict == VERDICT_HOLD


def test_vorwaerts_und_rollback_benutzen_denselben_codepfad() -> None:
    """Ein Skript, ein Weg — nicht zwei, von denen eines den Marker kennt."""
    src = (REPO_ROOT / "scripts" / "pi_activate_release.sh").read_text(encoding="utf-8")
    assert src.count("deployment_marker") >= 1
    assert "--release" in src
    # Kein zweiter, marker-loser Umschaltpfad im Repo.
    andere = [
        f
        for f in (REPO_ROOT / "scripts").glob("*.sh")
        if f.name != "pi_activate_release.sh" and "mv -T" in f.read_text(encoding="utf-8")
    ]
    assert andere == [], f"zweiter Umschaltpfad ohne Marker: {[f.name for f in andere]}"
