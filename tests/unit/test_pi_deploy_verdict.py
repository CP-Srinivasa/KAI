r"""Ein Deploy darf sich nicht selbst gruen melden.

Vorfall 2026-08-20. ``kai_deploy.sh`` rief den Unit-Sync so auf::

    pi_unit_sync_apply || echo "unit-sync: rc=$? (10=zurueckgestellt, 1=Fehler)"

Der Sync scheiterte an ``sudo: a password is required`` und gab rc=1 zurueck.
``|| echo`` machte daraus einen Text — und einen Erfolg. Danach lief der
/health-Smoke, fand erwartungsgemaess 200 (der Server war nie angefasst worden),
und der Deploy meldete Gruen. Alle 24 Unit-Dateien blieben divergent.

Zwei Krankheiten, die dieser Test getrennt festhaelt:

1. ``|| echo`` verwandelt jeden Fehlschlag in eine Notiz. Der Exit-Code ist das
   einzige Signal, das ein Aufrufer maschinell lesen kann — er darf nicht
   verloren gehen.
2. ``/health=200`` wurde als URTEIL gelesen, obwohl es nur eine NACHBEDINGUNG
   ist. Es beweist, dass der Server laeuft, nicht dass das Deploy ankam. Bei
   Unit-Drift beweist es nachweislich nichts: Unit-Dateien beruehren den
   laufenden uvicorn ueberhaupt nicht.

Der dritte Befund derselben Messung steht nicht im Code, sondern in seiner Lage:
``kai_deploy.sh`` liegt in ``~/KAI-mirror`` — ausserhalb der Versionskontrolle
und ausserhalb CI. Kein Test konnte die Zeile je sehen. Deshalb liegt der
Deploy-Schritt jetzt als ``scripts/pi_deploy_step.sh`` im Repo.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "scripts" / "lib" / "pi_deploy_verdict.sh"
STEP = REPO / "scripts" / "pi_deploy_step.sh"
_BASH = shutil.which("bash")
_GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")


def _bash(script: str, cwd: Path | None = None, env: dict[str, str] | None = None):
    assert _BASH is not None
    import os

    merged = dict(os.environ)
    merged.update(env or {})
    return subprocess.run(  # noqa: S603
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env=merged,
    )


def _verdict(*reasons: str) -> tuple[str, int]:
    args = " ".join(f'"{r}"' for r in reasons)
    proc = _bash(f'set -uo pipefail; . "{LIB.as_posix()}"; pi_deploy_verdict {args}')
    return proc.stdout.strip(), proc.returncode


def _reasons(health: str, drift: str, caused: str, *extra: str) -> list[str]:
    args = " ".join(f'"{e}"' for e in extra)
    proc = _bash(
        f'set -uo pipefail; . "{LIB.as_posix()}"; '
        f'pi_deploy_reasons "{health}" "{drift}" "{caused}" {args}'
    )
    assert proc.returncode == 0, proc.stderr
    return [line for line in proc.stdout.splitlines() if line.strip()]


# ── Die reine Urteilslogik ──────────────────────────────────────────────────


def test_the_real_incident_is_not_a_success() -> None:
    """Der Kern: Units divergent, /health gruen — das ist KEIN erfolgreiches Deploy."""
    reasons = _reasons("200", "24", "1")

    assert reasons == ["SYSTEMD_CHANGE_REQUIRES_OPERATOR:24"]
    assert _verdict(*reasons) == ("DEPLOY_HOLD", 10)


def test_clean_deploy_is_a_success() -> None:
    """Gegenprobe — sonst waere der Waechter nur ein Daueralarm."""
    assert _reasons("200", "0", "0") == []
    assert _verdict() == ("DEPLOY_SUCCESS", 0)


def test_health_not_200_is_a_real_failure() -> None:
    """Die Nachbedingung darf in die andere Richtung sehr wohl urteilen."""
    reasons = _reasons("000", "0", "0")

    assert reasons == ["HEALTH_NOT_200:000"]
    assert _verdict(*reasons) == ("DEPLOY_FAILED", 1)


def test_hard_failure_beats_hold() -> None:
    """Ein echter Fehlschlag darf sich nie hinter einem weicheren HOLD verstecken."""
    assert _verdict("SYSTEMD_DRIFT_PREEXISTING:3", "HEALTH_NOT_200:502") == ("DEPLOY_FAILED", 1)
    assert _verdict("HEALTH_NOT_200:502", "SYSTEMD_DRIFT_PREEXISTING:3") == ("DEPLOY_FAILED", 1)


def test_unknown_reason_token_fails_closed() -> None:
    """Wer einen neuen Grund einfuehrt, muss ihn einordnen.

    Ein unbekanntes Token darf NICHT stillschweigend zu HOLD oder SUCCESS
    abrutschen — sonst waechst die Menge der Gruende, die nichts mehr bewirken.
    """
    assert _verdict("SOMETHING_NOBODY_CLASSIFIED") == ("DEPLOY_FAILED", 1)


def test_unmeasurable_drift_is_not_zero_drift() -> None:
    """ "Nicht messbar" ist keine Synchronitaet.

    Ohne diese Unterscheidung wuerde ein kaputter Abgleich als "keine
    Abweichung" durchgehen — genau die Sorte Stille, die den Vorfall traegt.
    """
    assert _reasons("200", "unknown", "0") == ["SYSTEMD_DRIFT_UNKNOWN"]
    assert _verdict("SYSTEMD_DRIFT_UNKNOWN") == ("DEPLOY_HOLD", 10)


def test_cause_is_distinguished_from_state() -> None:
    """Derselbe Ausgang, aber ein anderer Befund: habe ICH das verursacht?"""
    assert _reasons("200", "2", "1") == ["SYSTEMD_CHANGE_REQUIRES_OPERATOR:2"]
    assert _reasons("200", "2", "0") == ["SYSTEMD_DRIFT_PREEXISTING:2"]
    assert _verdict("SYSTEMD_DRIFT_PREEXISTING:2") == ("DEPLOY_HOLD", 10)


def test_writer_freeze_deferral_holds_but_does_not_fail() -> None:
    """Bewusst zurueckgestellt ist nicht kaputt — aber auch nicht fertig."""
    assert _verdict("WRITER_FREEZE_DEFERRED") == ("DEPLOY_HOLD", 10)


def test_explain_names_the_remedy_not_only_the_problem() -> None:
    """Ein Gate, das kein Mittel nennt, ist nur eine Blockade."""
    proc = _bash(
        f'set -uo pipefail; . "{LIB.as_posix()}"; '
        'pi_deploy_explain "SYSTEMD_CHANGE_REQUIRES_OPERATOR:24"'
    )

    assert "operator-privilegiert" in proc.stdout
    assert "24" in proc.stdout


# ── Struktur: die Krankheit selbst darf nicht zurueckkehren ─────────────────


def _code_without_comments(path: Path) -> str:
    """Kommentare strippen — sonst trifft die Suche die Erklaerung des Fehlers.

    Der Kopfkommentar von ``pi_deploy_step.sh`` ZITIERT die kaputte Zeile. Ein
    naiver Textsuch-Waechter wuerde daran haengenbleiben und waere damit genau so
    wertlos wie der Deploy, den er schuetzen soll.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


@pytest.mark.parametrize(
    "path",
    [
        STEP,
        LIB,
        REPO / "scripts" / "lib" / "pi_unit_sync.sh",
        REPO / "scripts" / "pi_apply_systemd_units.sh",
    ],
)
def test_no_blanket_swallowing_of_exit_codes(path: Path) -> None:
    """Ein KOMMANDO-Fehlschlag darf nicht zu einer Notiz werden.

    Die Grenze liegt links vom ``||``: ``[[ -e X ]] || echo ...`` ist ein
    Bedingungstest und voellig in Ordnung — dort gibt es keinen Fehlschlag zu
    verschlucken. ``pi_unit_sync_apply || echo ...`` ist die Krankheit: ein
    Kommando scheitert, und der Exit-Code, das einzige maschinell lesbare
    Signal, verschwindet.

    Ausnahmen, beide belegt statt geglaubt:

    * ``grep -c`` gibt bei null Treffern rc=1 zurueck — dokumentiert kein Fehler.
    * ``paper_writer_freeze.sh`` darf in aelteren Checkouts fehlen; das ist ein
      erwarteter Zustand, kein Fehlschlag.
    """
    offenders = []
    for line in _code_without_comments(path).splitlines():
        stripped = line.strip()
        for operator in ("|| echo", "|| true"):
            if operator not in stripped:
                continue
            left = stripped.split(operator)[0].rstrip()
            if left.endswith("]"):
                continue  # [[ ... ]] / [ ... ] — Test, kein Kommando
            if "grep -c" in left or "paper_writer_freeze.sh" in left:
                continue
            offenders.append(stripped)

    assert not offenders, (
        f"{path.name} verschluckt Kommando-Exit-Codes: "
        + "; ".join(offenders)
        + " — genau dieses Muster hat am 2026-08-20 einen gescheiterten "
        "Unit-Sync in ein gruenes Deploy verwandelt."
    )


def test_step_script_is_syntactically_valid() -> None:
    for path in (STEP, LIB):
        proc = _bash(f'bash -n "{path.as_posix()}"')
        assert proc.returncode == 0, f"{path.name}: {proc.stderr}"


# ── Ende zu Ende: der Schritt, wie er auf der Pi laeuft ─────────────────────
#
# Die reine Logik oben kann stimmen und die Verdrahtung trotzdem falsch sein —
# genau das war der Vorfall. Deshalb hier ein echtes Git-Fixture (bare origin +
# Arbeitskopie, kein Netz), gefaelschtes curl und ein gefaelschter Broker.

pytestmark_git = pytest.mark.skipif(_GIT is None, reason="git not available")


def _git_in(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(  # noqa: S603
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, f"git {' '.join(args)} -> {proc.stderr}"
    return proc


@pytest.fixture
def pi(tmp_path: Path):
    """Bare origin + Arbeitskopie + ein leeres /etc-Aequivalent."""
    if _GIT is None:
        pytest.skip("git not available")
    origin = tmp_path / "origin.git"
    subprocess.run(  # noqa: S603
        ["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True
    )
    work = tmp_path / "work"
    (work / "deploy" / "systemd").mkdir(parents=True)
    (work / "deploy" / "systemd" / "kai-x.timer").write_text("OnCalendar=*:0/5\n", encoding="utf-8")
    _git_in(work, "init", "-b", "main")
    _git_in(work, "config", "user.email", "t@example.invalid")
    _git_in(work, "config", "user.name", "Test")
    _git_in(work, "add", "-A")
    _git_in(work, "commit", "-m", "init")
    _git_in(work, "remote", "add", "origin", origin.as_posix())
    _git_in(work, "push", "-u", "origin", "main")

    etc = tmp_path / "etc"
    etc.mkdir()

    class _Pi:
        pass

    obj = _Pi()
    obj.origin, obj.work, obj.etc, obj.tmp = origin, work, etc, tmp_path  # type: ignore[attr-defined]
    return obj


def _sync_etc(pi_obj) -> None:
    """/etc auf den Stand des Checkouts bringen — der gesunde Ausgangszustand."""
    for src in (pi_obj.work / "deploy" / "systemd").iterdir():
        (pi_obj.etc / src.name).write_bytes(src.read_bytes())


def _run_step(pi_obj, *, health: str = "200", args: str = "", broker_rc: int = 0):
    curl = pi_obj.tmp / "fakecurl.sh"
    # Echtes curl gibt bei unerreichbarem Ziel "000" aus UND scheitert mit rc=7.
    # Der Fake bildet beides ab, sonst wuerde der Fehlerpfad nie durchlaufen.
    curl.write_text(
        f'printf "%s" "{health}"\nexit {7 if health == "000" else 0}\n', encoding="utf-8"
    )
    broker = pi_obj.tmp / "fakebroker.sh"
    broker.write_text(f'echo "broker $*"\nexit {broker_rc}\n', encoding="utf-8")

    base = _git_in(pi_obj.work, "rev-parse", "HEAD").stdout.strip()
    return _bash(
        f'bash "{STEP.as_posix()}" --base "{base}" --branch main {args}',
        cwd=pi_obj.work,
        env={
            "PI_DEPLOY_CURL": f"bash {curl.as_posix()}",
            "PI_DEPLOY_BROKER": f"bash {broker.as_posix()}",
            "PI_DEPLOY_UNIT_DST": pi_obj.etc.as_posix(),
            "PI_DEPLOY_HEALTH_TRIES": "1",
            "PI_DEPLOY_HEALTH_SLEEP": "0",
            "PI_DEPLOY_RESTART_SETTLE": "0",
        },
    )


def test_clean_state_deploys_green(pi) -> None:
    """Gegenprobe zuerst: ohne sie waere jeder Waechter nur ein Daueralarm."""
    _sync_etc(pi)

    proc = _run_step(pi)

    assert "DEPLOY_VERDICT=DEPLOY_SUCCESS" in proc.stdout
    assert proc.returncode == 0


def test_the_incident_no_longer_reports_green(pi) -> None:
    """DER Regressionstest: Units divergent, /health 200 — Exit-Code darf nicht 0 sein.

    Genau diese Kombination hat am 2026-08-20 ein gruenes Deploy gemeldet,
    waehrend 24 Unit-Dateien unangetastet blieben.
    """
    proc = _run_step(pi)  # /etc leer -> kai-x.timer fehlt dort

    assert proc.returncode == 10, proc.stdout + proc.stderr
    assert "DEPLOY_VERDICT=DEPLOY_HOLD" in proc.stdout
    assert "health:200" in proc.stdout, "die Nachbedingung wird weiterhin gemessen"


def test_hold_names_the_concrete_remedy(pi) -> None:
    """Der Operator soll nicht raten muessen, WELCHE Datei er anfassen muss."""
    proc = _run_step(pi)

    assert "sudo cp deploy/systemd/kai-x.timer /etc/systemd/system/kai-x.timer" in proc.stdout
    assert "sudo systemctl daemon-reload" in proc.stdout
    assert "sudo systemctl restart kai-x.timer" in proc.stdout


def test_merge_that_changes_units_is_named_as_the_cause(pi) -> None:
    """ "Ich habe das gerade verursacht" ist ein anderer Befund als "lag schon an"."""
    _sync_etc(pi)
    up = pi.tmp / "up"
    subprocess.run(  # noqa: S603
        ["git", "clone", pi.origin.as_posix(), str(up)], check=True, capture_output=True
    )
    _git_in(up, "config", "user.email", "t@example.invalid")
    _git_in(up, "config", "user.name", "Test")
    (up / "deploy" / "systemd" / "kai-x.timer").write_text("OnCalendar=*:0/15\n", encoding="utf-8")
    _git_in(up, "commit", "-am", "Kadenz aendern")
    _git_in(up, "push")

    proc = _run_step(pi)

    assert proc.returncode == 10
    assert "dieser Merge aendert" in proc.stdout


def test_unreachable_health_fails_the_deploy(pi) -> None:
    """Der Fehlerpfad von curl selbst — nicht nur ein von Hand gesetzter Code."""
    _sync_etc(pi)

    proc = _run_step(pi, health="000")

    assert proc.returncode == 1
    assert "DEPLOY_VERDICT=DEPLOY_FAILED" in proc.stdout


def test_failed_restart_is_not_swallowed(pi) -> None:
    """Vorher waere ein gescheiterter Restart hinter /health=200 verschwunden."""
    _sync_etc(pi)

    proc = _run_step(pi, args="--restart kai-server", broker_rc=1)

    assert proc.returncode == 1
    assert "DEPLOY_VERDICT=DEPLOY_FAILED" in proc.stdout


def test_successful_restart_goes_through_the_broker(pi) -> None:
    """Der Restart laeuft ueber kai-service-control, nicht ueber ein rohes systemctl."""
    _sync_etc(pi)

    proc = _run_step(pi, args="--restart kai-server")

    assert proc.returncode == 0
    assert "broker restart kai-server.service" in proc.stdout
