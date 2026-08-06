"""Live-Operator-Board: offene Prä-Regs statt handgepflegtem Snapshot.

Der Operator-Befund (2026-07-30): das Board meldete „Stand 2026-07-12 · 18 Tage
alt — veraltet, bitte pflegen", obwohl die kuratierte Datei ausschliesslich
ABGESCHLOSSENE Phasen enthielt. Zwei Defekte in einem: (a) es gab keine
live-berechnete Sektion, (b) der Stale-Alarm feuerte auf einem reinen
Chronik-Log, das per Definition nicht veralten kann.

Getestet wird das VERHALTEN des reinen Assemblers (kein I/O, keine DB), plus
die Stale-Semantik. Die Reife-Zahlen kommen als Rohwerte herein — genau wie bei
``build_n_overview``.
"""

from __future__ import annotations

from app.observability.operator_board_live import (
    build_live_board,
    curated_is_stale,
    open_preregs,
)


def _reg(pid: str, name: str, target: int, created: str = "2026-07-01T00:00:00+00:00") -> dict:
    return {
        "prereg_id": pid,
        "name": name,
        "direction": "neutral",
        "horizon": "1d",
        "success_criteria": "x>0",
        "sample_size_target": target,
        "created_at_utc": created,
    }


# --------------------------------------------------------------------------- #
# open_preregs — register minus resolve
# --------------------------------------------------------------------------- #


def test_open_preregs_excludes_resolved_claims() -> None:
    """Ein aufgelöster Claim ist kein offener Punkt mehr."""
    ledger = [_reg("aaa", "claim_a", 100), _reg("bbb", "claim_b", 200)]
    verdicts = [{"prereg_id": "aaa", "verdict": "NOT_MET"}]

    rows = open_preregs(ledger, verdicts)

    assert [r["prereg_id"] for r in rows] == ["bbb"]


def test_open_preregs_insufficient_n_stays_open() -> None:
    """INSUFFICIENT_N ist KEIN terminales Verdikt — der Claim bleibt offen.

    Sonst verschwindet ein Claim aus dem Board, obwohl er nur noch reift.
    """
    ledger = [_reg("aaa", "claim_a", 100)]
    verdicts = [{"prereg_id": "aaa", "verdict": "INSUFFICIENT_N"}]

    rows = open_preregs(ledger, verdicts)

    assert [r["prereg_id"] for r in rows] == ["aaa"]
    assert rows[0]["last_verdict"] == "INSUFFICIENT_N"


def test_open_preregs_tolerates_corrupt_rows() -> None:
    """Eine kaputte Zeile darf das Panel nie kippen (read-only-safe)."""
    ledger = [{"nope": 1}, _reg("bbb", "claim_b", 200), None]
    verdicts = [{"garbage": True}]

    rows = open_preregs(ledger, verdicts)  # type: ignore[arg-type]

    assert [r["prereg_id"] for r in rows] == ["bbb"]


def test_open_preregs_dedupes_repeat_registration() -> None:
    """Identische Re-Registrierung (gleiche id) erscheint EINMAL."""
    ledger = [_reg("aaa", "claim_a", 100), _reg("aaa", "claim_a", 100)]

    rows = open_preregs(ledger, [])

    assert len(rows) == 1


# --------------------------------------------------------------------------- #
# build_live_board — Reife-Anreicherung + Priorisierung
# --------------------------------------------------------------------------- #


def test_live_board_marks_due_claim_as_due() -> None:
    """Reife-Ziel erreicht → handlungsbeduerftig, mit n/Ziel sichtbar.

    Ohne ``state`` im Rohsatz ist das der konservative Zustand ``eval_check``:
    Ziel-n nur im Proxy erreicht, der exakte Evaluator muss laufen.
    """
    board = build_live_board(
        ledger=[_reg("aaa", "directional_news_hedged_1d_drift", 300)],
        verdicts=[],
        maturity_rows=[
            {
                "name": "directional_news_hedged_1d_drift",
                "n_proxy": 300,
                "n_target": 300,
                "due": True,
                "per_source": {"all": 300},
            }
        ],
    )

    (row,) = board["open_preregs"]
    assert row["state"] == "eval_check"
    assert row["n_proxy"] == 300 and row["n_target"] == 300
    assert board["due_count"] == 1
    assert board["judgeable_count"] == 0


def test_live_board_maturing_claim_reports_progress() -> None:
    """Noch nicht reif → 'maturing' mit ehrlichem Fortschritt, NICHT fällig."""
    board = build_live_board(
        ledger=[_reg("aaa", "directional_news_hedged_1d_drift", 300)],
        verdicts=[],
        maturity_rows=[
            {
                "name": "directional_news_hedged_1d_drift",
                "n_proxy": 247,
                "n_target": 300,
                "due": False,
                "per_source": {"all": 247},
            }
        ],
    )

    (row,) = board["open_preregs"]
    assert row["state"] == "maturing"
    assert row["progress_pct"] == 82.3
    assert board["due_count"] == 0


def test_live_board_claim_without_counter_is_honest() -> None:
    """Kein Reife-Spec → 'no_counter', keine erfundene Zahl.

    Lehre kai_news_direction_v2_immature: fehlende Reife-Info ist kein PASS und
    auch kein FAIL — sie ist 'nicht gezählt'.
    """
    board = build_live_board(
        ledger=[_reg("zzz", "m3_external_validation_first_signal", 1)],
        verdicts=[],
        maturity_rows=[],
    )

    (row,) = board["open_preregs"]
    assert row["state"] == "no_counter"
    assert row["n_proxy"] is None and row["progress_pct"] is None


def test_live_board_sorts_due_first_then_by_progress() -> None:
    """Handlungsreihenfolge: fällig zuerst, dann am weitesten gereift."""
    board = build_live_board(
        ledger=[
            _reg("a1", "far", 100),
            _reg("a2", "due_one", 100),
            _reg("a3", "near", 100),
            _reg("a4", "uncounted", 100),
        ],
        verdicts=[],
        maturity_rows=[
            {"name": "far", "n_proxy": 10, "n_target": 100, "due": False, "per_source": {}},
            {"name": "due_one", "n_proxy": 100, "n_target": 100, "due": True, "per_source": {}},
            {"name": "near", "n_proxy": 90, "n_target": 100, "due": False, "per_source": {}},
        ],
    )

    assert [r["name"] for r in board["open_preregs"]] == ["due_one", "near", "far", "uncounted"]


def test_live_board_counts_and_flags_are_consistent() -> None:
    """open_count spiegelt die Liste; has_content nur wenn wirklich Inhalt da ist."""
    empty = build_live_board(ledger=[], verdicts=[], maturity_rows=[])
    assert empty["open_count"] == 0 and empty["has_content"] is False

    filled = build_live_board(ledger=[_reg("aaa", "c", 5)], verdicts=[], maturity_rows=[])
    assert filled["open_count"] == 1 and filled["has_content"] is True


def test_live_board_maturity_never_marks_a_claim_passed() -> None:
    """Reife ist Upper-Bound-Proxy: 'due' heisst 'Eval fahren', nie 'bestanden'."""
    board = build_live_board(
        ledger=[_reg("aaa", "x", 300)],
        verdicts=[],
        maturity_rows=[
            {"name": "x", "n_proxy": 999, "n_target": 300, "due": True, "per_source": {}}
        ],
    )

    (row,) = board["open_preregs"]
    assert row["state"] == "eval_check"
    assert "verdict" not in row or row.get("last_verdict") is None
    assert "Evaluator" in row["action"] and "kein PASS" in row["action"]
    assert board["judgeable_count"] == 0


def test_eval_check_due_is_not_reported_as_judgeable() -> None:
    """P0-01-Doktrin: ``EVAL_CHECK_DUE`` heisst „exakten Evaluator fahren", NIE „urteilbar".

    ``compute_maturity`` unterscheidet seit dem 07-30-Review drei Zustände und
    warnt im Code ausdrücklich: das Kompat-Bit ``due`` bedeutet NIE urteilbar.
    Das Board darf beide Zustände darum nicht auf ein „fällig" kollabieren —
    sonst liest der Operator einen Proxy-Treffer als Urteilsreife.
    """
    board = build_live_board(
        ledger=[_reg("aaa", "proxy_claim", 300)],
        verdicts=[],
        maturity_rows=[
            {
                "name": "proxy_claim",
                "n_proxy": 351,
                "n_target": 300,
                "state": "EVAL_CHECK_DUE",
                "due": True,
                "per_source": {"stories": 351, "events": 1167},
            }
        ],
    )

    (row,) = board["open_preregs"]
    assert row["state"] == "eval_check"
    assert "Evaluator" in row["action"]
    assert board["judgeable_count"] == 0
    assert board["eval_check_count"] == 1


def test_judgeable_state_is_distinguished() -> None:
    """Nur wenn die Zählung SELBST der exakte Evaluator ist, ist der Claim urteilbar."""
    board = build_live_board(
        ledger=[_reg("aaa", "exact_claim", 200)],
        verdicts=[],
        maturity_rows=[
            {
                "name": "exact_claim",
                "n_proxy": 200,
                "n_target": 200,
                "state": "JUDGEABLE",
                "due": True,
                "per_source": {"resolved": 200},
            }
        ],
    )

    (row,) = board["open_preregs"]
    assert row["state"] == "judgeable"
    assert board["judgeable_count"] == 1
    assert board["eval_check_count"] == 0


def test_verified_truth_resolution_removes_claim_from_open_board() -> None:
    """Truth-Kette schließt auch ohne redundante prereg_verdicts-Zeile.

    Realfall ND-v2: seq 73 war terminal attestiert, aber im separaten
    ``prereg_verdicts.jsonl`` fehlte der Eintrag. Das Board darf ihn deshalb
    nicht weiter als offen ausweisen.
    """
    board = build_live_board(
        ledger=[_reg("b20ef1487ccba99d", "directional_news_hedged_1d_drift_v2", 300)],
        verdicts=[],
        maturity_rows=[
            {
                "name": "directional_news_hedged_1d_drift",
                "prereg_id": "b20ef1487ccba99d",
                "n_proxy": 302,
                "n_target": 300,
                "state": "RESOLVED",
                "state_source": "truth_ledger",
                "due": False,
                "resolution": {"status": "resolved", "verdict_class": "NOT_MET", "seq": 73},
                "per_source": {"stories": 302},
            }
        ],
    )

    assert board["open_preregs"] == []
    assert board["open_count"] == 0
    assert board["has_content"] is False


def test_resolution_evidence_hold_is_visible_but_never_due() -> None:
    """Inkonsistente Truth-Evidenz wird nicht als neuer Eval-Auftrag verkauft."""
    board = build_live_board(
        ledger=[_reg("aaa", "claim", 300)],
        verdicts=[],
        maturity_rows=[
            {
                "name": "claim",
                "prereg_id": "aaa",
                "n_proxy": 300,
                "n_target": 300,
                "state": "RESOLUTION_HOLD",
                "state_source": "truth_ledger",
                "due": False,
                "resolution": {"status": "conflict"},
                "per_source": {},
            }
        ],
    )

    (row,) = board["open_preregs"]
    assert row["state"] == "evidence_hold"
    assert "HOLD" in row["action"]
    assert board["evidence_hold_count"] == 1
    assert board["due_count"] == 0


def test_legacy_rows_without_state_still_work() -> None:
    """Reihen ohne ``state`` (Alt-Format) fallen auf das ``due``-Bit zurück.

    Konservativ: ohne Zustandsangabe wird der schwächere Zustand angenommen
    (Evaluator fahren), nie der stärkere.
    """
    board = build_live_board(
        ledger=[_reg("aaa", "legacy", 100)],
        verdicts=[],
        maturity_rows=[
            {"name": "legacy", "n_proxy": 100, "n_target": 100, "due": True, "per_source": {}}
        ],
    )

    (row,) = board["open_preregs"]
    assert row["state"] == "eval_check"
    assert board["judgeable_count"] == 0


def test_live_board_joins_maturity_by_prereg_id_not_name() -> None:
    """Reife hängt am versiegelten Claim, auch wenn der Spec-Name abweicht.

    Realfall 2026-07-30: der Spec heisst ``directional_news_hedged_1d_drift``,
    die zugehörige Prä-Reg ``…_drift_v2`` (b20ef1487ccba99d) — daneben existiert
    ein v1-Claim mit EXAKT dem Spec-Namen. Ein Namens-Join hätte die Reife dem
    v1-Claim zugeschrieben und den fälligen v2-Claim als ungezählt gemeldet.
    """
    board = build_live_board(
        ledger=[
            _reg("4a3b1b0c5a94b73c", "directional_news_hedged_1d_drift", 300),
            _reg("b20ef1487ccba99d", "directional_news_hedged_1d_drift_v2", 300),
        ],
        verdicts=[],
        maturity_rows=[
            {
                "name": "directional_news_hedged_1d_drift",
                "prereg_id": "b20ef1487ccba99d",
                "n_proxy": 247,
                "n_target": 300,
                "due": False,
                "per_source": {"all": 247},
            }
        ],
    )

    by_id = {r["prereg_id"]: r for r in board["open_preregs"]}
    # Die Reife gehört dem v2-Claim …
    assert by_id["b20ef1487ccba99d"]["state"] == "maturing"
    assert by_id["b20ef1487ccba99d"]["n_proxy"] == 247
    # … und der namensgleiche v1-Claim bleibt ehrlich ungezählt.
    assert by_id["4a3b1b0c5a94b73c"]["state"] == "no_counter"
    assert by_id["4a3b1b0c5a94b73c"]["n_proxy"] is None


def test_live_board_name_join_still_works_without_prereg_id() -> None:
    """Rückfall: Specs ohne prereg_id joinen weiterhin über den Namen."""
    board = build_live_board(
        ledger=[_reg("aaa", "legacy_claim", 100)],
        verdicts=[],
        maturity_rows=[
            {"name": "legacy_claim", "n_proxy": 100, "n_target": 100, "due": True, "per_source": {}}
        ],
    )

    (row,) = board["open_preregs"]
    assert row["state"] == "eval_check" and row["n_proxy"] == 100


# --------------------------------------------------------------------------- #
# curated_is_stale — der eigentliche Fehlalarm
# --------------------------------------------------------------------------- #


def test_chronicle_of_done_phases_is_never_stale() -> None:
    """DER Operator-Bug: 10 erledigte Phasen, 0 Todos → kein 'veraltet'-Alarm.

    Ein Log abgeschlossener Phasen kann nicht veralten; nur OFFENE kuratierte
    Punkte können ungepflegt sein.
    """
    curated = {
        "todos": [],
        "improvements": [],
        "phases": [{"label": "P0", "status": "done"}, {"label": "P1", "status": "done"}],
    }

    assert curated_is_stale(curated, age_days=18) is False


def test_open_curated_item_still_goes_stale() -> None:
    """Ein OFFENER kuratierter Punkt von vor 18 Tagen ist sehr wohl ungepflegt."""
    curated = {"todos": [{"text": "irgendwas"}], "improvements": [], "phases": []}

    assert curated_is_stale(curated, age_days=18) is True
    assert curated_is_stale(curated, age_days=3) is False


def test_active_phase_counts_as_open_curated_content() -> None:
    """status != done → offener Punkt → Frische zählt."""
    curated = {"todos": [], "improvements": [], "phases": [{"label": "X", "status": "active"}]}

    assert curated_is_stale(curated, age_days=18) is True


def test_unknown_age_is_not_reported_stale() -> None:
    """Unparsebares Datum → keine erfundene Alterung."""
    curated = {"todos": [{"text": "offen"}], "improvements": [], "phases": []}

    assert curated_is_stale(curated, age_days=None) is False
