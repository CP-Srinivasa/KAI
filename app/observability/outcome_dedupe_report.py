"""Outcome dedupe report — raw vs latest-per-document_id vs episodes.

The 2026-05-26 daily-strategy review reported 4409 raw rows /
3981 inconclusive vs 410 unique documents / 35 inconclusive once
deduped to the latest row per ``document_id``. Multi-Window-Outcome
(PR #74) writes new rows for each later window, so the raw aggregate
buries the resolved outcomes under historic inconclusives.

The 2026-07-07 daily-strategy review (V1) found a second inflation
axis: the 2026-07-06 backlog batch annotated ~150 hit/miss rows from
parallel ``tradingview_webhook`` signal paths that all resolved on the
same BTC move. Per-document dedup cannot catch this — every path has
its own document_ids — so ``build_episode_dedupe_report`` additionally
clusters resolved outcomes into market *episodes*: same asset,
direction and horizon, chained while the dispatch gap stays within the
horizon. One episode counts once (majority vote, tie -> miss).

This module is read-only. It returns raw, per-document and per-episode
counts so the operator (and the daily-strategy bootstrap / briefing)
can cite an honest precision without re-implementing the rule per
caller.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.storage.jsonl_io import iter_jsonl_tolerant

_DEFAULT_AUDIT = Path("artifacts/alert_outcomes.jsonl")
_DEFAULT_ALERT_AUDIT = Path("artifacts/alert_audit.jsonl")

# Auto-annotator note prefix, e.g. "auto@4h: bullish BTC/USDT $100->$104 …".
_NOTE_HORIZON_RE = re.compile(r"auto@(\d+(?:\.\d+)?)h")
_NOTE_DIRECTION_RE = re.compile(r"\b(bullish|bearish)\b")
_DEFAULT_HORIZON_HOURS = 4.0


def _precision(hit: int, miss: int) -> str:
    decided = hit + miss
    if decided == 0:
        return "n/a"
    return f"{100.0 * hit / decided:.1f}% ({hit}/{decided})"


@dataclass(frozen=True)
class OutcomeDedupeReport:
    raw_total: int
    raw_hit: int
    raw_miss: int
    raw_inconclusive: int
    deduped_total: int
    deduped_hit: int
    deduped_miss: int
    deduped_inconclusive: int
    dropped_inconclusive_dupes: int
    audit_path: str

    @property
    def raw_precision_str(self) -> str:
        return _precision(self.raw_hit, self.raw_miss)

    @property
    def deduped_precision_str(self) -> str:
        return _precision(self.deduped_hit, self.deduped_miss)

    def to_dict(self) -> dict[str, object]:
        return {
            "raw_total": self.raw_total,
            "raw_hit": self.raw_hit,
            "raw_miss": self.raw_miss,
            "raw_inconclusive": self.raw_inconclusive,
            "raw_precision": self.raw_precision_str,
            "deduped_total": self.deduped_total,
            "deduped_hit": self.deduped_hit,
            "deduped_miss": self.deduped_miss,
            "deduped_inconclusive": self.deduped_inconclusive,
            "deduped_precision": self.deduped_precision_str,
            "dropped_inconclusive_dupes": self.dropped_inconclusive_dupes,
            "audit_path": self.audit_path,
        }


def build_outcome_dedupe_report(
    *,
    audit_path: str | Path = _DEFAULT_AUDIT,
) -> OutcomeDedupeReport:
    path = Path(audit_path)
    raw_hit = 0
    raw_miss = 0
    raw_inconclusive = 0
    raw_total = 0
    latest: dict[str, dict[str, object]] = {}
    raw_inconclusive_by_doc: dict[str, int] = {}

    if path.exists():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            raw_total += 1
            outcome = rec.get("outcome")
            if outcome == "hit":
                raw_hit += 1
            elif outcome == "miss":
                raw_miss += 1
            elif outcome == "inconclusive":
                raw_inconclusive += 1
            doc_id = rec.get("document_id")
            if isinstance(doc_id, str) and doc_id:
                # The "latest" rule honours the file's append order — the
                # last write per document_id wins, mirroring the storage
                # contract used by app/regime/lookup.py.
                latest[doc_id] = rec
                if outcome == "inconclusive":
                    raw_inconclusive_by_doc[doc_id] = raw_inconclusive_by_doc.get(doc_id, 0) + 1

    deduped_hit = 0
    deduped_miss = 0
    deduped_inconclusive = 0
    dropped_inconclusive_dupes = 0
    for doc_id, rec in latest.items():
        outcome = rec.get("outcome")
        if outcome == "hit":
            deduped_hit += 1
        elif outcome == "miss":
            deduped_miss += 1
        elif outcome == "inconclusive":
            deduped_inconclusive += 1
        # Count redundant inconclusive rows superseded by a later
        # resolved outcome (hit/miss). Inconclusives that stay
        # inconclusive after dedupe are not "dropped" — only the
        # extras over the final state count.
        per_doc_inc = raw_inconclusive_by_doc.get(doc_id, 0)
        if outcome in {"hit", "miss"}:
            dropped_inconclusive_dupes += per_doc_inc
        elif outcome == "inconclusive" and per_doc_inc > 1:
            dropped_inconclusive_dupes += per_doc_inc - 1

    return OutcomeDedupeReport(
        raw_total=raw_total,
        raw_hit=raw_hit,
        raw_miss=raw_miss,
        raw_inconclusive=raw_inconclusive,
        deduped_total=len(latest),
        deduped_hit=deduped_hit,
        deduped_miss=deduped_miss,
        deduped_inconclusive=deduped_inconclusive,
        dropped_inconclusive_dupes=dropped_inconclusive_dupes,
        audit_path=str(path),
    )


@dataclass(frozen=True)
class EpisodeDedupeReport:
    """Resolved outcomes clustered into market episodes (V1, 2026-07-07)."""

    resolved_rows: int
    episode_total: int
    episode_hit: int
    episode_miss: int
    unanchored_rows: int
    largest_episode_size: int
    audit_path: str
    alert_audit_path: str
    # Alle Episodengrößen (absteigend). Bewusst NICHT in to_dict(): Konsument
    # ist die Invariante TL-004 (Zählung "Episoden über Schwelle"), das
    # persistierte Artefakt-Schema bleibt unverändert.
    episode_sizes: tuple[int, ...] = ()

    @property
    def episode_precision_str(self) -> str:
        return _precision(self.episode_hit, self.episode_miss)

    def to_dict(self) -> dict[str, object]:
        return {
            "resolved_rows": self.resolved_rows,
            "episode_total": self.episode_total,
            "episode_hit": self.episode_hit,
            "episode_miss": self.episode_miss,
            "episode_precision": self.episode_precision_str,
            "unanchored_rows": self.unanchored_rows,
            "largest_episode_size": self.largest_episode_size,
            "audit_path": self.audit_path,
            "alert_audit_path": self.alert_audit_path,
        }


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _horizon_hours(rec: dict[str, object]) -> float:
    note = rec.get("note")
    if isinstance(note, str):
        m = _NOTE_HORIZON_RE.search(note)
        if m:
            return float(m.group(1))
    # Fallback: the multi-window field carries "1h"/"4h"/… on hits.
    window = rec.get("hit_at_window")
    if isinstance(window, str) and window.endswith("h"):
        try:
            return float(window[:-1])
        except ValueError:
            pass
    return _DEFAULT_HORIZON_HOURS


def _direction(rec: dict[str, object], sentiment: str | None) -> str:
    note = rec.get("note")
    if isinstance(note, str):
        m = _NOTE_DIRECTION_RE.search(note)
        if m:
            return m.group(1)
    if sentiment in {"bullish", "bearish"}:
        return sentiment
    return "unknown"


def build_episode_dedupe_report(
    *,
    audit_path: str | Path = _DEFAULT_AUDIT,
    alert_audit_path: str | Path = _DEFAULT_ALERT_AUDIT,
    doc_filter: Callable[[str], bool] | None = None,
) -> EpisodeDedupeReport:
    """Cluster doc-deduped hit/miss rows into market episodes.

    Episode rule: within (asset, direction, horizon) sort by dispatch
    time and chain rows while the gap to the previous row is <= the
    evaluation horizon — parallel signal paths resolving on the same
    move form one episode. Episode outcome = majority vote, tie counts
    as miss (conservative). Rows without an ``alert_audit`` dispatch
    timestamp fall back to ``annotated_at`` (counted in
    ``unanchored_rows``); rows without any parseable anchor become
    singleton episodes.

    ``doc_filter`` restricts the population by ``document_id`` BEFORE
    clustering (V7 Option B, 2026-09-16). Without it the report counts
    exactly as before.
    """
    outcomes_path = Path(audit_path)
    dispatch_path = Path(alert_audit_path)
    resolved, anchors = _load_resolved_with_anchors(outcomes_path, dispatch_path)
    if doc_filter is not None:
        resolved = {d: r for d, r in resolved.items() if doc_filter(d)}
    return _cluster_episodes(resolved, anchors, outcomes_path, dispatch_path)


# ---------------------------------------------------------------------------
# V7 Option B (Daily Review 2026-09-16) — PRAEZISION JE SIGNALPFAD
# ---------------------------------------------------------------------------
# Die gemeinsame Episoden-Praezision mischt Pfade, die das Eligibility-Gate
# unterschiedlich (oder gar nicht) passieren. Gemessen am 16.09. auf dem Pi,
# 3 998 aufgeloeste Zeilen: ``tv:`` 3 284 (82 %, am Gate vorbei),
# ``technical_paper_…`` 336 (umgeht die Narrativ-Gates), UUID-Dokumente 378
# (Nachrichten, durch das Gate). Im Fenster ab 19.08. bestand die gemeinsame
# Zahl zu ~99 % aus TradingView, und das Gate liess ausserhalb davon nur 14
# direktionale Alerts durch — die Qualitaet der Nachrichten war ungemessen.
#
# Die Aufteilung ist ADDITIV und nutzt dieselbe Episoden-Regel. Zugeordnet wird
# ueber die ``document_id``, die jeder Pfad selbst vergibt (``tv:`` deckte sich
# 3 284 : 3 284 mit ``provenance.source``). Ein unbekanntes Praefix landet in
# ``other`` — nie still in den Nachrichten.

SIGNAL_PATH_TRADINGVIEW = "tradingview"
SIGNAL_PATH_NEWS = "news"
SIGNAL_PATH_TECHNICAL = "technical"
SIGNAL_PATH_OTHER = "other"
SIGNAL_PATH_ORDER: tuple[str, ...] = (
    SIGNAL_PATH_TRADINGVIEW,
    SIGNAL_PATH_NEWS,
    SIGNAL_PATH_TECHNICAL,
    SIGNAL_PATH_OTHER,
)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_LABEL_DE = {
    SIGNAL_PATH_TRADINGVIEW: "TradingView",
    SIGNAL_PATH_NEWS: "Nachrichten",
    SIGNAL_PATH_TECHNICAL: "Technical",
    SIGNAL_PATH_OTHER: "sonstige",
}
_LABEL_EN = {
    SIGNAL_PATH_TRADINGVIEW: "TradingView",
    SIGNAL_PATH_NEWS: "News",
    SIGNAL_PATH_TECHNICAL: "Technical",
    SIGNAL_PATH_OTHER: "Other",
}


def signal_path_of(document_id: str) -> str:
    """Signalpfad eines Alerts, abgeleitet aus seiner ``document_id``. Rein."""
    doc = (document_id or "").strip()
    if doc.startswith("tv:"):
        return SIGNAL_PATH_TRADINGVIEW
    if doc.startswith("technical_paper"):
        return SIGNAL_PATH_TECHNICAL
    if _UUID_RE.match(doc):
        return SIGNAL_PATH_NEWS
    return SIGNAL_PATH_OTHER


def build_episode_reports_by_path(
    *,
    audit_path: str | Path = _DEFAULT_AUDIT,
    alert_audit_path: str | Path = _DEFAULT_ALERT_AUDIT,
) -> dict[str, EpisodeDedupeReport]:
    """Ein Episoden-Bericht je Signalpfad, aus EINEM Lesedurchgang.

    Nur Pfade mit mindestens einer aufgeloesten Zeile erscheinen. Die Zeilen der
    Teile summieren sich zur gemeinsamen Zahl; die Episoden nicht, weil die
    gemeinsame Zaehlung pfaduebergreifende Bewegungen zusammenlegt.
    """
    outcomes_path = Path(audit_path)
    dispatch_path = Path(alert_audit_path)
    resolved, anchors = _load_resolved_with_anchors(outcomes_path, dispatch_path)
    by_path: dict[str, dict[str, dict[str, object]]] = {}
    for doc_id, rec in resolved.items():
        by_path.setdefault(signal_path_of(doc_id), {})[doc_id] = rec
    return {
        path: _cluster_episodes(by_path[path], anchors, outcomes_path, dispatch_path)
        for path in SIGNAL_PATH_ORDER
        if by_path.get(path)
    }


def _wilson_pct(hit: int, total: int) -> tuple[float, float] | None:
    from app.alerts.provenance_metrics import wilson_ci

    ci = wilson_ci(hit, total)
    return None if ci is None else (100.0 * ci[0], 100.0 * ci[1])


def format_path_precision_de(reports: dict[str, EpisodeDedupeReport]) -> str:
    """Eine Tabellenzelle fuer die Daily-Strategy: Pfade mit ``·`` getrennt."""
    parts: list[str] = []
    for path in SIGNAL_PATH_ORDER:
        rep = reports.get(path)
        if rep is None or rep.episode_total == 0:
            continue
        pct = 100.0 * rep.episode_hit / rep.episode_total
        ci = _wilson_pct(rep.episode_hit, rep.episode_total)
        rng = f", Wilson95 {ci[0]:.1f}–{ci[1]:.1f} %" if ci else ""
        parts.append(
            f"{_LABEL_DE[path]} {pct:.1f} % ({rep.episode_hit}/{rep.episode_total} Episoden{rng})"
        )
    return " · ".join(parts) if parts else "—"


def format_path_precision_en(reports: dict[str, EpisodeDedupeReport]) -> list[str]:
    """Eingerueckte Zeilen fuer das Daily Briefing (englisch wie der Rest)."""
    lines: list[str] = []
    for path in SIGNAL_PATH_ORDER:
        rep = reports.get(path)
        if rep is None or rep.episode_total == 0:
            continue
        pct = 100.0 * rep.episode_hit / rep.episode_total
        ci = _wilson_pct(rep.episode_hit, rep.episode_total)
        rng = f", CI {ci[0]:.1f}–{ci[1]:.1f}%" if ci else ""
        label = f"{_LABEL_EN[path]}:".ljust(13)
        lines.append(f"    {label}{pct:.1f}% ({rep.episode_hit}/{rep.episode_total} episodes{rng})")
    return lines


def _load_resolved_with_anchors(
    outcomes_path: Path, dispatch_path: Path
) -> tuple[dict[str, dict[str, object]], dict[str, tuple[datetime | None, str | None]]]:
    # Latest row per document_id (same append-order rule as
    # build_outcome_dedupe_report), then keep resolved outcomes only.
    latest: dict[str, dict[str, object]] = {}
    for rec in iter_jsonl_tolerant(outcomes_path):
        doc_id = rec.get("document_id")
        if isinstance(doc_id, str) and doc_id:
            latest[doc_id] = rec
    resolved = {d: r for d, r in latest.items() if r.get("outcome") in {"hit", "miss"}}

    # Join dispatch time + sentiment from the alert audit trail —
    # streamed, keeping only the document_ids we actually need.
    anchors: dict[str, tuple[datetime | None, str | None]] = {}
    if resolved:
        for rec in iter_jsonl_tolerant(dispatch_path):
            doc_id = rec.get("document_id")
            if not isinstance(doc_id, str) or doc_id not in resolved:
                continue
            sentiment = rec.get("sentiment_label")
            anchors[doc_id] = (
                _parse_ts(rec.get("dispatched_at")),
                sentiment if isinstance(sentiment, str) else None,
            )
    return resolved, anchors


def _cluster_episodes(
    resolved: dict[str, dict[str, object]],
    anchors: dict[str, tuple[datetime | None, str | None]],
    outcomes_path: Path,
    dispatch_path: Path,
) -> EpisodeDedupeReport:
    unanchored_rows = 0
    groups: dict[tuple[str, str, float], list[tuple[datetime | None, str]]] = {}
    for doc_id, rec in resolved.items():
        dispatched_at, sentiment = anchors.get(doc_id, (None, None))
        anchor = dispatched_at
        if anchor is None:
            anchor = _parse_ts(rec.get("annotated_at"))
            unanchored_rows += 1
        asset = rec.get("asset")
        key = (
            asset if isinstance(asset, str) and asset else "unknown",
            _direction(rec, sentiment),
            _horizon_hours(rec),
        )
        outcome = "hit" if rec.get("outcome") == "hit" else "miss"
        groups.setdefault(key, []).append((anchor, outcome))

    episode_hit = 0
    episode_miss = 0
    episode_total = 0
    largest_episode_size = 0
    episode_sizes: list[int] = []
    for (_, _, horizon), rows in groups.items():
        max_gap = timedelta(hours=horizon)
        anchored: list[tuple[datetime, str]] = [
            (anchor, outcome) for anchor, outcome in rows if anchor is not None
        ]
        anchored.sort(key=lambda r: r[0])
        episodes: list[list[str]] = []
        prev: datetime | None = None
        for anchor, outcome in anchored:
            if prev is None or anchor - prev > max_gap:
                episodes.append([])
            episodes[-1].append(outcome)
            prev = anchor
        # Rows without any parseable anchor cannot be clustered — count
        # each as its own (singleton) episode rather than dropping data.
        episodes.extend([outcome] for anchor, outcome in rows if anchor is None)
        for members in episodes:
            episode_total += 1
            episode_sizes.append(len(members))
            largest_episode_size = max(largest_episode_size, len(members))
            hits = sum(1 for o in members if o == "hit")
            if hits > len(members) - hits:
                episode_hit += 1
            else:
                episode_miss += 1

    return EpisodeDedupeReport(
        resolved_rows=len(resolved),
        episode_total=episode_total,
        episode_hit=episode_hit,
        episode_miss=episode_miss,
        unanchored_rows=unanchored_rows,
        largest_episode_size=largest_episode_size,
        audit_path=str(outcomes_path),
        alert_audit_path=str(dispatch_path),
        episode_sizes=tuple(sorted(episode_sizes, reverse=True)),
    )


__all__ = [
    "SIGNAL_PATH_NEWS",
    "SIGNAL_PATH_ORDER",
    "SIGNAL_PATH_OTHER",
    "SIGNAL_PATH_TECHNICAL",
    "SIGNAL_PATH_TRADINGVIEW",
    "EpisodeDedupeReport",
    "OutcomeDedupeReport",
    "build_episode_dedupe_report",
    "build_episode_reports_by_path",
    "build_outcome_dedupe_report",
    "format_path_precision_de",
    "format_path_precision_en",
    "signal_path_of",
]
