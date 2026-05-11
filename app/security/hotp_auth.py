"""Phase-0 HOTP-Verifier für KAI-Live-Trading-Auth.

Spec: docs/security/kai_light_live_phase0_spec.md §2.

RFC 4226 HOTP (Counter-basiert, im Gegensatz zu TOTP/Zeit-basiert).
Counter wird in einem append-only JSONL gepflegt — jede erfolgreiche
Verifikation MUSS einen neuen Eintrag schreiben, sonst ist Replay
möglich. Counter darf nur monoton steigen.

Sicherheits-Annahmen:
- Seed-File-Permissions sind responsability des Operators (`chmod 600`).
  Wir lesen ohne Permission-Check, weil das Filesystem-ACL-Pflicht ist;
  Code-side Checks würden falsche Sicherheit suggerieren.
- Tolerance-Window 3 deckt typische App↔Pi-Desync ab (Operator drückt
  am Phone 3 Mal "weiter" ohne dass Pi etwas registriert). Höhere
  Toleranzen senken die effektive HOTP-Security exponentiell — daher
  hartcodiert auf max 3, nicht configurable.

Status 2026-05-11: Modul-only, kein /trade-Command verdrahtet (Spec Step 6,
``telegram_bot.py`` Erweiterung folgt nach N+2). Test-Pfad steht über die
public API ``HotpVerifier``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyotp

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


@dataclass(frozen=True)
class HotpVerifyResult:
    """Result einer erfolgreichen Verifikation."""

    counter_used: int
    counter_advance: int  # = counter_used - last_used (mindestens 1)
    verified_at_utc: str


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
            raise ValueError(
                f"allow_advance={allow_advance} außerhalb [1, {MAX_ADVANCE_WINDOW}]"
            )
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
            raise HotpSeedInvalid(
                f"seed not base32 (only A-Z2-7=): {self._seed_path}"
            )
        return cleaned

    def last_used_counter(self) -> int:
        """Höchster bisher akzeptierter Counter, oder -1 wenn Journal leer/fehlt.

        Liest das gesamte Journal — append-only-design garantiert dass das
        max-counter == letzter-eintrag wäre, aber wir scannen defensiv für den
        Fall dass extern manipuliert wurde.
        """
        if not self._journal_path.exists():
            return -1
        max_counter = -1
        try:
            with self._journal_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning(
                            "hotp_journal_corrupt_line file=%s line=%r",
                            self._journal_path, line[:80],
                        )
                        continue
                    counter = record.get("counter")
                    if isinstance(counter, int) and counter > max_counter:
                        max_counter = counter
        except OSError as exc:
            logger.error("hotp_journal_read_failed: %s", exc)
            return -1
        return max_counter

    def next_expected_counter(self) -> int:
        """Counter, den der nächste gültige Code matcht."""
        last = self.last_used_counter()
        return 0 if last < 0 else last + 1

    def _append_journal(self, counter: int, advance: int) -> str:
        """Append einer Verifikations-Spur. Returns ISO-Zeitstempel.

        Wir öffnen mit 'a' (append) — POSIX garantiert Atomarität bei
        small writes (<PIPE_BUF). Falls je zwei concurrent verifications
        sich hier rein-racen, sind beide Lines geschrieben, der `verify`-
        loop hat aber bereits durch `last_used_counter()` davor monotonie
        sichergestellt — die zweite race-loser-Line ist harmlos.

        Phase-1 wird das durch portalocker-Lock härten; in Phase-0 reicht
        die Single-Process-Annahme (kai-server ist single-instance).
        """
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")
        record = {
            "counter": counter,
            "advance": advance,
            "verified_at_utc": timestamp,
            "schema_version": "hotp-v1",
        }
        with self._journal_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
            fh.flush()
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
            ValueError: code-Format invalid (nicht 6 stellen oder nicht digit-only).
        """
        cleaned = code.strip().replace(" ", "")
        if len(cleaned) != HOTP_DIGITS or not cleaned.isdigit():
            raise ValueError(f"code must be {HOTP_DIGITS} digits, got {len(cleaned)}")

        seed = self._load_seed()
        hotp = pyotp.HOTP(seed, digits=HOTP_DIGITS)

        next_counter = self.next_expected_counter()

        # Tolerance-Loop: probiere [next, next+1, ..., next+allow_advance-1].
        for offset in range(self._allow_advance):
            candidate_counter = next_counter + offset
            if hotp.verify(cleaned, candidate_counter):
                # Defense-in-Depth: monotonie nochmal hart prüfen, falls
                # next_expected_counter zwischenzeitlich von extern manipuliert.
                last = self.last_used_counter()
                if candidate_counter <= last:
                    raise HotpReplayDetected(
                        f"counter {candidate_counter} <= last_used {last}"
                    )
                advance = candidate_counter - last  # ≥ 1
                ts = self._append_journal(candidate_counter, advance)
                logger.info(
                    "hotp_verify_ok counter=%d advance=%d",
                    candidate_counter, advance,
                )
                return HotpVerifyResult(
                    counter_used=candidate_counter,
                    counter_advance=advance,
                    verified_at_utc=ts,
                )

        # Kein Match in der Tolerance-Window.
        logger.warning(
            "hotp_verify_failed next_expected=%d window=%d",
            next_counter, self._allow_advance,
        )
        raise HotpVerificationFailed(
            f"code rejected (window {next_counter}…{next_counter + self._allow_advance - 1})"
        )


def humanize_counter(verifier: HotpVerifier) -> str:
    """Human-readable summary for /live status command output."""
    last = verifier.last_used_counter()
    nxt = verifier.next_expected_counter()
    if last < 0:
        return "HOTP-Counter: virgin (next expected = 0)"
    return f"HOTP-Counter: last_used={last}, next_expected={nxt}"
