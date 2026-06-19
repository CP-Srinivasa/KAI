"""Shadow-Candidate-Ledger — learn the entry signal WITHOUT trading (Phase B).

Background (2026-06-02): the autonomous entry loop is ``EXECUTION_ENTRY_MODE=
disabled`` because its closed-trade cohort has negative cost-adjusted edge
(P(mu_net>0)=2.56%, gross_bps_mean=-35; root-cause report
``root_cause_stopout_cascade_20260602``). Phase-A could only *retrospectively*
reconstruct MAE/MFE for 52/110 trades (no intra-trade prices stored) and regime
per-trade was not reconstructable at all.

Phase B closes that instrumentation gap **without** re-enabling trading: for
every signal the loop WOULD have entered, we record a hypothetical candidate
(entry/SL/TP/geometry/regime/gate-decision) and later resolve forward returns +
MAE/MFE from market klines. No paper fill, no position, no order — pure
observation. This is strictly safer than ``entry_mode=paper`` and gives the same
(better: regime-tagged, MAE/MFE-complete) evidence for the next entry/exit
candidate.

Diagnose buckets the resolved data is meant to separate (see ``classify``):

    ADVERSE_SELECTION : MAE hits fast, MFE ~0, forward returns negative from t0
    STOP_IN_NOISE_BAND: MFE often > stop distance before MAE hits the stop
    TP_UNREACHABLE    : MFE rarely reaches TP, stop reached more often
    REGIME_MISMATCH   : losses concentrate in a regime / side / symbol

Design: compute functions are pure / IO-free (unit-testable with synthetic
bars). Persistence + kline fetching are thin shells around them.
"""

from __future__ import annotations

import json
import logging
import statistics
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

LEDGER_PATH = Path("artifacts/shadow_candidate_ledger.jsonl")
RESOLVED_PATH = Path("artifacts/shadow_candidate_resolved.jsonl")

# Forward-return horizons in seconds. The longest also bounds the MAE/MFE window.
HORIZONS_S: tuple[int, ...] = (60, 300, 900, 3600)

# A price bar: (open_time_ms, high, low, close). Matches the first/2nd/3rd/4th
# Binance kline fields — but the ledger never imports a network client; a fetcher
# is injected so tests stay offline and the source stays swappable.
Bar = tuple[int, float, float, float]
KlineFetcher = Callable[[str, int, int], Sequence[Bar] | None]


# --------------------------------------------------------------------------- #
# Pure compute
# --------------------------------------------------------------------------- #


def side_adjusted_bps(entry: float, price: float, side: str) -> float:
    """Return in bps from entry to ``price``, signed so positive = favourable."""
    if entry <= 0:
        return 0.0
    raw = (price - entry) / entry * 1e4
    return raw if side == "long" else -raw


def compute_forward_returns(
    *,
    entry_price: float,
    side: str,
    entry_ts_ms: int,
    bars: Sequence[Bar],
    horizons_s: Sequence[int] = HORIZONS_S,
) -> dict[str, float | None]:
    """Side-adjusted close-to-close return at each horizon (bps).

    For horizon ``h`` we take the close of the last bar whose open-time is
    <= entry_ts + h. ``None`` when no bar covers the horizon yet (not elapsed /
    no data) — never silently 0.
    """
    out: dict[str, float | None] = {}
    last_open_ms = bars[-1][0] if bars else None
    for h in horizons_s:
        target_ms = entry_ts_ms + h * 1000
        # Horizon is only resolvable if the series actually extends to it; a
        # series ending before the horizon yields None (never carry-forward the
        # last close as if it were the horizon price).
        if last_open_ms is None or last_open_ms < target_ms:
            out[f"fwd_{h}s_bps"] = None
            continue
        chosen: float | None = None
        for open_ms, _hi, _lo, close in bars:
            if open_ms <= target_ms:
                chosen = close
            else:
                break
        out[f"fwd_{h}s_bps"] = (
            None if chosen is None else round(side_adjusted_bps(entry_price, chosen, side), 2)
        )
    return out


@dataclass
class ExcursionResult:
    mae_bps: float | None = None
    mfe_bps: float | None = None
    mfe_before_mae: bool | None = None
    time_to_mae_s: float | None = None
    time_to_mfe_s: float | None = None
    bars_seen: int = 0


def compute_excursion(
    *,
    entry_price: float,
    side: str,
    entry_ts_ms: int,
    bars: Sequence[Bar],
    window_s: int = max(HORIZONS_S),
) -> ExcursionResult:
    """MAE/MFE over [entry_ts, entry_ts+window] using bar high/low extremes."""
    if entry_price <= 0:
        return ExcursionResult()
    end_ms = entry_ts_ms + window_s * 1000
    best_fav = -1e18
    worst_adv = 1e18
    fav_ms: int | None = None
    adv_ms: int | None = None
    seen = 0
    for open_ms, hi, lo, _close in bars:
        if open_ms < entry_ts_ms or open_ms > end_ms:
            continue
        seen += 1
        if side == "long":
            fav = side_adjusted_bps(entry_price, hi, "long")
            adv = side_adjusted_bps(entry_price, lo, "long")
        else:
            # short: favourable when price falls (low), adverse when it rises (high)
            fav = side_adjusted_bps(entry_price, lo, "short")
            adv = side_adjusted_bps(entry_price, hi, "short")
        if fav > best_fav:
            best_fav, fav_ms = fav, open_ms
        if adv < worst_adv:
            worst_adv, adv_ms = adv, open_ms
    if seen == 0:
        return ExcursionResult()
    return ExcursionResult(
        mae_bps=round(worst_adv, 2),
        mfe_bps=round(best_fav, 2),
        mfe_before_mae=(fav_ms is not None and adv_ms is not None and fav_ms <= adv_ms),
        time_to_mae_s=None if adv_ms is None else (adv_ms - entry_ts_ms) / 1000.0,
        time_to_mfe_s=None if fav_ms is None else (fav_ms - entry_ts_ms) / 1000.0,
        bars_seen=seen,
    )


# --------------------------------------------------------------------------- #
# Candidate record
# --------------------------------------------------------------------------- #


@dataclass
class ShadowCandidate:
    """A hypothetical entry the loop WOULD have taken (no execution)."""

    candidate_id: str
    ts_utc: str  # hypothetical entry time (= signal time)
    symbol: str
    side: str
    entry_price: float
    stop_price: float | None = None
    take_price: float | None = None
    stop_dist_bps: float | None = None
    take_dist_bps: float | None = None
    rr: float | None = None
    regime: str | None = None
    regime_vol_class: str | None = None
    cooldown_state: str | None = None
    signal_confidence: float | None = None
    recommended_priority: int | None = None
    gate_would_reject: bool | None = None
    gate_reason_codes: list[str] = field(default_factory=list)
    entry_mode: str = "disabled"
    # NEO-P-002 (Weg B) source/stage attribution. Only fields REALLY derivable in
    # the shadow path are populated by the loop; the rest stay explicit
    # unknown/missing rather than fabricated (CLAUDE.md: no silent assumptions).
    # See _record_shadow_candidate in trading_loop.py for the field-by-field
    # provenance. ``source`` default "autonomous_loop" is preserved ONLY so the
    # 644 legacy v1 ledger rows read back unchanged — the loop now writes
    # "canary_probe"/"autonomous_generator" via derive_autonomous_signal_source.
    source: str = "autonomous_loop"
    candidate_kind: str | None = None
    source_stage: str | None = None
    score_source: str | None = None
    signal_origin: str | None = None
    document_id: str | None = None
    cycle_id: str | None = None
    is_canary: bool = False
    is_synthetic_default: bool = False
    priority: int | None = None
    sentiment: str | None = None
    directional_state: str | None = None
    # WP-H (2026-06-15): TradingView Recommend.All evidence (unofficial datafeed,
    # default-off). Recorded for measurement only — does NOT mutate
    # signal_confidence (keeps WP-D calibration pure). ``tv_contradiction`` = TV
    # strongly opposes the signal direction (a future execution-time dampening
    # candidate, once confluence is shown to add edge).
    tv_rating: float | None = None
    tv_contradiction: bool | None = None
    schema_version: str = "v2"

    @staticmethod
    def from_geometry(
        *,
        candidate_id: str,
        ts_utc: str,
        symbol: str,
        side: str,
        entry_price: float,
        stop_price: float | None,
        take_price: float | None,
        **extra: object,
    ) -> ShadowCandidate:
        sd = td = rr = None
        if stop_price and entry_price > 0:
            sd = round(abs(entry_price - stop_price) / entry_price * 1e4, 2)
        if take_price and entry_price > 0:
            td = round(abs(take_price - entry_price) / entry_price * 1e4, 2)
        if sd and td:
            rr = round(td / sd, 3)
        return ShadowCandidate(
            candidate_id=candidate_id,
            ts_utc=ts_utc,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_price=stop_price,
            take_price=take_price,
            stop_dist_bps=sd,
            take_dist_bps=td,
            rr=rr,
            **extra,  # type: ignore[arg-type]
        )


def normalize_source_name(raw: str | None) -> str:
    """Canonicalize a news source_name for stable cohort bucketing.

    Measure-only hygiene for the source x direction x forward-bps bridge: the
    observed splits are pure casing (``decrypt``/``Decrypt``,
    ``cointelegraph``/``CoinTelegraph``), so lowercasing + whitespace-collapsing
    re-joins them without risking over-merge of genuinely distinct sources.
    Missing/blank -> ``"unknown"`` (never a fabricated source). Does NOT mutate
    any signal, gate, or execution state.
    """
    if raw is None:
        return "unknown"
    norm = " ".join(str(raw).strip().lower().split())
    return norm or "unknown"


def record_candidate(candidate: ShadowCandidate, *, path: Path = LEDGER_PATH) -> bool:
    """Append a candidate to the ledger. Fail-soft: never raises into the loop."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(candidate), ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        logger.warning("[shadow] candidate write failed: %s", exc)
        return False


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def _as_float(v: object) -> float | None:
    """Coerce a JSON-loaded value to float, or None if not numeric."""
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _parse_ts_ms(ts_utc: str) -> int | None:
    try:
        return int(datetime.fromisoformat(ts_utc.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    out: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# NEO-P-002 (Weg B): candidate_kinds representing an actual hypothetical entry
# worth resolving forward returns for. raw_scan / no_candidate / synthetic-default
# carry no entry geometry of interest and (at ~372/441 near-identical canary scan
# rows/day) would only burn kline-API calls. Legacy rows have candidate_kind None:
# those are pre-NEO-P-002 real candidates, so they ARE resolved by default (None
# treated as resolvable) to stay backward-compatible.
#
# WP-A (2026-06-16): "technical" = the WP-D technical-screener shadow candidates.
# They carry a real entry_price + side (distinct symbols, not near-identical scan
# rows), so resolving forward returns is exactly the intended measurement — without
# this, every screener candidate was silently skipped (skipped_kind) and the
# technical path produced ZERO edge evidence. They bucket into by_source /
# by_candidate_kind only; source "technical_screener" is NOT in REAL_SOURCES, so
# they never enter the autonomous-generator headline / primary_class (B-002).
RESOLVABLE_CANDIDATE_KINDS: frozenset[str] = frozenset(
    {"signal_candidate", "gate_candidate", "would_have_traded", "technical"}
)
# Sources excluded from the default (headline) resolution. ``canary_probe`` is
# the hardcoded synthetic probe (#137); ``real_analysis`` (Goal 2026-06-10) is
# the new decoupled paper-learning feeder — a separately-evaluated stream that
# must not silently merge into the autonomous-generator headline (B-002). Both
# are still resolvable via the explicit ``include_canary`` diagnostic option.
_SKIP_SOURCES: frozenset[str] = frozenset({"canary_probe", "real_analysis"})


def _is_resolvable_candidate(c: dict[str, object], *, include_canary: bool) -> bool:
    """True if a ledger row should be resolved by default.

    candidate_kind None == legacy real candidate -> resolvable. Explicit
    non-resolvable kinds (raw_scan/no_candidate/synthetic-default) and
    canary_probe source are skipped unless ``include_canary`` is set (the
    explicit diagnostic option). ``is_synthetic_default`` rows are never resolved
    by default.
    """
    if include_canary:
        return True
    kind = c.get("candidate_kind")
    if kind is not None and str(kind) not in RESOLVABLE_CANDIDATE_KINDS:
        return False
    if str(c.get("source")) in _SKIP_SOURCES:
        return False
    if bool(c.get("is_synthetic_default")):
        return False
    return True


def resolve_pending(
    *,
    fetch_klines: KlineFetcher,
    now: datetime | None = None,
    ledger_path: Path = LEDGER_PATH,
    resolved_path: Path = RESOLVED_PATH,
    window_s: int = max(HORIZONS_S),
    include_canary: bool = False,
) -> dict[str, int]:
    """Resolve candidates whose full MAE/MFE window has elapsed.

    Idempotent: candidates already present in ``resolved_path`` are skipped. Only
    candidates older than ``window_s`` are resolved (so the excursion window is
    complete). By default only real entry candidates are resolved; raw_scan /
    no_candidate / synthetic-default / canary_probe rows are skipped (counted in
    ``skipped_kind``) unless ``include_canary=True`` (explicit diagnostic).
    Returns counts {resolved, skipped_recent, skipped_kind, already, no_data}.
    """
    now = now or datetime.now(UTC)
    now_ms = int(now.timestamp() * 1000)
    candidates = _read_jsonl(ledger_path)
    done = {r.get("candidate_id") for r in _read_jsonl(resolved_path)}
    counts = {
        "resolved": 0,
        "skipped_recent": 0,
        "skipped_kind": 0,
        "already": 0,
        "no_data": 0,
    }

    for c in candidates:
        cid = c.get("candidate_id")
        if cid in done:
            counts["already"] += 1
            continue
        if not _is_resolvable_candidate(c, include_canary=include_canary):
            counts["skipped_kind"] += 1
            continue
        entry_ms = _parse_ts_ms(str(c.get("ts_utc", "")))
        if entry_ms is None:
            counts["no_data"] += 1
            continue
        if now_ms < entry_ms + window_s * 1000:
            counts["skipped_recent"] += 1
            continue
        bars = fetch_klines(
            str(c.get("symbol", "")), entry_ms - 60_000, entry_ms + window_s * 1000 + 60_000
        )
        if not bars:
            counts["no_data"] += 1
            continue
        entry_price = _as_float(c.get("entry_price")) or 0.0
        side = str(c.get("side") or "long")
        fwd = compute_forward_returns(
            entry_price=entry_price, side=side, entry_ts_ms=entry_ms, bars=bars
        )
        exc = compute_excursion(
            entry_price=entry_price, side=side, entry_ts_ms=entry_ms, bars=bars, window_s=window_s
        )
        sd = _as_float(c.get("stop_dist_bps"))
        td = _as_float(c.get("take_dist_bps"))
        resolution = {
            "candidate_id": cid,
            # Source-attribution (measure-only): carry the originating
            # document_id so the resolved row is self-contained for the
            # source x direction x forward-bps bridge. ``source_name`` is NOT
            # snapshotted here (the resolver stays DB-free / offline-pure) — it
            # derives deterministically + immutably from document_id via
            # canonical_documents at report time (normalize_source_name).
            "document_id": c.get("document_id"),
            "symbol": c.get("symbol"),
            "side": side,
            "regime": c.get("regime"),
            # #137/#140: carry source + signal_confidence so the report can
            # separate the canary probe (constant confidence) from real signals.
            "source": c.get("source"),
            "signal_confidence": _as_float(c.get("signal_confidence")),
            # NEO-P-002 (Weg B): carry the additional attribution axes forward so
            # the resolved report can split by_candidate_kind / by_score_source.
            # Legacy rows without these keys resolve to None and bucket cleanly.
            "candidate_kind": c.get("candidate_kind"),
            "score_source": c.get("score_source"),
            "signal_origin": c.get("signal_origin"),
            "is_canary": c.get("is_canary"),
            "stop_dist_bps": sd,
            "take_dist_bps": td,
            "gate_would_reject": c.get("gate_would_reject"),
            "resolved_at_utc": now.isoformat(),
            **fwd,
            "mae_bps": exc.mae_bps,
            "mfe_bps": exc.mfe_bps,
            "mfe_before_mae": exc.mfe_before_mae,
            "time_to_mae_s": exc.time_to_mae_s,
            "time_to_mfe_s": exc.time_to_mfe_s,
            "bars_seen": exc.bars_seen,
            "reached_take": (None if (td is None or exc.mfe_bps is None) else exc.mfe_bps >= td),
            "reached_stop": (None if (sd is None or exc.mae_bps is None) else exc.mae_bps <= -sd),
            "schema_version": "v2",
        }
        try:
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            with resolved_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(resolution, ensure_ascii=False) + "\n")
            counts["resolved"] += 1
        except OSError as exc_io:
            logger.warning("[shadow] resolution write failed: %s", exc_io)
    return counts


# --------------------------------------------------------------------------- #
# Report + root-cause classification
# --------------------------------------------------------------------------- #

# Heuristic verdict labels. These are HINTS over the raw distribution, never a
# replacement for it — the report always carries the numbers so a human decides.
CLASS_INSUFFICIENT = "INSUFFICIENT_DATA"
CLASS_ADVERSE = "ADVERSE_SELECTION"
CLASS_STOP_NOISE = "STOP_IN_NOISE_BAND"
CLASS_TP_UNREACHABLE = "TP_UNREACHABLE"
CLASS_PROFIT_NOT_HARVESTED = "PROFIT_NOT_HARVESTED"
CLASS_UNCLASSIFIED = "UNCLASSIFIED"

# Sources whose resolved candidates count as REAL signal evidence in the headline
# edge stats / primary_class.
#
# NEO-P-002 (Weg B) tightens #140 here: #140 kept the legacy hardcode
# ``autonomous_loop`` in REAL_SOURCES, so the 644 alt-ledger rows (which ALL carry
# the old hardcoded ``autonomous_loop``) still counted as real edge. The operator
# requirement is explicit: those legacy rows must NOT enter headline/primary_class.
# So REAL_SOURCES is now only the unified ``autonomous_generator`` AND a row must
# additionally be ``schema_version >= 2`` (see _is_real_row). ``autonomous_loop``
# is no longer real — it is fenced into the legacy buckets below. This is a
# deliberate behaviour change relative to #140 (documented in the PR body).
REAL_SOURCES: frozenset[str] = frozenset({"autonomous_generator"})

# signal_confidence informativeness (NEO-P-128-INSTR-01). A constant feature
# (e.g. the hardcoded canary 0.85) carries zero edge information and must disable
# any confidence-based conclusion downstream.
CONF_INFORMATIVE = "INFORMATIVE"
CONF_NON_INFORMATIVE_CONSTANT = "NON_INFORMATIVE_CONSTANT_FEATURE"
CONF_NO_DATA = "NO_CONFIDENCE_DATA"

MIN_SAMPLE_FOR_CLASS = 20


def _median(xs: Sequence[float]) -> float | None:
    return statistics.median(xs) if xs else None


def _rate(rows: list[dict[str, object]], key: str) -> float | None:
    vals = [bool(r.get(key)) for r in rows if isinstance(r.get(key), bool)]
    return round(sum(vals) / len(vals), 4) if vals else None


def _f(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _split(rows: list[dict[str, object]], key: str) -> dict[str, dict[str, object]]:
    buckets: dict[str, list[dict[str, object]]] = {}
    for r in rows:
        buckets.setdefault(str(r.get(key)), []).append(r)
    out: dict[str, dict[str, object]] = {}
    for k, rs in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        mfe = [v for v in (_f(r.get("mfe_bps")) for r in rs) if v is not None]
        fwd = [v for v in (_f(r.get("fwd_3600s_bps")) for r in rs) if v is not None]
        out[k] = {
            "count": len(rs),
            "reached_take_rate": _rate(rs, "reached_take"),
            "reached_stop_rate": _rate(rs, "reached_stop"),
            "median_mfe_bps": _median(mfe),
            "median_fwd_3600s_bps": _median(fwd),
        }
    return out


def classify(stats: dict[str, object]) -> str:
    """Heuristic primary root-cause class from aggregate stats (a hint)."""
    n = int(_f(stats.get("n_resolved")) or 0)
    if n < MIN_SAMPLE_FOR_CLASS:
        return CLASS_INSUFFICIENT
    mbm = _f(stats.get("mfe_before_mae_rate")) or 0.0
    take_rate = _f(stats.get("reached_take_rate")) or 0.0
    stop_rate = _f(stats.get("reached_stop_rate")) or 0.0
    med_mfe = _f(stats.get("median_mfe_bps")) or 0.0
    med_stop = _f(stats.get("median_stop_dist_bps")) or 0.0
    med_take = _f(stats.get("median_take_dist_bps")) or 0.0
    med_fwd_early = _f(stats.get("median_fwd_300s_bps")) or 0.0
    med_fwd_late = _f(stats.get("median_fwd_3600s_bps")) or 0.0

    # 1) Entry itself toxic: little favourable run, adverse comes first, early
    #    forward return already negative.
    if mbm < 0.40 and (med_stop <= 0 or med_mfe < 0.5 * med_stop) and med_fwd_early < 0:
        return CLASS_ADVERSE
    # 2) Favourable run is real and sizeable but the TP is set beyond it and the
    #    late forward return gives it back → profit not harvested.
    if (
        mbm >= 0.60
        and med_take > 0
        and med_mfe >= 0.5 * med_take
        and take_rate < 0.30
        and med_fwd_late <= 0
    ):
        return CLASS_PROFIT_NOT_HARVESTED
    # 3) Stop sits inside the favourable wiggle: price runs past the stop distance
    #    favourably first, then stops out.
    if mbm >= 0.60 and med_stop > 0 and med_mfe >= med_stop and stop_rate >= 0.50:
        return CLASS_STOP_NOISE
    # 4) TP simply rarely reached while the stop is hit often.
    if take_rate < 0.30 and stop_rate >= 0.55 and med_take > 0 and med_mfe < med_take:
        return CLASS_TP_UNREACHABLE
    return CLASS_UNCLASSIFIED


def _confidence_status(resolved: list[dict[str, object]]) -> dict[str, object]:
    """Classify signal_confidence informativeness (NEO-P-128-INSTR-01).

    A constant feature (all-equal, e.g. the hardcoded canary 0.85) carries zero
    edge information; the report flags it so no confidence-based conclusion is
    drawn. Pure. Returns status + distinct-value count + the constant value.
    """
    vals = [v for v in (_f(r.get("signal_confidence")) for r in resolved) if v is not None]
    distinct = sorted({round(v, 6) for v in vals})
    if not vals:
        return {
            "confidence_analysis_status": CONF_NO_DATA,
            "signal_confidence_distinct_count": 0,
            "signal_confidence_constant_value": None,
            "confidence_buckets_enabled": False,
        }
    if len(distinct) < 2:
        return {
            "confidence_analysis_status": CONF_NON_INFORMATIVE_CONSTANT,
            "signal_confidence_distinct_count": 1,
            "signal_confidence_constant_value": distinct[0],
            "confidence_buckets_enabled": False,
        }
    return {
        "confidence_analysis_status": CONF_INFORMATIVE,
        "signal_confidence_distinct_count": len(distinct),
        "signal_confidence_constant_value": None,
        "confidence_buckets_enabled": True,
    }


def _dedup_count(resolved: list[dict[str, object]]) -> int:
    """Conservative dedup count: collapse near-identical (canary/scan) rows.

    Non-destructive — only counts distinct keys; the report keeps ``raw_count``
    too. Keyed on geometry+source+confidence so real per-cycle candidates are not
    merged while the ~N identical canary scan rows collapse to one.
    """
    seen: set[tuple[object, ...]] = set()
    for r in resolved:
        seen.add(
            (
                r.get("symbol"),
                r.get("side"),
                r.get("source"),
                r.get("regime"),
                _f(r.get("stop_dist_bps")),
                _f(r.get("take_dist_bps")),
                _f(r.get("signal_confidence")),
            )
        )
    return len(seen)


def _schema_major(row: dict[str, object]) -> int:
    """Best-effort major version of a resolved row. Missing/garbled -> 1 (legacy)."""
    raw = row.get("schema_version")
    if raw is None:
        return 1
    s = str(raw).lstrip("vV")
    head = s.split(".", 1)[0]
    try:
        return int(head)
    except ValueError:
        return 1


def _is_real_row(row: dict[str, object]) -> bool:
    """NEO-P-002 (Weg B): a row counts as REAL edge only if attributed AND v2+."""
    return row.get("source") in REAL_SOURCES and _schema_major(row) >= 2


def _is_legacy_canary_suspect(row: dict[str, object]) -> bool:
    """NEO-P-002 (Weg B): conservative heuristic for the 644 alt-ledger rows.

    The legacy rows carry the old hardcoded ``source="autonomous_loop"`` with the
    constant control-plane fingerprint (confidence 0.85, rr 2.0, gate not
    rejecting) and no candidate_kind. We flag a row as a canary suspect ONLY when
    ALL of those hold together, to avoid sweeping a genuine pre-NEO-P-002 real
    candidate into the canary bucket. Anything else stays ``legacy_unattributed``.
    """
    if str(row.get("source")) != "autonomous_loop":
        return False
    if row.get("candidate_kind") is not None:
        return False
    conf = _f(row.get("signal_confidence"))
    if conf is None or abs(conf - 0.85) > 1e-9:
        return False
    # rr is not persisted on the resolved row; reconstruct from stop/take dist.
    sd = _f(row.get("stop_dist_bps"))
    td = _f(row.get("take_dist_bps"))
    if sd is None or td is None or sd <= 0:
        return False
    if abs((td / sd) - 2.0) > 1e-6:
        return False
    if bool(row.get("gate_would_reject")):
        return False
    return True


def build_shadow_report(
    resolved: list[dict[str, object]],
    *,
    total_candidates: int | None = None,
    include_legacy: bool = False,
    inloop_funnel: dict[str, object] | None = None,
) -> dict[str, object]:
    """Aggregate resolved shadow candidates into a root-cause report (pure).

    Attribution layering (most-restrictive headline):

    * #137 split out ``source == "canary_probe"`` rows.
    * #140 quarantined source-less pre-V1 rows as ``unattributed_resolved``.
    * #139 added the confidence-informativeness guard + raw/deduped counts.
    * NEO-P-002 (Weg B) additionally fences off the 644 LEGACY alt-ledger rows
      (old hardcoded ``source="autonomous_loop"``, schema v1, no candidate_kind).
      Headline + ``primary_class`` are computed ONLY over REAL rows: an
      explicitly-attributed real source (``REAL_SOURCES`` = autonomous_generator)
      AND ``schema_version >= 2`` (see ``_is_real_row``). canary -> separate;
      everything else is legacy/unattributed, surfaced via ``legacy_counts``
      (``legacy_canary_suspect`` / ``legacy_unattributed``) + the retained #140
      ``unattributed_resolved`` total. Legacy rows are decision-irrelevant; they
      enter the resolved ``by_source`` split only with ``include_legacy=True``
      (diagnostic). Alt rows are NEVER rewritten. ``real_resolved`` of 0 means
      NO real-signal evidence yet (-> INSUFFICIENT_DATA), never "no edge".
    """
    canary = [r for r in resolved if r.get("source") == "canary_probe"]
    # Goal 2026-06-10: the decoupled real-analysis paper feeder is its OWN stream
    # (B-002) — separated from the autonomous-generator headline exactly like
    # canary, so it is neither counted as real-generator edge nor mis-bucketed as
    # legacy/unattributed.
    real_analysis = [r for r in resolved if r.get("source") == "real_analysis"]
    real = [r for r in resolved if _is_real_row(r)]
    # Everything that is neither real nor canary nor real_analysis is
    # legacy/unattributed (#140's quarantine bucket, now further split for the 644
    # autonomous_loop rows).
    legacy = [
        r
        for r in resolved
        if not _is_real_row(r)
        and r.get("source") != "canary_probe"
        and r.get("source") != "real_analysis"
    ]
    legacy_canary_suspect = [r for r in legacy if _is_legacy_canary_suspect(r)]
    legacy_unattributed = [r for r in legacy if not _is_legacy_canary_suspect(r)]
    n = len(real)
    total = total_candidates if total_candidates is not None else n
    mfe = [v for v in (_f(r.get("mfe_bps")) for r in real) if v is not None]
    mae = [v for v in (_f(r.get("mae_bps")) for r in real) if v is not None]
    sd = [v for v in (_f(r.get("stop_dist_bps")) for r in real) if v is not None]
    td = [v for v in (_f(r.get("take_dist_bps")) for r in real) if v is not None]

    def _trim_mean(xs: list[float]) -> float | None:
        if not xs:
            return None
        s = sorted(xs)
        k = max(1, len(s) // 10) if len(s) >= 10 else 0
        core = s[k : len(s) - k] if k else s
        return round(statistics.fmean(core), 2)

    fwd_medians = {
        f"median_fwd_{h}s_bps": _median(
            [v for v in (_f(r.get(f"fwd_{h}s_bps")) for r in real) if v is not None]
        )
        for h in HORIZONS_S
    }
    stats: dict[str, object] = {
        "n_resolved": n,
        "total_candidates": total,
        "pending": max(0, total - n),
        "resolution_coverage_pct": round(100.0 * n / total, 1) if total else 0.0,
        "real_resolved": n,
        "canary_probe_resolved": len(canary),
        "real_analysis_resolved": len(real_analysis),
        # #140 field retained = total legacy/unattributed (now split below).
        "unattributed_resolved": len(legacy),
        "raw_count": len(resolved),
        "deduped_count": _dedup_count(resolved),
        "mfe_before_mae_rate": _rate(real, "mfe_before_mae"),
        "reached_take_rate": _rate(real, "reached_take"),
        "reached_stop_rate": _rate(real, "reached_stop"),
        "gate_would_reject_rate": _rate(real, "gate_would_reject"),
        "median_mfe_bps": _median(mfe),
        "median_mae_bps": _median(mae),
        "trimmed_mfe_bps": _trim_mean(mfe),
        "trimmed_mae_bps": _trim_mean(mae),
        "median_stop_dist_bps": _median(sd),
        "median_take_dist_bps": _median(td),
        **fwd_medians,
        **_confidence_status(resolved),
    }
    # NEO-P-002 (Weg B): legacy rows reported separately, decision-irrelevant.
    stats["legacy_counts"] = {
        "legacy_canary_suspect": len(legacy_canary_suspect),
        "legacy_unattributed": len(legacy_unattributed),
    }
    stats["primary_class"] = classify(stats)
    stats["by_symbol"] = _split(real, "symbol")
    stats["by_side"] = _split(real, "side")
    stats["by_regime"] = _split(real, "regime")
    stats["by_gate_would_reject"] = _split(real, "gate_would_reject")
    # Attribution axes. by_source spans canary + real (+ legacy only when the
    # operator asks for it) so the full bucket distribution stays visible; the
    # NEO-P-002 axes split the real signal further by kind / score origin.
    split_rows = resolved if include_legacy else (real + canary)
    stats["by_source"] = _split(split_rows, "source")
    stats["by_candidate_kind"] = _split(real, "candidate_kind")
    stats["by_score_source"] = _split(real, "score_source")
    # #175: surface the in-loop funnel + its rejected_funnel breakdown so a
    # real_resolved=0 headline stays explainable (priority-gate? generator-none?)
    # rather than an unexplained INSUFFICIENT_DATA. Diagnostic only — it never
    # changes primary_class (computed above over real rows).
    if inloop_funnel is not None:
        stats["in_loop_funnel"] = inloop_funnel
        rejected = inloop_funnel.get("rejected_funnel")
        if rejected is not None:
            stats["rejected_funnel"] = rejected
    return stats


__all__ = [
    "CLASS_ADVERSE",
    "CLASS_INSUFFICIENT",
    "CLASS_PROFIT_NOT_HARVESTED",
    "CLASS_STOP_NOISE",
    "CLASS_TP_UNREACHABLE",
    "CLASS_UNCLASSIFIED",
    "CONF_INFORMATIVE",
    "CONF_NON_INFORMATIVE_CONSTANT",
    "CONF_NO_DATA",
    "HORIZONS_S",
    "REAL_SOURCES",
    "RESOLVABLE_CANDIDATE_KINDS",
    "Bar",
    "ExcursionResult",
    "ShadowCandidate",
    "build_shadow_report",
    "classify",
    "compute_excursion",
    "compute_forward_returns",
    "record_candidate",
    "resolve_pending",
    "side_adjusted_bps",
]
