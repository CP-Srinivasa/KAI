"""HTML scrape helpers for grey-area exploration probes.

These do NOT bypass auth/paywalls/CAPTCHAs (hard line under DEC-SRC-EXPLORE-001).
They fetch the public static HTML and honestly report *what structured data the
static document actually contains* — title, meta/OpenGraph tags, JSON-LD blocks,
and whether the page ships its data inline (``__NEXT_DATA__`` / ``__NUXT__``) or
is a JS shell that renders client-side. That distinction is exactly the learning
the exploration phase needs: a JS-shell page yields almost nothing to a static
scraper, which is a finding, not a failure to hide.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from selectolax.parser import HTMLParser

logger = logging.getLogger(__name__)

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE
)
# A loose price token finder for the "did static HTML even contain a price" signal.
_PRICE_RE = re.compile(r"\$\s?([0-9][0-9,]*\.?[0-9]*)")


def parse_html_signals(html: str) -> dict[str, Any]:
    """Extract a uniform 'what does the static HTML give us' signal record.

    Returns a flat dict (so the coverage report can aggregate it) describing the
    structured-data surface of the page. Never raises.
    """
    record: dict[str, Any] = {
        "html_bytes": len(html or ""),
        "title": None,
        "meta_description": None,
        "og_title": None,
        "og_description": None,
        "json_ld_count": 0,
        "json_ld_types": None,
        "has_next_data": False,
        "next_data_bytes": 0,
        "first_price_guess": None,
    }
    if not html:
        return record

    try:
        tree = HTMLParser(html)
    except Exception as exc:  # noqa: BLE001 — malformed HTML is itself a finding
        logger.debug("[exploration] HTML parse failed: %s", exc)
        record["parse_error"] = str(exc)
        return record

    title_node = tree.css_first("title")
    if title_node:
        record["title"] = title_node.text(strip=True) or None

    for node in tree.css("meta"):
        name = (node.attributes.get("name") or "").lower()
        prop = (node.attributes.get("property") or "").lower()
        content = node.attributes.get("content")
        if not content:
            continue
        if name == "description":
            record["meta_description"] = content
        elif prop == "og:title":
            record["og_title"] = content
        elif prop == "og:description":
            record["og_description"] = content

    ld_types: list[str] = []
    for node in tree.css('script[type="application/ld+json"]'):
        record["json_ld_count"] += 1
        raw = node.text() or ""
        try:
            data = json.loads(raw)
            blocks = data if isinstance(data, list) else [data]
            for block in blocks:
                if isinstance(block, dict) and "@type" in block:
                    ld_types.append(str(block["@type"]))
        except (json.JSONDecodeError, TypeError):
            continue
    if ld_types:
        record["json_ld_types"] = ",".join(sorted(set(ld_types)))

    next_match = _NEXT_DATA_RE.search(html)
    if next_match:
        record["has_next_data"] = True
        record["next_data_bytes"] = len(next_match.group(1))
    elif "__NUXT__" in html or "window.__NUXT__" in html:
        record["has_next_data"] = True

    price_match = _PRICE_RE.search(html)
    if price_match:
        record["first_price_guess"] = price_match.group(1)

    return record
