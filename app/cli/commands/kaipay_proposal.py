"""``kai kaipay-proposal``: Vorschlagsquelle fuer KAI-Pay anlegen und anzeigen (D-297).

Auf der Pi auszufuehren (der Dienst selbst darf ~/kai-secrets nicht beschreiben). Gibt nie den
privaten Schluessel aus - nur Fingerabdruck, oeffentlichen Quellen-Code und Links. Links und
Token gehen roh ueber ``typer.echo`` raus, damit sie ohne Terminal-Farbcodes kopierbar bleiben.
"""

from __future__ import annotations

import typer
from rich.console import Console

from app.kai_pay_bridge.keys import KeyMissingError, create_key, load_key
from app.kai_pay_bridge.proposal import (
    ProposalError,
    encode_source,
    fingerprint,
    key_id,
    sign_proposal,
    spki_of,
)
from app.kai_pay_bridge.settings import KaiPayProposalSettings
from app.kai_pay_bridge.telegram import wallet_link

kaipay_proposal_app = typer.Typer(
    help="KI-Zahlungsvorschlaege fuer die Wallet KAI-Pay (signiert, zahlt nie)."
)
console = Console(highlight=False)


@kaipay_proposal_app.command("init")
def init() -> None:
    """Legt den Signierschluessel an (0600, ~/kai-secrets/kai-pay/). Ueberschreibt nie."""
    cfg = KaiPayProposalSettings()
    try:
        key = create_key(cfg.key_path)
    except FileExistsError:
        console.print(
            "[yellow]Schluessel existiert bereits - nichts geaendert.[/yellow] "
            "Anzeigen: kai kaipay-proposal show"
        )
        raise typer.Exit(code=1) from None
    console.print(
        f"[green]Quelle angelegt.[/green] Fingerabdruck: {fingerprint(key_id(spki_of(key)))}"
    )
    console.print("Weiter: in Telegram /vorschlag quelle, Link auf dem Handy oeffnen.")


@kaipay_proposal_app.command("show")
def show() -> None:
    """Zeigt Fingerabdruck und Einrichtungs-Link der Quelle (oeffentlich)."""
    cfg = KaiPayProposalSettings()
    try:
        key = load_key(cfg.key_path)
    except KeyMissingError:
        console.print("Keine Quelle angelegt: kai kaipay-proposal init")
        raise typer.Exit(code=1) from None
    spki = spki_of(key)
    console.print(f"Quelle: {cfg.source_name}  Fingerabdruck: {fingerprint(key_id(spki))}")
    typer.echo(wallet_link(cfg.app_url, "kaisrc", encode_source(cfg.source_name, spki)))


@kaipay_proposal_app.command("new")
def new(
    to: str = typer.Argument(..., help="Ziel: Lightning-Adresse, Rechnung oder Bitcoin-Adresse"),
    sat: int = typer.Argument(..., help="Betrag in sat (ohne Gebuehr)"),
    purpose: str = typer.Argument(
        ..., help="Zweck (wird in der Wallet als Angabe der Quelle gezeigt)"
    ),
) -> None:
    """Erzeugt einen signierten Vorschlag als Link - bezahlt nichts."""
    cfg = KaiPayProposalSettings()
    try:
        key = load_key(cfg.key_path)
    except KeyMissingError:
        console.print("Keine Quelle angelegt: kai kaipay-proposal init")
        raise typer.Exit(code=1) from None
    try:
        token = sign_proposal(key, to=to, sat=sat, purpose=purpose, ttl_s=cfg.ttl_hours * 3600)
    except ProposalError as exc:
        console.print(f"Vorschlag ungueltig ({exc}) - nichts signiert.")
        raise typer.Exit(code=1) from None
    typer.echo(wallet_link(cfg.app_url, "kaiprop", token))
    typer.echo(token)
