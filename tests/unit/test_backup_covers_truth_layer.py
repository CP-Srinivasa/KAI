"""Das Backup muss genau das enthalten, was seine Überschrift verspricht.

``kai_backup_artifacts.sh`` trug den Kommentar „Critical artifacts that, if lost,
cannot be reconstructed from code alone" — und listete darunter Alert-, Paper-
und Telegram-Ströme, aber **keine** der Dateien, auf die das buchstäblich
zutrifft: Prä-Reg-Ledger, Attestierungskette, Hypothesen-Ledger, Verdikte.
Evidenz-Ströme sind aus der DB und den Quellen teilweise rekonstruierbar; ein
versiegeltes Kriterium samt Hash ist es nicht.

Dazu kam: unter 52 Timern sicherte keiner. Das Skript existierte, lief aber nie.

Dieser Test ist ein Kontrakt, kein Verhaltenstest — er hält fest, WAS gesichert
werden muss, damit die Liste nicht wieder auseinanderläuft. Er liest die
Shell-Quelle, weil die Liste dort die einzige Wahrheit ist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "kai_backup_artifacts.sh"
UNIT_DIR = REPO_ROOT / "deploy" / "systemd"

# Ohne diese Dateien ist die Forschungshistorie unwiederbringlich: die
# versiegelten Kriterien, die Kette darüber und der Trial-Count, an dem die
# DSR-Deflation hängt.
UNRECONSTRUCTABLE = (
    "artifacts/research/prereg_ledger.jsonl",
    "artifacts/truth/attestation_ledger.jsonl",
    "artifacts/research/hypothesis_ledger.jsonl",
    "artifacts/research/falsification_verdicts.jsonl",
)


@pytest.fixture(scope="module")
def script_text() -> str:
    return BACKUP_SCRIPT.read_text(encoding="utf-8")


def _array_entries(text: str, name: str) -> set[str]:
    match = re.search(rf"^{name}=\((.*?)^\)", text, re.S | re.M)
    if not match:
        return set()
    return set(re.findall(r'"([^"]+)"', match.group(1)))


@pytest.mark.parametrize("relpath", UNRECONSTRUCTABLE)
def test_truth_layer_ist_im_backup(script_text: str, relpath: str) -> None:
    sources = _array_entries(script_text, "DEFAULT_SOURCES")
    assert relpath in sources, (
        f"{relpath} fehlt in DEFAULT_SOURCES — ohne diese Datei beweist ein "
        "OTS-Anker einen Hash zu einem Inhalt, den niemand mehr hat."
    )


def test_versiegelte_prognosen_werden_als_verzeichnis_gesichert(script_text: str) -> None:
    dirs = _array_entries(script_text, "DEFAULT_SOURCE_DIRS")
    assert "artifacts/research/forecaster_panel" in dirs


def test_fehlende_wahrheitsquelle_ist_ein_fehler_kein_hinweis(script_text: str) -> None:
    required = _array_entries(script_text, "REQUIRED_SOURCES")
    assert "artifacts/research/prereg_ledger.jsonl" in required
    assert "artifacts/truth/attestation_ledger.jsonl" in required
    # Der Abbruchpfad muss existieren, sonst ist die Liste Dekoration.
    assert "fail_missing_required" in script_text


def test_evidenzstroeme_bleiben_erhalten(script_text: str) -> None:
    """Rein additiv — die bisherige Abdeckung darf nicht verlorengehen."""
    sources = _array_entries(script_text, "DEFAULT_SOURCES")
    for relpath in (
        "artifacts/alert_audit.jsonl",
        "artifacts/alert_outcomes.jsonl",
        "artifacts/paper_execution_audit.jsonl",
        "artifacts/trading_loop_audit.jsonl",
    ):
        assert relpath in sources


def test_backup_hat_eine_unit_und_einen_timer() -> None:
    """Ein Skript ohne Timer ist ein Backup, das nie stattfindet."""
    assert (UNIT_DIR / "kai-backup-artifacts.service").is_file()
    timer = UNIT_DIR / "kai-backup-artifacts.timer"
    assert timer.is_file()
    text = timer.read_text(encoding="utf-8")
    assert "OnCalendar=" in text
    assert "Persistent=true" in text
    # Timer-Requires-Kaskade (#414): niemals Requires= auf einem Timer.
    # Nur echte Direktiven prüfen — der Kommentar darüber nennt das Wort
    # absichtlich und ist kein Verstoß.
    directives = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any(line.startswith("Requires=") for line in directives)


def test_backup_unit_ist_gehaertet() -> None:
    text = (UNIT_DIR / "kai-backup-artifacts.service").read_text(encoding="utf-8")
    assert "User=ubuntu" in text
    assert "NoNewPrivileges=true" in text
    assert "ProtectSystem=strict" in text


# --------------------------------------------------------------------------- #
# Geld-Journale (ADR 0017 §5, Architect P1)
# --------------------------------------------------------------------------- #

# Diese drei tragen jede Wertbewegung, jede Freigabe und jeden HOTP-Counter.
# Aus Code sind sie nicht rekonstruierbar, aus der DB auch nicht — und ohne den
# HOTP-Counter ist der Geldpfad nach einem Restore fail-closed dicht.
MONEY_JOURNALS = (
    "artifacts/payments/payment_journal.jsonl",
    "artifacts/ln_ops_ledger_v2.jsonl",
    "artifacts/ln_hotp_journal.jsonl",
)


@pytest.mark.parametrize("relpath", MONEY_JOURNALS)
def test_geldjournale_liegen_im_archiv(script_text: str, relpath: str) -> None:
    """``REQUIRED_SOURCES`` allein sichert NICHTS.

    Die Liste ist eine reine Anwesenheitspruefung; gepackt wird
    ``DEFAULT_SOURCES``. Ein Geld-Journal nur dort einzutragen haette eine
    Zusage erzeugt, die das Skript nicht einloest.
    """
    assert relpath in _array_entries(script_text, "DEFAULT_SOURCES")


@pytest.mark.parametrize("relpath", MONEY_JOURNALS)
def test_ein_verschwundenes_geldjournal_ist_ein_fehler(script_text: str, relpath: str) -> None:
    """Was einmal im Archiv lag, muss es weiter tun.

    In ``REQUIRED_SOURCES`` gehoeren sie NICHT: vor der ersten Zahlung
    existiert keins von ihnen, und ein Backup, das auf einer frischen Anlage
    mit Exit 3 abbricht, wird abgeschaltet. Die Bedingung ist deshalb an
    Evidenz geknuepft — das Manifest des letzten Archivs.
    """
    assert relpath in _array_entries(script_text, "MONEY_SOURCES")
    assert "fail_missing_money_journal" in script_text


def test_die_evidenz_fuer_verschwunden_kommt_aus_dem_manifest(script_text: str) -> None:
    """Ohne Manifest keine Aussage — und dann auch kein Abbruch."""
    assert "manifest.json" in script_text
