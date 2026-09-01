"""ONE_PRODUCTION_REVISION: läuft jeder Dienst auf dem Stand, den der Deploy behauptet?

Anlass 2026-09-01, und die Entstehung gehört zum Test dazu, weil sie zeigt,
warum die Messung vom Prozess ausgehen muss:

``kai-tg-listener`` startete laut ``ExecStart`` aus
``/home/kai/ai_analyst_trading_bot/.venv/bin/python``. Ich habe daraus auf einen
zweiten, nicht aktualisierten Checkout geschlossen und das als Befund gemeldet
— **falsch**: ``/home/kai/...`` löst auf ``/home/ubuntu/...`` auf, es gibt genau
einen Baum. Der echte Defekt war subtiler: der Prozess lief seit dem 26.08. und
hielt die alten Bibliotheken im Speicher, während auf der Platte neue lagen.

Aus dem Unit-Text war beides nicht zu unterscheiden. Aus ``/proc/<pid>`` schon.
Deshalb misst der Vertrag den PROZESS, nicht die Unit — und deshalb steht der
Fall unten als Test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.observability.runtime_provenance import (
    MARKER_SCHEMA,
    REASON_CODE_DRIFT,
    REASON_DEPENDENCY_DRIFT,
    REASON_NOT_MEASURABLE,
    VERDICT_HOLD,
    VERDICT_OK,
    ServiceRuntime,
    build_marker,
    collect_runtime_services,
    evaluate_dependency_marker,
    evaluate_provenance,
    read_marker,
    render_verdict,
)

_SHA = "8e93224f1a2b3c4d5e6f70819293a4b5c6d7e8f9"
_ALT = "0000000011112222333344445555666677778888"
_LOCK = "65de2c3439f9bc06" + "0" * 48


def _svc(unit: str, sha: str | None = _SHA, **kw) -> ServiceRuntime:
    base = {
        "unit": unit,
        "user": "ubuntu",
        "pid": 1234,
        "executable": "/usr/bin/python3.12",
        "cwd": "/home/ubuntu/ai_analyst_trading_bot",
        "repo_root": "/home/ubuntu/ai_analyst_trading_bot",
        "repo_sha": sha,
    }
    base.update(kw)
    return ServiceRuntime(**base)  # type: ignore[arg-type]


def _marker(sha: str = _SHA, lock: str = _LOCK) -> dict:
    return {
        "schema": MARKER_SCHEMA,
        "repo_sha": sha,
        "requirements_lock_sha256": lock,
        "python_executable": "/home/ubuntu/ai_analyst_trading_bot/.venv/bin/python3",
        "installed_at_utc": "2026-09-01T07:14:36+00:00",
    }


# ── Der Code-Vertrag ────────────────────────────────────────────────────────


def test_alle_dienste_auf_dem_erwarteten_stand_ist_ok() -> None:
    v = evaluate_provenance(
        [_svc("kai-server.service"), _svc("kai-agent-worker.service")],
        expected_sha=_SHA,
        marker=_marker(),
        checkout_sha=_SHA,
        checkout_lock_sha256=_LOCK,
    )
    assert v.verdict == VERDICT_OK
    assert v.reasons == ()
    assert v.services_repo_based == 2


def test_ein_dienst_auf_altem_code_ist_hold_kein_warning() -> None:
    """Der Kern: ein Dienst auf altem Stand macht das Deploy-Urteil ungueltig."""
    v = evaluate_provenance(
        [_svc("kai-server.service"), _svc("kai-tg-listener.service", sha=_ALT)],
        expected_sha=_SHA,
        marker=_marker(),
        checkout_sha=_SHA,
        checkout_lock_sha256=_LOCK,
    )
    assert v.verdict == VERDICT_HOLD
    assert REASON_CODE_DRIFT in v.reasons
    assert [d["unit"] for d in v.drifted] == ["kai-tg-listener.service"]
    assert "CODE-DRIFT kai-tg-listener.service" in render_verdict(v)


def test_nicht_repo_basierte_dienste_fallen_nicht_unter_den_code_vertrag() -> None:
    """Positivkontrolle gegen Ueberdehnung: was kein Repo hat, hat keinen SHA."""
    v = evaluate_provenance(
        [_svc("kai-server.service"), _svc("fremd.service", repo_root=None, repo_sha=None)],
        expected_sha=_SHA,
        marker=_marker(),
        checkout_sha=_SHA,
        checkout_lock_sha256=_LOCK,
    )
    assert v.verdict == VERDICT_OK
    assert v.services_total == 2
    assert v.services_repo_based == 1


def test_ein_nicht_messbarer_dienst_ist_ein_befund_kein_schweigen() -> None:
    """Fail-closed: „konnte nicht messen" darf nie wie „in Ordnung" aussehen."""
    v = evaluate_provenance(
        [_svc("kai-server.service"), _svc("fremd.service", measurable=False, note="kein /proc")],
        expected_sha=_SHA,
        marker=_marker(),
        checkout_sha=_SHA,
        checkout_lock_sha256=_LOCK,
    )
    assert v.verdict == VERDICT_HOLD
    assert REASON_NOT_MEASURABLE in v.reasons
    assert "NICHT MESSBAR fremd.service" in render_verdict(v)


# ── Der Abhaengigkeits-Vertrag ──────────────────────────────────────────────


def test_ohne_marker_ist_die_synchronisierung_unbelegt() -> None:
    findings = evaluate_dependency_marker(None, checkout_sha=_SHA, checkout_lock_sha256=_LOCK)
    assert len(findings) == 1
    assert "unbelegt" in findings[0]


def test_marker_aus_einer_anderen_revision_faellt_auf() -> None:
    findings = evaluate_dependency_marker(
        _marker(sha=_ALT), checkout_sha=_SHA, checkout_lock_sha256=_LOCK
    )
    assert any("repo_sha" in f for f in findings)


def test_geaendertes_lock_seit_der_synchronisierung_faellt_auf() -> None:
    """Genau der Fall vom 01.09.: neues Lock gemergt, venv noch nicht nachgezogen."""
    findings = evaluate_dependency_marker(
        _marker(lock="a" * 64), checkout_sha=_SHA, checkout_lock_sha256=_LOCK
    )
    assert any("requirements.lock hat sich" in f for f in findings)


def test_unlesbares_lock_ist_ein_befund() -> None:
    findings = evaluate_dependency_marker(_marker(), checkout_sha=_SHA, checkout_lock_sha256=None)
    assert any("nicht lesbar" in f for f in findings)


def test_dependency_drift_macht_das_gesamturteil_hold() -> None:
    v = evaluate_provenance(
        [_svc("kai-server.service")],
        expected_sha=_SHA,
        marker=None,
        checkout_sha=_SHA,
        checkout_lock_sha256=_LOCK,
    )
    assert v.verdict == VERDICT_HOLD
    assert REASON_DEPENDENCY_DRIFT in v.reasons


# ── Der Marker darf keinen fehlgeschlagenen Lauf dokumentieren ─────────────


def test_marker_wird_ohne_erfolgreichen_pip_check_nicht_gebaut() -> None:
    """Ein Marker ueber einen gescheiterten Lauf waere schlimmer als keiner."""
    with pytest.raises(ValueError, match="pip check"):
        build_marker(
            repo_sha=_SHA,
            requirements_lock_sha256=_LOCK,
            python_executable="/x/python3",
            installed_at=datetime(2026, 9, 1, tzinfo=UTC),
            pip_check_ok=False,
        )


def test_marker_traegt_die_vier_geforderten_felder() -> None:
    m = build_marker(
        repo_sha=_SHA,
        requirements_lock_sha256=_LOCK,
        python_executable="/x/python3",
        installed_at=datetime(2026, 9, 1, 7, 14, 36, tzinfo=UTC),
        pip_check_ok=True,
    )
    for feld in ("repo_sha", "requirements_lock_sha256", "python_executable", "installed_at_utc"):
        assert feld in m, feld
    assert m["installed_at_utc"].startswith("2026-09-01T07:14:36")


def test_ein_kaputter_marker_gilt_als_keiner(tmp_path) -> None:
    p = tmp_path / "marker.json"
    p.write_text("{ kaputt", encoding="utf-8")
    assert read_marker(p) is None
    p.write_text('{"schema": "etwas/anderes"}', encoding="utf-8")
    assert read_marker(p) is None


# ── Die Erhebung misst den PROZESS, nicht die Unit ─────────────────────────


def test_die_erhebung_folgt_proc_und_nicht_dem_execstart(tmp_path) -> None:
    """Der Fall, der dieses Modul ausgeloest hat.

    ``ExecStart`` nennt ``/home/kai/...``, ``/proc/<pid>/cwd`` loest auf
    ``/home/ubuntu/...`` auf. Der Vertrag muss dem PROZESS folgen — sonst meldet
    er einen zweiten Checkout, den es nicht gibt.
    """
    repo = tmp_path / "home" / "ubuntu" / "ai_analyst_trading_bot"
    (repo / ".git").mkdir(parents=True)
    (repo / "requirements.lock").write_text("anthropic==1.2.0\n", encoding="utf-8")

    def fake_run(cmd: list[str]) -> str:
        if cmd[:2] == ["systemctl", "list-units"]:
            return "kai-tg-listener.service loaded active running KAI Listener"
        if cmd[:2] == ["systemctl", "show"]:
            if "User" in cmd:
                return "ubuntu"
            if "MainPID" in cmd:
                return "865040"
        if cmd[0] == "readlink":
            # /proc loest den Symlink auf — genau das ist der Unterschied.
            return "/usr/bin/python3.12" if cmd[-1].endswith("/exe") else str(repo)
        if cmd[:2] == ["git", "-C"]:
            return _SHA
        return ""

    (svc,) = collect_runtime_services(runner=fake_run)

    assert svc.unit == "kai-tg-listener.service"
    assert svc.repo_root == str(repo), "muss dem aufgeloesten /proc-cwd folgen"
    assert svc.repo_sha == _SHA
    assert svc.measurable is True
    assert svc.lock_sha256 is not None


def test_ein_prozess_ohne_proc_zugriff_wird_als_unmessbar_gefuehrt() -> None:
    def fake_run(cmd: list[str]) -> str:
        if cmd[:2] == ["systemctl", "list-units"]:
            return "kai-fremd.service loaded active running Fremd"
        if cmd[:2] == ["systemctl", "show"]:
            return "999" if "MainPID" in cmd else "kai"
        return ""  # readlink liefert nichts -> kein Zugriff

    (svc,) = collect_runtime_services(runner=fake_run)

    assert svc.measurable is False
    assert "nicht lesbar" in svc.note
