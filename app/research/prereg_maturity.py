"""Maturity tracking for open out-of-sample pre-registrations.

"Re-evaluate when n>=300" must not live in an operator's memory. Each open
out-of-sample hypothesis gets a SPEC here (how to count its cohort) and
``compute_maturity`` reports n vs target — the weekly timer surfaces DUE
claims via journal/artifact, read-only.

The count is a deliberate UPPER-BOUND PROXY: it counts qualifying cohort
members; the eval itself drops some events (no OHLCV series, entry-lag gaps),
so a DUE signal means "run the eval now", never "the claim passed".

WICHTIG (Lehre 2026-07-30): der Zähler muss das GATE-LEVEL des versiegelten
Claims respektieren. ``b20ef1487ccba99d`` gatet auf ``level:"stories"`` —
die Event-Level-Zählung meldete FÄLLIG bei n≈1165, obwohl die Story-Kohorte
erst 247/300 hatte. Specs mit ``"level": "stories"`` zählen darum
story-dedupliziert (``cluster_stories``, identische Fenster-Semantik wie der
Evaluator); der rohe Event-Count bleibt als Kontext sichtbar.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Registered out-of-sample windows start at the claim's registration time — these
# constants ARE part of the doctrine (auditable against the prereg ledger).
# ``prereg_id`` bindet jeden Spec EXPLIZIT an die versiegelte Prä-Reg, die er
# zählt. Vorher stand diese Zuordnung nur in Prosa — und der Spec-Name wich beim
# hedged-drift-Claim vom Ledger-Namen ab (Spec ``…_drift`` vs. Prä-Reg
# ``…_drift_v2``). Solange nichts beides jointe, war das harmlos; sobald ein
# Konsument (Operator-Board) über den Namen joint, hängt die Reife am FALSCHEN
# Claim. Die ids sind gegen das Ledger verifiziert (2026-07-30): bei den beiden
# Quoten-Claims ist ``since_utc`` byte-identisch zu ``created_at_utc``, beim
# hedged-drift-Claim liegt das Fenster ab 07-02 (v2, 05:43) und NICHT ab v1
# (07-01, 22:09).
MATURITY_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "directional_news_hedged_1d_drift",
        # Prä-Reg ist der v2-Claim; der Spec-Name blieb aus Kompatibilität stehen.
        "prereg_id": "b20ef1487ccba99d",
        "kind": "documents",
        "since_utc": "2026-07-02",
        "sources": None,  # all sources
        "exclude_first_ticker": "BTC/USDT",  # hedged construction skips BTC events
        "n_target": 300,
        # Gate b20ef1487ccba99d urteilt auf Story-Level (cluster_stories, 24h).
        "level": "stories",
    },
    {
        "name": "directional_news_3d_theblock_newsbtc",
        "prereg_id": "7e8d66314dd7c64e",
        "kind": "documents",
        "since_utc": "2026-07-01",
        "sources": ("theblock", "newsbtc"),
        "exclude_first_ticker": None,
        "n_target": 100,  # per source
    },
    # Quoten-Prä-Regs 2026-07-29 — Kohorten leben in Artefakt-JSONL, nicht im
    # Dokumenten-Store; gezählt wird über die H1/H2-Evaluatoren selbst (DRY,
    # identische Populations-Definition wie das spätere Verdikt).
    {
        "name": "technical_paper_precision_fwd_v1",
        "prereg_id": "fd6f5f7842f49244",
        "kind": "tech_precision",
        "since_utc": "2026-07-29T09:14:47.210068+00:00",
        "n_target": 200,
    },
    {
        "name": "execution_translation_hit_to_win_v1",
        "prereg_id": "0c7ead764621dd17",
        "kind": "exec_translation",
        "since_utc": "2026-07-29T09:15:10.626958+00:00",
        "n_target": 50,
    },
)

_COUNT_SQL = """
SELECT COALESCE(source_name, 'unknown') AS src, COUNT(*) AS n
FROM canonical_documents
WHERE sentiment_label IN ('bullish', 'bearish')
  AND tickers IS NOT NULL
  AND json_array_length(tickers) > 0
  AND published_at >= :since
  AND (:exclude_ticker IS NULL OR json_extract(tickers, '$[0]') != :exclude_ticker)
GROUP BY source_name
"""

_STORY_ROWS_SQL = """
SELECT json_extract(tickers, '$[0]') AS sym,
       sentiment_label AS side,
       published_at AS pub
FROM canonical_documents
WHERE sentiment_label IN ('bullish', 'bearish')
  AND tickers IS NOT NULL
  AND json_array_length(tickers) > 0
  AND published_at >= :since
  AND (:exclude_ticker IS NULL OR json_extract(tickers, '$[0]') != :exclude_ticker)
ORDER BY published_at
"""


def _as_dt(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, str) and raw:
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return None


async def _count_stories(session: AsyncSession, spec: dict[str, Any]) -> int:
    """Story-dedupliziertes n — Proxy auf published_at statt entry_ts (der
    Evaluator ankert auf dem ersten OHLCV-Open NACH publish; für die Reife-
    Frage ist die Publish-Zeit dieselbe 24h-Fenster-Semantik)."""
    from app.research.news_stories import cluster_stories

    rows = (
        await session.execute(
            text(_STORY_ROWS_SQL),
            {"since": spec["since_utc"], "exclude_ticker": spec["exclude_first_ticker"]},
        )
    ).all()
    outcomes = []
    for r in rows:
        ts = _as_dt(r.pub)
        if ts is None or not r.sym:
            continue
        outcomes.append({"symbol": str(r.sym), "side": str(r.side), "entry_ts": ts})
    return len(cluster_stories(outcomes))


async def _maturity_documents(
    session: AsyncSession, spec: dict[str, Any]
) -> tuple[int, dict[str, int], bool]:
    rows = (
        await session.execute(
            text(_COUNT_SQL),
            {"since": spec["since_utc"], "exclude_ticker": spec["exclude_first_ticker"]},
        )
    ).all()
    by_source = {str(r.src): int(r.n) for r in rows}
    sources = spec["sources"]
    if sources is None:
        n_events = sum(by_source.values())
        if spec.get("level") == "stories":
            n_stories = await _count_stories(session, spec)
            return (
                n_stories,
                {"stories": n_stories, "events": n_events},
                (n_stories >= int(spec["n_target"])),
            )
        return n_events, {"all": n_events}, n_events >= int(spec["n_target"])
    detail = {s: by_source.get(s, 0) for s in sources}
    n = sum(detail.values())
    return n, detail, all(v >= int(spec["n_target"]) for v in detail.values())


def _maturity_tech_precision(
    spec: dict[str, Any], artifacts_dir: Path
) -> tuple[int, dict[str, int], bool]:
    from app.research.quote_evals import evaluate_technical_paper_precision

    ev = evaluate_technical_paper_precision(
        outcomes_path=artifacts_dir / "alert_outcomes.jsonl",
        exec_audit_path=artifacts_dir / "paper_execution_audit.jsonl",
        registered_at_utc=str(spec["since_utc"]),
    )
    pop = ev["population"]
    n = int(pop["docs_resolved"])
    detail = {
        "resolved": n,
        "inconclusive": int(pop["docs_inconclusive"]),
        "pending": int(pop["docs_pending_no_outcome"]),
    }
    return n, detail, n >= int(spec["n_target"])


def _maturity_exec_translation(
    spec: dict[str, Any], artifacts_dir: Path
) -> tuple[int, dict[str, int], bool]:
    from app.research.quote_evals import evaluate_execution_translation

    ev = evaluate_execution_translation(
        outcomes_path=artifacts_dir / "alert_outcomes.jsonl",
        exec_audit_path=artifacts_dir / "paper_execution_audit.jsonl",
        registered_at_utc=str(spec["since_utc"]),
    )
    pop = ev["population"]
    n = int(pop["docs_joined_to_hit"])
    detail = {
        "joined": n,
        "closed_docs": int(pop["closed_docs_since_reg"]),
    }
    return n, detail, n >= int(spec["n_target"])


async def compute_maturity(
    session: AsyncSession,
    *,
    specs: tuple[dict[str, Any], ...] = MATURITY_SPECS,
    artifacts_dir: Path = Path("artifacts"),
) -> list[dict[str, Any]]:
    """Count each spec's out-of-sample cohort; ``due`` when target is reached."""
    out: list[dict[str, Any]] = []
    for spec in specs:
        kind = str(spec.get("kind", "documents"))
        if kind == "tech_precision":
            n, detail, due = _maturity_tech_precision(spec, artifacts_dir)
        elif kind == "exec_translation":
            n, detail, due = _maturity_exec_translation(spec, artifacts_dir)
        else:
            n, detail, due = await _maturity_documents(session, spec)
        out.append(
            {
                "name": spec["name"],
                # Durchgereicht, damit Konsumenten über die versiegelte Identität
                # joinen können statt über den (driftenden) Namen.
                "prereg_id": spec.get("prereg_id"),
                "since_utc": spec["since_utc"],
                "n_target": spec["n_target"],
                "n_proxy": n,
                "per_source": detail,
                "due": due,
            }
        )
    return out


__all__ = ["MATURITY_SPECS", "compute_maturity"]
