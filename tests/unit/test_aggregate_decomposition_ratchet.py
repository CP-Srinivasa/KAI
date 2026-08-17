"""RATCHET: neue Aggregate ohne Zerlegung dürfen nicht dazukommen.

Welle 1 (#682) machte die Zerlegung für die Quoten-Evaluatoren zur Pflicht,
Welle 2 (#684) zog Paper-Qualität, News-Evaluator und ``/quality`` nach. Beide
decken aber nur ab, was sie explizit kennen — ein **neuer** Aggregat-Produzent
an anderer Stelle fiele niemandem auf. Genau diese Lücke schließt dieser Test.

**Warum ein Ratchet und keine Vollabdeckung:** ein Scan über den Bestand
findet ~90 Funktionen, die irgendeine Kennzahl erzeugen, ohne eine Zerlegung
mitzuliefern. Die meisten sind harmlos (Serialisierer, Einzelwert-Features,
Konfigurationsschwellen), einige nicht. Alle auf einmal zu erzwingen würde
einen Test erzeugen, der bei jedem Lauf ~90 Meldungen ausspuckt — und eine
Wache, die immer schreit, wird ignoriert (die TL-008-Lehre, die schon zweimal
teuer war). Stattdessen: der Ist-Zustand ist als Baseline eingefroren und
sichtbar, **wachsen darf er nicht**. Wer eine neue Kennzahl baut, liefert ihre
Zerlegung mit — oder trägt sie bewusst und begründet in die Baseline ein.

Die Baseline ist eine Schuldenliste, kein Freibrief: sie soll schrumpfen.
Deshalb schlägt der Test auch an, wenn ein Eintrag verschwindet, ohne dass die
Baseline nachgezogen wurde — sonst verwässert sie still.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

# Tokens werden an Wortgrenzen verglichen, NICHT als Substring: sonst matcht
# "gene-rate-d" auf "rate" und der Scan meldet `generated_at_utc` als Kennzahl.
AGG_TOKENS = frozenset(
    {
        "rate",
        "pct",
        "precision",
        "mean",
        "hit",
        "hits",
        "share",
        "avg",
        "expectancy",
        "ratio",
        "winrate",
        "accuracy",
        # --- Geld- und Risiko-Kennzahlen (ergänzt 2026-08-17) ---------------
        # Befund: die zwölf ursprünglichen Tokens trafen KEINE einzige
        # Geld-Kennzahl. `p_mu_net_positive`, `fee_drag`, `sharpe`,
        # `median_bps`, `pnl_usd` und `drawdown` liefen allesamt am Ratchet
        # vorbei — ausgerechnet die Zahlen, für die die Direktive
        # "kein Aggregat ohne Zerlegung" (2026-08-08) geschrieben wurde.
        # `mu` steht für die canonical-edge-Kennzahl `p_mu_net_positive`;
        # als eigenes Wort ist es im Bestand eindeutig (0 Falsch-Positive).
        "pnl",
        "bps",
        "sharpe",
        "drawdown",
        "fee",
        "fees",
        "drag",
        "edge",
        "equity",
        "profit",
        "loss",
        "exposure",
        "turnover",
        "slippage",
        "mu",
    }
)
DECO_TOKENS = frozenset(
    {"decomposition", "assessment", "cells", "diagnostics", "concentration", "flags"}
)
DECO_PREFIXES = ("by_", "per_", "without_", "leave_one")

# ``app/execution`` ergänzt 2026-08-17: dort entstehen die Geld-Kennzahlen
# (Close-PnL, Entry-Fee-Matching, Reconciliation) — die Quelle der Zahlen, die
# der Ratchet bis dahin nicht einmal als Kennzahl erkannt hat.
# ``app/cli`` bleibt bewusst DRAUSSEN: die CLI rendert überwiegend, was andere
# Schichten berechnet haben, und brächte ~20 weitere Baseline-Einträge mit dem
# geringsten Erkenntnisgewinn. Eine Schuldenliste, die niemand mehr liest, ist
# der Fehler, den dieser Ratchet gerade vermeiden soll.
SCAN_ROOTS = (
    "app/research",
    "app/observability",
    "app/api/routers",
    "app/alerts",
    "app/execution",
)
# Das Modul IST die Zerlegung — es gegen sich selbst zu prüfen ergibt nichts.
SKIP_FILES = frozenset({"app/research/decomposition.py"})
# Reine Deserialisierung erzeugt keine Kennzahl, sie liest eine zurück.
SKIP_FUNCTIONS = frozenset({"from_dict", "from_json", "parse", "_parse"})

BASELINE_PATH = Path(__file__).parent / "aggregate_decomposition_baseline.json"


def _tokens(key: str) -> set[str]:
    return set(re.split(r"[_\W]+", key.lower()))


def _is_aggregate(key: str) -> bool:
    return bool(_tokens(key) & AGG_TOKENS)


def _is_decomposition(key: str) -> bool:
    return bool(_tokens(key) & DECO_TOKENS) or key.lower().startswith(DECO_PREFIXES)


def _emitted_keys(fn: ast.AST) -> list[str]:
    """Alle Schlüssel, die diese Funktion erzeugt — Dict-Literale und kwargs.

    kwargs zählen mit, weil Dataclass-Konstruktoren (``PaperQualitySnapshot(...)``)
    Kennzahlen genauso ausliefern wie ein Dict-Literal.
    """
    out: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            out += [
                k.value
                for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            ]
        elif isinstance(node, ast.Call):
            out += [kw.arg for kw in node.keywords if kw.arg]
    return out


def scan_source(source: str, rel: str) -> set[str]:
    """Kern des Scans über EINEN Modul-Quelltext — separat testbar.

    Getrennt von :func:`scan_repo`, damit die Erkennung gegen synthetischen
    Code positiv-kontrolliert werden kann. Ohne diese Kontrolle wäre ein
    grüner Ratchet nicht von einem kaputten Scanner zu unterscheiden
    (Lehre aus ``feedback_prereg_evaluator_must_be_committed``).
    """
    found: set[str] = set()
    try:
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensiv
        return found
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if fn.name in SKIP_FUNCTIONS:
            continue
        keys = _emitted_keys(fn)
        if not keys:
            continue
        if any(_is_aggregate(k) for k in keys) and not any(_is_decomposition(k) for k in keys):
            found.add(f"{rel}::{fn.name}")
    return found


def scan_repo(root: Path) -> set[str]:
    """``datei::funktion`` für jede Funktion mit Kennzahl, aber ohne Zerlegung."""
    found: set[str] = set()
    for rel_root in SCAN_ROOTS:
        base = root / rel_root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if rel in SKIP_FILES:
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:  # pragma: no cover - defensiv
                continue
            found |= scan_source(source, rel)
    return found


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_no_new_aggregate_without_decomposition() -> None:
    """Der Bestand darf schrumpfen, aber nicht wachsen."""
    baseline = set(json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["entries"])
    found = scan_repo(_repo_root())

    added = sorted(found - baseline)
    assert not added, (
        "Neue Kennzahl(en) ohne Zerlegung (Direktive 2026-08-08 — kein Aggregat "
        "ohne Zerlegung):\n  " + "\n  ".join(added) + "\n\n"
        "Entweder eine Zerlegung mitliefern (app/research/decomposition.py: "
        "`decompose_rate` / `decompose_mean` / `assess_group_table`) ODER — wenn "
        "es nachweislich kein urteilstragendes Aggregat ist — bewusst in "
        f"{BASELINE_PATH.name} eintragen."
    )


def test_baseline_has_no_stale_entries() -> None:
    """Behobene Altlasten muessen aus der Baseline verschwinden.

    Ohne diese Richtung verwaessert die Schuldenliste still: Eintraege bleiben
    stehen, obwohl der Fall laengst geloest ist, und niemand sieht den
    Fortschritt.
    """
    baseline = set(json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["entries"])
    found = scan_repo(_repo_root())

    stale = sorted(baseline - found)
    assert not stale, (
        "Baseline-Eintraege, die es nicht mehr gibt (behoben oder umbenannt) — "
        f"bitte aus {BASELINE_PATH.name} entfernen:\n  " + "\n  ".join(stale)
    )


def test_scanner_uses_word_boundaries_not_substrings() -> None:
    """``generated_at_utc`` darf NICHT als Kennzahl gelten.

    Der erste Entwurf des Scans matchte Substrings und meldete `generated`
    wegen des enthaltenen "rate". Ein Scanner mit solchen Fehlalarmen waere
    wertlos — deshalb ist die Wortgrenze hier festgehalten.
    """
    assert not _is_aggregate("generated_at_utc")
    assert not _is_aggregate("operating_mode")
    assert _is_aggregate("win_rate")
    assert _is_aggregate("precision_pct")
    assert _is_aggregate("mean_net_bps")


def test_decomposition_keys_are_recognised() -> None:
    assert _is_decomposition("decomposition")
    assert _is_decomposition("by_symbol")
    assert _is_decomposition("per_source")
    assert _is_decomposition("win_rate_by_symbol_assessment")
    assert _is_decomposition("without_top")
    assert not _is_decomposition("win_rate")


def test_wave1_and_wave2_modules_are_clean() -> None:
    """Die bereits erfassten Produzenten duerfen NICHT in der Baseline stehen.

    Sie sind der Beweis, dass die Regel erfuellbar ist — taeuchten sie in der
    Schuldenliste auf, waere die Ausweitung nur behauptet.
    """
    baseline = set(json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["entries"])
    must_be_clean = (
        "app/research/quote_evals.py::evaluate_technical_paper_precision",
        "app/research/quote_evals.py::evaluate_execution_translation",
        "app/research/quote_evals.py::evaluate_hit_to_win_conversion",
        "app/observability/paper_quality_snapshot.py::build_paper_quality_snapshot",
        "app/research/news_signal_eval.py::evaluate_cohort",
    )
    for entry in must_be_clean:
        assert entry not in baseline, (
            f"{entry} liefert eine Zerlegung — gehoert nicht in die Baseline"
        )


# ── Positivkontrolle: ein gruener Ratchet muss BEWEISBAR wachsam sein ────────


def test_positive_control_scanner_catches_a_bare_aggregate() -> None:
    """Eine neue Kennzahl ohne Zerlegung MUSS erkannt werden.

    Ohne diese Kontrolle waere ein gruener Ratchet nicht von einem kaputten
    Scanner zu unterscheiden — genau der Fehler, der beim C1-Evaluator schon
    einmal ein nicht reproduzierbares Verdikt erzeugt hat.
    """
    src = """
def build_report():
    return {"win_rate": 0.42, "sample_n": 10}
"""
    assert scan_source(src, "x.py") == {"x.py::build_report"}


def test_positive_control_scanner_accepts_an_aggregate_with_decomposition() -> None:
    """Mit Zerlegung MUSS der Scanner schweigen — sonst ist er nicht benutzbar."""
    src = """
def build_report():
    return {"win_rate": 0.42, "by_symbol": {"BTC": 1}}
"""
    assert scan_source(src, "x.py") == set()


def test_positive_control_kwargs_count_as_emitted_keys() -> None:
    """Dataclass-Konstruktoren liefern Kennzahlen wie Dict-Literale aus."""
    src = """
def build():
    return Snapshot(win_rate=0.5, closures_total=3)
"""
    assert scan_source(src, "x.py") == {"x.py::build"}

    src_ok = """
def build():
    return Snapshot(win_rate=0.5, win_rate_by_symbol_assessment={})
"""
    assert scan_source(src_ok, "x.py") == set()


def test_deserialisers_are_not_treated_as_producers() -> None:
    """``from_dict`` liest eine Kennzahl zurueck, es erzeugt keine."""
    src = """
def from_dict(d):
    return {"mean_net_bps": d["mean_net_bps"]}
"""
    assert scan_source(src, "x.py") == set()
