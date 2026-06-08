#!/usr/bin/env python3
"""NEO-P-002-r3 — read-only Live-Eligibility-Probe (Go/No-Go vor Feeder-Aktivierung).

Beantwortet die EINE offene Zahl vor `EXECUTION_SHADOW_REAL_GENERATOR=true`:
Wie viele real analysierte Dokumente erfüllen live das Feeder-Eligibility-Prädikat
(Symbol ∧ kalibrierbare confidence ∧ directional) und davon das D-182-Gate
(priority_score ≥ 10)?

STRICT READ-ONLY: nur DocumentRepository.list(). Keine Writes, keine Execution,
kein Loop, kein entry_mode-Touch. Spiegelt is_eligible aus #177 (real_analysis_provider).

Aufruf auf der Pi:  python scripts/eligibility_probe.py
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime, timedelta

from app.core.settings import get_settings
from app.storage.db.session import build_session_factory
from app.storage.repositories.document_repo import DocumentRepository

GATE_THRESHOLD = 10  # D-182 paper_min_priority
WINDOWS = {"24h": 24, "48h": 48, "7d": 24 * 7}


def _has_symbol(doc) -> bool:
    return bool(list(doc.tickers or []) or list(getattr(doc, "crypto_assets", []) or []))


def _has_confidence(doc) -> bool:
    # #177: unbekannte confidence (keine Achse persistiert) ist NICHT eligible.
    return doc.credibility_score is not None or doc.spam_probability is not None


def _directional(doc) -> bool:
    dc = doc.directional_confidence
    return dc is not None and float(dc) > 0.0


def _reject_reason(doc) -> str:
    # Reihenfolge identisch zu is_eligible (#177).
    if not _has_symbol(doc):
        return "no_symbol"
    if not _has_confidence(doc):
        return "no_confidence_signal"
    if not _directional(doc):
        return "non_directional"
    return "eligible"


def _gate_pass(doc) -> bool:
    p = doc.priority_score
    return p is not None and int(p) >= GATE_THRESHOLD


async def _run() -> dict:
    settings = get_settings()
    factory = build_session_factory(settings.db)
    now = datetime.now(UTC)
    oldest = now - timedelta(hours=WINDOWS["7d"])

    async with factory.begin() as session:
        repo = DocumentRepository(session)
        docs = await repo.list(
            is_analyzed=True,
            is_duplicate=False,
            published_after=oldest,
            limit=20000,
        )

    out: dict = {"generated_at": now.isoformat(), "gate_threshold": GATE_THRESHOLD, "windows": {}}
    for wname, whours in WINDOWS.items():
        cutoff = now - timedelta(hours=whours)
        win = []
        for d in docs:
            pub = d.published_at
            if pub is None:
                continue
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=UTC)
            if pub >= cutoff:
                win.append(d)

        funnel: Counter = Counter()
        by_source_elig: Counter = Counter()
        by_sentiment_elig: Counter = Counter()
        eligible = 0
        eligible_and_gate = 0
        for d in win:
            r = _reject_reason(d)
            funnel[r] += 1
            if r == "eligible":
                eligible += 1
                by_source_elig[d.source_name or "?"] += 1
                lbl = d.sentiment_label.value if d.sentiment_label else "?"
                by_sentiment_elig[lbl] += 1
                if _gate_pass(d):
                    eligible_and_gate += 1

        out["windows"][wname] = {
            "total_analyzed": len(win),
            "funnel": {
                "no_symbol": funnel["no_symbol"],
                "no_confidence_signal": funnel["no_confidence_signal"],
                "non_directional": funnel["non_directional"],
                "eligible": funnel["eligible"],
            },
            "eligible_feeder": eligible,
            "eligible_AND_gate_ge_10": eligible_and_gate,
            "eligible_by_source": dict(by_source_elig.most_common(15)),
            "eligible_by_sentiment": dict(by_sentiment_elig.most_common()),
        }
    return out


def main() -> None:
    res = asyncio.run(_run())
    print(json.dumps(res, indent=2, ensure_ascii=False))
    print("\n--- GO/NO-GO ---")
    for w in ("24h", "48h", "7d"):
        x = res["windows"][w]
        print(
            f"{w:>4}: analyzed={x['total_analyzed']:>5}  "
            f"eligible(feeder)={x['eligible_feeder']:>4}  "
            f"eligible AND gate>=10={x['eligible_AND_gate_ge_10']:>4}"
        )
    g = res["windows"]["48h"]["eligible_AND_gate_ge_10"]
    verdict = (
        "GO (substanzielles Real-Sample moeglich)"
        if g >= 10
        else (
            "DUENN - ehrliches Schweigen wahrscheinlich, Feeder-ON bringt wenig"
            if g > 0
            else "NO-GO heute - 0 eligible AND gate in 48h (Feeder-ON = leeres Ledger)"
        )
    )
    print(f"\n48h eligible AND gate>=10 = {g}  ->  {verdict}")


if __name__ == "__main__":
    main()
