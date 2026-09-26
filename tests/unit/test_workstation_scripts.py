"""Workstation-Skripte (MindBlow 2.0, W0-8): versioniert heisst auch geprueft.

Die Vault- und Backup-Skripte des Laptops lagen bis zum 26.09.2026 nur auf dem
Laptop. Diese Tests fahren die ECHTEN Skripte unter pwsh: Syntax aller Dateien,
die GFS-Aufbewahrung von kai_vault.ps1 als reine Funktion, das eingebettete
Pi-Skript unter ``bash -n``, den Anstecken-Filter der Task und den Installer
gegen ein Temp-Verzeichnis. Ohne pwsh werden sie uebersprungen (CI-Runner und
Laptop haben es).
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
WS = ROOT / "scripts" / "workstation"
VAULT = WS / "kai_vault.ps1"
INSTALL = WS / "install_workstation.ps1"
PWSH = shutil.which("pwsh")

needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh nicht installiert")


def _pwsh(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    assert PWSH is not None
    return subprocess.run(  # noqa: S603
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def _bash() -> str | None:
    if sys.platform == "win32":
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        return str(git_bash) if git_bash.exists() else None
    return shutil.which("bash")


@needs_pwsh
@pytest.mark.parametrize("script", sorted(p.name for p in WS.glob("*.ps1")))
def test_script_parses(script: str) -> None:
    code = (
        "$e=$null; $null=[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{WS / script}',[ref]$null,[ref]$e); "
        "if ($e) { $e | ForEach-Object { $_.Message }; exit 1 }"
    )
    done = _pwsh(code)
    assert done.returncode == 0, done.stdout + done.stderr


@needs_pwsh
def test_gfs_retention_keeps_daily_weekly_monthly_and_minimum() -> None:
    """Die Aufbewahrung loescht nur, was keine Regel mehr haelt (7 T / 4 W / 12 M, min 3)."""
    code = f"""
$src = Get-Content -LiteralPath '{VAULT}' -Raw
$ast = [System.Management.Automation.Language.Parser]::ParseInput($src, [ref]$null, [ref]$null)
$want = 'ConvertFrom-GenName','Test-GenName','Get-GfsKeep'
$fdef = [System.Management.Automation.Language.FunctionDefinitionAst]
$fns = $ast.FindAll({{ $args[0] -is $fdef -and $args[0].Name -in $want }}, $true)
foreach ($f in $fns) {{ . ([scriptblock]::Create($f.Extent.Text)) }}
$base = [datetime]::SpecifyKind([datetime]'2026-09-25T03:00:00', 'Utc')
$names = @(0..119 | ForEach-Object {{ $base.AddDays(-$_).ToString("yyyy-MM-dd'T'HH-mm-ss'Z'") }})
$names += '2026-09-25T01-00-00Z'
$two = @('2026-09-24T03-00-00Z','2026-09-25T03-00-00Z')
@{{
    big = @(Get-GfsKeep -Verified $names | Sort-Object)
    two = @(Get-GfsKeep -Verified $two | Sort-Object)
    junk = @(Get-GfsKeep -Verified @('kaputt','2026-09-25T03-00-00Z'))
}} | ConvertTo-Json -Compress
"""
    done = _pwsh(code)
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout)
    assert got["big"] == [
        "2026-05-31T03-00-00Z",  # Monate
        "2026-06-30T03-00-00Z",
        "2026-07-31T03-00-00Z",
        "2026-08-31T03-00-00Z",
        "2026-09-06T03-00-00Z",  # Wochen
        "2026-09-13T03-00-00Z",
        "2026-09-19T03-00-00Z",  # 7 Tage
        "2026-09-20T03-00-00Z",
        "2026-09-21T03-00-00Z",
        "2026-09-22T03-00-00Z",
        "2026-09-23T03-00-00Z",
        "2026-09-24T03-00-00Z",
        "2026-09-25T01-00-00Z",  # Mindestbestand 3 (neueste)
        "2026-09-25T03-00-00Z",
    ]
    assert got["two"] == ["2026-09-24T03-00-00Z", "2026-09-25T03-00-00Z"]
    assert got["junk"] in (["2026-09-25T03-00-00Z"], "2026-09-25T03-00-00Z")


def test_embedded_pi_script_is_valid_bash(tmp_path: Path) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("bash nicht verfuegbar")
    src = VAULT.read_text(encoding="utf-8")
    match = re.search(r"\$piScript = @'\r?\n(.*?)\r?\n'@", src, re.S)
    assert match, "eingebettetes Pi-Skript nicht gefunden"
    body = match.group(1)
    # Klartext darf die Pi nie verlassen: Zustand nur verschluesselt, Snapshots per trap weg.
    assert "trap 'rm -rf \"$W/consistent\"' EXIT" in body
    assert '-out "$W/pi_state.tar.gz.enc"' in body
    script = tmp_path / "pi_vault.sh"
    script.write_bytes(body.replace("\r\n", "\n").encode("utf-8"))
    done = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True, check=False)  # noqa: S603
    assert done.returncode == 0, done.stderr


def test_attach_task_fires_only_on_plug_in_of_the_vault_disk() -> None:
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    root = ET.parse(WS / "tasks" / "KAI-Vault-OnAttach.xml").getroot()  # noqa: S314
    sub = root.find("t:Triggers/t:EventTrigger/t:Subscription", ns)
    assert sub is not None and sub.text is not None
    assert "EventID=1006" in sub.text
    assert "Data[@Name='SerialNumber']='21493T400199'" in sub.text
    assert "Data[@Name='Capacity']!='0'" in sub.text  # Abziehen (Capacity 0) loest nicht aus
    args = root.find("t:Actions/t:Exec/t:Arguments", ns)
    assert args is not None and args.text is not None
    assert "kai_vault.ps1" in args.text and "-Quiet" in args.text
    policy = root.find("t:Settings/t:MultipleInstancesPolicy", ns)
    assert policy is not None and policy.text == "IgnoreNew"


def _lagebild() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "kai_session_lagebild", WS / "kai_session_lagebild.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Kein __pycache__ neben den Betriebsskripten: der Installer-Abgleich unten
    # zaehlt jede Datei in scripts/workstation/.
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = before
    return mod


def test_lagebild_shows_only_live_claims_and_counts_stale_ones() -> None:
    """Abgelaufene Leases sind frei (Regel 2) und duerfen das Lagebild nicht fuellen."""
    mod = _lagebild()
    now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    table = "\n".join(
        [
            "| claim_id | owner | wt | scope | created_at | expires_at | status |",
            "|---|---|---|---|---|---|---|",
            "| live | a | wt | s | 2026-09-26T10:00Z | 2026-09-27T10:00Z | active |",
            "| stale | b | wt | s | 2026-09-20T10:00Z | 2026-09-21T10:00Z | active |",
            "| undated | c | wt | s | 2026-07-11 | — | active (nachgetragen) |",
            "| blocked-live | d | wt | s | 2026-09-26T09:00Z | 2026-09-26T18:00+02:00 | blocked |",
            "| done | e | wt | s | 2026-09-26T09:00Z | 2026-09-27T09:00Z | closed — gemergt |",
            "| pipe | f | wt | a | b | 2026-09-26T09:00Z | 2026-09-27T09:00Z | **PAUSED** bis |",
        ]
    )
    active, stale = mod.open_claims(table, now)
    assert [row.split()[0] for row in active] == ["live", "blocked-live", "pipe"]
    assert stale == ["stale", "undated"], "ohne expires_at gilt created_at + 24 h"


def test_lagebild_never_fails_the_session_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Repo, ohne gh-Treffer, ohne Claims: trotzdem Ausgabe statt Ausnahme."""
    mod = _lagebild()
    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(mod, "CLAIMS", tmp_path / "fehlt.md")
    monkeypatch.setattr(mod, "GH_REPO", "invalid/does-not-exist")
    mod.main()
    out = capsys.readouterr().out
    assert out.startswith("KAI-Lagebild ")
    assert "Mainline origin/" in out and "nicht lesbar" in out
    assert "Claims: ACTIVE_CLAIMS.md nicht lesbar" in out


def test_every_workstation_file_is_installed_somewhere() -> None:
    src = INSTALL.read_text(encoding="utf-8")
    mapped = set(re.findall(r"^\s+'([^']+)'\s+=\s+'", src, re.M))
    present = {
        str(p.relative_to(WS)).replace("/", "\\")
        for p in WS.rglob("*")
        if p.is_file()
        and p.name not in {"install_workstation.ps1", "README.md"}
        and "__pycache__" not in p.parts
    }
    assert present == mapped


@needs_pwsh
def test_installer_reports_drift_and_applies(tmp_path: Path) -> None:
    home = tmp_path / "home"
    first = _pwsh(f"& '{INSTALL}' -HomeRoot '{home}'; exit $LASTEXITCODE")
    assert first.returncode == 3 and "MISSING" in first.stdout
    applied = _pwsh(f"& '{INSTALL}' -HomeRoot '{home}' -Apply; exit $LASTEXITCODE")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert (home / "KAI-mirror" / "scripts" / "kai_vault.ps1").read_bytes() == VAULT.read_bytes()
    live = home / ".local" / "bin" / "restore_test.ps1"
    live.write_text("# lokal veraendert\n", encoding="utf-8")
    drift = _pwsh(f"& '{INSTALL}' -HomeRoot '{home}'; exit $LASTEXITCODE")
    assert drift.returncode == 3 and "DRIFT" in drift.stdout
    fixed = _pwsh(f"& '{INSTALL}' -HomeRoot '{home}' -Apply; exit $LASTEXITCODE")
    assert fixed.returncode == 0
    assert list(live.parent.glob("restore_test.ps1.bak-*")), "alte Betriebskopie nicht gesichert"
