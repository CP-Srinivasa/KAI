"""Alert message formatters.

Produces channel-specific text from AlertMessage / digest lists.
Pure functions — no I/O, fully testable.
"""

from __future__ import annotations

from app.alerts.base.interfaces import AlertMessage

_SENTIMENT_EMOJI: dict[str, str] = {
    "bullish": "🟢",
    "bearish": "🔴",
    "neutral": "⚪",
    "mixed": "🟡",
}

_PRIORITY_RANGES: list[tuple[range, str]] = [
    (range(1, 4), "Low"),
    (range(4, 7), "Medium"),
    (range(7, 9), "High"),
    (range(9, 11), "Critical"),
]

# D-149/D-150: priority=10 qualifies as high-conviction tier based on live
# hit-rate evidence (P10=69.57% precision on n=46, CI95=[55.19,80.92] vs
# P7-P9=27.87% on n=183).  Marker is visual-only — routing/gating unchanged.
_HIGH_CONVICTION_THRESHOLD = 10
_HIGH_CONVICTION_PREFIX = "🔥 HIGH-CONVICTION"


def _is_high_conviction(priority: int) -> bool:
    return priority >= _HIGH_CONVICTION_THRESHOLD


def _priority_label(priority: int) -> str:
    for r, label in _PRIORITY_RANGES:
        if priority in r:
            return label
    return "Unknown"


def _escape_md(text: str) -> str:
    """Escape Telegram Markdown v1 special characters."""
    return text.replace("*", "\\*").replace("_", "\\_").replace("`", "\\`").replace("[", "\\[")


# ── Telegram ─────────────────────────────────────────────────────────────────


def format_telegram_message(msg: AlertMessage) -> str:
    """Format a single alert as Telegram Markdown v1 text."""
    emoji = _SENTIMENT_EMOJI.get(msg.sentiment_label.lower(), "⚪")
    label = _priority_label(msg.priority)
    assets = ", ".join(msg.affected_assets) if msg.affected_assets else "—"
    actionable_str = "Actionable" if msg.actionable else "Informational"

    lines = []
    if _is_high_conviction(msg.priority):
        lines.append(f"*{_HIGH_CONVICTION_PREFIX}*")
    lines += [
        f"{emoji} *Priority {msg.priority}/10 — {label}*",
        f"*{_escape_md(msg.title)}*",
        "",
        _escape_md(msg.explanation),
        "",
        f"Assets: {_escape_md(assets)}",
        actionable_str,
        "",
        f"[Read more]({msg.url})",
    ]
    if msg.source_name:
        lines.append(f"Source: {_escape_md(msg.source_name)}")
    return "\n".join(lines)


def format_telegram_digest(messages: list[AlertMessage], period: str) -> str:
    """Format a digest of multiple alerts as Telegram Markdown v1 text."""
    header = [
        f"*Alert Digest — {_escape_md(period)}*",
        f"_{len(messages)} alert(s)_",
        "",
    ]
    items = []
    for msg in messages:
        emoji = _SENTIMENT_EMOJI.get(msg.sentiment_label.lower(), "⚪")
        short_title = msg.title[:60] + ("…" if len(msg.title) > 60 else "")
        hc_marker = "🔥 " if _is_high_conviction(msg.priority) else ""
        items.append(
            f"{hc_marker}{emoji} P{msg.priority} "
            f"[{_escape_md(short_title)}]({msg.url})"
        )
    return "\n".join(header + items)


# ── Email ─────────────────────────────────────────────────────────────────────


def format_email_subject(msg: AlertMessage) -> str:
    label = _priority_label(msg.priority)
    title_truncated = msg.title[:80]
    hc_tag = "[HIGH-CONVICTION] " if _is_high_conviction(msg.priority) else ""
    return f"[KAI Alert] {hc_tag}{label} P{msg.priority}: {title_truncated}"


def format_email_body(msg: AlertMessage) -> str:
    """Format a single alert as plain-text email body."""
    assets = ", ".join(msg.affected_assets) if msg.affected_assets else "—"
    actionable_str = "Yes" if msg.actionable else "No"
    tags_str = ", ".join(msg.tags) if msg.tags else "—"
    return (
        "KAI Market Alert\n" + "=" * 40 + "\n\n"
        f"Priority:    {msg.priority}/10 ({_priority_label(msg.priority)})\n"
        f"Sentiment:   {msg.sentiment_label.upper()}\n"
        f"Actionable:  {actionable_str}\n\n"
        f"Title:\n  {msg.title}\n\n"
        f"Analysis:\n  {msg.explanation}\n\n"
        f"Assets:      {assets}\n"
        f"Tags:        {tags_str}\n\n"
        f"URL: {msg.url}\n"
        f"Source: {msg.source_name or '—'}\n"
    )


def format_email_digest_subject(count: int, period: str) -> str:
    return f"[KAI Digest] {count} alert(s) — {period}"


def format_email_digest_body(messages: list[AlertMessage], period: str) -> str:
    """Format a digest as plain-text email body."""
    lines: list[str] = [
        f"KAI Alert Digest — {period}",
        "=" * 40,
        f"{len(messages)} alert(s) in this period.\n",
    ]
    for i, msg in enumerate(messages, 1):
        excerpt = msg.explanation[:120]
        lines.append(f"{i}. [P{msg.priority}] {msg.title}\n   {excerpt}\n   {msg.url}\n")
    return "\n".join(lines)
