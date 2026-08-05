"""CORE8 forecaster panel — SHADOW-epoch issuance/resolution engine + store.

Internal research machinery ONLY (design draft v0.6, NOT sealed, NOT a product,
no dashboard, no publication). The shadow epoch ("shadow-0") is the data clock
that exercises the issuance -> resolution -> scoring pipeline with the
deterministic baselines B0/SHADOW-B1. Doctrine: KAI issues no unsealed
forecasts — therefore ``p_kai`` is ALWAYS ``null`` here and every record
carries ``sealed: false``.

Pieces:

* :func:`issue_panel` — computes all 8 CORE8 questions for an anchor date t0
  (daily candle, 00:00 UTC), seals the trailing-median constants and the
  baseline probabilities INTO the record, and appends one JSONL line to
  ``artifacts/research/forecaster_panel/shadow_panels.jsonl`` with a monotone
  ``panel_index`` and ``prev_panel_hash`` = SHA-256 of the previous raw line
  (genesis = 64x"0").
* :func:`resolve_due` — resolves questions whose final data date is complete,
  appending lines to ``resolutions.jsonl`` (reference: panel_index +
  question_id) with Brier scores for B0/B1 computed against the RECORDED
  baseline probabilities. Missing datapoints -> ``INVALID_PREDECLARED``
  (reason ``data-gap``). Records are NEVER deleted or rewritten.
* :func:`panel_status` — per-question counters (issued/resolved/invalid/pending).
* :func:`verify_panel_chain` — hash-chain + index-monotony check for the store.
* :func:`build_binance_daily_provider` — production provider on the existing
  read-only Binance daily-klines path (no API key, fail-closed). No network
  call happens at import time; providers are always injected.

Outcome taxonomy (draft §4): ``RESOLVED`` / ``INVALID_PREDECLARED`` /
``UNRESOLVABLE`` / ``MISSED_ISSUANCE``. The shadow engine itself emits the
first two; the latter two are reserved states of the sealed protocol and exist
here as constants so downstream tooling shares one vocabulary.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from app.research.forecaster_resolvers import (
    BTC_SYMBOL,
    CORE8,
    ETH_SYMBOL,
    QUESTION_IDS,
    DailyCandle,
    DailyKlinesProvider,
    DataGap,
    KlinesUnavailableError,
    PanelData,
    WindowCache,
    baseline_b0,
    baseline_b1,
    median_window_ends,
    question_outcome,
    sealed_median_for,
)

logger = logging.getLogger(__name__)

DEFAULT_STORE_DIR = Path("artifacts/research/forecaster_panel")
PANELS_FILENAME = "shadow_panels.jsonl"
RESOLUTIONS_FILENAME = "resolutions.jsonl"

PANEL_SCHEMA = "forecaster_panel/shadow-v1"
RESOLUTION_SCHEMA = "forecaster_resolution/shadow-v1"
EPOCH_ID = "shadow-0"
BASELINE_FAMILY = "SHADOW-B0/B1-v1"
GENESIS_PREV_HASH = "0" * 64

STATUS_ISSUED = "ISSUED"
STATUS_RESOLVED = "RESOLVED"
STATUS_INVALID_PREDECLARED = "INVALID_PREDECLARED"
STATUS_UNRESOLVABLE = "UNRESOLVABLE"  # reserved (sealed protocol)
STATUS_MISSED_ISSUANCE = "MISSED_ISSUANCE"  # reserved (sealed protocol)
REASON_DATA_GAP = "data-gap"

# Trailing days fetched at issuance: deepest need is B0's 365d anchors, whose
# per-anchor Q7 medians reach back a further 90 + 6 days (see resolvers).
ISSUANCE_LOOKBACK_DAYS = 470
# Resolution needs at most vol[t0-6 .. t0] plus closes/lows out to t0+30.
RESOLUTION_LOOKBACK_DAYS = 6
MAX_HORIZON_DAYS = 30

BRIER_QUANTUM = Decimal("0.00000001")

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class PanelRecord:
    """One issued panel: the appended JSONL line plus convenience fields."""

    panel_index: int
    reference_observation_id: str
    panel_hash: str
    line: str
    payload: dict[str, Any]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _midnight_utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Deterministic JSON: sorted keys, compact separators, no ASCII-escaping."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def line_hash(line: str) -> str:
    """SHA-256 over one raw JSONL line (without its trailing newline)."""
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def compute_panel_hash(record: Mapping[str, Any]) -> str:
    """SHA-256 over the canonical record WITHOUT the ``panel_hash`` field itself."""
    payload = {k: v for k, v in record.items() if k != "panel_hash"}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def brier_score(p: Decimal, outcome: bool) -> Decimal:
    """Brier score of one probability against a binary outcome, quantized."""
    y = Decimal(1) if outcome else Decimal(0)
    return ((p - y) ** 2).quantize(BRIER_QUANTUM, rounding=ROUND_HALF_EVEN)


# --------------------------------------------------------------------------- #
# Store primitives (append-only JSONL)
# --------------------------------------------------------------------------- #


def _panels_path(store_dir: Path) -> Path:
    return Path(store_dir) / PANELS_FILENAME


def _resolutions_path(store_dir: Path) -> Path:
    return Path(store_dir) / RESOLUTIONS_FILENAME


def _read_lines(path: Path) -> list[str]:
    """Non-empty raw lines of a JSONL file ([] when missing)."""
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()


def read_panels(store_dir: Path = DEFAULT_STORE_DIR) -> list[dict[str, Any]]:
    """All parseable panel records (tolerant read for status/reporting)."""
    out: list[dict[str, Any]] = []
    for raw in _read_lines(_panels_path(store_dir)):
        try:
            parsed = json.loads(raw)
        except ValueError:
            logger.warning("forecaster_panel_corrupt_line file=%s", _panels_path(store_dir))
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def read_resolutions(store_dir: Path = DEFAULT_STORE_DIR) -> list[dict[str, Any]]:
    """All parseable resolution records (tolerant read)."""
    out: list[dict[str, Any]] = []
    for raw in _read_lines(_resolutions_path(store_dir)):
        try:
            parsed = json.loads(raw)
        except ValueError:
            logger.warning(
                "forecaster_resolution_corrupt_line file=%s", _resolutions_path(store_dir)
            )
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def verify_panel_chain(store_dir: Path = DEFAULT_STORE_DIR) -> list[str]:
    """Verify the panel hash chain. Returns a list of errors (empty = intact).

    Checks per line: parseability, ``panel_index == position``,
    ``prev_panel_hash`` == SHA-256 of the previous raw line (genesis for the
    first), and that the embedded ``panel_hash`` recomputes from the record.
    """
    errors: list[str] = []
    prev = GENESIS_PREV_HASH
    for idx, raw in enumerate(_read_lines(_panels_path(store_dir))):
        try:
            record = json.loads(raw)
        except ValueError:
            errors.append(f"unparseable_line idx={idx}")
            prev = line_hash(raw)
            continue
        if not isinstance(record, dict):
            errors.append(f"non_object_line idx={idx}")
            prev = line_hash(raw)
            continue
        if record.get("panel_index") != idx:
            errors.append(f"panel_index_mismatch idx={idx} got={record.get('panel_index')!r}")
        if record.get("prev_panel_hash") != prev:
            errors.append(f"chain_break idx={idx}")
        recomputed = compute_panel_hash(record)
        if record.get("panel_hash") != recomputed:
            errors.append(f"panel_hash_mismatch idx={idx}")
        prev = line_hash(raw)
    return errors


# --------------------------------------------------------------------------- #
# Issuance
# --------------------------------------------------------------------------- #


def _fetch_panel_data(
    provider: DailyKlinesProvider, start: date, end: date
) -> dict[str, Mapping[date, DailyCandle]]:
    return {
        BTC_SYMBOL: provider(BTC_SYMBOL, start, end),
        ETH_SYMBOL: provider(ETH_SYMBOL, start, end),
    }


def _build_question(
    spec_id: str,
    data: PanelData,
    t0: date,
    cache: WindowCache,
) -> dict[str, Any]:
    """One question entry of the panel record (sealed constants + baselines)."""
    spec = next(s for s in CORE8 if s.question_id == spec_id)
    due_at = _midnight_utc(t0 + timedelta(days=spec.horizon_days + 1)).isoformat()
    entry: dict[str, Any] = {
        "question_id": spec.question_id,
        "title": spec.title,
        "rule": spec.rule,
        "symbols": list(spec.symbols),
        "horizon_days": spec.horizon_days,
        "due_at": due_at,
        "median_sealed": None,
        "p_kai": None,  # shadow epoch: NO model forecast, ever
        "baselines": None,
        "status": STATUS_ISSUED,
        "invalid_reason": None,
        "missing_data": None,
    }

    median: Decimal | None = None
    if spec.needs_sealed_median:
        sealed = sealed_median_for(spec.question_id, data, t0, cache)
        if isinstance(sealed, DataGap):
            entry["status"] = STATUS_INVALID_PREDECLARED
            entry["invalid_reason"] = REASON_DATA_GAP
            entry["missing_data"] = {"detail": list(sealed.missing), "count": sealed.count}
            return entry
        assert isinstance(sealed, Decimal)
        median = sealed
        ends = median_window_ends(spec.question_id, t0)
        entry["median_sealed"] = {
            "value": str(median),
            "constant": spec.median_constant,
            "n_windows": len(ends),
            "window_ends_from": ends[-1].isoformat(),
            "window_ends_to": ends[0].isoformat(),
        }

    b0, b0_n = baseline_b0(spec.question_id, data, t0, cache)
    b1, b1_n = baseline_b1(spec.question_id, data, t0, median_sealed=median, cache=cache)
    entry["baselines"] = {"b0": str(b0), "b0_n": b0_n, "b1": str(b1), "b1_n": b1_n}
    return entry


def issue_panel(
    t0: date,
    klines_provider: DailyKlinesProvider,
    *,
    store_dir: Path = DEFAULT_STORE_DIR,
    clock: Clock | None = None,
    venue_names: Sequence[str] = ("binance",),
) -> PanelRecord:
    """Issue the CORE8 shadow panel anchored at daily candle ``t0`` (00:00 UTC).

    Computes sealed medians + B0/SHADOW-B1 per question from provider data
    ``[t0 - 470d, t0]`` and appends ONE JSONL line (append-only, hash-chained).
    A question whose sealed median cannot be computed (data gap) is issued as
    ``INVALID_PREDECLARED(data-gap)`` — the record is kept, never deleted.

    Raises:
        ValueError: a panel for this ``t0`` already exists, or the store
            contains an unparseable line (chain integrity — fail loudly).
        Exception: provider failures propagate; no partial record is written
            (an operationally missed slot is a MISSED_ISSUANCE at ops level).
    """
    now = clock if clock is not None else _utc_now
    panels_path = _panels_path(Path(store_dir))
    existing = _read_lines(panels_path)

    prev_hash = GENESIS_PREV_HASH
    reference_id = t0.isoformat()
    for raw in existing:
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise ValueError(f"corrupt panel store (unparseable line): {panels_path}") from exc
        if isinstance(parsed, dict) and parsed.get("reference_observation_id") == reference_id:
            raise ValueError(f"panel already issued for t0={reference_id}")
    if existing:
        prev_hash = line_hash(existing[-1])

    data = _fetch_panel_data(klines_provider, t0 - timedelta(days=ISSUANCE_LOOKBACK_DAYS), t0)
    cache: WindowCache = {}
    questions = [_build_question(qid, data, t0, cache) for qid in QUESTION_IDS]

    data_cutoff_at = _midnight_utc(t0 + timedelta(days=1)).isoformat()
    record: dict[str, Any] = {
        "schema": PANEL_SCHEMA,
        "epoch_id": EPOCH_ID,
        "sealed": False,  # shadow epoch — explicitly unsealed research record
        "baseline_family": BASELINE_FAMILY,
        "panel_index": len(existing),
        "prev_panel_hash": prev_hash,
        "reference_observation_id": reference_id,
        "data_cutoff_at": data_cutoff_at,
        "forecast_computed_at": now().isoformat(),
        "forecast_effective_at": data_cutoff_at,
        "venues": list(venue_names),
        "shadow_single_venue": len(venue_names) < 2,
        "questions": questions,
    }
    record["panel_hash"] = compute_panel_hash(record)

    line = canonical_json(record)
    _append_line(panels_path, line)
    logger.info(
        "forecaster_panel_issued t0=%s panel_index=%d invalid=%d",
        reference_id,
        record["panel_index"],
        sum(1 for q in questions if q["status"] != STATUS_ISSUED),
    )
    return PanelRecord(
        panel_index=int(record["panel_index"]),
        reference_observation_id=reference_id,
        panel_hash=str(record["panel_hash"]),
        line=line,
        payload=record,
    )


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def _parse_due_at(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _recorded_median(question: Mapping[str, Any]) -> Decimal | None:
    sealed = question.get("median_sealed")
    if isinstance(sealed, Mapping):
        value = sealed.get("value")
        if isinstance(value, str):
            return Decimal(value)
    return None


def _recorded_baselines(question: Mapping[str, Any]) -> tuple[Decimal, Decimal] | None:
    baselines = question.get("baselines")
    if not isinstance(baselines, Mapping):
        return None
    b0 = baselines.get("b0")
    b1 = baselines.get("b1")
    if isinstance(b0, str) and isinstance(b1, str):
        return Decimal(b0), Decimal(b1)
    return None


def resolve_due(
    now: datetime,
    klines_provider: DailyKlinesProvider,
    *,
    store_dir: Path = DEFAULT_STORE_DIR,
) -> list[dict[str, Any]]:
    """Resolve all issued questions whose outcome window is complete at ``now``.

    A question is due once ``due_at <= now`` (due_at = close time of its final
    daily candle). The outcome is evaluated against the RECORDED sealed median;
    Brier scores use the RECORDED B0/B1 probabilities — resolution never
    recomputes issuance-time quantities. Already-resolved (panel_index,
    question_id) pairs are skipped, so the call is idempotent. A provider
    failure skips the affected panel (stays pending — fail-closed) instead of
    writing false data-gap invalidations; an authoritative provider response
    with missing days yields ``INVALID_PREDECLARED(data-gap)``.

    Returns the list of resolution payloads written (possibly empty).
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    store = Path(store_dir)
    done: set[tuple[int, str]] = set()
    for resolution in read_resolutions(store):
        idx = resolution.get("panel_index")
        qid = resolution.get("question_id")
        if isinstance(idx, int) and isinstance(qid, str):
            done.add((idx, qid))

    written: list[dict[str, Any]] = []
    for panel in read_panels(store):
        panel_index = panel.get("panel_index")
        reference_id = panel.get("reference_observation_id")
        questions = panel.get("questions")
        if not isinstance(panel_index, int) or not isinstance(reference_id, str):
            continue
        if not isinstance(questions, list):
            continue
        try:
            t0 = date.fromisoformat(reference_id)
        except ValueError:
            logger.warning("forecaster_panel_bad_reference id=%r", reference_id)
            continue

        due: list[Mapping[str, Any]] = []
        for question in questions:
            if not isinstance(question, Mapping):
                continue
            qid = question.get("question_id")
            if not isinstance(qid, str) or (panel_index, qid) in done:
                continue
            if question.get("status") != STATUS_ISSUED:
                continue  # INVALID_PREDECLARED at issuance is terminal
            due_at = _parse_due_at(question.get("due_at"))
            if due_at is None or due_at > now:
                continue
            due.append(question)
        if not due:
            continue

        try:
            data = _fetch_panel_data(
                klines_provider,
                t0 - timedelta(days=RESOLUTION_LOOKBACK_DAYS),
                t0 + timedelta(days=MAX_HORIZON_DAYS),
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed: leave pending
            logger.warning(
                "forecaster_panel_fetch_failed panel_index=%s t0=%s exc=%s",
                panel_index,
                reference_id,
                exc,
            )
            continue

        for question in due:
            qid = str(question["question_id"])
            outcome = question_outcome(qid, data, t0, _recorded_median(question))
            payload: dict[str, Any] = {
                "schema": RESOLUTION_SCHEMA,
                "epoch_id": EPOCH_ID,
                "panel_index": panel_index,
                "reference_observation_id": reference_id,
                "question_id": qid,
                "resolved_at": now.isoformat(),
                "status": STATUS_RESOLVED,
                "invalid_reason": None,
                "missing_data": None,
                "outcome": None,
                "brier": None,
            }
            if isinstance(outcome, DataGap):
                payload["status"] = STATUS_INVALID_PREDECLARED
                payload["invalid_reason"] = REASON_DATA_GAP
                payload["missing_data"] = {"detail": list(outcome.missing), "count": outcome.count}
            else:
                payload["outcome"] = outcome
                recorded = _recorded_baselines(question)
                if recorded is not None:
                    b0_p, b1_p = recorded
                    payload["brier"] = {
                        "b0": str(brier_score(b0_p, outcome)),
                        "b1": str(brier_score(b1_p, outcome)),
                    }
            _append_line(_resolutions_path(store), canonical_json(payload))
            done.add((panel_index, qid))
            written.append(payload)

    if written:
        logger.info("forecaster_panel_resolved n=%d now=%s", len(written), now.isoformat())
    return written


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #


def panel_status(*, store_dir: Path = DEFAULT_STORE_DIR) -> dict[str, Any]:
    """Counters per question: issued / invalid / resolved / pending."""
    store = Path(store_dir)
    panels = read_panels(store)
    resolutions = read_resolutions(store)

    by_key: dict[tuple[int, str], str] = {}
    for resolution in resolutions:
        idx = resolution.get("panel_index")
        qid = resolution.get("question_id")
        status = resolution.get("status")
        if isinstance(idx, int) and isinstance(qid, str) and isinstance(status, str):
            by_key[(idx, qid)] = status

    counters: dict[str, dict[str, int]] = {
        qid: {
            "issued": 0,
            "invalid_at_issuance": 0,
            "resolved": 0,
            "invalid_at_resolution": 0,
            "pending": 0,
        }
        for qid in QUESTION_IDS
    }
    for panel in panels:
        panel_index = panel.get("panel_index")
        questions = panel.get("questions")
        if not isinstance(panel_index, int) or not isinstance(questions, list):
            continue
        for question in questions:
            if not isinstance(question, Mapping):
                continue
            qid = question.get("question_id")
            if not isinstance(qid, str) or qid not in counters:
                continue
            row = counters[qid]
            if question.get("status") != STATUS_ISSUED:
                row["invalid_at_issuance"] += 1
                continue
            row["issued"] += 1
            resolution_status = by_key.get((panel_index, qid))
            if resolution_status == STATUS_RESOLVED:
                row["resolved"] += 1
            elif resolution_status == STATUS_INVALID_PREDECLARED:
                row["invalid_at_resolution"] += 1
            else:
                row["pending"] += 1

    return {
        "schema": "forecaster_panel_status/shadow-v1",
        "epoch_id": EPOCH_ID,
        "store_dir": str(store),
        "panels": len(panels),
        "resolutions": len(resolutions),
        "questions": counters,
    }


# --------------------------------------------------------------------------- #
# Production provider (read-only Binance daily klines — reuses existing path)
# --------------------------------------------------------------------------- #


def build_binance_daily_provider() -> DailyKlinesProvider:
    """Daily-klines provider on the existing read-only Binance path (no key).

    Reuses :class:`app.market_data.binance_adapter.BinanceAdapter` +
    :func:`app.market_data.history_loader.load_ohlcv_history` (pagination,
    de-dup, gap accounting). Raises :class:`KlinesUnavailableError` when the
    venue returns NOTHING for a non-empty range (transport failure), so the
    engine skips instead of minting false data-gap invalidations; days missing
    from an otherwise-populated response surface as data gaps downstream.

    Honesty note: the shared adapter parses floats; values are converted to
    ``Decimal`` via ``str`` at this boundary. All resolver arithmetic beyond
    this point is Decimal-only. The sealed epoch replaces this with a
    decimal-pure multi-venue fetch (design draft §4).
    """
    import asyncio

    from app.market_data.binance_adapter import BinanceAdapter
    from app.market_data.history_loader import load_ohlcv_history
    from app.market_data.models import OHLCV

    adapter = BinanceAdapter()

    def fetch(symbol: str, start: date, end: date) -> Mapping[date, DailyCandle]:
        start_ms = int(_midnight_utc(start).timestamp() * 1000)
        end_ms = int(_midnight_utc(end).timestamp() * 1000)

        async def _fetch(sym: str, timeframe: str, start_time_ms: int, limit: int) -> list[OHLCV]:
            return await adapter.get_ohlcv(sym, timeframe, limit=limit, start_time_ms=start_time_ms)

        async def _run() -> Any:
            return await load_ohlcv_history(symbol, "1d", start_ms, end_ms, fetch=_fetch)

        history = asyncio.run(_run())
        if not history.candles:
            raise KlinesUnavailableError(
                f"binance returned no daily candles for {symbol} [{start}..{end}]:"
                f" {adapter.last_error or 'unknown'}"
            )
        out: dict[date, DailyCandle] = {}
        for candle in history.candles:
            try:
                day = datetime.fromisoformat(candle.timestamp_utc).date()
            except ValueError:
                continue
            out[day] = DailyCandle(
                day=day,
                close=Decimal(str(candle.close)),
                low=Decimal(str(candle.low)),
                volume=Decimal(str(candle.volume)),
            )
        return out

    return fetch


__all__ = [
    "BASELINE_FAMILY",
    "BRIER_QUANTUM",
    "DEFAULT_STORE_DIR",
    "EPOCH_ID",
    "GENESIS_PREV_HASH",
    "ISSUANCE_LOOKBACK_DAYS",
    "PANELS_FILENAME",
    "PANEL_SCHEMA",
    "REASON_DATA_GAP",
    "RESOLUTIONS_FILENAME",
    "RESOLUTION_SCHEMA",
    "STATUS_INVALID_PREDECLARED",
    "STATUS_ISSUED",
    "STATUS_MISSED_ISSUANCE",
    "STATUS_RESOLVED",
    "STATUS_UNRESOLVABLE",
    "Clock",
    "PanelRecord",
    "brier_score",
    "build_binance_daily_provider",
    "canonical_json",
    "compute_panel_hash",
    "issue_panel",
    "line_hash",
    "panel_status",
    "read_panels",
    "read_resolutions",
    "resolve_due",
    "verify_panel_chain",
]
