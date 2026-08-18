"""Ein fehlgeschlagener HOTP-Versuch war folgenlos — beliebig oft.

Der Docstring von ``HotpVerificationFailed`` verlangt es seit jeher wörtlich:

    "Caller MUSS dies wie eine Brute-Force-Indikation behandeln
     (Rate-Limit, Audit-Log)."

Getan hat es niemand. Ein Fehlversuch schrieb eine ``logger.warning``-Zeile und
war danach vergessen; der nächste Versuch startete bei null. Audit-Befund
2026-08-09 (P1, Re-Arm-Blocker fuer den Lightning-Zahlpfad).

Die Rechnung: 6 Stellen und ein Toleranzfenster von 3 machen **4 von 10^6**
Codes gueltig, also ~1:250 000 je Versuch. Ohne Bremse und bei ~10 Anfragen/s
ist der Erwartungswert rund **7 Stunden**. Mit 5 Versuchen je 15 min sind es
480 Versuche/Tag und damit ~520 Tage — bei einem Geheimnis, das der Operator
laengst rotiert haette.

Die Sperre lebt im SELBEN append-only Journal wie der Counter: eine separate
Datei koennte geloescht werden, um die Sperre zurueckzusetzen. Das Journal
nicht — es zu loeschen oder zu kuerzen bricht den Geldpfad vollstaendig und
faellt fail-closed (``HotpJournalNotInitialized`` / ``HotpJournalCorrupt``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyotp
import pytest

from app.security.hotp_auth import (
    HOTP_FAILURE_LOCKOUT_SECONDS,
    HOTP_MAX_FAILED_ATTEMPTS,
    HotpLockedOut,
    HotpVerificationFailed,
    HotpVerifier,
    bootstrap_hotp_journal,
)

_TEST_SEED = "JBSWY3DPEHPK3PXP"


def _verifier(tmp_path: Path) -> HotpVerifier:
    seed = tmp_path / "seed.b32"
    seed.write_text(_TEST_SEED, encoding="ascii")
    journal = tmp_path / "hotp_counter.jsonl"
    bootstrap_hotp_journal(journal, next_counter=0)
    return HotpVerifier(seed_path=seed, journal_path=journal)


def _wrong_code(counter: int) -> str:
    """Ein Code, der garantiert NICHT im Fenster liegt."""
    return pyotp.HOTP(_TEST_SEED, digits=6).at(counter + 10_000)


def test_fehlversuche_werden_gezaehlt_und_sperren(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path)
    for i in range(HOTP_MAX_FAILED_ATTEMPTS):
        with pytest.raises(HotpVerificationFailed):
            verifier.verify(_wrong_code(i))
    # Der naechste Versuch prallt an der Sperre ab — auch mit RICHTIGEM Code.
    correct = pyotp.HOTP(_TEST_SEED, digits=6).at(0)
    with pytest.raises(HotpLockedOut):
        verifier.verify(correct)


def test_sperre_verbraucht_den_counter_nicht(tmp_path: Path) -> None:
    """Ein abgewiesener Versuch darf den Geldpfad-Counter nicht bewegen."""
    verifier = _verifier(tmp_path)
    before = verifier.last_used_counter()
    for i in range(HOTP_MAX_FAILED_ATTEMPTS + 2):
        with pytest.raises((HotpVerificationFailed, HotpLockedOut)):
            verifier.verify(_wrong_code(i))
    assert verifier.last_used_counter() == before


def test_erfolg_setzt_die_zaehlung_zurueck(tmp_path: Path) -> None:
    """Vier Fehlversuche und ein Erfolg duerfen nicht kumulieren."""
    verifier = _verifier(tmp_path)
    hotp = pyotp.HOTP(_TEST_SEED, digits=6)
    for i in range(HOTP_MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(HotpVerificationFailed):
            verifier.verify(_wrong_code(i))
    assert verifier.verify(hotp.at(0)).counter_used == 0
    # Zaehlung steht wieder bei 0 — sonst waere der Operator nach dem naechsten
    # Vertipper ausgesperrt.
    for i in range(HOTP_MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(HotpVerificationFailed):
            verifier.verify(_wrong_code(i))
    assert verifier.verify(hotp.at(1)).counter_used == 1


def test_sperre_laeuft_ab(tmp_path: Path) -> None:
    """Nach dem Fenster ist der Operator nicht dauerhaft ausgesperrt."""
    verifier = _verifier(tmp_path)
    for i in range(HOTP_MAX_FAILED_ATTEMPTS):
        with pytest.raises(HotpVerificationFailed):
            verifier.verify(_wrong_code(i))
    # Fehl-Eintraege kuenstlich altern lassen (das Journal ist die Uhr).
    journal = tmp_path / "hotp_counter.jsonl"
    stale = (datetime.now(UTC) - timedelta(seconds=HOTP_FAILURE_LOCKOUT_SECONDS + 60)).isoformat(
        timespec="seconds"
    )
    lines = []
    for raw in journal.read_text(encoding="utf-8").splitlines():
        record = json.loads(raw)
        if record.get("schema_version") == "hotp-fail-v1":
            record["failed_at_utc"] = stale
        lines.append(json.dumps(record, sort_keys=True))
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert verifier.verify(pyotp.HOTP(_TEST_SEED, digits=6).at(0)).counter_used == 0


def test_formatfehler_zaehlt_nicht_als_versuch(tmp_path: Path) -> None:
    """Sonst sperrt ein trivialer Mülleingang den Operator aus (DoS)."""
    verifier = _verifier(tmp_path)
    for _ in range(HOTP_MAX_FAILED_ATTEMPTS + 3):
        with pytest.raises(ValueError):
            verifier.verify("abc")
    assert verifier.verify(pyotp.HOTP(_TEST_SEED, digits=6).at(0)).counter_used == 0


def test_sperr_eintrag_bricht_die_journal_pruefung_nicht(tmp_path: Path) -> None:
    """Der strikte Parser muss den neuen Datensatz kennen, sonst faellt der
    Geldpfad nach dem ersten Vertipper dauerhaft in HotpJournalCorrupt."""
    verifier = _verifier(tmp_path)
    with pytest.raises(HotpVerificationFailed):
        verifier.verify(_wrong_code(0))
    # last_used_counter() validiert das KOMPLETTE Journal.
    assert verifier.last_used_counter() == -1


def test_telegram_nennt_die_sperre_statt_zu_crashen() -> None:
    """``HotpLockedOut`` war in keinem Handler — der Befehl waere durchgeschlagen.

    Die Sperre wird dem Operator ausdruecklich GENANNT. Das ist kein
    Side-Channel: sie greift VOR jedem Code-Vergleich und verraet daher nichts
    ueber den Code. Verschwiege man sie, hielte der Operator ein korrekt
    abgewiesenes Geraet fuer kaputt — waehrend gerade jemand durchprobiert.
    """
    from app.messaging.live_telegram_commands import handle_live_unlock

    class _LockedEngine:
        def unlock(self, code: str) -> None:
            raise HotpLockedOut("5 failed attempts")

    reply = handle_live_unlock("/live unlock 123456", _LockedEngine())
    assert "gesperrt" in reply.lower()
    assert "abgelehnt" not in reply.lower()
