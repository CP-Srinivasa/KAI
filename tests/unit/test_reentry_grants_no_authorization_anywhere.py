"""Der Re-Entry-Zustand darf nirgends zu einer Freigabe werden.

Operator-Bedingung 2026-09-09: "``no_current_authorization`` darf nicht nur ein
UI-Label sein, wenn andere Teile von KAI diesen Zustand auswerten. Die Semantik
sollte ueberall eindeutig bleiben: Kein aktuelles Re-Entry-Gate definiert →
keine Freigabe ableiten → fail closed."

Das ist die operationalisierte Fassung von FS-4 aus dem Audit vom 2026-06-08
(Befund G): der Zustand darf in keinem Pfad zu ``can_execute`` mappen.

Zwei Waechter:

1. Der Payload selbst traegt die Freigabe-Antwort als eigenes Feld, das
   konstruktionsbedingt immer ``False`` ist — damit ein Konsument, der den
   Status-String nicht kennt, auf "keine Freigabe" faellt statt auf eine
   Vermutung.
2. Ausserhalb des Erzeugers liest kein Produktionsmodul ``reentry``. Waechst
   diese Liste, muss jemand bewusst hinsehen: ein neuer Leser koennte der erste
   sein, der aus Evidenz eine Freigabe macht.

Bewusst NICHT geprueft wird ``re_entry_mode`` in settings/health_check — das ist
ein anderes Ding (Capability-Switch-Profil ohne Datumsbezug), wie der Audit
ausdruecklich festhaelt.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.api.routers import dashboard as dashboard_mod

REPO = Path(__file__).resolve().parents[2]

# Der Erzeuger selbst. Alles andere waere ein neuer Leser.
ALLOWED_READERS = {"app/api/routers/dashboard.py"}


@pytest.mark.parametrize(
    "target",
    ["2026-05-16", "2020-01-01", "2099-12-31", "not-a-date", "", None],
)
def test_authorization_field_is_always_false(target: str | None) -> None:
    state = dashboard_mod._reentry_status(target_date=target)
    assert state["grants_execution_authorization"] is False


def test_authorization_field_is_present_in_every_branch() -> None:
    """Ein fehlendes Feld waere schlimmer als ein falsches: der Konsument raet."""
    for target in ("2026-05-16", "2099-12-31", "not-a-date"):
        assert "grants_execution_authorization" in dashboard_mod._reentry_status(target_date=target)


def test_execution_flags_do_not_follow_the_reentry_state() -> None:
    """Ein laufendes Ziel darf die Ausfuehrungs-Flags nicht bewegen."""
    lapsed = dashboard_mod._reentry_status(target_date="2020-01-01")
    running = dashboard_mod._reentry_status(target_date="2099-12-31")
    assert lapsed["grants_execution_authorization"] == running["grants_execution_authorization"]


def _production_readers_of_reentry() -> set[str]:
    """Produktionsmodule, die ``reentry`` lesen — ohne den Erzeuger."""
    found: set[str] = set()
    for path in sorted((REPO / "app").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel in ALLOWED_READERS:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "reentry" not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            # ["reentry"] oder .get("reentry") — echte Zugriffe, keine Kommentare.
            if isinstance(node, ast.Constant) and node.value == "reentry":
                found.add(rel)
                break
    return found


def test_no_production_module_outside_the_producer_reads_reentry() -> None:
    readers = _production_readers_of_reentry()
    assert readers == set(), (
        "Neue Leser des Re-Entry-Zustands:\n  "
        + "\n  ".join(sorted(readers))
        + "\n\nBitte pruefen, dass daraus keine Ausfuehrungsfreigabe abgeleitet wird "
        "(FS-4). Der Zustand ist Evidenz; die Freigabe ist execution_enabled / "
        "entry_mode. Ist der Leser harmlos, in ALLOWED_READERS eintragen."
    )


def test_the_scanner_would_actually_find_a_reader() -> None:
    """Positivkontrolle: ein gruener Waechter darf kein kaputter Scanner sein."""
    tree = ast.parse('payload = data["reentry"]')
    hits = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == "reentry"]
    assert hits, "Der Scanner erkennt einen offensichtlichen Zugriff nicht"
