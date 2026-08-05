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

P0-01 (Review 07-30): drei explizite Reife-Zustände statt eines ``due``-Bits —
selbst der Story-Proxy bleibt eine OBERGRENZE (der exakte Evaluator verliert
zusätzlich Events an OHLCV-Lücken/Symbol-Caps, gemessen ~30 %):

* ``NOT_DUE``          — auch der Proxy liegt unter dem Ziel.
* ``EVAL_CHECK_DUE``   — Proxy erreicht das Ziel; der EXAKTE Evaluator muss
                         laufen. NIEMALS ein Verdikt aus diesem Zustand.
* ``JUDGEABLE``        — nur wenn die Zählung selbst der exakte Evaluator ist
                         (kind ``tech_precision``/``exec_translation``) und
                         deren n das Ziel erreicht.

``due`` bleibt als Kompat-Feld erhalten (True == Zustand != NOT_DUE); jede
Zeile trägt die versiegelte ``prereg_id``.

WICHTIG (Lehre 2026-08-02, zweiter Wiederholungsfall): der Story-Proxy meldete
FÄLLIG bei 380, während der exakte Evaluator am Gate-Horizont 273/300 zählte.
Drei getrennte Ursachen, alle hier adressiert:

1. Der Spec-Anker stand auf Mitternacht statt auf dem versiegelten
   ``created_at_utc`` — 19 Stories aus der Zeit VOR der Registrierung wurden
   mitgezählt (381 → 362 gemessen).
2. Stories, deren GATE-HORIZONT noch nicht verstrichen ist, können nicht
   aufgelöst sein; der Proxy zählte sie mit (362 → 355 gemessen).
3. Auch der bereinigte Proxy bleibt eine Obergrenze (355 vs. exakt 273). Eine
   FRISCHE exakte Beobachtung (``artifacts/research/prereg_exact_observations.jsonl``,
   geschrieben von ``trading prereg-observe --from-json``) dominiert deshalb den
   Proxy vollständig — sie IST der exakte Evaluator, mit allen Konsequenzen:
   unter Ziel ⇒ ``NOT_DUE``, am Ziel ⇒ ``JUDGEABLE``. Nach
   ``EXACT_OBSERVATION_MAX_AGE_DAYS`` verfällt sie und der Proxy übernimmt
   wieder — eine alte Messung darf den Alarm nicht dauerhaft stummschalten.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

STATE_NOT_DUE = "NOT_DUE"
STATE_EVAL_CHECK_DUE = "EVAL_CHECK_DUE"
STATE_JUDGEABLE = "JUDGEABLE"

# Eine exakte Messung altert: die Kohorte wächst weiter (~9 Stories/Tag bei
# b20ef1487ccba99d). Drei Tage decken diesen Drift ab und erzwingen danach eine
# neue Messung, statt den Alarm unbefristet zu unterdrücken.
EXACT_OBSERVATION_MAX_AGE_DAYS = 3
EXACT_OBSERVATIONS_RELPATH = Path("research") / "prereg_exact_observations.jsonl"

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
        # Byte-identisch zu ``created_at_utc`` im Ledger. Vorher stand hier das
        # blosse Datum (Mitternacht) — das zählte 5 h 43 min VOR der Versiegelung
        # publizierte Stories in die OOS-Kohorte (gemessen 19 Stück, 2026-08-02).
        "since_utc": "2026-07-02T05:43:32.211092+00:00",
        "sources": None,  # all sources
        "exclude_first_ticker": "BTC/USDT",  # hedged construction skips BTC events
        "n_target": 300,
        # Gate b20ef1487ccba99d urteilt auf Story-Level (cluster_stories, 24h).
        "level": "stories",
        # gate.horizon_s aus dem Ledger: Stories jünger als das sind noch nicht
        # auflösbar und gehören nicht in den Reife-Zähler.
        "gate_horizon_s": 86400,
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


async def _count_stories(
    session: AsyncSession, spec: dict[str, Any], now: datetime
) -> tuple[int, int]:
    """Story-dedupliziertes n — Proxy auf published_at statt entry_ts (der
    Evaluator ankert auf dem ersten OHLCV-Open NACH publish; für die Reife-
    Frage ist die Publish-Zeit dieselbe 24h-Fenster-Semantik).

    Stories, deren Gate-Horizont noch nicht verstrichen ist, sind strukturell
    nicht auflösbar und zählen nicht mit; sie werden als ``inside_horizon``
    separat zurückgegeben, damit die Restlaufzeit sichtbar bleibt.
    """
    from app.research.news_stories import cluster_stories

    rows = (
        await session.execute(
            text(_STORY_ROWS_SQL),
            {"since": spec["since_utc"], "exclude_ticker": spec["exclude_first_ticker"]},
        )
    ).all()
    horizon_s = int(spec.get("gate_horizon_s") or 0)
    resolvable_before = now - timedelta(seconds=horizon_s)
    mature: list[dict[str, Any]] = []
    inside: list[dict[str, Any]] = []
    for r in rows:
        ts = _as_dt(r.pub)
        if ts is None or not r.sym:
            continue
        outcome = {"symbol": str(r.sym), "side": str(r.side), "entry_ts": ts}
        (mature if ts <= resolvable_before else inside).append(outcome)
    n_mature = len(cluster_stories(mature))
    n_all = len(cluster_stories(mature + inside))
    return n_mature, max(n_all - n_mature, 0)


async def _maturity_documents(
    session: AsyncSession, spec: dict[str, Any], now: datetime
) -> tuple[int, dict[str, int], str]:
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
            n_stories, n_inside = await _count_stories(session, spec, now)
            reached = n_stories >= int(spec["n_target"])
            return (
                n_stories,
                {
                    "stories": n_stories,
                    "stories_inside_horizon": n_inside,
                    "events": n_events,
                },
                STATE_EVAL_CHECK_DUE if reached else STATE_NOT_DUE,
            )
        reached = n_events >= int(spec["n_target"])
        return n_events, {"all": n_events}, STATE_EVAL_CHECK_DUE if reached else STATE_NOT_DUE
    detail = {s: by_source.get(s, 0) for s in sources}
    n = sum(detail.values())
    reached = all(v >= int(spec["n_target"]) for v in detail.values())
    return n, detail, STATE_EVAL_CHECK_DUE if reached else STATE_NOT_DUE


def _maturity_tech_precision(
    spec: dict[str, Any], artifacts_dir: Path
) -> tuple[int, dict[str, int], str]:
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
    return n, detail, STATE_JUDGEABLE if n >= int(spec["n_target"]) else STATE_NOT_DUE


def _maturity_exec_translation(
    spec: dict[str, Any], artifacts_dir: Path
) -> tuple[int, dict[str, int], str]:
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
    return n, detail, STATE_JUDGEABLE if n >= int(spec["n_target"]) else STATE_NOT_DUE


def load_exact_observations(artifacts_dir: Path) -> dict[str, dict[str, Any]]:
    """Neueste exakte Evaluator-Beobachtung je ``prereg_id`` (fail-soft).

    Read-only; eine fehlende oder kaputte Datei liefert schlicht nichts und
    lässt den Proxy-Pfad unverändert.
    """
    path = artifacts_dir / EXACT_OBSERVATIONS_RELPATH
    newest: dict[str, dict[str, Any]] = {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return newest
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        pid = rec.get("prereg_id")
        observed = _as_dt(rec.get("observed_at_utc"))
        if not isinstance(pid, str) or observed is None:
            continue
        if not isinstance(rec.get("n_exact"), int):
            continue
        prev = newest.get(pid)
        if prev is None or observed > prev["_observed"]:
            newest[pid] = {**rec, "_observed": observed}
    return newest


def _seals_hedged_construction(success_criteria: str) -> bool:
    """Verlangt der versiegelte Freitext eine BTC-gehedgte Konstruktion?

    Die Hedge-Klausel steht NUR im ``success_criteria`` — das maschinelle
    ``gate`` (level/horizon/n_min/p_min/...) kennt sie nicht. Deshalb wird hier
    gegen den Wortlaut geprüft und nicht gegen das Gate.
    """
    return re.search(r"\bhedged\b", success_criteria, re.IGNORECASE) is not None


def _verify_construction_matches_prereg(success_criteria: str, eval_result: dict[str, Any]) -> None:
    """Abbrechen, wenn der Lauf eine ANDERE Kohorte gemessen hat als versiegelt.

    Befund 2026-08-05: ein ``news-eval``-Lauf ohne ``--hedge`` wurde als exakte
    Messung akzeptiert und schrieb n=360/300 fest, obwohl ``b20ef1487ccba99d``
    ausdrücklich "BTC-hedged (beta=1)" versiegelt. Die Spot-Konstruktion
    verwirft Events ohne Hedge-Symbol nicht und zählt darum systematisch mehr
    Stories (369 statt 303) — die 300er-Latte wäre zu früh gerissen. Bei ND-v2
    ist ein FAIL terminal, ein verfrühtes Verdikt also nicht heilbar.
    """
    meta = eval_result.get("meta")
    construction = meta.get("construction") if isinstance(meta, dict) else None
    if not isinstance(construction, str) or not construction:
        raise ValueError(
            "evaluator output carries no meta.construction — the measured cohort "
            "cannot be checked against the sealed criteria, so nothing is recorded"
        )
    expected_hedged = _seals_hedged_construction(success_criteria)
    actual_hedged = "hedged" in construction.lower()
    if expected_hedged != actual_hedged:
        want = "BTC-hedged" if expected_hedged else "un-hedged (spot)"
        raise ValueError(
            f"construction mismatch: pre-registration seals a {want} cohort, but the "
            f"evaluator ran {construction!r} — a different cohort was measured, so "
            "nothing is recorded (re-run news-eval "
            f"{'WITH' if expected_hedged else 'WITHOUT'} --hedge)"
        )


def record_exact_observation(
    *,
    prereg_id: str,
    gate: dict[str, Any],
    n_target: int,
    eval_result: dict[str, Any],
    artifacts_dir: Path,
    observed_at: datetime,
    source_json: str | None = None,
    success_criteria: str | None = None,
) -> dict[str, Any]:
    """Exaktes n aus einem Evaluator-Lauf gegen das VERSIEGELTE Gate festhalten.

    Das n wird nicht neu berechnet, sondern aus ``check_gate`` gezogen — genau
    die Zahl, die das Verdikt später gegen ``n_min`` prüft. Ist der geurteilte
    Block im Ergebnis nicht vorhanden, ist nichts gemessen worden und es wird
    NICHTS geschrieben (``ValueError``) — eine erfundene Null wäre schlimmer als
    keine Beobachtung.

    Wird ``success_criteria`` mitgegeben, muss ausserdem die KONSTRUKTION des
    Laufs zum versiegelten Wortlaut passen (hedged vs. spot). Das richtige n
    aus der falschen Kohorte ist keine gültige Messung.
    """
    from app.research.prereg_gate import check_gate

    if success_criteria is not None:
        _verify_construction_matches_prereg(success_criteria, eval_result)

    result = check_gate(gate, eval_result)
    n_check = next((c for c in result["checks"] if c["name"] == "n_min"), None)
    if n_check is None:
        raise ValueError(
            f"gate row {gate.get('level')}@{gate.get('horizon_s')}s not present in evaluator "
            "output — nothing was measured, so nothing is recorded"
        )
    record = {
        "prereg_id": prereg_id,
        "observed_at_utc": observed_at.astimezone(UTC).isoformat(),
        "n_exact": int(n_check["actual"]),
        "n_target": int(n_target),
        "level": str(gate.get("level")),
        "horizon_s": int(gate.get("horizon_s", 0)),
        "gate_passed": bool(result["passed"]),
        "source_json": source_json,
    }
    path = artifacts_dir / EXACT_OBSERVATIONS_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def _state_from_exact(
    observation: dict[str, Any] | None, *, n_target: int, now: datetime
) -> tuple[int, str] | None:
    """Zustand aus einer FRISCHEN exakten Messung — oder None (Proxy übernimmt)."""
    if not observation:
        return None
    age = now - observation["_observed"]
    if age > timedelta(days=EXACT_OBSERVATION_MAX_AGE_DAYS) or age < timedelta(0):
        return None
    # Gegen eine ANDERE Latte gemessen ist keine Aussage über DIESE Reife.
    observed_target = observation.get("n_target")
    if isinstance(observed_target, int) and observed_target != n_target:
        return None
    n_exact = int(observation["n_exact"])
    return n_exact, (STATE_JUDGEABLE if n_exact >= n_target else STATE_NOT_DUE)


async def compute_maturity(
    session: AsyncSession,
    *,
    specs: tuple[dict[str, Any], ...] = MATURITY_SPECS,
    artifacts_dir: Path = Path("artifacts"),
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Count each spec's out-of-sample cohort; ``due`` when target is reached.

    Eine frische exakte Beobachtung schlägt den Proxy — der Proxy ist per
    Konstruktion eine Obergrenze und darf keine Fälligkeit gegen eine
    vorliegende Messung behaupten.
    """
    now_utc = now or datetime.now(UTC)
    observations = load_exact_observations(artifacts_dir)
    out: list[dict[str, Any]] = []
    for spec in specs:
        kind = str(spec.get("kind", "documents"))
        if kind == "tech_precision":
            n, detail, state = _maturity_tech_precision(spec, artifacts_dir)
        elif kind == "exec_translation":
            n, detail, state = _maturity_exec_translation(spec, artifacts_dir)
        else:
            n, detail, state = await _maturity_documents(session, spec, now_utc)
        n_target = int(spec["n_target"])
        exact = _state_from_exact(
            observations.get(str(spec.get("prereg_id") or "")), n_target=n_target, now=now_utc
        )
        n_exact: int | None = None
        state_source = "proxy"
        if exact is not None:
            n_exact, state = exact
            state_source = "exact_observation"
        out.append(
            {
                "name": spec["name"],
                # Durchgereicht, damit Konsumenten über die versiegelte Identität
                # joinen können statt über den (driftenden) Namen.
                "prereg_id": spec.get("prereg_id"),
                "since_utc": spec["since_utc"],
                "n_target": spec["n_target"],
                "n_proxy": n,
                # n der letzten EXAKTEN Messung (None = keine frische vorhanden).
                "n_exact": n_exact,
                # Woher der Zustand stammt — ein Verdikt darf sich nie auf "proxy"
                # stützen, auch nicht implizit über ``due``.
                "state_source": state_source,
                "per_source": detail,
                "state": state,
                # Kompat-Bit: True == Zustand != NOT_DUE. Bedeutet NIE "urteilbar" —
                # ein Verdikt braucht STATE_JUDGEABLE bzw. den exakten Evaluator (P0-01).
                "due": state != STATE_NOT_DUE,
            }
        )
    return out


__all__ = [
    "EXACT_OBSERVATIONS_RELPATH",
    "EXACT_OBSERVATION_MAX_AGE_DAYS",
    "MATURITY_SPECS",
    "STATE_EVAL_CHECK_DUE",
    "STATE_JUDGEABLE",
    "STATE_NOT_DUE",
    "compute_maturity",
    "load_exact_observations",
    "record_exact_observation",
]
