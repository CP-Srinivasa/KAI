from pathlib import Path

from app.observability.service_name import UNKNOWN, parse_cgroup, service_name


def test_cgroup_v2_nennt_die_unit() -> None:
    assert parse_cgroup("0::/system.slice/kai-server.service\n") == "kai-server"


def test_template_unit_und_v1_zeilen() -> None:
    text = "12:pids:/system.slice/kai-unit-failure-notify@x.service\n1:name=systemd:/\n"
    assert parse_cgroup(text) == "kai-unit-failure-notify@x"


def test_ohne_service_unbekannt() -> None:
    assert parse_cgroup("0::/user.slice/user-1000.slice/session-3.scope\n") == UNKNOWN


def test_ohne_datei_unbekannt(tmp_path: Path) -> None:
    service_name.cache_clear()
    assert service_name(tmp_path / "fehlt") == UNKNOWN
    service_name.cache_clear()
