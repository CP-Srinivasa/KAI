"""``/vorschlag``: KAI schlaegt eine Zahlung fuer die KAI-Pay-Wallet vor (D-297, ADR 0021 I5).

Der Bot verdrahtet nur (``telegram_bot._cmd_vorschlag``); die Logik steht hier. Die Antwort ist
immer ein String, es wird nie geworfen. Bezahlt wird nichts: Der Link oeffnet die Pruefseite der
Wallet, die Signatur, erlaubte Empfaenger und Budget prueft; der Nutzer bestaetigt dort selbst.
KAI fuehrt keinen Zahlungszustand (kein zweiter Truth-State).

  /vorschlag                         Hilfe
  /vorschlag quelle                  Link zum Einrichten der Quelle in der Wallet (+ Fingerabdruck)
  /vorschlag <Ziel> <sat> <Zweck>    signierter Vorschlag als Link (und Code zum Einfuegen)
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse

from app.kai_pay_bridge.keys import KeyMissingError, load_key
from app.kai_pay_bridge.proposal import (
    MAX_SAT,
    ProposalError,
    encode_source,
    fingerprint,
    key_id,
    sign_proposal,
    spki_of,
)
from app.kai_pay_bridge.settings import KaiPayProposalSettings

USAGE = (
    "KI-Zahlungsvorschlag fuer KAI-Pay (bezahlt nichts - du bestaetigst in der Wallet):\n"
    "/vorschlag quelle - Quelle in der Wallet einrichten\n"
    "/vorschlag <Ziel> <sat> <Zweck> - z. B. /vorschlag name@breez.tips 2100 Server Oktober"
)
NO_KEY = (
    "Keine Vorschlagsquelle angelegt. Auf der Pi einmalig ausfuehren:\n"
    "kai kaipay-proposal init\n"
    "Danach /vorschlag quelle und den Link auf dem Handy oeffnen."
)
GROUP_REFUSED = "/vorschlag nur im privaten Chat mit KAI."
_REASONS = {
    "to": "Ziel ungueltig (keine Leer- oder Steuerzeichen, hoechstens 2000 Zeichen).",
    "sat": f"Betrag ungueltig: ganze sat von 1 bis {MAX_SAT:,}.".replace(",", "."),
    "purpose": "Zweck fehlt oder ist zu lang (hoechstens 140 Zeichen, keine Steuerzeichen).",
    "ttl": "Laufzeit ungueltig (hoechstens 7 Tage).",
    "size": "Vorschlag zu lang - Ziel oder Zweck kuerzen (oder per CLI: kai kaipay-proposal new).",
}
#: Ab dieser Laenge nur der Link: Link und Code zusammen passten nicht in eine Nachricht (4096).
_CODE_MAX = 1800
#: Darueber passt selbst der Link nicht mehr in eine Telegram-Nachricht - dann die CLI nehmen.
_LINK_MAX = 3700


def _short(value: str) -> str:
    """Lange Ziele (Rechnungen) gekuerzt anzeigen - vollstaendig zeigt sie die Wallet."""
    return value if len(value) <= 80 else f"{value[:48]}…{value[-16:]}"


def _code(value: str) -> str:
    """Fremdtext als Inline-Code: Telegram-Markdown (``_ * [``) greift darin nicht."""
    return "`" + value.replace("`", "'").replace("\n", " ") + "`"


def wallet_link(app_url: str, param: str, token: str) -> str:
    return f"{app_url.rstrip('/')}/#{param}={quote(token, safe='')}"


def handle_vorschlag(
    args: str,
    settings: KaiPayProposalSettings | None = None,
    *,
    now: int | None = None,
    chat_id: int | None = None,
) -> str:
    if chat_id is not None and chat_id < 0:  # Gruppen-IDs sind negativ
        return (
            GROUP_REFUSED  # die Signatur soll den Operator bezeugen, nicht "jemand in der Gruppe"
        )
    cfg = settings or KaiPayProposalSettings()
    parts = args.split()
    if not parts or parts[0].lower() in {"hilfe", "help"}:
        return USAGE
    try:
        key = load_key(cfg.key_path)
    except KeyMissingError:
        return NO_KEY
    except (ValueError, OSError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "nicht lesbar"
        return f"Vorschlagsquelle unbrauchbar: {reason}. Siehe docs/runbooks/kai_pay_proposals.md."
    spki = spki_of(key)
    fp = fingerprint(key_id(spki))

    if parts[0].lower() in {"quelle", "source"}:
        try:
            token = encode_source(cfg.source_name, spki)
        except ProposalError:
            return "APP_KAIPAY_PROPOSAL_SOURCE_NAME ungueltig (1-40 Zeichen, keine Steuerzeichen)."
        return (
            f"Vorschlagsquelle {_code(cfg.source_name)}\nFingerabdruck: {fp}\n\n"
            f"[In der Wallet einrichten]({wallet_link(cfg.app_url, 'kaisrc', token)})\n"
            "Vergleiche den Fingerabdruck in der Wallet, trage erlaubte Empfaenger und Budget "
            "ein und speichere."
        )

    if len(parts) < 3 or not re.fullmatch(r"[0-9]{1,7}", parts[1]):
        return USAGE
    to, sat, purpose = parts[0], int(parts[1]), " ".join(parts[2:])
    try:
        token = sign_proposal(
            key, to=to, sat=sat, purpose=purpose, ttl_s=cfg.ttl_hours * 3600, now=now
        )
    except ProposalError as exc:
        return _REASONS.get(str(exc), "Vorschlag ungueltig.")
    if len(token) > _LINK_MAX:
        return _REASONS["size"]
    code = (
        f"Oder in der App unter Senden einfuegen:\n`{token}`\n" if len(token) <= _CODE_MAX else ""
    )
    return (
        f"KI-Zahlungsvorschlag: {sat:,} sat an ".replace(",", ".")
        + f"{_code(_short(to))}\nZweck: {_code(purpose)}\n"
        + f"Gueltig: {cfg.ttl_hours} h · Quelle {fp}\n\n"
        + f"[In KAI-Pay pruefen]({wallet_link(cfg.app_url, 'kaiprop', token)})\n"
        + code
        + "Bezahlt wird erst, wenn du in der Wallet bestaetigst. "
        + f"Gueltig sind nur Links auf {urlparse(cfg.app_url).netloc}."
    )
