"""Phase-0 HOTP-Verifier für KAI-Live-Trading-Auth.

Spec: docs/security/kai_light_live_phase0_spec.md §2.

RFC 4226 HOTP (Counter-basiert, im Gegensatz zu TOTP/Zeit-basiert).
Counter wird in einem append-only JSONL gepflegt — jede erfolgreiche
Verifikation MUSS einen neuen Eintrag schreiben, sonst ist Replay
möglich. Counter darf nur monoton steigen.

Das Journal hat keine implizite "virgin"-Semantik: fehlt es oder ist es
leer/korrupt/unlesbar, wird jede Verifikation verweigert. Erst-Inbetriebnahme
und Recovery erfolgen ausschließlich über ``bootstrap_hotp_journal`` bzw.
``scripts/hotp_bootstrap.py`` mit einem expliziten nächsten Counter.

Sicherheits-Annahmen:
- Seed-File-Permissions sind responsability des Operators (`chmod 600`).
  Wir lesen ohne Permission-Check, weil das Filesystem-ACL-Pflicht ist;
  Code-side Checks würden falsche Sicherheit suggerieren.
- Tolerance-Window 3 deckt typische App↔Pi-Desync ab (Operator drückt
  am Phone 3 Mal "weiter" ohne dass Pi etwas registriert). Höhere
  Toleranzen senken die effektive HOTP-Security exponentiell — daher
  hartcodiert auf max 3, nicht configurable.

Der komplette Read→Verify→Append-Pfad hält einen strikten Cross-Process-Lock;
ein Erfolg wird erst nach durable ``fsync`` zurückgegeben.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyotp

from app.core.file_lock import FileLockError, append_lock

logger = logging.getLogger(__name__)

# Hardcoded — siehe Modul-Docstring. Erhöhung = Code-Edit + Re-Deploy.
MAX_ADVANCE_WINDOW: int = 3
HOTP_DIGITS: int = 6  # RFC 4226 default; Authenticator-Apps zeigen 6-stellig.


class HotpError(Exception):
    """Basis für alle HOTP-spezifischen Fehler."""


class HotpSeedMissing(HotpError):
    """Seed-File existiert nicht oder ist leer."""


class HotpSeedInvalid(HotpError):
    """Seed-File-Inhalt ist kein gültiger base32-Wert."""


class HotpReplayDetected(HotpError):
    """Der gelieferte Code wurde bereits einmal akzeptiert (counter ≤ last)."""


class HotpVerificationFailed(HotpError):
    """Code matched in 0…MAX_ADVANCE_WINDOW Counter-Schritten nicht.

    Caller MUSS dies wie eine Brute-Force-Indikation behandeln (Rate-Limit,
    Audit-Log). Niemals an Operator zurück propagieren, ob der Counter
    "knapp daneben" war — das wäre ein Side-Channel.
    """


class HotpJournalError(HotpError):
    """Basisfehler für einen nicht vertrauenswürdig les-/schreibbaren Counter-Stand."""


class HotpJournalNotInitialized(HotpJournalError):
    """Journal fehlt oder ist leer; ein expliziter Operator-Bootstrap ist nötig."""


class HotpJournalCorrupt(HotpJournalError):
    """Journal-Inhalt ist unvollständig, ungültig oder nicht monoton."""


class HotpJournalUnavailable(HotpJournalError):
    """Journal oder sein strikter Lock ist wegen eines I/O-Fehlers nicht nutzbar."""


@dataclass(frozen=True)
class HotpVerifyResult:
    """Result einer erfolgreichen Verifikation."""

    counter_used: int
    counter_advance: int  # = counter_used - last_used (mindestens 1)
    verified_at_utc: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def bootstrap_hotp_journal(journal_path: Path, *, next_counter: int) -> str:
    """Initialisiert ein noch nie vorhandenes Journal explizit und dauerhaft.

    ``next_counter`` ist absichtlich verpflichtend: bei Erst-Inbetriebnahme gibt
    der Operator ``0`` an; bei einer kontrollierten Wiederherstellung muss er den
    authenticator-seitig bekannten nächsten Counter angeben. So wird ein
    verschwundenes Journal niemals still als "virgin" interpretiert.

    Ein vorhandener Pfad wird nicht überschrieben, auch nicht wenn er leer ist.
    Der Operator muss den unbekannten Zustand zuerst separat sichern/auflösen.
    """
    if isinstance(next_counter, bool) or not isinstance(next_counter, int) or next_counter < 0:
        raise ValueError("next_counter must be an integer >= 0")

    timestamp = _utc_now()
    record = {
        "schema_version": "hotp-bootstrap-v1",
        "event": "bootstrap",
        "last_used_counter": next_counter - 1,
        "bootstrapped_at_utc": timestamp,
    }
    try:
        with append_lock(journal_path, strict=True):
            if journal_path.exists():
                raise HotpJournalCorrupt(f"journal already exists: {journal_path}")
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            with journal_path.open("x", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
    except FileLockError as exc:
        raise HotpJournalUnavailable(f"journal bootstrap lock unavailable: {exc}") from exc
    except HotpJournalError:
        raise
    except OSError as exc:
        raise HotpJournalUnavailable(f"journal bootstrap failed: {exc}") from exc
    return timestamp


class HotpVerifier:
    """RFC 4226 HOTP-Verifikation mit append-only Counter-Journal.

    Public API:
        verify(code) -> HotpVerifyResult       # bei Erfolg, sonst HotpError
        last_used_counter() -> int             # letzte Counter aus Journal
        next_expected_counter() -> int         # last + 1

    Args:
        seed_path: Pfad zum base32-Seed (z.B. ``~/.config/kai/hotp_seed.b32``).
        journal_path: append-only JSONL für Counter-Tracking
            (z.B. ``artifacts/security/hotp_counter.jsonl``).
        allow_advance: max. Counter-Schritte voraus die akzeptiert werden.
            Hard-Limit ``MAX_ADVANCE_WINDOW``.

    Raises:
        ValueError: wenn ``allow_advance`` außerhalb [1, MAX_ADVANCE_WINDOW].
    """

    def __init__(
        self,
        *,
        seed_path: Path,
        journal_path: Path,
        allow_advance: int = MAX_ADVANCE_WINDOW,
    ) -> None:
        if not 1 <= allow_advance <= MAX_ADVANCE_WINDOW:
            raise ValueError(f"allow_advance={allow_advance} außerhalb [1, {MAX_ADVANCE_WINDOW}]")
        self._seed_path = seed_path
        self._journal_path = journal_path
        self._allow_advance = allow_advance

    def _load_seed(self) -> str:
        """Liest seed-file (base32), validiert Format."""
        try:
            raw = self._seed_path.read_text(encoding="ascii").strip()
        except FileNotFoundError as exc:
            raise HotpSeedMissing(f"seed file not found: {self._seed_path}") from exc
        except OSError as exc:
            raise HotpSeedMissing(f"seed file unreadable: {exc}") from exc

        if not raw:
            raise HotpSeedMissing(f"seed file empty: {self._seed_path}")

        # base32-Validierung — RFC 4648: A-Z2-7, optional padding =.
        cleaned = raw.replace(" ", "").replace("-", "").upper()
        valid_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567=")
        if not cleaned or any(ch not in valid_chars for ch in cleaned):
            raise HotpSeedInvalid(f"seed not base32 (only A-Z2-7=): {self._seed_path}")
        return cleaned

    def _read_last_counter_unlocked(self) -> int:
        """Validiert das komplette Journal; Caller hält den strikten Lock."""
        last_counter = -1
        saw_record = False
        try:
            with self._journal_path.open("r", encoding="utf-8") as fh:
                for line_number, raw_line in enumerate(fh, start=1):
                    line = raw_line.strip()
                    if not line:
                        raise HotpJournalCorrupt(
                            f"empty journal record at line {line_number}: {self._journal_path}"
                        )
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise HotpJournalCorrupt(
                            f"invalid JSON at line {line_number}: {self._journal_path}"
                        ) from exc
                    if not isinstance(record, dict):
                        raise HotpJournalCorrupt(
                            f"non-object record at line {line_number}: {self._journal_path}"
                        )

                    schema = record.get("schema_version")
                    if schema == "hotp-bootstrap-v1":
                        bootstrap_last = record.get("last_used_counter")
                        if (
                            saw_record
                            or record.get("event") != "bootstrap"
                            or isinstance(bootstrap_last, bool)
                            or not isinstance(bootstrap_last, int)
                            or bootstrap_last < -1
                            or not isinstance(record.get("bootstrapped_at_utc"), str)
                        ):
                            raise HotpJournalCorrupt(
                                f"invalid bootstrap record at line {line_number}: "
                                f"{self._journal_path}"
                            )
                        last_counter = bootstrap_last
                    elif schema == "hotp-v1":
                        counter = record.get("counter")
                        advance = record.get("advance")
                        if (
                            isinstance(counter, bool)
                            or not isinstance(counter, int)
                            or counter < 0
                            or isinstance(advance, bool)
                            or not isinstance(advance, int)
                            or advance < 1
                            or not isinstance(record.get("verified_at_utc"), str)
                            or counter <= last_counter
                        ):
                            raise HotpJournalCorrupt(
                                f"invalid/non-monotonic record at line {line_number}: "
                                f"{self._journal_path}"
                            )
                        last_counter = counter
                    else:
                        raise HotpJournalCorrupt(
                            f"unknown schema at line {line_number}: {self._journal_path}"
                        )
                    saw_record = True
        except FileNotFoundError as exc:
            raise HotpJournalNotInitialized(
                f"journal not initialized; run explicit bootstrap: {self._journal_path}"
            ) from exc
        except HotpJournalError:
            raise
        except OSError as exc:
            raise HotpJournalUnavailable(f"journal read failed: {exc}") from exc

        if not saw_record:
            raise HotpJournalNotInitialized(
                f"journal empty; run explicit bootstrap: {self._journal_path}"
            )
        return last_counter

    def last_used_counter(self) -> int:
        """Höchster akzeptierter Counter; unbekannter Zustand wird nie zu ``-1``."""
        try:
            with append_lock(self._journal_path, strict=True):
                return self._read_last_counter_unlocked()
        except FileLockError as exc:
            raise HotpJournalUnavailable(f"journal lock unavailable: {exc}") from exc

    def next_expected_counter(self) -> int:
        """Counter, den der nächste gültige Code matcht."""
        last = self.last_used_counter()
        return 0 if last < 0 else last + 1

    def _append_journal_unlocked(self, counter: int, advance: int) -> str:
        """Append + durable flush; Caller hält den strikten Journal-Lock."""
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = _utc_now()
        record = {
            "counter": counter,
            "advance": advance,
            "verified_at_utc": timestamp,
            "schema_version": "hotp-v1",
        }
        try:
            with self._journal_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise HotpJournalUnavailable(f"journal append failed: {exc}") from exc
        return timestamp

    def verify(self, code: str) -> HotpVerifyResult:
        """Verifiziert einen HOTP-Code gegen seed + counter-Journal.

        Fail-closed: jede Unsicherheit → HotpError.

        Args:
            code: 6-stelliger HOTP-Code vom Authenticator. Whitespace wird
                getrimmt, Format-Validierung erfolgt.

        Returns:
            HotpVerifyResult mit ``counter_used``, ``counter_advance`` und
            UTC-Zeitstempel der akzeptierten Verifikation.

        Raises:
            HotpSeedMissing: seed-file fehlt/leer.
            HotpSeedInvalid: seed-file kein base32.
            HotpVerificationFailed: code matched keinen Counter in
                ``[next, next+allow_advance-1]``.
            HotpReplayDetected: code würde counter ≤ last_used setzen
                (theoretisch unreachable wenn next_expected korrekt — aber
                Defense-in-Depth für externen Journal-Tamper).
            HotpJournalError: Journal fehlt, ist korrupt/unlesbar oder kann
                nicht strikt gesperrt bzw. dauerhaft geschrieben werden.
            ValueError: code-Format invalid (nicht 6 stellen oder nicht digit-only).
        """
        cleaned = code.strip().replace(" ", "")
        if len(cleaned) != HOTP_DIGITS or not cleaned.isdigit():
            raise ValueError(f"code must be {HOTP_DIGITS} digits, got {len(cleaned)}")

        seed = self._load_seed()
        hotp = pyotp.HOTP(seed, digits=HOTP_DIGITS)

        try:
            # The money-path invariant is one critical section: a contender may
            # not read the old counter until the winner's durable append exists.
            with append_lock(self._journal_path, strict=True):
                last = self._read_last_counter_unlocked()
                next_counter = last + 1

                # Tolerance-Loop: probiere [next, next+1, ..., next+allow_advance-1].
                for offset in range(self._allow_advance):
                    candidate_counter = next_counter + offset
                    if hotp.verify(cleaned, candidate_counter):
                        if candidate_counter <= last:
                            raise HotpReplayDetected(
                                f"counter {candidate_counter} <= last_used {last}"
                            )
                        advance = candidate_counter - last  # ≥ 1
                        ts = self._append_journal_unlocked(candidate_counter, advance)
                        logger.info(
                            "hotp_verify_ok counter=%d advance=%d",
                            candidate_counter,
                            advance,
                        )
                        return HotpVerifyResult(
                            counter_used=candidate_counter,
                            counter_advance=advance,
                            verified_at_utc=ts,
                        )
        except FileLockError as exc:
            raise HotpJournalUnavailable(f"journal lock unavailable: {exc}") from exc

        # Kein Match in der Tolerance-Window.
        logger.warning(
            "hotp_verify_failed next_expected=%d window=%d",
            next_counter,
            self._allow_advance,
        )
        raise HotpVerificationFailed(
            f"code rejected (window {next_counter}…{next_counter + self._allow_advance - 1})"
        )


def humanize_counter(verifier: HotpVerifier) -> str:
    """Human-readable summary for /live status command output."""
    last = verifier.last_used_counter()
    nxt = last + 1
    if last < 0:
        return "HOTP-Counter: explicitly bootstrapped (next expected = 0)"
    return f"HOTP-Counter: last_used={last}, next_expected={nxt}"
