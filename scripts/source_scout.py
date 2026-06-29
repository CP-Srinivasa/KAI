"""Source-Scout — füllt monitor/source_proposals.jsonl für den Discovery-Scheduler.

Shadow-first (Phase 3). Liest eine kuratierte/recherchierte Kandidatenliste
(``monitor/source_candidates_seed.json``), dedupliziert gegen die bestehende
Quellen-Registry (DB), bewertet — NUR wenn ``SOURCE_SCOUT_ENABLED=true`` — die
Feed-Gesundheit per Outbound-Probe, und schreibt eine ranked
``monitor/source_proposals.jsonl`` im Schema, das ``source_discovery_scheduler``
liest. Aktiviert NICHTS: Proposals sind eine prüfbare Datei, die der (selbst
gegatete) Scheduler nur DRY auditiert.

Kill-Switch ``SOURCE_SCOUT_ENABLED`` (default OFF): aus → KEIN Outbound-Probe,
Kandidaten werden nur dedupliziert + unbewertet durchgereicht (score=null). An →
jeder Kandidat wird einmal gefetcht (httpx, fail-closed gegen SSRF über die
Scheduler-Gate-Stufe), tote/leere Feeds fliegen raus, Rest wird nach Frische×
Volumen gerankt.

Usage:
    python scripts/source_scout.py            # respektiert SOURCE_SCOUT_ENABLED
    python scripts/source_scout.py --probe    # erzwingt Probe (lokaler Test)
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.settings import get_settings  # noqa: E402
from app.learning.source_reject_tombstone import load_active_rejections  # noqa: E402
from app.learning.source_scout import (  # noqa: E402
    ScoutProposal,
    dedup_against_registry,
    parse_feed_health,
    rank_proposals,
    score_candidate,
)
from app.storage.db.session import build_session_factory  # noqa: E402
from app.storage.repositories.source_repo import SourceRepository  # noqa: E402

_SEED_PATH = Path("monitor/source_candidates_seed.json")
_PROPOSALS_PATH = Path("monitor/source_proposals.jsonl")
_REJECTED_TOMBSTONE_PATH = Path("monitor/source_rejected_candidates.jsonl")


def _read_seed(path: Path) -> list[ScoutProposal]:
    out: list[ScoutProposal] = []
    if not path.exists():
        return out
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = row.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        out.append(
            ScoutProposal(
                url=url.strip(),
                access=str(row.get("access") or "unknown").strip().lower(),
                source_type=str(row.get("source_type") or "unresolved_source").strip().lower(),
                provider=(str(row["provider"]).strip() if row.get("provider") else None),
                notes=(str(row["notes"]) if row.get("notes") else None),
            )
        )
    return out


async def _existing_registry() -> tuple[set[str], set[str]]:
    """(normalisierte URLs, provider-slugs) aller bekannten Quellen aus der DB."""
    settings = get_settings()
    factory = build_session_factory(settings.db)
    urls: set[str] = set()
    providers: set[str] = set()
    async with factory() as session:
        repo = SourceRepository(session)
        for s in await repo.list():
            if s.original_url:
                urls.add(s.original_url)
            if s.normalized_url:
                urls.add(s.normalized_url)
            if s.provider:
                providers.add(s.provider)
    return urls, providers


async def _probe(
    candidates: list[ScoutProposal],
) -> tuple[list[ScoutProposal], list[tuple[str, str]]]:
    """Fetch jeden Feed einmal; tote/leere fliegen raus. Returns (alive, dropped)."""
    import httpx

    settings = get_settings()
    now = datetime.now(UTC)
    alive: list[ScoutProposal] = []
    dropped: list[tuple[str, str]] = []
    headers = {"User-Agent": settings.sources.user_agent}
    timeout = float(settings.sources.fetch_timeout)
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        for c in candidates:
            try:
                resp = await client.get(c.url)
            except (httpx.HTTPError, OSError) as exc:
                dropped.append((c.url, f"fetch_failed: {type(exc).__name__}"))
                continue
            if resp.status_code != 200:
                dropped.append((c.url, f"http_{resp.status_code}"))
                continue
            item_count, age = parse_feed_health(resp.text, now)
            if item_count <= 0:
                dropped.append((c.url, "no_items"))
                continue
            alive.append(
                score_candidate(
                    ScoutProposal(
                        url=c.url,
                        access=c.access,
                        source_type=c.source_type,
                        provider=c.provider,
                        notes=c.notes,
                        item_count=item_count,
                        latest_age_days=age,
                    )
                )
            )
    return alive, dropped


async def run_scout(*, force_probe: bool = False) -> int:
    """Seed → dedup → (optional probe) → rank → proposals.jsonl. Returns #proposals."""
    settings = get_settings()
    probe = bool(settings.sources.scout_enabled) or force_probe
    mode = "PROBE" if probe else "PASS-THROUGH"

    seed = _read_seed(_SEED_PATH)
    if not seed:
        print(f"source_scout [{mode}]: kein Seed ({_SEED_PATH}) — no-op")
        return 0

    urls, providers = await _existing_registry()
    tombstoned = load_active_rejections(_REJECTED_TOMBSTONE_PATH, datetime.now(UTC))
    kept, deduped = dedup_against_registry(
        seed,
        existing_normalized_urls=urls,
        existing_providers=providers,
        tombstoned=tombstoned,
    )

    dead: list[tuple[str, str]] = []
    if probe:
        kept, dead = await _probe(kept)

    ranked = rank_proposals(kept)
    _PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _PROPOSALS_PATH.open("w", encoding="utf-8") as fh:
        for p in ranked:
            fh.write(json.dumps(p.to_proposal_row(), ensure_ascii=False, sort_keys=True) + "\n")

    print(
        f"source_scout [{mode}]: seed={len(seed)} deduped={len(deduped)} "
        f"dead={len(dead)} -> proposals={len(ranked)} ({_PROPOSALS_PATH})"
    )
    for url, reason in (deduped + dead)[:20]:
        print(f"  drop  {reason:24s} {url}")
    return len(ranked)


def main() -> None:
    force = "--probe" in sys.argv[1:]
    asyncio.run(run_scout(force_probe=force))


if __name__ == "__main__":
    main()
