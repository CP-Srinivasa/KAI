"""
Email Alert Adapter
===================
Sends alert messages via SMTP (supports TLS/STARTTLS).

[REQUIRES: EMAIL_SMTP_USER, EMAIL_SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO in .env]
[REQUIRES: EMAIL_ENABLED=true in .env]

Features:
- Plain text + HTML multipart messages
- Breaking alert and digest templates
- Dry-run mode (logs instead of sending)
- STARTTLS (port 587) and SSL (port 465) support
- Connection pooling via asyncio executor

Configuration:
    EMAIL_SMTP_HOST=smtp.gmail.com
    EMAIL_SMTP_PORT=587
    EMAIL_SMTP_USER=you@gmail.com
    EMAIL_SMTP_PASSWORD=app-password
    EMAIL_FROM=alerts@yourdomain.com
    EMAIL_TO=trader@yourdomain.com
    EMAIL_ENABLED=true
    EMAIL_USE_TLS=true
"""

from __future__ import annotations

import asyncio
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from app.alerts.evaluator import AlertDecision, DocumentScores
from app.core.enums import AlertType, DocumentPriority
from app.core.logging import get_logger

logger = get_logger(__name__)

_PRIORITY_LABEL = {
    DocumentPriority.CRITICAL: "🚨 CRITICAL",
    DocumentPriority.HIGH:     "🔴 HIGH",
    DocumentPriority.MEDIUM:   "🟡 MEDIUM",
    DocumentPriority.LOW:      "🟢 LOW",
    DocumentPriority.NOISE:    "⚪ NOISE",
}

_SENTIMENT_COLOR = {
    "positive": "#2ecc71",
    "negative": "#e74c3c",
    "neutral":  "#95a5a6",
}


# ─────────────────────────────────────────────
# Plain-text formatters
# ─────────────────────────────────────────────

def format_breaking_text(scores: DocumentScores) -> str:
    priority = _PRIORITY_LABEL.get(scores.recommended_priority, "UNKNOWN")
    lines = [
        f"BREAKING ALERT [{priority}]",
        "=" * 50,
        f"Title: {scores.title}",
        f"Sentiment: {scores.sentiment_label} ({scores.sentiment_score:+.2f})",
        f"Impact: {scores.impact_score:.0%}",
        f"Relevance: {scores.relevance_score:.0%}",
    ]
    if scores.affected_assets:
        lines.append(f"Assets: {', '.join(scores.affected_assets[:10])}")
    if scores.matched_entities:
        lines.append(f"Entities: {', '.join(scores.matched_entities[:5])}")
    if scores.explanation_short:
        lines.append("")
        lines.append(f"Summary: {scores.explanation_short}")
    if scores.bull_case:
        lines.append(f"Bull case: {scores.bull_case[:200]}")
    if scores.bear_case:
        lines.append(f"Bear case: {scores.bear_case[:200]}")
    if scores.url:
        lines.append("")
        lines.append(f"Link: {scores.url}")
    if scores.published_at:
        lines.append(f"Published: {scores.published_at.strftime('%Y-%m-%d %H:%M UTC')}")
    return "\n".join(lines)


def format_digest_text(items: list[DocumentScores], period: str = "Daily") -> str:
    date = datetime.utcnow().strftime("%Y-%m-%d")
    lines = [
        f"{period} Digest — {date}",
        f"{len(items)} documents analyzed",
        "=" * 50,
        "",
    ]
    for i, scores in enumerate(items[:20], 1):
        priority = _PRIORITY_LABEL.get(scores.recommended_priority, "?")
        lines.append(f"{i}. [{priority}] {scores.title[:100]}")
        if scores.explanation_short:
            lines.append(f"   {scores.explanation_short[:150]}")
        if scores.url:
            lines.append(f"   {scores.url}")
        lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# HTML formatters
# ─────────────────────────────────────────────

def _html_header(title: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          max-width: 700px; margin: 0 auto; padding: 20px; color: #2c3e50; }}
  .alert-box {{ border-left: 4px solid #e74c3c; padding: 15px 20px;
                background: #fdf2f2; border-radius: 4px; margin-bottom: 20px; }}
  .metric {{ display: inline-block; margin: 4px 8px 4px 0; padding: 3px 10px;
             background: #ecf0f1; border-radius: 12px; font-size: 13px; }}
  .positive {{ color: #27ae60; }} .negative {{ color: #c0392b; }} .neutral {{ color: #7f8c8d; }}
  .case {{ background: #f8f9fa; padding: 10px; border-radius: 4px; margin: 6px 0; font-size: 14px; }}
  h1 {{ font-size: 22px; margin-bottom: 5px; }} h2 {{ font-size: 16px; color: #7f8c8d; font-weight: normal; }}
  a {{ color: #2980b9; }} footer {{ color: #bdc3c7; font-size: 12px; margin-top: 30px; }}
</style></head><body>
<h2>AI Analyst Trading Bot</h2>
<h1>{title}</h1>"""


def format_breaking_html(scores: DocumentScores) -> str:
    priority = _PRIORITY_LABEL.get(scores.recommended_priority, "UNKNOWN")
    sent_color = _SENTIMENT_COLOR.get(scores.sentiment_label, "#95a5a6")

    assets_html = ""
    if scores.affected_assets:
        tags = "".join(f'<span class="metric">{a}</span>' for a in scores.affected_assets[:8])
        assets_html = f"<p><strong>Affected Assets:</strong> {tags}</p>"

    entities_html = ""
    if scores.matched_entities:
        tags = "".join(f'<span class="metric">{e}</span>' for e in scores.matched_entities[:5])
        entities_html = f"<p><strong>Watchlist Entities:</strong> {tags}</p>"

    cases_html = ""
    if scores.bull_case:
        cases_html += f'<div class="case positive">📈 <strong>Bull:</strong> {scores.bull_case[:300]}</div>'
    if scores.bear_case:
        cases_html += f'<div class="case negative">📉 <strong>Bear:</strong> {scores.bear_case[:300]}</div>'

    link_html = f'<p><a href="{scores.url}">🔗 Read full article</a></p>' if scores.url else ""
    pub_html = f"<p>Published: {scores.published_at.strftime('%Y-%m-%d %H:%M UTC')}</p>" if scores.published_at else ""

    body = f"""
<div class="alert-box">
  <p><strong>Priority:</strong> {priority}</p>
  <h2 style="color:#2c3e50">{scores.title}</h2>
  {f'<p>{scores.explanation_short}</p>' if scores.explanation_short else ''}
</div>
<p>
  <span class="metric">Sentiment: <span style="color:{sent_color}">{scores.sentiment_label} ({scores.sentiment_score:+.2f})</span></span>
  <span class="metric">Impact: {scores.impact_score:.0%}</span>
  <span class="metric">Relevance: {scores.relevance_score:.0%}</span>
  <span class="metric">Credibility: {scores.credibility_score:.0%}</span>
</p>
{assets_html}{entities_html}{cases_html}{link_html}{pub_html}
<footer>AI Analyst Trading Bot · {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</footer>
</body></html>"""

    return _html_header(f"Breaking Alert: {scores.title[:60]}") + body


def format_digest_html(items: list[DocumentScores], period: str = "Daily") -> str:
    date = datetime.utcnow().strftime("%Y-%m-%d")
    rows = ""
    for i, scores in enumerate(items[:20], 1):
        priority = _PRIORITY_LABEL.get(scores.recommended_priority, "?")
        sent_color = _SENTIMENT_COLOR.get(scores.sentiment_label, "#95a5a6")
        link = f'<a href="{scores.url}">🔗</a>' if scores.url else ""
        summary = f"<br><small>{scores.explanation_short[:120]}</small>" if scores.explanation_short else ""
        rows += f"""<tr>
          <td style="padding:8px;border-bottom:1px solid #eee">{i}</td>
          <td style="padding:8px;border-bottom:1px solid #eee">{priority}</td>
          <td style="padding:8px;border-bottom:1px solid #eee">
            <strong>{scores.title[:100]}</strong>{summary}
          </td>
          <td style="padding:8px;border-bottom:1px solid #eee;color:{sent_color}">{scores.sentiment_label}</td>
          <td style="padding:8px;border-bottom:1px solid #eee">{link}</td>
        </tr>"""

    body = f"""
<p><em>{len(items)} documents analyzed · {date}</em></p>
<table style="width:100%;border-collapse:collapse">
  <thead><tr style="background:#ecf0f1">
    <th style="padding:8px;text-align:left">#</th>
    <th style="padding:8px;text-align:left">Priority</th>
    <th style="padding:8px;text-align:left">Title</th>
    <th style="padding:8px;text-align:left">Sentiment</th>
    <th style="padding:8px;text-align:left">Link</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
<footer style="color:#bdc3c7;font-size:12px;margin-top:30px">
  AI Analyst Trading Bot · {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC
</footer></body></html>"""

    return _html_header(f"{period} Digest — {date}") + body


# ─────────────────────────────────────────────
# Email Adapter
# ─────────────────────────────────────────────

class EmailAdapter:
    """
    Sends alert emails via SMTP.

    [REQUIRES: EMAIL_SMTP_USER, EMAIL_SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO]
    [REQUIRES: EMAIL_ENABLED=true]

    Args:
        smtp_host:     SMTP server hostname
        smtp_port:     Port (587=STARTTLS, 465=SSL)
        smtp_user:     SMTP authentication username
        smtp_password: SMTP authentication password
        from_address:  Sender address
        to_address:    Recipient address (single; use BCC for multiple)
        use_tls:       Use STARTTLS (default True)
        dry_run:       Log instead of sending
    """

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: str,
        from_address: str,
        to_address: str,
        use_tls: bool = True,
        dry_run: bool = False,
    ) -> None:
        self._host = smtp_host
        self._port = smtp_port
        self._user = smtp_user
        self._password = smtp_password
        self._from = from_address
        self._to = to_address
        self._use_tls = use_tls
        self._dry_run = dry_run

    def _build_message(
        self,
        subject: str,
        text_body: str,
        html_body: str,
    ) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._from
        msg["To"] = self._to
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        return msg

    def _send_sync(self, msg: MIMEMultipart) -> None:
        """Synchronous SMTP send (runs in executor)."""
        if self._port == 465:
            server = smtplib.SMTP_SSL(self._host, self._port)
        else:
            server = smtplib.SMTP(self._host, self._port)
            if self._use_tls:
                server.starttls()
        try:
            server.login(self._user, self._password)
            server.sendmail(self._from, [self._to], msg.as_string())
        finally:
            server.quit()

    async def _send_async(self, msg: MIMEMultipart) -> bool:
        """Run synchronous SMTP in thread executor to avoid blocking the event loop."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._send_sync, msg)
            return True
        except Exception as e:
            logger.error("email_send_failed", error=str(e), to=self._to)
            return False

    async def send_alert(self, decision: AlertDecision) -> bool:
        """Format and send an AlertDecision as an email."""
        scores = decision.document_scores
        if scores is None:
            return False

        subject = f"[{decision.severity.value.upper()}] {scores.title[:80]}"
        text_body = format_breaking_text(scores)
        html_body = format_breaking_html(scores)

        if self._dry_run:
            logger.info(
                "email_dry_run",
                subject=subject,
                to=self._to,
                text_preview=text_body[:200],
            )
            return True

        if not self._user or not self._password:
            logger.warning("email_not_configured")
            return False

        msg = self._build_message(subject, text_body, html_body)
        success = await self._send_async(msg)
        if success:
            logger.info("email_sent", to=self._to, subject=subject)
        return success

    async def send_digest(
        self,
        items: list[DocumentScores],
        period: str = "Daily",
    ) -> bool:
        """Format and send a digest email."""
        date = datetime.utcnow().strftime("%Y-%m-%d")
        subject = f"[Digest] {period} AI Analyst Brief — {date}"
        text_body = format_digest_text(items, period)
        html_body = format_digest_html(items, period)

        if self._dry_run:
            logger.info(
                "email_digest_dry_run",
                subject=subject,
                items=len(items),
                to=self._to,
            )
            return True

        if not self._user or not self._password:
            logger.warning("email_not_configured")
            return False

        msg = self._build_message(subject, text_body, html_body)
        return await self._send_async(msg)

    async def healthcheck(self) -> dict[str, Any]:
        """Verify SMTP connection."""
        if self._dry_run:
            return {"healthy": True, "mode": "dry_run"}
        if not self._user:
            return {"healthy": False, "reason": "EMAIL_SMTP_USER not set"}
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._verify_connection)
            return {"healthy": True, "host": self._host, "port": self._port}
        except Exception as e:
            return {"healthy": False, "error": str(e)}

    def _verify_connection(self) -> None:
        if self._port == 465:
            server = smtplib.SMTP_SSL(self._host, self._port)
        else:
            server = smtplib.SMTP(self._host, self._port)
            if self._use_tls:
                server.starttls()
        server.login(self._user, self._password)
        server.quit()
