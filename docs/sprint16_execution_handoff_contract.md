# Sprint 16 Contract — Immutable Signal Handoff Layer

> Canonical reference for the KAI external signal consumption boundary.
>
> Runtime: `app/research/execution_handoff.py`
> Invariants: `docs/contracts.md` I-101–I-108
> Upstream: Sprint 18 (mcp_server.py), Sprint 14 (distribution.py), Sprint 4 (signals.py)

---

## Purpose

Sprint 16 defines the hard boundary between KAI's analyst layer and any external
execution consumer. It introduces `SignalHandoff` — an immutable, frozen artifact
that is the canonical delivery format for signals leaving the KAI platform.

**Core principle (I-101):**
> KAI produces signals. KAI does NOT execute trades.
> Signal delivery ≠ execution.
> External agent ≠ trusted control plane.

The `SignalHandoff` is intentionally stripped of all internal KAI metadata that could
be misused as execution instructions.

---

## Non-Negotiable Rules

| Rule | Statement |
|------|-----------|
| No execution trigger | `SignalHandoff` MUST NOT be an execution hook. Advisory only. |
| No `recommended_next_step` forwarding | Internal KAI field — MUST NOT appear in handoff or serialization (I-107) |
| Evidence truncation | `evidence_summary` MUST be ≤ 500 chars — no full document text (I-106) |
| Immutability | `frozen=True` — no mutation after construction (I-105) |
| Unique handoff ID | New UUID `handoff_id` per `create_signal_handoff()` call (I-105) |
| Consumer note always present | `consumer_note` = mandatory disclaimer on every artifact (I-108) |
| Provenance completeness | `provenance_complete=False` if `signal_id`, `document_id`, or `analysis_source` is empty (I-108) |
| No write-back channel | No fills, no acknowledgements, no reverse flow in-platform (I-101, I-104) |

---

## Artifact Contract

### `SignalHandoff` (frozen dataclass)

```python
@dataclass(frozen=True)
class SignalHandoff:
    # Identity
    handoff_id: str       # UUID — new per create_signal_handoff() call
    signal_id: str
    document_id: str

    # Signal semantics
    target_asset: str
    direction_hint: str   # "bullish" | "bearish" | "neutral" — HINT, not a direction order
    priority: int         # 1–10
    confidence: float     # 0.0–1.0

    # Provenance (I-108)
    analysis_source: str  # "RULE" | "INTERNAL" | "EXTERNAL_LLM"

    # Context
    sentiment: str
    market_scope: str
    affected_assets: list[str]
    evidence_summary: str  # truncated to _MAX_EVIDENCE_CHARS = 500 (I-106)
    risk_notes: str

    # Timestamps (ISO 8601)
    published_at: str | None
    extracted_at: str
    handoff_at: str

    # Audit
    provenance_complete: bool
    consumer_note: str    # always = _CONSUMER_NOTE (I-108)
```

### What is NOT in `SignalHandoff`

| Field | Reason excluded |
|-------|-----------------|
| `recommended_next_step` | Internal KAI field — MUST NOT be forwarded (I-107) |
| Full document text | Truncated to 500 chars in `evidence_summary` (I-106) |
| DB session / repository reference | Delivery artifact, not a live query handle |
| Execution flags | No execution surface exists in KAI (I-101) |

---

## Factory Function

### `create_signal_handoff(candidate: SignalCandidate) -> SignalHandoff`

- Generates new `handoff_id` = `str(uuid.uuid4())`
- Sets `handoff_at` = `datetime.now(UTC).isoformat()`
- Truncates `supporting_evidence` to `_MAX_EVIDENCE_CHARS`
- Converts `sentiment` and `market_scope` to string values
- Does NOT copy `recommended_next_step`
- Computes `provenance_complete` from `signal_id`, `document_id`, `analysis_source`

---

## Serialization

### `to_json_dict() -> dict[str, object]`

Required keys:

```
report_type, handoff_id, signal_id, document_id, target_asset, direction_hint,
priority, confidence, analysis_source, sentiment, market_scope, affected_assets,
evidence_summary, risk_notes, extracted_at, handoff_at, provenance_complete,
consumer_note
```

`report_type` is always `"signal_handoff"`.

### `save_signal_handoff(handoff, path) -> Path`

Writes a single `SignalHandoff` as pretty-printed JSON. Creates parent directories.

### `save_signal_handoff_batch_jsonl(handoffs, path) -> Path`

Writes multiple `SignalHandoff` objects as JSONL (one JSON per line). Empty list → empty file.
Pull-only batch format for external consumers (I-108).

---

## CLI

```
research signal-handoff [OPTIONS]

Options:
  --limit INT        Number of recent documents to scan (default: 100)
  --min-priority INT Minimum priority score (default: 8)
  --watchlist TEXT   Watchlist name to boost priority of matching assets
  --out PATH         Output .jsonl file for batch handoff
  --out-json PATH    Output .json file for single handoff (first result)
```

Output always states: "advisory only, not execution (I-101)"

---

## Consumer Note

Every `SignalHandoff` carries the mandatory disclaimer:

```
"Signal delivery is not execution (I-101).
This handoff is advisory only.
Consumption does not confirm trade intent (I-104).
KAI does not execute trades."
```

---

## Relation to Other Layers

```
SignalCandidate (internal)           SignalHandoff (external delivery)
────────────────────────────         ──────────────────────────────────
Full analysis context                Truncated evidence (≤ 500 chars)
recommended_next_step                — excluded —
Mutable (Pydantic model)             Immutable (frozen dataclass)
Internal KAI processing              External consumer artifact
```

```
distribution.py (ExecutionHandoffReport)    execution_handoff.py (SignalHandoff)
─────────────────────────────────────────   ────────────────────────────────────
Batch read-only report (I-101–I-104)        Immutable per-signal artifact (I-105–I-108)
Source-level provenance (route, provider)   Consumer-level provenance (handoff_id, audit)
MCP: get_signals_for_execution              CLI: signal-handoff / MCP: get_signals_for_execution
```

---

## MCP Integration

Sprint 18 MCP server exposes `SignalHandoff` via:

- `get_signals_for_execution` — returns `ExecutionHandoffReport` (distribution layer)
- `acknowledge_signal_handoff` — blocked fail-closed; no write-back channel is exposed for signals
- `get_handoff_summary` — read-only summary for legacy local audit files only

---

## Invariants (I-105–I-108)

Full text in `docs/contracts.md §28`. Summary:

| ID | Summary |
|----|---------|
| I-105 | `SignalHandoff` is frozen. No mutation after construction. New UUID per call. |
| I-106 | `evidence_summary` MUST be ≤ 500 chars. No full document text forwarded. |
| I-107 | `recommended_next_step` MUST NOT be included in any `SignalHandoff` or its serialization. |
| I-108 | `consumer_note` always present. `provenance_complete=False` if any of `signal_id`/`document_id`/`analysis_source` is empty. |

---

## Sprint 16 Completion Criteria

```
Sprint 16 gilt als abgeschlossen wenn:
  - [x] 16.1: app/research/execution_handoff.py — SignalHandoff frozen dataclass ✅
  - [x] 16.2: create_signal_handoff(), save_signal_handoff(), save_signal_handoff_batch_jsonl() ✅
  - [x] 16.3: test_execution_handoff.py — 22 Tests gruen ✅
  - [x] 16.4: CLI: research signal-handoff --out/--out-json ✅
  - [x] 16.5: test_cli.py — 3 signal-handoff Tests gruen ✅
  - [x] 16.6: I-105–I-108 in docs/contracts.md §28 ✅
  - [x] 16.7: docs/sprint16_execution_handoff_contract.md vollstaendig ✅
  - [x] 16.8: AGENTS.md P24 eingetragen ✅
  - [x] 16.9: TASKLIST.md Sprint-16 vollstaendig ✅
  - [x] 16.10: intelligence_architecture.md Sprint-16 Zeile ✅
  - [x] ruff check . sauber ✅
  - [x] pytest passing (897 Tests, kein Rueckschritt) ✅
  - [x] recommended_next_step ausgeschlossen ✅
  - [x] Evidence auf 500 chars begrenzt ✅
  - [x] consumer_note immer gesetzt ✅
  - [x] provenance_complete korrekt berechnet ✅
```
