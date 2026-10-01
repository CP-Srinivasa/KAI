"""Oracle-Rechtsseiten: „Leistungen & Bedingungen“, „Hilfe & Beschwerde“ und Widerruf.

Öffentlich (``/oracle/...``) erst nach Freigabe: ``APP_LN_ORACLE_LEGAL_PUBLISHED`` und
keine ``[[OFFEN: …]]`` mehr in den Vorlagen (``app/oracle_legal``). Vorher antworten
die öffentlichen Pfade mit 404. Die Vorschau für Operator und Anwalt liegt unter
``/dashboard/api/oracle/rechtsseiten/{seite}`` hinter dem Dashboard-Schutz.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import oracle_legal as legal
from app.core.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_BODY = 16 * 1024
_limiter = legal.RateLimiter()


def _page_values() -> dict[str, int]:
    from app.api.routers.truth_oracle import _ACCESS_WINDOW_S, _INVOICE_EXPIRY_MINUTES

    return {
        "price_sat": get_settings().lightning.l402_default_price_sat,
        "access_min": _ACCESS_WINDOW_S // 60,
        "invoice_min": _INVOICE_EXPIRY_MINUTES,
    }


def _published() -> bool:
    return legal.is_published(get_settings().lightning.oracle_legal_published)


def _require_published() -> None:
    if not _published():
        raise HTTPException(status_code=404, detail="not found")


@router.get("/oracle/bedingungen", response_class=HTMLResponse, include_in_schema=False)
async def terms_page() -> HTMLResponse:
    _require_published()
    return HTMLResponse(legal.render("bedingungen", preview=False, **_page_values()))


@router.get("/oracle/hilfe", response_class=HTMLResponse, include_in_schema=False)
async def help_page() -> HTMLResponse:
    _require_published()
    return HTMLResponse(legal.render("hilfe", preview=False, **_page_values()))


@router.get("/oracle/datenschutz", response_class=HTMLResponse, include_in_schema=False)
async def privacy_page() -> HTMLResponse:
    _require_published()
    return HTMLResponse(legal.render("datenschutz", preview=False, **_page_values()))


async def _form(request: Request) -> dict[str, str]:
    body = await request.body()
    if len(body) > _MAX_BODY:
        raise legal.FormError("Die Eingabe ist zu groß.")
    parsed = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items() if values}


async def _submit(request: Request, kind: str) -> HTMLResponse:
    _require_published()
    from app.api.client_ip import resolve_client_ip
    from app.lightning.demand_ledger import requester_fingerprint

    settings = get_settings()
    key = requester_fingerprint(resolve_client_ip(request), secret=settings.lightning.l402_secret)
    if not _limiter.allow(key or "anon"):
        return HTMLResponse(
            legal.error_html("Zu viele Meldungen in kurzer Zeit. Bitte später erneut versuchen."),
            status_code=429,
        )
    try:
        form = await _form(request)
        fields = legal.parse_withdrawal(form) if kind == "widerruf" else legal.parse_report(form)
    except legal.FormError as exc:
        return HTMLResponse(legal.error_html(str(exc)), status_code=422)
    try:
        case = legal.record_case(kind, fields, now=datetime.now(UTC))
    except OSError:
        logger.exception("[oracle-legal] Vorgang nicht gespeichert")
        return HTMLResponse(
            legal.error_html(
                "Ihre Eingabe konnte gerade nicht gespeichert werden. Bitte versuchen Sie es "
                "erneut oder schreiben Sie an die Anschrift in den Bedingungen."
            ),
            status_code=503,
        )
    try:
        from app.alerts.notify import send_operator_notification

        await send_operator_notification(legal.operator_text(case))
    except Exception:  # noqa: BLE001 — Benachrichtigung ist Zusatz, der Vorgang steht
        logger.warning("[oracle-legal] Betreiber-Benachrichtigung fehlgeschlagen", exc_info=True)
    return HTMLResponse(legal.receipt_html(case), status_code=201)


@router.post("/oracle/hilfe/meldung", response_class=HTMLResponse, include_in_schema=False)
async def submit_report(request: Request) -> HTMLResponse:
    return await _submit(request, "meldung")


# Kein Online-Widerruf (Operator 01.10.2026): Ohne E-Mail-Versand liesse sich der Eingang
# nicht auf einem dauerhaften Datentraeger bestaetigen (§ 356 Abs. 1 BGB). Widerrufe gehen
# per E-Mail oder Post an die Formsys GmbH und werden dort von Hand bearbeitet.


@router.get("/dashboard/api/oracle/rechtsseiten", tags=["dashboard"])
async def legal_status() -> JSONResponse:
    """Freigabestand für Operator und Anwalt: Schalter, offene Punkte, Version."""
    flag = get_settings().lightning.oracle_legal_published
    return JSONResponse(
        {
            "version": legal.VERSION,
            "stand": legal.STAND,
            "switch_on": flag,
            "published": legal.is_published(flag),
            "open_items": legal.open_items(),
            "preview": [f"/dashboard/api/oracle/rechtsseiten/{p}" for p in legal.PAGES],
        },
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get(
    "/dashboard/api/oracle/rechtsseiten/{seite}",
    response_class=HTMLResponse,
    tags=["dashboard"],
)
async def legal_preview(seite: str) -> HTMLResponse:
    """Vorschau mit ENTWURF-Banner und markierten offenen Punkten (nicht indexiert)."""
    if seite not in legal.PAGES:
        raise HTTPException(status_code=404, detail="unknown page")
    return HTMLResponse(legal.render(seite, preview=True, **_page_values()))
