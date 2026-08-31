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
* ``RESOLVED``         — ein terminales Verdict für die versiegelte
                         ``prereg_id`` liegt in der vollständig verifizierten
                         Truth-Kette; keine erneute Auswertung.
* ``RESOLUTION_HOLD``  — die Resolution-Evidenz ist beschädigt,
                         widersprüchlich oder nicht eindeutig klassifizierbar;
                         HOLD statt Doppel-Auswertung.

``due`` bleibt als Kompat-Feld erhalten und ist nur für
``EVAL_CHECK_DUE``/``JUDGEABLE`` wahr; jede Zeile trägt die versiegelte
``prereg_id``.

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

from app.truth.ledger import read_verified_ledger

STATE_NOT_DUE = "NOT_DUE"
STATE_EVAL_CHECK_DUE = "EVAL_CHECK_DUE"
STATE_JUDGEABLE = "JUDGEABLE"
STATE_RESOLVED = "RESOLVED"
STATE_RESOLUTION_HOLD = "RESOLUTION_HOLD"
# Ein versiegelter Claim, den die Wachliste NICHT kennt und den die Truth-Kette
# NICHT terminal entschieden hat. Kein Reifegrad, sondern eine Luecke in der
# Aufsicht selbst — deshalb ein eigener Zustand und nicht "NOT_DUE".
STATE_UNWATCHED = "UNWATCHED"
# Beaufsichtigt durch eine Operator-Entscheidung im Register, nicht durch einen
# Zaehler. Ein Mensch mit Termin ist Aufsicht — nur eben keine, die eine
# Wachliste kennt. Bis 2026-08-31 fehlte dieser Zustand, und der taegliche
# Alarm nannte ``6751bc33`` deshalb "in KEINER Wachliste": unwahr, seit das
# Register am 27.08. dafuer MANUAL_SCHEDULED_REVIEW mit Termin 15.09. fuehrt.
STATE_SUPERVISED = "SUPERVISED"

# Operator-Aufsichtsregister. Liegt im Repo (versioniert), nicht unter
# ``artifacts`` — deshalb ein eigener Default statt eines Relpath.
DEFAULT_SUPERVISION_REGISTER = Path("config/prereg_supervision.json")

# Geschlossene Liste, bewusst kein "alles ausser UNWATCHED": ein neuer Zustand
# muss hier eingetragen werden, bevor er einen Claim aus der Aufsichtsluecke
# holt. Sonst schaltete ein Tippfehler im Register den Waechter still.
SUPERVISING_DECISION_STATES = frozenset(
    {
        "WATCH",
        "MANUAL_IMMEDIATE_VERDICT",
        "MANUAL_SCHEDULED_REVIEW",
        "SUPERSEDED",
    }
)

# ``MANUAL_IMMEDIATE_VERDICT`` traegt laut Register-Invariante diesen Wert
# statt eines Datums — er bedeutet faellig, nicht "kein Termin".
_DUE_NOW = "DUE_NOW"
# Ein versiegelter Claim, fuer den ein Verdikt in einer SEITENABLAGE liegt
# (prereg_verdicts.jsonl, ln_reconciliation_verdict.jsonl), aber keines in
# der verifizierten Truth-Kette. Kein Abschluss — aber eine andere Handlung
# als UNWATCHED: attestieren, nicht auswerten. Live 2026-08-26: 0879a65c (LN)
# trug ein PASS in der Seitenablage und stand trotzdem als "kein Verdikt".
STATE_VERDICT_UNATTESTED = "VERDICT_UNATTESTED"
PREREG_LEDGER_RELPATH = Path("research") / "prereg_ledger.jsonl"
PREREG_VERDICTS_RELPATH = Path("research") / "prereg_verdicts.jsonl"
# Nur diese Resolution-Status beenden einen Claim. `conflict`,
# `untrusted_attestation`, `unclassified` und `invalid_ledger` sind
# ausdruecklich KEIN Abschluss: sie heissen "die Evidenz traegt nicht", und ein
# Claim ohne tragende Evidenz bleibt unter Aufsicht (fail-closed).
_TERMINAL_RESOLUTION_STATUS = frozenset({"resolved"})

# Eine exakte Messung altert: die Kohorte wächst weiter (~9 Stories/Tag bei
# b20ef1487ccba99d). Drei Tage decken diesen Drift ab und erzwingen danach eine
# neue Messung, statt den Alarm unbefristet zu unterdrücken.
EXACT_OBSERVATION_MAX_AGE_DAYS = 3
EXACT_OBSERVATIONS_RELPATH = Path("research") / "prereg_exact_observations.jsonl"
TRUTH_LEDGER_RELPATH = Path("truth") / "attestation_ledger.jsonl"

_VERDICT_NON_TERMINAL_PREFIXES = ("INSUFFICIENT_N", "PENDING", "NOT_DUE", "INCONCLUSIVE")
_VERDICT_NOT_MET_PREFIXES = ("NOT_MET", "FAILED", "FAIL")
_VERDICT_MET_PREFIXES = ("MET", "PASSED", "PASS")
# Terminale Abschlüsse OHNE Sachverdikt. Der Claim ist beendet, trägt aber
# weder MET noch NOT_MET — beides gleichzeitig wahr und deshalb eine eigene
# Klasse. Ohne sie landet ein sauber geschlossener Claim in ``UNKNOWN`` und
# damit im HOLD, also im selben Topf wie eine beschädigte Truth-Kette (Befund
# 2026-08-17 an H2/seq 79). Die ``_BY_TIMEOUT``-Variante ist bewusst NICHT vom
# nicht-terminalen ``INCONCLUSIVE`` abgedeckt: die Underscore-Regel in
# ``starts_with_token`` trennt beide, und genau diese Trennung ist gewollt —
# "noch nicht auswertbar" und "Frist abgelaufen, kein Sachverdikt" sind
# gegensätzliche Zustände.
_VERDICT_CLOSED_NO_VERDICT_PREFIXES = (
    "CLOSED_UNMEASURABLE",
    "CLOSED_NO_VERDICT",
    "INCONCLUSIVE_BY_TIMEOUT",
)
VERDICT_CLASS_CLOSED_NO_VERDICT = "CLOSED_NO_VERDICT"

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
        # gate.horizon_s aus dem Ledger — EXPLIZIT durchgereicht statt auf den
        # Modul-Default des Evaluators zu vertrauen (Divergenz-Bauart von #648).
        "gate_horizon_s": 604800,
    },
    # H2 ist am 2026-08-08 als CLOSED_UNMEASURABLE geschlossen (Verdict-Report
    # 20260808_103720, attestiert). Der Spec bleibt bewusst STEHEN: die
    # Truth-Kette liefert die Resolution und der Eintrag zeigt sie an — löschen
    # würde einen entschiedenen Claim unsichtbar machen statt abgeschlossen.
    {
        "name": "execution_translation_hit_to_win_v1",
        "prereg_id": "0c7ead764621dd17",
        "kind": "exec_translation",
        "since_utc": "2026-07-29T09:15:10.626958+00:00",
        "n_target": 50,
        "gate_horizon_s": 86400,
        "note": (
            "GESCHLOSSEN 2026-08-08 als CLOSED_UNMEASURABLE bei n=14/50 — kein "
            "Sachverdikt. Nur ~26 % der Closes konnten die Population je erreichen "
            "(28/53 ohne Outcome-Eintrag, davon 26x real_analysis; 12 weitere miss "
            "und per Konstruktion ausgeschlossen). Nachfolger: "
            "signal_hit_to_win_conversion_v2 (26d3e0eb29f553f3)."
        ),
    },
    # H2-Nachfolger (2026-08-08): gleiche Frage, reparierte Messung. Trägt als
    # ERSTER n-basierter Claim eine Frist — genau die Bremse, deren Fehlen den
    # Vorgänger unbegrenzt "reifen" ließ.
    {
        "name": "signal_hit_to_win_conversion_v2",
        "prereg_id": "26d3e0eb29f553f3",
        "kind": "hit_to_win",
        "since_utc": "2026-08-08T10:41:26.736211+00:00",
        "n_target": 30,
        "gate_horizon_s": 86400,
        "window_end_utc": "2026-09-22T00:00:00+00:00",
        "note": (
            "Frist 2026-09-22: wird n>=30 bis dahin nicht erreicht, schliesst der "
            "Claim als INCONCLUSIVE_BY_TIMEOUT — kein Sachverdikt, keine "
            "Verlaengerung, keine Kriteriumsaenderung. Gatend ist NUR die "
            "hit-Konversion; miss-Seite/Trennschaerfe sind pflicht-ausgewiesene "
            "Diagnostik und urteilen nicht mit."
        ),
    },
    # Fensterbasierte Demand-Probe (gate=null, free-text-era): Reife ist hier
    # KEIN n, sondern das versiegelte Fensterende. Nach Ablauf ⇒ EVAL_CHECK_DUE
    # (die versiegelte Regel anwenden), niemals JUDGEABLE aus diesem Zähler.
    # Fenster + Vermerk aus artifacts/research/analyst_probe_evaluation_rule_20260805.json
    # (Pi, sealed 2026-08-05); Audit-Befund P0-3: lief sonst in KEINER Überwachung.
    # K1-Kanal-Audit (gate=null, free-text-era). Fenster-Anker ist die
    # Versiegelung 2026-07-04T12:51:11Z + horizon 30d ⇒ 2026-08-03T12:51:11Z.
    # Der versiegelte Text ankert an "publishing the anonymized K1 pilot
    # report" — nicht maschinenlesbar; #714 weist auf der oeffentlichen /paper
    # bereits genau dieses Datum aus, der Spec schreibt es also nicht neu,
    # sondern zieht es unter Aufsicht. Bis 2026-08-18 stand dieser Claim in
    # KEINER Wachliste: Fenster seit 15 Tagen zu, kein Verdikt, kein Waechter.
    {
        "name": "k1_channel_audit_resonance",
        "prereg_id": "00c75a76a2b0e78b",
        "kind": "deadline",
        "since_utc": "2026-07-04T12:51:11.469459+00:00",
        "window_end_utc": "2026-08-03T12:51:11.469459+00:00",
        "n_target": 5,
        "note": (
            "Zaehlung ist NICHT maschinell: >=5 qualifizierte schriftliche Anfragen "
            "(je ein konkret benannter zu auditierender Signalanbieter ODER ein klares "
            "Zahlungsbereitschafts-Signal) im Fenster; Spam, Selbstbewerbung ohne "
            "Audit-Wunsch und interne Anfragen zaehlen nicht. Nur der Operator kann "
            "den Posteingang auszaehlen. <5 ⇒ KILL des Angebots (keine weiteren "
            "unaufgeforderten Kanal-Audits), Truth-Infra-Fokus unveraendert."
        ),
    },
    {
        "name": "analyst_prediction_ledger_demand_v1",
        "prereg_id": "f0e1a3a8073fd4c0",
        "kind": "deadline",
        "since_utc": "2026-07-11T00:13:00+00:00",
        "window_end_utc": "2026-08-10T00:13:00+00:00",
        "n_target": 0,
        "note": (
            "Verdikt NUR mit Confounder-Vermerk AP-DEF-2 (Mail-Rückkanal war kein "
            "Postfach) nach versiegelter Regel analyst_probe_evaluation_rule_20260805.json; "
            "PASS-Nachweis primär public_showcase, das Log kann Signale nur verschweigen."
        ),
    },
    # M3-Fristwächter (STAB-06a, Operator-Klassifikation 2026-08-27 = WATCH).
    # Der einzige der sieben aufsichtsoffenen Claims, bei dem Nichtstun eine
    # RICHTUNGSENTSCHEIDUNG verschluckt: der Seal bindet ausdrücklich
    # "Zero of three by revisit means M3 NOT reached, trigger ADR-0012 exit
    # review". Fenster-Anker ist NICHT Versiegelung + horizon, sondern das im
    # Kriterium GENANNTE Revisit-Datum 2026-09-29 — 90 d ab Versiegelung
    # (2026-07-04T09:15:41Z) ergäbe den 2026-10-02 und damit eine Frist, die
    # drei Tage NACH der Entscheidung liegt, die sie auslösen soll.
    # Kein Zähler: die drei Zweige sind externe Ereignisse, keine Kohorte.
    # Zweig (c) ist bereits widerlegt (C1 9cab81fae4823482 = FAIL/NO_DEMAND,
    # 0 settled Payments); (a) und (b) hängen an unaufgeforderten Dritten —
    # ⛔ Kalt-Ansprache ist ausgeschlossen, Warten ist die einzige zulässige
    # Handlung. Läuft im BESTEHENDEN kai-prereg-maturity, kein neuer Timer.
    {
        "name": "m3_external_validation_first_signal",
        "prereg_id": "c489079289070a8c",
        "kind": "deadline",
        "since_utc": "2026-07-04T09:15:41.100686+00:00",
        "window_end_utc": "2026-09-29T09:15:41.100686+00:00",
        "n_target": 1,
        "note": (
            "PASS beim ERSTEN von drei externen Signalen: (a) unabhaengige Partei "
            "rechnet einen versiegelten canonical-edge-Report nach und meldet VERIFY OK, "
            "ODER (b) qualifiziertes Fachfeedback zum C3-Methodenpapier von >=1 "
            "unabhaengigem Praktiker, ODER (c) >=1 zahlende externe Partei ueber das "
            "C1-Fee-Truth-Listing. Zweig (c) ist bereits widerlegt (C1 FAIL = NO_DEMAND). "
            "Null von drei bis zum Revisit ⇒ M3 NICHT erreicht ⇒ ADR-0012-Exit-Review. "
            "⛔ Keine Kalt-Ansprache, um ein Signal zu erzeugen — das Warten IST die Methode."
        ),
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
    from app.research import quote_evals

    ev = quote_evals.evaluate_technical_paper_precision(
        outcomes_path=artifacts_dir / "alert_outcomes.jsonl",
        exec_audit_path=artifacts_dir / "paper_execution_audit.jsonl",
        registered_at_utc=str(spec["since_utc"]),
        horizon_s=int(spec["gate_horizon_s"]),
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
    from app.research import quote_evals

    ev = quote_evals.evaluate_execution_translation(
        outcomes_path=artifacts_dir / "alert_outcomes.jsonl",
        exec_audit_path=artifacts_dir / "paper_execution_audit.jsonl",
        registered_at_utc=str(spec["since_utc"]),
        horizon_s=int(spec["gate_horizon_s"]),
    )
    pop = ev["population"]
    n = int(pop["docs_joined_to_hit"])
    detail = {
        "joined": n,
        "closed_docs": int(pop["closed_docs_since_reg"]),
    }
    return n, detail, STATE_JUDGEABLE if n >= int(spec["n_target"]) else STATE_NOT_DUE


def _maturity_hit_to_win(
    spec: dict[str, Any], artifacts_dir: Path
) -> tuple[int, dict[str, int], str]:
    """H2-Nachfolger: gezählt wird die GATENDE hit-Kohorte, nichts sonst.

    Die ``diagnostics`` des Evaluators (miss-Seite, Trennschärfe) sind
    ausdrücklich NICHT reiferelevant — sie erklären ein Ergebnis, sie
    erzeugen keines. Identische Populations-Definition wie das spätere
    Verdikt (DRY, Divergenz-Bauart von #648 vermieden).
    """
    from app.research import quote_evals

    ev = quote_evals.evaluate_hit_to_win_conversion(
        outcomes_path=artifacts_dir / "alert_outcomes.jsonl",
        exec_audit_path=artifacts_dir / "paper_execution_audit.jsonl",
        registered_at_utc=str(spec["since_utc"]),
        horizon_s=int(spec["gate_horizon_s"]),
    )
    n = int(ev["overall"]["n"])
    detail = {
        "n_hit_gating": n,
        "n_miss_diagnostic": int(ev["diagnostics"]["n_miss"]),
        "closed_docs": int(ev["population"]["closed_docs_since_reg"]),
        "absent_from_ledger": int(ev["population"]["absent_from_outcome_ledger"]),
    }
    return n, detail, STATE_JUDGEABLE if n >= int(spec["n_target"]) else STATE_NOT_DUE


def _maturity_deadline(spec: dict[str, Any], now: datetime) -> tuple[int, dict[str, Any], str]:
    """Fensterbasierte Prä-Reg (gate=null): Reife = versiegeltes Fensterende.

    Nach Ablauf ⇒ ``EVAL_CHECK_DUE`` — die versiegelte Auswertungsregel muss
    angewandt werden. NIEMALS ``JUDGEABLE`` aus diesem Zähler: das Verdikt
    fällt die Regel (manuell, attestiert), nicht der Kalender. Ein kaputtes
    ``window_end_utc`` ist fail-closed sofort fällig statt still nie.
    """
    window_end = _as_dt(spec.get("window_end_utc"))
    if window_end is None:
        return 0, {"window_end_utc": None, "days_remaining": 0}, STATE_EVAL_CHECK_DUE
    remaining = max((window_end - now).total_seconds(), 0.0)
    days_remaining = int(remaining // 86400)
    detail: dict[str, Any] = {
        "window_end_utc": str(spec.get("window_end_utc")),
        "days_remaining": days_remaining,
    }
    state = STATE_EVAL_CHECK_DUE if now >= window_end else STATE_NOT_DUE
    return 0, detail, state


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


def _terminal_verdict_class(verdict: object) -> str | None:
    """Classify the explicit terminal prefix of an attested verdict.

    ``None`` means explicitly non-terminal. Unknown prose is deliberately not
    guessed into MET/NOT_MET: a verdict controls whether an irreversible
    evaluation is repeated, so ambiguous wording becomes a visible HOLD.
    """
    if not isinstance(verdict, str) or not verdict.strip():
        return "UNKNOWN"
    normalized = verdict.strip().upper()

    def starts_with_token(prefixes: tuple[str, ...]) -> bool:
        for prefix in prefixes:
            if not normalized.startswith(prefix):
                continue
            suffix = normalized[len(prefix) :]
            if not suffix or (not suffix[0].isalnum() and suffix[0] != "_"):
                return True
        return False

    # Zuerst: ein ausdrücklicher Abschluss ohne Sachverdikt. Muss VOR der
    # nicht-terminalen Liste stehen, damit die Reihenfolge die Absicht trägt
    # statt sie einer Präfix-Feinheit zu überlassen.
    if starts_with_token(_VERDICT_CLOSED_NO_VERDICT_PREFIXES):
        return VERDICT_CLASS_CLOSED_NO_VERDICT
    if starts_with_token(_VERDICT_NON_TERMINAL_PREFIXES):
        return None
    if starts_with_token(_VERDICT_NOT_MET_PREFIXES):
        return "NOT_MET"
    if starts_with_token(_VERDICT_MET_PREFIXES):
        return "MET"
    return "UNKNOWN"


def load_attested_resolutions(
    artifacts_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    """Read terminal prereg resolutions from the verified Truth ledger.

    Returns ``(by_prereg_id, global_error)``. The complete ledger is verified
    before a single verdict is trusted. A broken chain therefore yields one
    global fail-closed error instead of partially trusting the readable prefix.

    A verdict additionally needs the canonical report provenance used by
    ``attest_verdict_reports``: ``subject_id == payload_hash``. Multiple reports
    in the same terminal direction are harmless (for example a robustness
    annex after a FAILED report). Opposite terminal directions are a conflict.
    Explicit ``INSUFFICIENT_N``-class reports remain non-terminal.
    """
    path = artifacts_dir / TRUTH_LEDGER_RELPATH
    if not path.exists():
        return {}, None

    snapshot = read_verified_ledger(path)
    if not snapshot.get("ok"):
        return {}, {
            "status": "invalid_ledger",
            "ledger": str(path),
            "errors": list(snapshot.get("errors") or []),
        }

    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in snapshot["records"]:
        if record.get("kind") != "verdict":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        prereg_id = payload.get("prereg_id")
        if not isinstance(prereg_id, str) or not prereg_id:
            continue
        verdict_class: str | None
        if record.get("subject_id") != record.get("payload_hash"):
            verdict_class = "UNTRUSTED"
        else:
            verdict_class = _terminal_verdict_class(payload.get("verdict"))
        if verdict_class is None:
            continue
        grouped.setdefault(prereg_id, []).append(
            {
                "verdict_class": verdict_class,
                "seq": int(record["seq"]),
                "subject_id": str(record.get("subject_id") or ""),
            }
        )

    resolutions: dict[str, dict[str, Any]] = {}
    for prereg_id, records in grouped.items():
        untrusted_seqs = [r["seq"] for r in records if r["verdict_class"] == "UNTRUSTED"]
        if untrusted_seqs:
            resolutions[prereg_id] = {
                "status": "untrusted_attestation",
                "seqs": untrusted_seqs,
            }
            continue
        known = {r["verdict_class"] for r in records if r["verdict_class"] in {"MET", "NOT_MET"}}
        unknown_seqs = [r["seq"] for r in records if r["verdict_class"] == "UNKNOWN"]
        if len(known) > 1:
            resolutions[prereg_id] = {
                "status": "conflict",
                "verdict_classes": sorted(known),
                "seqs": [r["seq"] for r in records],
            }
            continue
        # Ein Sachverdikt schlägt einen Abschluss ohne Sachverdikt: beide sind
        # terminal, aber MET/NOT_MET trägt mehr Information.
        if not known and any(
            r["verdict_class"] == VERDICT_CLASS_CLOSED_NO_VERDICT for r in records
        ):
            known = {VERDICT_CLASS_CLOSED_NO_VERDICT}
        if known:
            verdict_class = next(iter(known))
            matching = [r for r in records if r["verdict_class"] == verdict_class]
            latest = max(matching, key=lambda r: r["seq"])
            resolutions[prereg_id] = {
                "status": "resolved",
                "verdict_class": verdict_class,
                "seq": latest["seq"],
                "subject_id": latest["subject_id"],
                "unclassified_seqs": unknown_seqs,
            }
            continue
        resolutions[prereg_id] = {
            "status": "unclassified",
            "seqs": unknown_seqs,
        }
    return resolutions, None


def _offchain_verdict_sources(artifacts_dir: Path) -> dict[str, list[str]]:
    """``prereg_id`` → Seitenablagen mit einem terminalen Verdikt-Datensatz.

    Rein diagnostisch (siehe ``find_unwatched_preregs``): die Seitenablage ist
    nicht signaturverkettet und kann einen Claim nicht terminal schliessen.
    Die Leseregel (juengster Datensatz je Datei, nur terminale Klassen) lebt
    in ``prereg_reconciliation`` — EINE Implementierung fuer alle Leser.
    """
    from app.research.prereg_reconciliation import load_offchain_verdicts

    return {
        pid: [row["source"] for row in rows]
        for pid, rows in load_offchain_verdicts(artifacts_dir).items()
    }


def find_resolved_unspecced_preregs(
    artifacts_dir: Path,
    *,
    specs: Any = MATURITY_SPECS,
    resolutions: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Terminal entschiedene Claims OHNE Reife-Spec — als RESOLVED-Zeilen.

    Befund 2026-08-26: 19 versiegelte Claims, 14 im Reifeblick. Die fuenf
    fehlenden trugen alle ein terminales Verdikt in der Truth-Kette und
    fielen genau deshalb durch: ``find_unwatched_preregs`` schloss sie zu
    Recht aus, eine Spec-Zeile gab es nie. "Entschieden" sah aus wie
    "existiert nicht" — ein Reifeblick, der nicht die ganze Grundgesamtheit
    zeigt, laesst sich nicht gegen das Ledger abgleichen.
    """
    from app.research.prereg_reconciliation import load_sealed_entries

    watched = {
        str(spec.get("prereg_id"))
        for spec in specs
        if isinstance(spec.get("prereg_id"), str) and spec.get("prereg_id")
    }
    rows: list[dict[str, Any]] = []
    for record in load_sealed_entries(artifacts_dir):
        prereg_id = str(record["prereg_id"])
        if prereg_id in watched:
            continue
        resolution = (resolutions or {}).get(prereg_id)
        if not isinstance(resolution, dict):
            continue
        if str(resolution.get("status")) not in _TERMINAL_RESOLUTION_STATUS:
            continue
        rows.append(
            {
                "name": str(record.get("name") or "?"),
                "kind": "ledger_resolved",
                "note": None,
                "prereg_id": prereg_id,
                "since_utc": str(record.get("created_at_utc") or ""),
                "n_target": record.get("sample_size_target"),
                "n_proxy": None,
                "n_exact": None,
                "state_source": "truth_ledger",
                "per_source": {
                    "sealed_at_utc": str(record.get("created_at_utc") or ""),
                    "horizon": str(record.get("horizon") or ""),
                },
                "state": STATE_RESOLVED,
                "window_end_utc": None,
                "timed_out": False,
                "resolution": resolution,
                "due": False,
            }
        )
    return rows


def review_is_due(next_review_utc: Any, now: datetime) -> bool:
    """Faellig heisst: jetzt oder ueberfaellig. Ein fehlender Termin ist NICHT faellig.

    Ein unlesbares Datum gilt als faellig — lieber eine Meldung zu viel als ein
    Termin, den ein Tippfehler unsichtbar macht.
    """
    if not isinstance(next_review_utc, str) or not next_review_utc.strip():
        return False
    raw = next_review_utc.strip()
    if raw == _DUE_NOW:
        return True
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when <= now


def load_supervision_register(path: Path) -> dict[str, dict[str, Any]]:
    """Je ``prereg_id`` die Operator-Aufsichtsentscheidung — fail-soft, fail-closed.

    EINE Implementierung fuer beide Leser (``find_unwatched_preregs`` hier und
    ``classify_ledger_entries`` in :mod:`app.research.prereg_reconciliation`).
    Zwei Kopien haetten sich denselben Weg gedriftet wie #723/#748/#755.

    Fail-soft: fehlende oder kaputte Datei -> ``{}``; der Abgleich meldet dann
    wieder Aufsichtsluecken, was korrekt ist. Fail-closed: nur Zustaende aus
    :data:`SUPERVISING_DECISION_STATES` zaehlen; ``UNWATCHED``, ``UNRESOLVED``,
    leer oder unbekannt wird verworfen statt still als Aufsicht durchzugehen.

    Der Abgleich gegen das versiegelte Ledger passiert NICHT hier: auch
    Eintraege ohne versiegelten Claim kommen zurueck, damit der Aufrufer diese
    Drift melden kann (Spiegelbild zu ``ghost_specs``).
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    entries = raw.get("entries")
    if not isinstance(entries, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        prereg_id = entry.get("prereg_id")
        state = entry.get("decision_state")
        if not isinstance(prereg_id, str) or not prereg_id:
            continue
        if not isinstance(state, str) or state not in SUPERVISING_DECISION_STATES:
            continue
        out[prereg_id] = entry
    return out


def supervision_view(entry: dict[str, Any] | None, now: datetime) -> dict[str, Any] | None:
    """Nur die handlungsrelevanten Felder — kein Durchreichen des Registers.

    Insbesondere wandert KEINE Begruendungs-Prosa aus dem Register in einen
    Alarmtext; gerendert werden Zustand, Eigentuemer und Termin.
    """
    if not entry:
        return None
    return {
        "decision_state": str(entry.get("decision_state")),
        "owner": str(entry.get("owner") or "unknown"),
        "next_review_utc": entry.get("next_review_utc"),
        "due": review_is_due(entry.get("next_review_utc"), now),
    }


def find_unwatched_preregs(
    artifacts_dir: Path,
    *,
    specs: Any = MATURITY_SPECS,
    resolutions: dict[str, dict[str, Any]] | None = None,
    supervision_register: Path | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Versiegelte Claims, die weder beobachtet noch terminal entschieden sind.

    ``MATURITY_SPECS`` ist handgepflegt. Was niemand eintrug, existierte fuer
    den Waechter nicht — und ein Claim kann so unbemerkt verrotten: sein
    Fenster laeuft ab, niemand wendet die versiegelte Regel an, niemand merkt
    es. Live gemessen am 2026-08-18: 19 versiegelte Prae-Regs, 6 in der
    Wachliste, 9 terminal in der Truth-Kette -> **8 Claims ohne jede Aufsicht**,
    darunter ``00c75a76a2b0e78b`` mit einem seit dem 03.08. geschlossenen
    Fenster.

    Der Abgleich fuehrt keine neue Wahrheitsquelle ein: Grundgesamtheit ist das
    versiegelte Ledger, Ausnahmen sind nur Beobachtung (Spec) oder Abschluss
    (terminale Resolution). Read-only und fail-soft — ein fehlendes oder
    kaputtes Ledger liefert eine leere Liste statt eines Absturzes; die
    Existenz des Ledgers wacht der Health-Check separat (``prereg_ledger_presence``).
    """
    path = artifacts_dir / PREREG_LEDGER_RELPATH
    if not path.exists():
        return []
    watched = {
        str(spec.get("prereg_id"))
        for spec in specs
        if isinstance(spec.get("prereg_id"), str) and spec.get("prereg_id")
    }
    # Diagnose, KEINE Ausnahme: ein Verdikt in der Seitenablage beendet den
    # Claim nicht — terminal ist nur die verifizierte Truth-Kette. Der Marker
    # trennt aber die zwei Operator-Handlungen: "Verdikt attestieren" ist
    # etwas anderes als "versiegelte Regel ueberhaupt erst anwenden".
    offchain = _offchain_verdict_sources(artifacts_dir)
    supervision = load_supervision_register(
        DEFAULT_SUPERVISION_REGISTER if supervision_register is None else supervision_register
    )
    at = now or datetime.now(UTC)
    closed = {
        pid
        for pid, res in (resolutions or {}).items()
        if str(res.get("status")) in _TERMINAL_RESOLUTION_STATUS
    }
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            # Eine kaputte Zeile darf den Abgleich nicht beenden — sonst
            # verdeckt ein Schreibfehler alle nachfolgenden Claims.
            continue
        if not isinstance(record, dict):
            continue
        prereg_id = record.get("prereg_id")
        if not isinstance(prereg_id, str) or not prereg_id:
            continue
        if prereg_id in seen or prereg_id in watched or prereg_id in closed:
            seen.add(prereg_id)
            continue
        seen.add(prereg_id)
        offchain_sources = offchain.get(prereg_id, [])
        # Nur ein SUPERVISED-Claim kann nicht faellig sein; alles andere in
        # dieser Funktion ist per Definition eine offene Handlung.
        supervision_due = True
        if offchain_sources:
            # Ein Off-Chain-Verdikt schliesst nicht, aendert aber die Handlung:
            # die versiegelte Regel WURDE angewandt — was fehlt, ist die
            # Attestierung in die Truth-Kette. "Regel anwenden" waere hier
            # eine zweite Auswertung desselben Claims.
            state = STATE_VERDICT_UNATTESTED
            note = (
                "Versiegelt, Verdikt liegt in der Seitenablage "
                f"({', '.join(offchain_sources)}), aber NICHT in der verifizierten "
                "Truth-Kette. Verdikt attestieren — nicht erneut auswerten."
            )
        elif prereg_id in supervision:
            # Beaufsichtigt, nicht uebersehen: das Register nennt Zustand,
            # Eigentuemer und Termin. Faellig bleibt faellig — der Eintrag
            # verschiebt die Frist nicht, er benennt nur den Zustaendigen.
            view = supervision_view(supervision[prereg_id], at) or {}
            state = STATE_SUPERVISED
            note = (
                "Versiegelt und ohne terminales Verdikt, aber unter "
                f"Operator-Aufsicht ({view.get('decision_state')}, "
                f"Eigentuemer {view.get('owner')}, Termin "
                f"{view.get('next_review_utc') or 'offen'}). Keine "
                "Aufsichtsluecke — die Handlung liegt beim Termin, nicht bei "
                "einem fehlenden Spec."
            )
            supervision_due = bool(view.get("due"))
        else:
            state = STATE_UNWATCHED
            note = (
                "Versiegelt, aber in keiner Wachliste und ohne terminales "
                "Verdikt in der verifizierten Truth-Kette. Entweder einen "
                "Spec eintragen oder die versiegelte Regel anwenden und das "
                "Verdikt attestieren — nicht liegen lassen."
            )
        rows.append(
            {
                "name": str(record.get("name") or "?"),
                "kind": "unwatched",
                "note": note,
                "prereg_id": prereg_id,
                "since_utc": str(record.get("created_at_utc") or ""),
                "n_target": record.get("sample_size_target"),
                "n_proxy": None,
                "n_exact": None,
                "state_source": "prereg_ledger",
                "per_source": {
                    "sealed_at_utc": str(record.get("created_at_utc") or ""),
                    "horizon": str(record.get("horizon") or ""),
                    "offchain_verdict": bool(offchain_sources),
                    "offchain_sources": list(offchain_sources),
                },
                "state": state,
                "window_end_utc": None,
                "timed_out": False,
                "resolution": (resolutions or {}).get(prereg_id),
                "supervision": supervision_view(supervision.get(prereg_id), at),
                "due": supervision_due,
            }
        )
    return rows


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
    raw_meta = eval_result.get("meta")
    record = {
        "prereg_id": prereg_id,
        "observed_at_utc": observed_at.astimezone(UTC).isoformat(),
        "n_exact": int(n_check["actual"]),
        "n_target": int(n_target),
        "level": str(gate.get("level")),
        "horizon_s": int(gate.get("horizon_s", 0)),
        "gate_passed": bool(result["passed"]),
        "source_json": source_json,
        # Voller Konstruktions-Fingerabdruck des Laufs (P0-2, Audit 2026-08-06):
        # der Guard prüft nur die Hedge-Achse; max_symbols/tiered_costs/timeframe
        # verändern die Kohorte ebenso. Ohne persistiertes meta wäre ein mit
        # abweichenden Parametern gefahrener Lauf nachträglich nicht erkennbar.
        "meta": raw_meta if isinstance(raw_meta, dict) else None,
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
    vorliegende Messung behaupten. Eine terminale Resolution aus der
    verifizierten Truth-Kette schlägt beide: ein entschiedener Claim darf nie
    erneut als auswertungsfällig erscheinen.
    """
    now_utc = now or datetime.now(UTC)
    observations = load_exact_observations(artifacts_dir)
    resolutions, resolution_error = load_attested_resolutions(artifacts_dir)
    out: list[dict[str, Any]] = []
    for spec in specs:
        kind = str(spec.get("kind", "documents"))
        detail: dict[str, Any]
        if kind == "tech_precision":
            n, detail, state = _maturity_tech_precision(spec, artifacts_dir)
        elif kind == "exec_translation":
            n, detail, state = _maturity_exec_translation(spec, artifacts_dir)
        elif kind == "hit_to_win":
            n, detail, state = _maturity_hit_to_win(spec, artifacts_dir)
        elif kind == "deadline":
            n, detail, state = _maturity_deadline(spec, now_utc)
        else:
            n, detail, state = await _maturity_documents(session, spec, now_utc)
        n_target = int(spec["n_target"])
        n_exact: int | None = None
        state_source = "proxy" if kind != "deadline" else "window"
        if kind != "deadline":
            # Deadline-Specs haben keinen exakten n-Evaluator — eine (fehlerhaft
            # zugeordnete) Beobachtung darf das Fenster nicht stummschalten.
            exact = _state_from_exact(
                observations.get(str(spec.get("prereg_id") or "")), n_target=n_target, now=now_utc
            )
            if exact is not None:
                n_exact, state = exact
                state_source = "exact_observation"
        # Optionale FRIST auf n-basierten Specs. H1/H2 hatten sie nicht: ohne
        # Frist kann ein Claim, der sein n_min nie erreicht, weder PASS noch
        # FAIL werden und "reift" unbegrenzt weiter (H2 stand bei 14/50, weil
        # nur ~26 % der Closes die Population je erreichen konnten). Läuft das
        # Fenster ab, BEVOR n_target erreicht ist, wird der Claim fällig — und
        # zwar als INCONCLUSIVE_BY_TIMEOUT: kein Sachverdikt, nur ein Ende.
        # Ein bereits erreichtes n_target bleibt JUDGEABLE (Frist bremst nicht).
        window_end = _as_dt(spec.get("window_end_utc")) if kind != "deadline" else None
        timed_out = False
        if window_end is not None and now_utc >= window_end:
            n_seen = n_exact if n_exact is not None else n
            if n_seen < n_target:
                timed_out = True
                state = STATE_EVAL_CHECK_DUE
                state_source = "window_timeout"
        resolution: dict[str, Any] | None = None
        prereg_id = spec.get("prereg_id")
        if resolution_error is not None:
            # Die Truth-Kette ist eine gemeinsame Wahrheitsquelle. Ist sie
            # ungültig, wird kein Claim erneut zur Auswertung empfohlen — auch
            # ein lesbarer Prefix wäre nicht mehr beweisbar vollständig.
            state = STATE_RESOLUTION_HOLD
            state_source = "truth_ledger"
            resolution = resolution_error
        elif isinstance(prereg_id, str) and prereg_id in resolutions:
            resolution = resolutions[prereg_id]
            state_source = "truth_ledger"
            state = (
                STATE_RESOLVED if resolution.get("status") == "resolved" else STATE_RESOLUTION_HOLD
            )
        out.append(
            {
                "name": spec["name"],
                # Zähl-Art durchgereicht: Konsumenten (Render/Board) müssen
                # fensterbasierte Reife anders darstellen als n-basierte.
                "kind": kind,
                # Vermerk aus dem Spec (z. B. Confounder-Pflicht) — hängt an der
                # Zeile, nicht am Operator-Gedächtnis.
                "note": spec.get("note"),
                # Durchgereicht, damit Konsumenten über die versiegelte Identität
                # joinen können statt über den (driftenden) Namen.
                "prereg_id": prereg_id,
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
                # Frist + Timeout-Bit durchgereicht, damit Render/Board ein
                # abgelaufenes n-Gate von einem erreichten unterscheiden können:
                # beide sind `due`, aber nur eines trägt ein Sachverdikt.
                "window_end_utc": spec.get("window_end_utc"),
                "timed_out": timed_out,
                "resolution": resolution,
                # Kompat-Bit: Nur die beiden echten Handlungszustände sind due.
                # RESOLVED und RESOLUTION_HOLD dürfen niemals eine zweite
                # Verdikt-Kette triggern.
                "due": state in {STATE_EVAL_CHECK_DUE, STATE_JUDGEABLE},
            }
        )
    # Abgleich gegen die Grundgesamtheit: die Wachliste oben ist handgepflegt,
    # das versiegelte Ledger ist es nicht. Ein Claim, der in keiner Zeile
    # vorkommt und kein terminales Verdikt traegt, faellt sonst durch jede
    # Ueberwachung (Befund 2026-08-18: 8 von 19 lagen so).
    out.extend(find_unwatched_preregs(artifacts_dir, specs=specs, resolutions=resolutions))
    # Und die dritte Gruppe: entschieden, aber ohne Spec. Ohne diese Zeilen
    # zeigte der Reifeblick 14 von 19 Claims (2026-08-26) — jeder Abgleich
    # gegen das Ledger haette "5 fehlen" gemeldet, nur gab es keinen.
    if resolution_error is None:
        out.extend(
            find_resolved_unspecced_preregs(artifacts_dir, specs=specs, resolutions=resolutions)
        )
    return out


def build_maturity_alert(rows: list[dict[str, Any]]) -> str | None:
    """Text für den Operator-Kanal, oder ``None`` wenn nichts fällig ist.

    ``compute_maturity`` rechnete die Fälligkeit korrekt aus und schrieb sie
    nach ``StandardOutput=journal`` — sie existierte damit nur, solange jemand
    hinschaute. Eine Frist, die niemanden erreicht, ist keine Frist.

    Der Text sagt ausdrücklich „wende die versiegelte Regel an", niemals „der
    Claim ist bestanden": ``due`` ist ein Handlungs-, kein Ergebniszustand, und
    der Proxy-Zähler ist eine Obergrenze, kein Verdikt.
    """
    due_rows = [r for r in rows if r.get("due")]
    if not due_rows:
        return None

    unwatched = sum(1 for r in due_rows if r.get("state") == STATE_UNWATCHED)
    lines: list[str] = [
        f"KAI Prae-Reg faellig: {len(due_rows)} offene Auswertung(en) "
        "— versiegelte Regel jetzt anwenden, kein Ergebnis behauptet.",
    ]
    if unwatched:
        lines.append(
            f"davon {unwatched} {STATE_UNWATCHED}: versiegelt, aber unbeobachtet "
            "und unentschieden — Aufsichtsluecke, kein Reifegrad."
        )
    supervised = sum(1 for r in due_rows if r.get("state") == STATE_SUPERVISED)
    if supervised:
        lines.append(
            f"davon {supervised} {STATE_SUPERVISED}: unter Operator-Aufsicht mit "
            "faelligem Termin — keine Aufsichtsluecke, sondern eine offene "
            "Entscheidung des Eigentuemers."
        )
    unattested = sum(1 for r in due_rows if r.get("state") == STATE_VERDICT_UNATTESTED)
    if unattested:
        lines.append(
            f"davon {unattested} {STATE_VERDICT_UNATTESTED}: Verdikt liegt in der "
            "Seitenablage, NICHT in der Truth-Kette — attestieren, nicht neu auswerten."
        )
    for row in due_rows:
        pid = str(row.get("prereg_id") or "ohne-prereg-id")
        name = str(row.get("name") or "?")
        detail = row.get("per_source") or {}
        window_end = detail.get("window_end_utc") or row.get("window_end_utc")
        if row.get("kind") == "unwatched":
            sealed = detail.get("sealed_at_utc") or row.get("since_utc") or "?"
            where = (
                "Verdikt liegt off-chain vor, aber NICHT attestiert"
                if detail.get("offchain_verdict")
                else "kein Verdikt in der verifizierten Truth-Kette"
            )
            supervision = row.get("supervision")
            if isinstance(supervision, dict):
                # "in KEINER Wachliste" waere hier eine Falschaussage: das
                # Register nennt Zustand, Eigentuemer und Termin. Faellig ist
                # die Zeile trotzdem — sie sagt nur, WER dran ist.
                whose = (
                    f"{supervision.get('decision_state')} bei "
                    f"{supervision.get('owner')}, Termin "
                    f"{supervision.get('next_review_utc') or 'offen'}"
                )
                evidence = (
                    f"versiegelt {sealed}, horizon {detail.get('horizon') or '?'} "
                    f"— unter Operator-Aufsicht ({whose}), {where}"
                )
            else:
                evidence = (
                    f"versiegelt {sealed}, horizon {detail.get('horizon') or '?'} "
                    f"— in KEINER Wachliste, {where}"
                )
        elif row.get("kind") == "deadline":
            evidence = f"Fenster endete {window_end}"
        else:
            n_exact = row.get("n_exact")
            n_shown = n_exact if n_exact is not None else row.get("n_proxy")
            basis = "exakt" if n_exact is not None else "Proxy/Obergrenze"
            evidence = f"n={n_shown}/{row.get('n_target')} ({basis})"
            if row.get("timed_out"):
                evidence += f", Frist {window_end} abgelaufen"
        lines.append(f"- {name} ({pid}): {evidence} -> {row.get('state')}")
    return "\n".join(lines)


__all__ = [
    "EXACT_OBSERVATIONS_RELPATH",
    "EXACT_OBSERVATION_MAX_AGE_DAYS",
    "MATURITY_SPECS",
    "STATE_EVAL_CHECK_DUE",
    "STATE_JUDGEABLE",
    "STATE_NOT_DUE",
    "STATE_RESOLUTION_HOLD",
    "STATE_RESOLVED",
    "STATE_SUPERVISED",
    "STATE_UNWATCHED",
    "STATE_VERDICT_UNATTESTED",
    "TRUTH_LEDGER_RELPATH",
    "PREREG_LEDGER_RELPATH",
    "PREREG_VERDICTS_RELPATH",
    "build_maturity_alert",
    "compute_maturity",
    "find_resolved_unspecced_preregs",
    "find_unwatched_preregs",
    "load_attested_resolutions",
    "load_exact_observations",
    "record_exact_observation",
]
