"""Coverage-Ratchet-Contract (B4): up-only, laut bei Regression, robust bei Müll.

Spiegel-Vertrag zu test_godfile_ratchet.py — dort down-only, hier up-only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import coverage_ratchet as cr  # noqa: E402


def _write_xml(path: Path, line_rate: float) -> None:
    """Schreibt einen minimalen Cobertura-Report mit gegebener line-rate (0..1)."""
    path.write_text(
        '<?xml version="1.0" ?>\n'
        f'<coverage line-rate="{line_rate}" branch-rate="0" lines-covered="1" '
        'lines-valid="1" branches-covered="0" branches-valid="0" complexity="0" '
        'version="7.14.1" timestamp="0">\n'
        "  <packages></packages>\n"
        "</coverage>\n",
        encoding="utf-8",
    )


def _write_baseline(path: Path, pct: float) -> None:
    path.write_text(
        json.dumps({"min_line_rate_pct": pct, "note": "test", "source": "test"}),
        encoding="utf-8",
    )


def _run(tmp_path: Path, *, line_rate: float, baseline_pct: float, update: bool = False):
    xml = tmp_path / "coverage.xml"
    bl = tmp_path / "coverage_baseline.json"
    _write_xml(xml, line_rate)
    _write_baseline(bl, baseline_pct)
    argv = ["--xml", str(xml), "--baseline", str(bl)]
    if update:
        argv.append("--update")
    rc = cr.main(argv)
    return rc, bl


def test_below_baseline_fails(tmp_path: Path) -> None:
    # gemessen 70.00% < Baseline 72.3% -> Regression
    rc, _ = _run(tmp_path, line_rate=0.70, baseline_pct=72.3)
    assert rc == cr.EXIT_REGRESSION


def test_at_baseline_passes(tmp_path: Path) -> None:
    # gemessen == Baseline -> ok (kein strikt-kleiner)
    rc, _ = _run(tmp_path, line_rate=0.723, baseline_pct=72.3)
    assert rc == cr.EXIT_OK


def test_above_baseline_passes(tmp_path: Path) -> None:
    rc, _ = _run(tmp_path, line_rate=0.80, baseline_pct=72.3)
    assert rc == cr.EXIT_OK


def test_update_raises_baseline_by_margin(tmp_path: Path) -> None:
    # gemessen 80.00% -> neue Baseline 80.0 - 0.5 = 79.5, > alte 72.3 -> anheben
    rc, bl = _run(tmp_path, line_rate=0.80, baseline_pct=72.3, update=True)
    assert rc == cr.EXIT_OK
    assert json.loads(bl.read_text(encoding="utf-8"))["min_line_rate_pct"] == 79.5


def test_update_never_lowers_baseline(tmp_path: Path) -> None:
    # gemessen 72.6% -> 72.6-0.5 = 72.1 < alte 72.3 -> NICHT senken, unveraendert
    rc, bl = _run(tmp_path, line_rate=0.726, baseline_pct=72.3, update=True)
    assert rc == cr.EXIT_OK
    assert json.loads(bl.read_text(encoding="utf-8"))["min_line_rate_pct"] == 72.3


def test_update_on_regression_still_fails_and_keeps_baseline(tmp_path: Path) -> None:
    # gemessen 60% < Baseline 72.3 -> auch im --update-Lauf FAIL, Baseline bleibt
    rc, bl = _run(tmp_path, line_rate=0.60, baseline_pct=72.3, update=True)
    assert rc == cr.EXIT_REGRESSION
    assert json.loads(bl.read_text(encoding="utf-8"))["min_line_rate_pct"] == 72.3


def test_update_preserves_other_baseline_fields(tmp_path: Path) -> None:
    xml = tmp_path / "coverage.xml"
    bl = tmp_path / "coverage_baseline.json"
    _write_xml(xml, 0.90)
    bl.write_text(
        json.dumps({"min_line_rate_pct": 50.0, "note": "keep-me", "source": "keep-src"}),
        encoding="utf-8",
    )
    assert cr.main(["--xml", str(xml), "--baseline", str(bl), "--update"]) == cr.EXIT_OK
    data = json.loads(bl.read_text(encoding="utf-8"))
    assert data["min_line_rate_pct"] == 89.5
    assert data["note"] == "keep-me"
    assert data["source"] == "keep-src"


def test_missing_xml_reports_clearly(tmp_path: Path, capsys) -> None:
    bl = tmp_path / "coverage_baseline.json"
    _write_baseline(bl, 72.3)
    rc = cr.main(["--xml", str(tmp_path / "nope.xml"), "--baseline", str(bl)])
    assert rc == cr.EXIT_BAD_REPORT
    out = capsys.readouterr().out
    assert "nicht gefunden" in out
    assert "--cov-report=xml" in out


def test_corrupt_xml_reports_clearly(tmp_path: Path, capsys) -> None:
    xml = tmp_path / "coverage.xml"
    xml.write_text("<coverage line-rate=  <<<not-xml", encoding="utf-8")
    bl = tmp_path / "coverage_baseline.json"
    _write_baseline(bl, 72.3)
    rc = cr.main(["--xml", str(xml), "--baseline", str(bl)])
    assert rc == cr.EXIT_BAD_REPORT
    assert "kein gueltiger Cobertura-Report" in capsys.readouterr().out


def test_wrong_root_element_reports_clearly(tmp_path: Path, capsys) -> None:
    xml = tmp_path / "coverage.xml"
    xml.write_text('<?xml version="1.0"?>\n<html><body>nope</body></html>\n', encoding="utf-8")
    bl = tmp_path / "coverage_baseline.json"
    _write_baseline(bl, 72.3)
    rc = cr.main(["--xml", str(xml), "--baseline", str(bl)])
    assert rc == cr.EXIT_BAD_REPORT
    assert "kein gueltiger Cobertura-Report" in capsys.readouterr().out


def test_missing_line_rate_attr_reports_clearly(tmp_path: Path, capsys) -> None:
    xml = tmp_path / "coverage.xml"
    xml.write_text(
        '<?xml version="1.0"?>\n<coverage version="7.14.1"></coverage>\n', encoding="utf-8"
    )
    bl = tmp_path / "coverage_baseline.json"
    _write_baseline(bl, 72.3)
    rc = cr.main(["--xml", str(xml), "--baseline", str(bl)])
    assert rc == cr.EXIT_BAD_REPORT
    assert "kein gueltiger Cobertura-Report" in capsys.readouterr().out
