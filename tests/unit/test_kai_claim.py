"""Claims-Helfer (MindBlow 2.0, W3): Register ACTIVE_CLAIMS.md bleibt die einzige Quelle.

Geprueft wird, dass das Werkzeug die Zeilen so schreibt wie ein Mensch (gleiche
Spalten, Lease 24 h, Zeilenenden der Datei), fremde aktive Claims zuverlaessig
als Kollision erkennt (Datei, Verzeichnis, Muster, nackter Dateiname) und dabei
eigene, geschlossene und abgelaufene Claims NICHT als Kollision zaehlt.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

WS = Path(__file__).resolve().parents[2] / "scripts" / "workstation"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("kai_claim", WS / "kai_claim.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        sys.modules["kai_claim"] = mod
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = before
    return mod


kc = _load()

HEAD = (
    "# ACTIVE_CLAIMS\n\nRegeln ...\n\n"
    "| claim_id | owner_agent | worktree/branch | scope (Pfade/Thema) "
    "| created_at | expires_at | status |\n"
    "|---|---|---|---|---|---|---|\n"
)


def _ts(delta_h: float) -> str:
    return f"{datetime.now(UTC) + timedelta(hours=delta_h):%Y-%m-%dT%H:%MZ}"


def _row(
    cid: str, owner: str, scope: str, *, created: float = -1, expires: float = 23, status="active"
):
    return f"| {cid} | {owner} | wt | {scope} | {_ts(created)} | {_ts(expires)} | {status} |\n"


def _register(tmp_path: Path, *rows: str, eol: str = "\n") -> Path:
    path = tmp_path / "ACTIVE_CLAIMS.md"
    text = HEAD + "".join(rows)
    path.write_bytes(text.replace("\n", eol).encode("utf-8"))
    return path


def test_check_finds_foreign_live_claims_by_file_dir_pattern_and_basename(tmp_path: Path) -> None:
    path = _register(
        tmp_path,
        _row("fd-1", "claude bin-fd", "app/pay/ und tests/unit/test_pay_outbox.py (Outbox)"),
        _row(
            "cx-1", "codex", "deploy/systemd/kai-standby-{data,system}.{service,timer} Kommentare"
        ),
        _row("bd-1", "claude bin-bd", "nur sync-scb-from-node.ps1 anfassen"),
    )
    claims = kc.parse(path.read_text(encoding="utf-8"))
    now = datetime.now(UTC)

    def hit(p: str) -> list[str]:
        return [c.claim_id for c, _, _ in kc.collisions(claims, [p], now, owner="bin-ea")]

    assert hit("app/pay/outbox.py") == ["fd-1"], "Datei unter geclaimtem Verzeichnis"
    assert hit("app/pay") == ["fd-1"], "Verzeichnis selbst"
    assert hit("tests/unit/test_pay_outbox.py") == ["fd-1"], "exakte Datei"
    assert hit("deploy/systemd/kai-standby-data.timer") == ["cx-1"], "Klammer-Muster"
    assert hit("C:/Users/sasch/KAI-mirror/sync-scb-from-node.ps1") == ["bd-1"], "nackter Dateiname"
    assert hit("app/alerts/health_check_host.py") == []
    assert hit("app/payments/journal.py") == [], "app/pay/ ist nicht app/payments/"


def test_own_closed_and_stale_claims_do_not_collide(tmp_path: Path) -> None:
    path = _register(
        tmp_path,
        _row("own", "claude bin-ea", "scripts/workstation/"),
        _row("done", "claude bin-fd", "scripts/workstation/", status="closed 2026-09-26 — gemergt"),
        _row("old", "codex", "scripts/workstation/", created=-50, expires=-26),
        _row("undated", "claude-parallel", "scripts/workstation/", created=-72, expires=0).replace(
            f"| {_ts(0)} |", "| — |"
        ),
    )
    claims = kc.parse(path.read_text(encoding="utf-8"))
    assert (
        kc.collisions(claims, ["scripts/workstation/kai_claim.py"], datetime.now(UTC), "bin-ea")
        == []
    )
    live, stale = kc.split_live(claims, datetime.now(UTC))
    assert [c.claim_id for c in live] == ["own"]
    assert sorted(c.claim_id for c in stale) == ["old", "undated"], (
        "ohne expires_at: created + 24 h"
    )


def test_add_writes_a_regular_row_after_the_separator_and_refuses_duplicates(
    tmp_path: Path,
) -> None:
    path = _register(tmp_path, _row("alt", "codex", "docs/x.md"))
    argv = ["--file", str(path), "add", "--id", "neu", "--owner", "claude bin-ea"]
    argv += ["--where", "C:/tmp/kai-neu / claude/neu", "--scope", "scripts/foo.py | mit Pipe"]
    assert kc.main(argv) == 0
    lines = path.read_text(encoding="utf-8").splitlines()
    sep = next(i for i, line in enumerate(lines) if line.startswith("|---"))
    row = lines[sep + 1]
    assert row.startswith(
        "| neu | claude bin-ea | C:/tmp/kai-neu / claude/neu | scripts/foo.py / mit Pipe |"
    )
    assert row.endswith("| active |")
    claim = kc.parse(path.read_text(encoding="utf-8"))[0]
    assert claim.claim_id == "neu" and claim.expiry() is not None
    assert kc.parse_ts(claim.expires) - kc.parse_ts(claim.created) == timedelta(hours=24)
    assert kc.main(argv) == 2, "doppelte claim_id wird abgelehnt"


def test_add_refuses_overlap_unless_forced(tmp_path: Path) -> None:
    path = _register(tmp_path, _row("fd-1", "claude bin-fd", "app/pay/"))
    argv = ["--file", str(path), "add", "--id", "x", "--owner", "claude bin-ea", "--where", "w"]
    assert kc.main([*argv, "--scope", "app/pay/outbox.py"]) == 1
    assert "| x |" not in path.read_text(encoding="utf-8")
    assert kc.main([*argv, "--scope", "app/pay/outbox.py", "--force"]) == 0


def test_close_and_expire_rewrite_only_the_status_cell(tmp_path: Path) -> None:
    path = _register(
        tmp_path,
        _row("live", "claude bin-ea", "a/b.py"),
        _row("old", "codex", "c/d.py", created=-50, expires=-26),
        eol="\r\n",
    )
    assert kc.main(["--file", str(path), "close", "--id", "live", "--note", "#1 gemergt"]) == 0
    assert kc.main(["--file", str(path), "close", "--id", "live", "--note", "nochmal"]) == 2
    assert kc.main(["--file", str(path), "expire"]) == 0
    raw = path.read_bytes().decode("utf-8")
    assert raw.count("\r\n") == raw.count("\n"), "Zeilenenden der Datei bleiben CRLF"
    rows = {c.claim_id: c for c in kc.parse(raw)}
    assert rows["live"].status.startswith("closed ") and rows["live"].status.endswith("#1 gemergt")
    assert rows["old"].status.startswith("expired (Lease abgelaufen")
    assert rows["old"].status.endswith("— vorher: active")
    assert rows["old"].scope == "c/d.py", "Freitext unveraendert"


def test_check_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _register(tmp_path, _row("fd-1", "claude bin-fd", "app/pay/"))
    assert kc.main(["--file", str(path), "check", "app/alerts/x.py", "--owner", "bin-ea"]) == 0
    assert kc.main(["--file", str(path), "check", "app/pay/y.py", "--owner", "bin-ea"]) == 1
    assert "KOLLISION: app/pay/y.py beruehrt app/pay/ — fd-1" in capsys.readouterr().out
    assert kc.main(["--file", str(path), "check", "app/pay/y.py", "--owner", "bin-fd"]) == 0
    assert kc.main(["--file", str(tmp_path / "fehlt.md"), "list"]) == 2


def test_lock_blocks_writers_and_a_stale_lock_expires(tmp_path: Path) -> None:
    path = _register(tmp_path)
    lock = path.with_name(path.name + ".lock")
    lock.write_text("4711", encoding="utf-8")
    with pytest.raises(TimeoutError):
        with kc._locked(path, wait_s=0.3):
            pass
    old = time.time() - 600
    import os

    os.utime(lock, (old, old))
    with kc._locked(path, wait_s=0.3):
        assert lock.exists()
    assert not lock.exists(), "Sperre nach dem Schreiben entfernt"
