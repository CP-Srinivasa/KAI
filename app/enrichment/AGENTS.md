# AGENTS.md — app/enrichment/

## Purpose
Post-ingestion enrichment: deduplication, entity extraction, tagging.
Input: `CanonicalDocument`. Output: enriched `CanonicalDocument`.
Stateless where possible.

## Public Interface

| File | Exports | Notes |
|---|---|---|
| `deduplication/deduplicator.py` | `DocumentDeduplicator` | Hash/URL/title-similarity dedup |

## Deduplication Strategy

Conservative — prefer false negatives over false positives:
1. Exact URL match
2. Content hash match
3. Title similarity (fuzzy, threshold-based)

## Planned modules (Phase 2+)

| Module | Purpose |
|---|---|
| `entities/` | Entity extraction (persons, orgs, assets) |
| `tagging/` | Auto-tagging from `monitor/keywords.txt` |
| `language/` | Language detection |

## Constraints

- No LLM calls in enrichment (rule-based only)
- No direct DB calls in deduplicator logic
- Entity aliases loaded from `monitor/entity_aliases.yml`

## Tests

```bash
pytest tests/unit/test_deduplicator.py
```
