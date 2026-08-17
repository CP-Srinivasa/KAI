"""Paper-fill quality snapshot — couple the ≥10-fill re-entry gate with
an honest PnL/win-rate view.

The 2026-05-26 daily-strategy review showed an 11-fill gate-grün state
sitting on top of a cumulative realized PnL of -349.79 USD, with the
three most-recent closures (ETH stop -276.67, HYPE take +53.25, BTC
stop -126.37) tilted negative. Without a coupled-view CLI, "≥10
fills" looked like a green light when the underlying quality was poor.

P0-Truth-Repair 2026-07-30 (Review-Befund, Operator-Go):

* **Fail-closed PnL:** Zeilen ohne ``trade_pnl_usd`` flossen bisher mit dem
  KUMULATIVEN ``realized_pnl_usd`` in die Summen (TL-003-Klasse) — dadurch
  entstanden ökonomisch unmögliche per-Asset-Ergebnisse, die über
  ``by_symbol`` die Asset-Rotation speisten. Jetzt zählen ausschließlich
  Zeilen mit ``trade_pnl_usd``; der Rest wird ausgeschlossen und in
  ``rows_missing_trade_pnl`` sichtbar gemacht.
* **Epochen-Scope:** Das Fenster lief bisher epochenübergreifend über das
  INVALID-Legacy-Buch hinweg. ``epoch_scope=True`` (Default) schneidet am
  letzten ``portfolio_epoch_reset``-Event; ``epoch_start_utc`` trägt den
  Schnitt, ``"legacy"``-Bücher ohne Reset bleiben unbeschnitten.

This module is intentionally read-only. It iterates the paper-execution
audit JSONL, picks position_closed events, and produces both an
aggregate and per-symbol / per-reason cuts.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.execution.open_fee_match import match_open_fees
from app.research.decomposition import assess_group_table

_DEFAULT_AUDIT = Path("artifacts/paper_execution_audit.jsonl")
_CLOSE_EVENTS = ("position_closed", "position_partial_closed")
_EPOCH_RESET_EVENT = "portfolio_epoch_reset"

PNL_BASIS = "trade_pnl_usd_fail_closed"


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class PaperQualitySnapshot:
    closures_total: int
    window_last_n: int
    window_closures: tuple[dict[str, object], ...]
    win_rate: float
    sum_trade_pnl_usd: float
    avg_trade_pnl_usd: float
    latest_realized_pnl_usd: float | None
    by_symbol: dict[str, dict[str, float]]
    by_reason: dict[str, dict[str, float]]
    audit_path: str
    # P0-Truth-Repair 2026-07-30 (alle additiv):
    pnl_basis: str = PNL_BASIS
    rows_missing_trade_pnl: int = 0
    # Voll belastete Parallelgroessen (additiv, 2026-08-10). ``sum_trade_pnl_usd``
    # traegt die Entry-Fee NICHT — der Name sagt das nicht, und genau deshalb
    # las das Rotations-Verdikt jahrelang eine zu optimistische Zahl.
    sum_net_pnl_usd: float = 0.0
    sum_open_fee_usd: float = 0.0
    epoch_scoped: bool = False
    epoch_start_utc: str | None = None
    # Direktive 2026-08-08 „kein Aggregat ohne Zerlegung": ``by_symbol``
    # existierte längst — nur BEWERTET hat es niemand. Dieses Feld sagt im
    # Klartext, ob die ``win_rate`` von einem Symbol getragen wird, statt das
    # dem Leser zu überlassen.
    #
    # Bewusst NICHT über ``by_reason``: ``reason`` ist mit dem Ergebnis
    # definitorisch gekoppelt (``take`` = Gewinn-Exit, ``stop`` = Verlust-Exit),
    # eine Win-Rate-Zerlegung darüber ergibt zwangsläufig 100 % vs 0 % und
    # trägt null Information. Am echten Buch feuerte genau dort ein Dauer-Flag.
    # Die stop/take-Verteilung bleibt als ``by_reason`` sichtbar — sie ist eine
    # eigene Kennzahl (Stop-Quote), keine Erklärung der Win-Rate.
    win_rate_by_symbol_assessment: dict[str, object] = field(default_factory=dict)


@dataclass
class _SymCounter:
    count: int = 0
    wins: int = 0
    losses: int = 0
    sum_pnl: float = 0.0
    # Voll belastet: zusaetzlich abzueglich der FIFO-gematchten Entry-Fee.
    # ``sum_pnl`` traegt sie NICHT (paper_engine zieht beim Close nur die
    # Close-Fee ab) — am Pi-Buch sind das 43,5 % des ausgewiesenen Betrags.
    sum_net_pnl: float = 0.0
    net_wins: int = 0
    open_fee: float = 0.0


def build_paper_quality_snapshot(
    *,
    audit_path: str | Path = _DEFAULT_AUDIT,
    last_n: int = 25,
    epoch_scope: bool = True,
) -> PaperQualitySnapshot:
    if last_n < 1:
        raise ValueError("last_n must be >= 1")

    path = Path(audit_path)
    # ALLE Events der laufenden Epoche, nicht nur die Closes: die FIFO-Zuordnung
    # der Entry-Fee braucht die oeffnenden Fills. Der Epochen-Reset leert die
    # Liste und damit auch die offenen Positionen — eine Fee aus dem
    # archivierten Buch darf keinen Trade der neuen Epoche belasten.
    epoch_records: list[dict[str, object]] = []
    epoch_start_utc: str | None = None
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
            event_type = rec.get("event_type")
            if epoch_scope and event_type == _EPOCH_RESET_EVENT:
                # Neue Epoche: alles davor gehört zum archivierten Buch
                # (INVALID_FOR_PERFORMANCE) und darf keine Quoten mehr speisen.
                epoch_records.clear()
                epoch_start_utc = str(rec.get("timestamp_utc") or "") or None
                continue
            epoch_records.append(rec)

    closures = [r for r in epoch_records if r.get("event_type") in _CLOSE_EVENTS]
    # ``trade_pnl_usd`` traegt NUR die Close-Fee (paper_engine). Die Entry-Fee
    # wurde beim Oeffnen vom Cash abgezogen und fehlt darin — am Pi-Buch sind
    # das 43,5 % des ausgewiesenen Betrags. ``churn_report`` korrigierte das
    # seit je nachtraeglich, dieser Pfad nicht, obwohl seine Ausgabe als
    # „net-of-fee realized PnL" ins Rotations-Verdikt fliesst.
    open_fee_by_record = {id(m.record): m.open_fee_usd for m in match_open_fees(epoch_records)}

    total = len(closures)
    window = closures[-last_n:]
    wins = 0
    losses = 0
    sum_pnl = 0.0
    sum_net_pnl = 0.0
    sum_open_fee = 0.0
    missing_pnl = 0
    latest_realized: float | None = None
    by_symbol: dict[str, _SymCounter] = defaultdict(_SymCounter)
    by_reason: dict[str, _SymCounter] = defaultdict(_SymCounter)

    for rec in window:
        pnl = _coerce_float(rec.get("trade_pnl_usd"))
        if pnl is None:
            # Fail-closed: realized_pnl_usd ist KUMULATIV (NEO-P-101-r2) und
            # niemals ein Trade-PnL-Ersatz. Ausschließen + sichtbar zählen.
            missing_pnl += 1
            continue
        sum_pnl += pnl
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        # Voll belastete Parallelgroesse. ``net_wins`` zaehlt eigenstaendig:
        # ein Trade knapp ueber Null ist nach Abzug der Entry-Fee ein Verlust,
        # und der Wilson-Arm des Rotations-Verdikts haengt an genau dieser
        # Zaehlung — beide Arme kippen also gemeinsam, nicht nur die Summe.
        open_fee = open_fee_by_record.get(id(rec), 0.0)
        net_pnl = pnl - open_fee
        sum_net_pnl += net_pnl
        sum_open_fee += open_fee
        symbol = str(rec.get("symbol", "?"))
        sc = by_symbol[symbol]
        sc.count += 1
        sc.sum_pnl += pnl
        sc.sum_net_pnl += net_pnl
        sc.open_fee += open_fee
        if net_pnl > 0:
            sc.net_wins += 1
        if pnl > 0:
            sc.wins += 1
        elif pnl < 0:
            sc.losses += 1
        reason = str(rec.get("reason", "?"))
        rc = by_reason[reason]
        rc.count += 1
        rc.sum_pnl += pnl
        if pnl > 0:
            rc.wins += 1
        elif pnl < 0:
            rc.losses += 1

    # Latest realized_pnl_usd — operator's running cumulative value.
    # Source-of-truth comes from the most recent closure that carries
    # realized_pnl_usd (per NEO-P-101-r2 the field is cumulative).
    for rec in reversed(window):
        cum = _coerce_float(rec.get("realized_pnl_usd"))
        if cum is not None:
            latest_realized = cum
            break

    decided = wins + losses
    counted = len(window) - missing_pnl
    win_rate = (wins / decided) if decided > 0 else 0.0
    avg_pnl = (sum_pnl / counted) if counted else 0.0

    return PaperQualitySnapshot(
        closures_total=total,
        window_last_n=last_n,
        window_closures=tuple(dict(rec) for rec in window),
        win_rate=win_rate,
        sum_trade_pnl_usd=sum_pnl,
        sum_net_pnl_usd=sum_net_pnl,
        sum_open_fee_usd=sum_open_fee,
        avg_trade_pnl_usd=avg_pnl,
        latest_realized_pnl_usd=latest_realized,
        by_symbol={
            sym: {
                "count": float(sc.count),
                "wins": float(sc.wins),
                "losses": float(sc.losses),
                "sum_pnl_usd": sc.sum_pnl,
                # Voll belastet (inkl. FIFO-gematchter Entry-Fee) — die Groesse,
                # auf der das Rotations-Verdikt rechnen MUSS.
                "sum_net_pnl_usd": sc.sum_net_pnl,
                "net_wins": float(sc.net_wins),
                "open_fee_usd": sc.open_fee,
            }
            for sym, sc in by_symbol.items()
        },
        by_reason={
            reason: {
                "count": float(rc.count),
                "wins": float(rc.wins),
                "losses": float(rc.losses),
                "sum_pnl_usd": rc.sum_pnl,
            }
            for reason, rc in by_reason.items()
        },
        audit_path=str(path),
        pnl_basis=PNL_BASIS,
        rows_missing_trade_pnl=missing_pnl,
        epoch_scoped=epoch_scope,
        epoch_start_utc=epoch_start_utc,
        # Nur ENTSCHIEDENE Trades zählen (wins+losses) — dieselbe Basis wie
        # ``win_rate``. Würde hier ``count`` stehen, wären unentschiedene
        # Zeilen stille Verlierer und die Zerlegung widerspräche der Quote.
        win_rate_by_symbol_assessment=assess_group_table(
            {
                sym: {"n": sc.wins + sc.losses, "positives": sc.wins}
                for sym, sc in by_symbol.items()
                if sc.wins + sc.losses > 0
            }
        ),
    )


__all__ = ["PNL_BASIS", "PaperQualitySnapshot", "build_paper_quality_snapshot"]
