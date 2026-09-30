"""Oracle-Vorgänge: niemand vergisst eine Käuferbeschwerde (Stream-Vertrag G4).

Die Hilfe-Seite sagt eine erste persönliche Antwort binnen zwei Werktagen und eine
Lösung oder einen begründeten Zwischenstand binnen sieben Werktagen zu. Der
Health-Check warnt, sobald ein Vorgang das überschreitet; ``scripts/oracle_case.py``
belegt Antwort und Abschluss append-only.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app import oracle_legal as legal
from app.alerts.health_check_payments import check_oracle_cases

_FRIDAY = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _cli():
    path = Path(__file__).resolve().parents[2] / "scripts" / "oracle_case.py"
    spec = importlib.util.spec_from_file_location("oracle_case", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _case(tmp_path: Path, when: datetime, kind: str = "meldung") -> tuple[Path, str]:
    adir = tmp_path / "artifacts"
    path = adir / "oracle" / "oracle_cases.jsonl"
    fields = (
        {"problem": "nicht_erhalten", "email": "k@example.org"}
        if kind == "meldung"
        else {
            "referenz": "ab" * 32,
            "email": "k@example.org",
        }
    )
    case = legal.record_case(kind, fields, now=when, path=path)
    return adir, case["case_id"]


def test_workdays_skip_the_weekend() -> None:
    assert legal.workdays_between(_FRIDAY, datetime(2026, 10, 5, tzinfo=UTC)) == 1  # Mo
    assert legal.workdays_between(_FRIDAY, datetime(2026, 10, 7, tzinfo=UTC)) == 3  # Mi
    assert legal.workdays_between(_FRIDAY, _FRIDAY) == 0


def test_no_file_and_fresh_cases_are_quiet(tmp_path: Path) -> None:
    assert check_oracle_cases(tmp_path, now=_FRIDAY) == []
    adir, _ = _case(tmp_path, _FRIDAY)
    assert check_oracle_cases(adir, now=datetime(2026, 10, 6, tzinfo=UTC)) == []  # 2 Werktage


def test_unanswered_after_two_workdays_warns(tmp_path: Path) -> None:
    adir, case_id = _case(tmp_path, _FRIDAY)
    [issue] = check_oracle_cases(adir, now=datetime(2026, 10, 7, tzinfo=UTC))
    assert issue.severity == "warning" and issue.component == "oracle_cases"
    assert case_id in issue.message and "ohne Antwort" in issue.message
    assert "k@example.org" not in issue.message


def test_answered_but_unresolved_after_seven_workdays_warns(tmp_path: Path) -> None:
    adir, case_id = _case(tmp_path, _FRIDAY, kind="widerruf")
    path = adir / "oracle" / "oracle_cases.jsonl"
    legal.mark_case(case_id, "beantwortet", "Eingang bestätigt", now=_FRIDAY, path=path)
    assert check_oracle_cases(adir, now=datetime(2026, 10, 9, tzinfo=UTC)) == []  # 5 Werktage
    [issue] = check_oracle_cases(adir, now=datetime(2026, 10, 14, tzinfo=UTC))  # 8 Werktage
    assert "nicht erledigt" in issue.message
    legal.mark_case(case_id, "erledigt", "Widerruf abgewickelt", now=_FRIDAY, path=path)
    assert check_oracle_cases(adir, now=datetime(2026, 10, 30, tzinfo=UTC)) == []


def test_unreadable_log_warns(tmp_path: Path) -> None:
    path = tmp_path / "oracle" / "oracle_cases.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("{kaputt\n", encoding="utf-8")
    [issue] = check_oracle_cases(tmp_path, now=_FRIDAY)
    assert "nicht lesbar" in issue.message


def test_marking_an_unknown_case_is_refused(tmp_path: Path) -> None:
    adir, _ = _case(tmp_path, _FRIDAY)
    with pytest.raises(KeyError):
        legal.mark_case(
            "KAI-O-000000-XXXXXX",
            "erledigt",
            "x",
            now=_FRIDAY,
            path=adir / "oracle" / "oracle_cases.jsonl",
        )
    with pytest.raises(ValueError):
        legal.mark_case(
            "x", "geloescht", "x", now=_FRIDAY, path=adir / "oracle" / "oracle_cases.jsonl"
        )


def test_cli_lists_and_marks(tmp_path: Path, capsys) -> None:
    adir, case_id = _case(tmp_path, _FRIDAY)
    path = adir / "oracle" / "oracle_cases.jsonl"
    cli = _cli()
    assert cli.main(["liste"], path=path) == 0
    assert case_id in capsys.readouterr().out
    assert cli.main(["erledigt", case_id, ""], path=path) == 2
    assert cli.main(["erledigt", case_id, "Zugang wiederhergestellt"], path=path) == 0
    assert cli.main(["liste"], path=path) == 0
    assert "Keine offenen Vorgänge" in capsys.readouterr().out
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, "append-only: Vorgang plus Abschluss, nichts ueberschrieben"
    sys.modules.pop("oracle_case", None)
