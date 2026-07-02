"""Premium-Signal Analytics — operatorzentrierte Auswertung pro Signal.

2026-05-28 /goal-Sprint. Aufbauend auf ``premium_signal_trail.build_trail``
(das die 4 Audit-Streams joint) liefert dieses Modul die *bewertende*
Schicht, die der Operator "auf einen Blick" braucht:

- eingesetztes Kapital + Anteil am verfügbaren Kapital
- Gewinn / Verlust (absolut + prozentual)
- Per-Target-Status (hit / missed / pending / unknown)
- Entry-Status + Wartezeit (on-time / waited / late / missed)
- Quellen-Qualität (good / medium / weak / unknown) über das Trail-Fenster
- automatisch abgeleitete Analyse-Hinweise

Design-Leitplanken (KAI-Master-Regeln + Goal-Vorgaben):

1. **Reine Funktionen, kein IO.** Caller (``build_trail``) reicht die
   bereits gejointen Records herein. Voll unit-testbar.
2. **Niemals Werte erfinden.** Fehlt eine belastbare Basis, ist das Feld
   ``None`` und trägt einen erklärenden ``*_note``/Status, statt zu raten.
   Die UI zeigt dann "nicht verfügbar" / "nicht bewertbar".
3. **Konservative Schwellen.** Source-Quality verlangt Mindeststichprobe;
   Entry-/Target-Klassifikation nutzt nur belastbare Fill-/Close-Evidenz.
4. **Division-by-zero-sicher.** Kapitalbasis 0/negativ → Prozent ``None``.

Annahmen (explizit, siehe Goal "saubere Annahmen dokumentieren"):

- ``available_capital_at_entry`` = freies Cash *vor* dem ersten Entry-Fill,
  rekonstruiert aus ``portfolio_cash`` (nach Fill) + Kosten + Gebühr des
  ersten Entry-Fills. Das ist die belastbar berechenbare "verfügbare Summe";
  Gesamt-Equity inkl. offener Positionen lässt sich aus einem einzelnen
  ``order_filled``-Record NICHT rekonstruieren und wird daher nicht behauptet.
- Entry-Timing-Schwellen: ``<=300s`` on-time, ``>3600s`` late, dazwischen /
  nach Pending-Phase = waited. Schwellen sind Heuristik, dokumentiert.
- Target "hit" nur bei belastbarer Preis-Evidenz (Close-Fill / exit_price,
  der das Target erreicht hat). Kein Markt-Backfill → bei nie eröffneten
  Signalen sind Targets "unknown", nicht "missed".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.learning.source_reliability import wilson_lower_bound

# ── Schwellen / Konstanten (dokumentierte Heuristik) ─────────────────────────

_ENTRY_ON_TIME_MAX_S = 300  # <= 5 min nach Signal = rechtzeitig
_ENTRY_LATE_MIN_S = 3600  # > 1 h nach Signal = verspätet
_HIGH_CAPITAL_PCT = 25.0  # Kapitalanteil-Warnschwelle
_PNL_EPSILON_USD = 0.01  # |PnL| <= eps → break-even
_TARGET_TOLERANCE = 0.001  # 0.1 % Slippage-Toleranz für Target-Treffer

# Source-Quality-Mindeststichproben (konservativ, analog source_reliability)
_SQ_MIN_SIGNALS = 5  # < 5 Signale je Quelle → nicht bewertbar
_SQ_MIN_RESOLVED = 3  # < 3 entschiedene Trades → nicht bewertbar
_SQ_GOOD_WIN_LB = 0.50  # Wilson-Lower-Bound der Win-Rate
_SQ_MEDIUM_WIN_LB = 0.30
_SQ_GOOD_ENTRY_RATE = 0.60  # Anteil tatsächlich eröffneter Signale

# Overall-Stati die als "abgebrochen / nie als Trade gelaufen" zählen
_CANCELLED_OVERALLS = frozenset(
    {
        "BRIDGE_REJECTED",
        "PAPER_REJECTED",
        "SOURCE_SKIPPED",
        "EXPIRED",
        "NOT_APPROVED",
        # 2026-06-04 RC-2: globaler Kill-Switch — das Signal ist nie als Trade
        # gelaufen. Darf NICHT als Trading-Miss in die Source-Quality zählen.
        "ENTRY_DISABLED",
    }
)


# ── Datentypen ───────────────────────────────────────────────────────────────


@dataclass
class TargetStatus:
    """Per-Target-Status für die UI-Target-Leiste."""

    target_number: int
    target_price: float
    status: str  # hit | missed | pending | skipped | unknown
    hit_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "target_number": self.target_number,
            "target_price": self.target_price,
            "status": self.status,
        }
        if self.hit_at is not None:
            out["hit_at"] = self.hit_at
        return out


@dataclass
class SignalAnalytics:
    """Operator-Auswertung für ein einzelnes Premium-Signal."""

    signal_type: str  # internal | external
    source_name: str | None
    invested_capital: float | None
    available_capital_at_entry: float | None
    invested_capital_pct: float | None
    capital_base_note: str | None
    actual_entry_price: float | None
    planned_entry_value: float | None
    entry_status: str  # entered_on_time | waited_for_entry | entered_late | missed_entry | unknown
    entry_delay_seconds: int | None
    entry_delay_label: str
    trade_result_status: str  # win | loss | break_even | open | cancelled | unknown
    final_pnl_usd: float | None
    final_pnl_pct: float | None
    final_pnl_source: str | None  # engine | fills | None — Herkunft des PnL-Werts
    targets: list[TargetStatus]
    source_quality_status: str  # good | medium | weak | unknown
    source_quality_reason: str
    analysis_hints: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_type": self.signal_type,
            "source_name": self.source_name,
            "invested_capital": self.invested_capital,
            "available_capital_at_entry": self.available_capital_at_entry,
            "invested_capital_pct": self.invested_capital_pct,
            "capital_base_note": self.capital_base_note,
            "actual_entry_price": self.actual_entry_price,
            "planned_entry_value": self.planned_entry_value,
            "entry_status": self.entry_status,
            "entry_delay_seconds": self.entry_delay_seconds,
            "entry_delay_label": self.entry_delay_label,
            "trade_result_status": self.trade_result_status,
            "final_pnl_usd": self.final_pnl_usd,
            "final_pnl_pct": self.final_pnl_pct,
            "final_pnl_source": self.final_pnl_source,
            "targets": [t.to_dict() for t in self.targets],
            "source_quality_status": self.source_quality_status,
            "source_quality_reason": self.source_quality_reason,
            "analysis_hints": list(self.analysis_hints),
        }


# ── kleine Helfer ────────────────────────────────────────────────────────────


def _safe_float(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _safe_str(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    cleaned = ts.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _event_type(ev: dict[str, Any]) -> str | None:
    et = ev.get("event_type") or ev.get("event")
    return et if isinstance(et, str) else None


def _is_fill(ev: dict[str, Any]) -> bool:
    return _event_type(ev) == "order_filled" or ev.get("status") == "filled"


def _fill_qty(ev: dict[str, Any]) -> float | None:
    return _safe_float(ev.get("filled_quantity")) or _safe_float(ev.get("quantity"))


def _opening_side(direction: str | None, side: str | None) -> str:
    """Welche Fill-Side eröffnet die Position (long→buy, short→sell)."""
    token = (direction or side or "").strip().lower()
    if token in {"short", "sell"}:
        return "sell"
    return "buy"


# ── Kapital ──────────────────────────────────────────────────────────────────


def _compute_capital(
    opening_fills: list[dict[str, Any]],
    bridge_fill: dict[str, Any] | None,
) -> tuple[float | None, float | None, float | None, str | None]:
    """(invested, available_at_entry, invested_pct, note).

    invested  = Σ fill_price·qty über Entry-Fills (Paper bevorzugt, sonst Bridge).
    available = freies Cash vor erstem Entry = portfolio_cash(nach 1. Fill)
                + Kosten + Gebühr des ersten Entry-Fills.
    pct       = invested / available · 100  (None bei fehlender/0 Basis).
    """
    invested = 0.0
    have_invested = False
    for ev in opening_fills:
        price = _safe_float(ev.get("fill_price"))
        qty = _fill_qty(ev)
        if price is not None and qty is not None and qty > 0:
            invested += price * qty
            have_invested = True

    # Fallback auf Bridge-Fill, wenn Paper-Engine keinen Entry-Fill geliefert hat
    if not have_invested and bridge_fill is not None:
        price = _safe_float(bridge_fill.get("fill_price"))
        qty = _safe_float(bridge_fill.get("quantity"))
        if price is not None and qty is not None and qty > 0:
            invested = price * qty
            have_invested = True

    if not have_invested:
        return None, None, None, "no_entry_fill"

    # Kapitalbasis nur aus erstem Paper-Entry-Fill mit portfolio_cash belastbar
    capital_base: float | None = None
    if opening_fills:
        first = opening_fills[0]
        cash_after = _safe_float(first.get("portfolio_cash"))
        if cash_after is not None:
            price = _safe_float(first.get("fill_price"))
            qty = _fill_qty(first)
            fee = _safe_float(first.get("fee_usd")) or 0.0
            cost = (price * qty) if (price is not None and qty is not None) else 0.0
            capital_base = cash_after + cost + fee

    if capital_base is None:
        return round(invested, 4), None, None, "capital_base_unavailable"
    if capital_base <= 0:
        return round(invested, 4), capital_base, None, "capital_base_non_positive"

    pct = invested / capital_base * 100.0
    return round(invested, 4), round(capital_base, 4), round(pct, 2), None


# ── Entry-Status / Wartezeit ─────────────────────────────────────────────────


def _entry_delay_label(seconds: int | None, *, missed: bool) -> str:
    if missed:
        return "Einstieg verfehlt"
    if seconds is None:
        return "—"
    if seconds < 60:
        return "sofort"
    if seconds < 3600:
        return f"nach {seconds // 60} Min"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if minutes:
        return f"nach {hours} Std {minutes} Min"
    return f"nach {hours} Std"


def _compute_entry(
    *,
    received_at: str | None,
    opening_fills: list[dict[str, Any]],
    bridge_fill: dict[str, Any] | None,
    had_pending: bool,
    overall: str,
) -> tuple[str, int | None, str, float | None]:
    """(entry_status, delay_seconds, delay_label, actual_entry_price)."""
    entry_ev: dict[str, Any] | None = opening_fills[0] if opening_fills else None
    actual_price: float | None = None
    entry_ts: str | None = None
    if entry_ev is not None:
        actual_price = _safe_float(entry_ev.get("fill_price"))
        entry_ts = _safe_str(entry_ev.get("filled_at")) or _safe_str(entry_ev.get("timestamp_utc"))
    elif bridge_fill is not None:
        actual_price = _safe_float(bridge_fill.get("fill_price"))
        entry_ts = _safe_str(bridge_fill.get("ts")) or _safe_str(bridge_fill.get("timestamp_utc"))

    entered = (
        actual_price is not None
        or entry_ev is not None
        or (bridge_fill is not None and entry_ts is not None)
    )

    if not entered:
        if overall == "EXPIRED":
            return "missed_entry", None, _entry_delay_label(None, missed=True), None
        return "unknown", None, "—", None

    delay_s: int | None = None
    recv_dt = _parse_iso(received_at)
    fill_dt = _parse_iso(entry_ts)
    if recv_dt is not None and fill_dt is not None:
        delta = (fill_dt - recv_dt).total_seconds()
        delay_s = int(delta) if delta >= 0 else 0

    if delay_s is None:
        status = "unknown"
    elif delay_s > _ENTRY_LATE_MIN_S:
        status = "entered_late"
    elif had_pending or delay_s > _ENTRY_ON_TIME_MAX_S:
        status = "waited_for_entry"
    else:
        status = "entered_on_time"

    return status, delay_s, _entry_delay_label(delay_s, missed=False), actual_price


# ── Targets ──────────────────────────────────────────────────────────────────


def _reached_price_events(
    closing_fills: list[dict[str, Any]],
    paper_events: list[dict[str, Any]],
) -> list[tuple[str | None, float]]:
    """(ts, price)-Liste belastbar erreichter Preise (Close-Fills + exit_price)."""
    events: list[tuple[str | None, float]] = []
    for ev in closing_fills:
        price = _safe_float(ev.get("fill_price"))
        if price is not None:
            events.append((_safe_str(ev.get("timestamp_utc")), price))
    for ev in paper_events:
        if _event_type(ev) == "position_closed":
            price = _safe_float(ev.get("exit_price"))
            if price is not None:
                events.append((_safe_str(ev.get("timestamp_utc")), price))
    events.sort(key=lambda e: e[0] or "")
    return events


def _compute_targets(
    *,
    targets_raw: list[float],
    direction: str | None,
    side: str | None,
    entered: bool,
    closed: bool,
    close_reason: str | None,
    closing_fills: list[dict[str, Any]],
    paper_events: list[dict[str, Any]],
) -> list[TargetStatus]:
    """Per-Target-Status nur aus belastbarer Preis-Evidenz.

    Treffer-Logik:
    - nie eröffnet           → "unknown" (kein Markt-Backfill, kein Raten)
    - Preis-Evidenz erreicht → "hit" (Close-Fill / exit_price ≥/≤ Target)
    - geschlossen ohne Evidenz:
        · Stop-Loss-Close → "missed" (SL beweist: Target nicht erreicht)
        · sonst (TP/manual, Audit ohne exit_price) → "unknown" (ehrlich:
          es wurde geschlossen, aber welche Targets, ist nicht belegt)
    - noch offen             → "pending"
    """
    if not targets_raw:
        return []

    is_long = _opening_side(direction, side) == "buy"
    reached = _reached_price_events(closing_fills, paper_events)
    is_stop_loss_close = bool(close_reason) and "stop" in str(close_reason).lower()

    out: list[TargetStatus] = []
    for idx, price in enumerate(targets_raw, start=1):
        if not entered:
            out.append(TargetStatus(idx, price, "unknown"))
            continue

        hit_at: str | None = None
        for ts, rp in reached:
            reached_it = (
                rp >= price * (1.0 - _TARGET_TOLERANCE)
                if is_long
                else rp <= price * (1.0 + _TARGET_TOLERANCE)
            )
            if reached_it:
                hit_at = ts
                break

        if hit_at is not None:
            out.append(TargetStatus(idx, price, "hit", hit_at=hit_at))
        elif not closed:
            out.append(TargetStatus(idx, price, "pending"))
        elif is_stop_loss_close:
            out.append(TargetStatus(idx, price, "missed"))
        else:
            # geschlossen, aber keine Preis-Evidenz und kein SL-Beweis
            out.append(TargetStatus(idx, price, "unknown"))
    return out


# ── Trade-Ergebnis ───────────────────────────────────────────────────────────


def _derive_pnl_from_fills(
    opening_fills: list[dict[str, Any]],
    closing_fills: list[dict[str, Any]],
    *,
    is_long: bool,
) -> float | None:
    """Belastbarer per-Trade-PnL aus den tatsächlichen Fill-Preisen.

    Fallback NUR wenn die Paper-Engine keinen ``trade_pnl_usd`` geliefert hat
    (pre-V4.1-Close-Pfad). Kein erfundener Wert: gerechnet wird ausschließlich
    aus den realen Fill-Preisen/-Mengen (+ Gebühren falls vorhanden).

    Konservativ: nur bei VOLLSTÄNDIGEM Close (verkaufte Menge ≈ Entry-Menge,
    ±2 %). Bei Teil-Close ist None ehrlicher als ein verzerrter Wert.
    """
    if not opening_fills or not closing_fills:
        return None
    open_qty = sum(_fill_qty(e) or 0.0 for e in opening_fills)
    close_qty = sum(_fill_qty(e) or 0.0 for e in closing_fills)
    if open_qty <= 0 or close_qty <= 0:
        return None
    if abs(close_qty - open_qty) / open_qty > 0.02:
        return None  # kein vollständiger Close → nicht belastbar

    def _val(fills: list[dict[str, Any]]) -> float:
        return sum((_safe_float(e.get("fill_price")) or 0.0) * (_fill_qty(e) or 0.0) for e in fills)

    def _fees(fills: list[dict[str, Any]]) -> float:
        return sum(_safe_float(e.get("fee_usd")) or 0.0 for e in fills)

    open_val = _val(opening_fills)
    close_val = _val(closing_fills)
    fees = _fees(opening_fills) + _fees(closing_fills)
    pnl = (close_val - open_val) if is_long else (open_val - close_val)
    return round(pnl - fees, 4)


def _compute_result(
    *,
    overall: str,
    engine_pnl: float | None,
    derived_pnl: float | None,
    invested_capital: float | None,
) -> tuple[str, float | None, float | None, str | None]:
    """(trade_result_status, final_pnl_usd, final_pnl_pct, final_pnl_source).

    PnL-Quelle: Engine-``trade_pnl_usd`` hat Vorrang; fehlt sie, wird der
    aus Fill-Preisen abgeleitete Wert genutzt (transparent als ``fills``
    markiert). Keiner vorhanden → ``unknown`` statt erfundenem Ergebnis.
    """
    if overall in ("OPEN", "PENDING_ENTRY", "PARTIALLY_CLOSED"):
        return "open", None, None, None
    if overall in _CANCELLED_OVERALLS:
        return "cancelled", None, None, None
    # 2026-06-04: die State-Machine zerlegt den früheren Sammel-State "CLOSED"
    # in CLOSED_TP/CLOSED_SL/CLOSED_MANUAL. Alle drei sind PnL-tragende Closes;
    # "CLOSED" bleibt als Legacy-Alias akzeptiert. Ein Close ohne ableitbaren
    # PnL kommt als overall=REQUIRES_REVIEW herein und fällt unten auf "unknown".
    if overall in ("CLOSED", "CLOSED_TP", "CLOSED_SL", "CLOSED_MANUAL"):
        if engine_pnl is not None:
            pnl: float = engine_pnl
            source: str = "engine"
        elif derived_pnl is not None:
            pnl = derived_pnl
            source = "fills"
        else:
            return "unknown", None, None, None
        if pnl > _PNL_EPSILON_USD:
            status = "win"
        elif pnl < -_PNL_EPSILON_USD:
            status = "loss"
        else:
            status = "break_even"
        pct: float | None = None
        if invested_capital is not None and invested_capital > 0:
            pct = round(pnl / invested_capital * 100.0, 2)
        return status, pnl, pct, source
    return "unknown", None, None, None


# ── Signal-Typ ───────────────────────────────────────────────────────────────


def _classify_signal_type(source: str | None) -> str:
    """internal | external. Premium-Signal-Trail ist Telegram-Channel-basiert
    (= external). ``internal`` nur bei explizitem Internal-Source-Marker,
    damit das Datenmodell interne Premium-Signale später trägt."""
    s = (source or "").strip().lower()
    if s.startswith(("internal", "kai_", "signal_generator", "ensemble")):
        return "internal"
    return "external"


# ── Analyse-Hinweise ─────────────────────────────────────────────────────────


def _build_hints(
    *,
    entry_status: str,
    invested_capital_pct: float | None,
    capital_base_note: str | None,
    trade_result_status: str,
    targets: list[TargetStatus],
    source_quality_status: str,
    overall: str,
    scale_unknown: bool,
) -> list[str]:
    hints: list[str] = []
    if entry_status == "missed_entry":
        hints.append("Einstieg verfehlt – Entry-Range prüfen")
    elif entry_status == "entered_late":
        if trade_result_status == "win":
            hints.append("Profitabel trotz verspätetem Einstieg")
        else:
            hints.append("Einstieg verspätet – Reaktionszeit/Latenz prüfen")

    if invested_capital_pct is not None and invested_capital_pct > _HIGH_CAPITAL_PCT:
        hints.append(f"Kapitalanteil hoch ({invested_capital_pct:.0f}%) – Risiko prüfen")
    elif capital_base_note in ("capital_base_unavailable", "no_entry_fill"):
        hints.append("Kapitalbasis nicht verfügbar – Audit unvollständig")

    hit = [t for t in targets if t.status == "hit"]
    missed = [t for t in targets if t.status == "missed"]
    if hit and missed and hit[0].target_number == 1:
        hints.append("TP1 erreicht, weitere Targets nicht – Exit-Strategie prüfen")

    if trade_result_status == "loss":
        hints.append("Verlust – SL/Entry-Logik prüfen")

    if overall == "PAPER_REJECTED" and scale_unknown:
        hints.append("Skalierung unbekannt – invalid SL, Scale-Resolver prüfen")

    if source_quality_status == "unknown":
        hints.append("Quelle nicht bewertbar – zu wenig historische Daten")
    elif source_quality_status == "weak":
        hints.append("Quelle schwach – häufige Misses/Verluste")

    # Maximal 3 Hinweise, damit die UI nicht überladen wird
    return hints[:3]


# ── Public: pro Signal ───────────────────────────────────────────────────────


def derive_signal_analytics(
    *,
    payload: dict[str, Any],
    source: str | None,
    received_at: str | None,
    overall: str,
    realized_pnl_usd: float | None,
    paper_events: list[dict[str, Any]],
    bridge_history: list[dict[str, Any]],
    paper_close_reason: str | None = None,
    scale_unknown: bool = False,
) -> SignalAnalytics:
    """Berechnet die Auswertungs-Schicht für EIN Premium-Signal.

    Reine Funktion. ``source_quality_*`` wird hier als ``unknown`` initialisiert
    und vom zweiten Pass (``annotate_source_quality``) über das gesamte
    Trail-Fenster gesetzt — eine einzelne Zeile kann keine Quelle bewerten.
    """
    direction = _safe_str(payload.get("direction"))
    side = _safe_str(payload.get("side"))
    opening_side = _opening_side(direction, side)
    closing_side = "sell" if opening_side == "buy" else "buy"

    fills = [ev for ev in paper_events if _is_fill(ev)]
    opening_fills = [ev for ev in fills if ev.get("side") == opening_side]
    closing_fills = [ev for ev in fills if ev.get("side") == closing_side]
    opening_fills.sort(key=lambda e: _safe_str(e.get("timestamp_utc")) or "")

    bridge_fill = next(
        (b for b in bridge_history if b.get("stage") == "filled"),
        None,
    )
    had_pending = any(b.get("stage") == "pending" for b in bridge_history)

    invested, available, invested_pct, cap_note = _compute_capital(opening_fills, bridge_fill)

    entry_status, delay_s, delay_label, actual_entry = _compute_entry(
        received_at=received_at,
        opening_fills=opening_fills,
        bridge_fill=bridge_fill,
        had_pending=had_pending,
        overall=overall,
    )

    entered = bool(opening_fills) or (bridge_fill is not None and actual_entry is not None)

    targets_raw: list[float] = []
    raw = payload.get("targets")
    if isinstance(raw, list):
        for t in raw:
            f = _safe_float(t)
            if f is not None:
                targets_raw.append(f)

    targets = _compute_targets(
        targets_raw=targets_raw,
        direction=direction,
        side=side,
        entered=entered,
        closed=overall == "CLOSED",
        close_reason=paper_close_reason,
        closing_fills=closing_fills,
        paper_events=paper_events,
    )

    derived_pnl = _derive_pnl_from_fills(
        opening_fills, closing_fills, is_long=opening_side == "buy"
    )
    result_status, final_pnl, final_pnl_pct, final_pnl_source = _compute_result(
        overall=overall,
        engine_pnl=realized_pnl_usd,
        derived_pnl=derived_pnl,
        invested_capital=invested,
    )

    hints = _build_hints(
        entry_status=entry_status,
        invested_capital_pct=invested_pct,
        capital_base_note=cap_note,
        trade_result_status=result_status,
        targets=targets,
        source_quality_status="unknown",
        overall=overall,
        scale_unknown=scale_unknown,
    )

    return SignalAnalytics(
        signal_type=_classify_signal_type(source),
        source_name=_safe_str(source),
        invested_capital=invested,
        available_capital_at_entry=available,
        invested_capital_pct=invested_pct,
        capital_base_note=cap_note,
        actual_entry_price=actual_entry,
        planned_entry_value=_safe_float(payload.get("entry_value")),
        entry_status=entry_status,
        entry_delay_seconds=delay_s,
        entry_delay_label=delay_label,
        trade_result_status=result_status,
        final_pnl_usd=final_pnl,
        final_pnl_pct=final_pnl_pct,
        final_pnl_source=final_pnl_source,
        targets=targets,
        source_quality_status="unknown",
        source_quality_reason="pending_aggregation",
        analysis_hints=hints,
    )


# ── Public: Source-Quality über das Trail-Fenster (zweiter Pass) ─────────────


@dataclass
class _SourceAgg:
    n_total: int = 0
    n_entered: int = 0
    n_win: int = 0
    n_loss: int = 0
    n_missed_entry: int = 0
    symbols: set[str] = field(default_factory=set)


def classify_source_quality(
    *,
    n_total: int,
    n_entered: int,
    n_win: int,
    n_loss: int,
    n_missed_entry: int,
) -> tuple[str, str]:
    """(status, reason). Konservativ: Mindeststichprobe vor jeder Bewertung."""
    if n_total < _SQ_MIN_SIGNALS:
        return "unknown", f"zu wenig Signale ({n_total})"
    resolved = n_win + n_loss
    if resolved < _SQ_MIN_RESOLVED:
        return "unknown", f"zu wenige abgeschlossene Trades ({resolved})"

    win_lb = wilson_lower_bound(n_win, resolved) or 0.0
    entry_rate = n_entered / n_total if n_total else 0.0
    win_pct = round(win_lb * 100)
    entry_pct = round(entry_rate * 100)

    if win_lb >= _SQ_GOOD_WIN_LB and entry_rate >= _SQ_GOOD_ENTRY_RATE:
        return "good", f"Trefferquote ≥{win_pct}% (95% LB), Entry-Rate {entry_pct}%"
    if win_lb >= _SQ_MEDIUM_WIN_LB:
        return "medium", f"Trefferquote ≥{win_pct}% (95% LB), Entry-Rate {entry_pct}%"
    miss_note = f", {n_missed_entry} verpasste Entries" if n_missed_entry else ""
    return "weak", f"Trefferquote ≥{win_pct}% (95% LB){miss_note}"


def annotate_source_quality(
    rows: list[tuple[Any, SignalAnalytics]],
) -> None:
    """Setzt ``source_quality_*`` je Signal anhand der Aggregation über alle
    übergebenen Zeilen (das aktuelle Trail-Fenster). Mutiert ``SignalAnalytics``
    in-place und aktualisiert die ``analysis_hints`` entsprechend.

    ``rows`` ist eine Liste von (trail_entry, analytics) — der erste Wert wird
    nur für ``overall``/``scale_unknown``-Hint-Re-Build gelesen, kann aber jedes
    Objekt mit diesen Attributen sein (entkoppelt von TrailEntry).
    """
    agg: dict[str, _SourceAgg] = {}
    for _entry, a in rows:
        src = a.source_name or "(unbekannt)"
        bucket = agg.setdefault(src, _SourceAgg())
        bucket.n_total += 1
        if a.actual_entry_price is not None or a.entry_status in (
            "entered_on_time",
            "waited_for_entry",
            "entered_late",
        ):
            bucket.n_entered += 1
        if a.trade_result_status == "win":
            bucket.n_win += 1
        elif a.trade_result_status == "loss":
            bucket.n_loss += 1
        if a.entry_status == "missed_entry":
            bucket.n_missed_entry += 1

    quality: dict[str, tuple[str, str]] = {}
    for src, b in agg.items():
        quality[src] = classify_source_quality(
            n_total=b.n_total,
            n_entered=b.n_entered,
            n_win=b.n_win,
            n_loss=b.n_loss,
            n_missed_entry=b.n_missed_entry,
        )

    for entry, a in rows:
        src = a.source_name or "(unbekannt)"
        status, reason = quality.get(src, ("unknown", "keine Daten"))
        a.source_quality_status = status
        a.source_quality_reason = reason
        a.analysis_hints = _build_hints(
            entry_status=a.entry_status,
            invested_capital_pct=a.invested_capital_pct,
            capital_base_note=a.capital_base_note,
            trade_result_status=a.trade_result_status,
            targets=a.targets,
            source_quality_status=status,
            overall=getattr(entry, "overall", "UNKNOWN"),
            scale_unknown=bool(getattr(entry, "scale_unknown", False)),
        )


__all__ = [
    "SignalAnalytics",
    "TargetStatus",
    "annotate_source_quality",
    "classify_source_quality",
    "derive_signal_analytics",
]
