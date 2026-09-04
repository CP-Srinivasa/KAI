"""Eigenstaendige Nachweise fuer die S6-Haertungen — nicht der Bericht, der Code.

Der Codex-Lauf wurde durch ein Nutzungslimit unterbrochen, waehrend genau diese
Punkte in Arbeit waren. Sie gelten hier als UNBEWIESEN, bis ein Test sie zeigt.

Drei Fragen tragen die Datei:

* Ein Retry ist ein Vorgang, keine zweite Stichprobe. Wer physische Versuche
  als unabhaengige Beobachtungen zaehlt, bekommt eine Erfolgsquote, die den
  Retry belohnt: je oefter etwas schiefging, desto mehr Zeilen, und die
  gelungene letzte zieht den Schnitt hoch.
* Fehlende Qualitaet ist nicht Gleichstand. `0.0` waere eine Messung, `null`
  ist das Eingestaendnis, dass keine vorliegt.
* Belegt ist nicht erlaubt. Die Consensus-Route kann vollstaendig belegt sein
  und darf trotzdem nie PRIMARY werden — und der Exit-Code eines Programms ist
  keine Freigabe.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from scripts.litellm_shadow_eval.engine import evaluate
from scripts.litellm_shadow_eval.loader import load_evidence
from scripts.litellm_shadow_eval.models import GraduationPolicy, GraduationStatus
from scripts.litellm_shadow_eval.policy import PolicyError, runtime_flags_from_dict
from scripts.litellm_shadow_eval.reporting import canonical_json, comparable_json

from tests.unit.litellm_shadow_eval.helpers import proven_flags, row, write_jsonl

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _evaluate(path: Path, *, minimum: int = 1, **policy: Any):
    return evaluate(
        [path],
        GraduationPolicy(minimum_sample_count=minimum, **policy),
        proven_flags(),
        clock=lambda: NOW,
    )


# ---------------------------------------------------------------------------
# 29/30 — Retries sind ein Vorgang, keine Stichproben.
# ---------------------------------------------------------------------------


def _retried_pair(number: int, attempts: int) -> list[dict[str, Any]]:
    """Eine logische SHADOW-Seite, verteilt auf mehrere physische Zeilen."""
    zeilen = [row("DIRECT", number, quality_score=0.8)]
    for attempt in range(1, attempts + 1):
        letzter = attempt == attempts
        zeilen.append(
            row(
                "SHADOW",
                number,
                quality_score=0.8,
                attempt_count=attempt,
                retry_count=attempt - 1,
                success=letzter,
                error_class=None if letzter else "timeout",
                outcome="success" if letzter else "fallthrough",
                latency_ms=5.0,
            )
        )
    return zeilen


def test_mehrere_versuchszeilen_werden_eine_logische_seite(tmp_path: Path) -> None:
    zeilen = _retried_pair(0, attempts=3)
    report = _evaluate(write_jsonl(tmp_path / "retry.jsonl", zeilen))
    metrics = report.metrics["standard"]

    assert report.record_count == 4, "vier physische Zeilen"
    assert metrics.complete_pair_count == 1, "eine logische Auswertung"
    assert metrics.invalid_record_count == 0, "ein Retry ist kein Fehler in den Daten"


def test_ein_retry_blaeht_die_stichprobe_nicht_auf(tmp_path: Path) -> None:
    """Sonst belohnt die Erfolgsquote genau das, was sie messen soll."""
    ohne = write_jsonl(
        tmp_path / "ohne.jsonl",
        [zeile for number in range(3) for zeile in _retried_pair(number, attempts=1)],
    )
    mit = write_jsonl(
        tmp_path / "mit.jsonl",
        [zeile for number in range(3) for zeile in _retried_pair(number, attempts=3)],
    )
    a = _evaluate(ohne).metrics["standard"]
    b = _evaluate(mit).metrics["standard"]

    assert a.sample_count == b.sample_count == 3
    assert a.complete_pair_count == b.complete_pair_count == 3
    assert b.shadow_success_rate == 1.0, "der letzte Versuch traegt das Ergebnis"


def test_die_physischen_versuche_bleiben_messbar(tmp_path: Path) -> None:
    report = _evaluate(write_jsonl(tmp_path / "retry.jsonl", _retried_pair(0, attempts=3)))
    metrics = report.metrics["standard"]

    assert metrics.retry_distribution == {"2": 1}, "zwei Wiederholungen, einmal beobachtet"
    assert metrics.shadow_retry_rate == 1.0


def test_die_fehler_der_zwischenversuche_gehen_nicht_verloren(tmp_path: Path) -> None:
    """Nach `timeout, timeout, ok` ist `ok` das Ergebnis und `keine Fehler` falsch."""
    report = _evaluate(write_jsonl(tmp_path / "retry.jsonl", _retried_pair(0, attempts=3)))
    metrics = report.metrics["standard"]

    assert metrics.error_distribution_shadow == {"UNKNOWN": 1}, "die Seite endete fehlerfrei"
    assert metrics.attempt_error_distribution_shadow == {"timeout": 2}, "der Weg dorthin nicht"


def test_unbekannte_kosten_eines_einzigen_versuchs_verunreinigen_die_summe(
    tmp_path: Path,
) -> None:
    """Ein Versuch ohne Preisangabe macht die Gesamtkosten unbekannt, nicht kleiner."""
    zeilen = _retried_pair(0, attempts=2)
    zeilen[1].update({"cost_usd": None, "cost_known": False})
    report = _evaluate(write_jsonl(tmp_path / "retry.jsonl", zeilen))
    metrics = report.metrics["standard"]

    assert metrics.cost_known_rate == 0.0
    assert metrics.shadow_mean_cost_usd is None
    assert metrics.unknown_cost_count == 1


def test_eine_luecke_in_der_versuchsfolge_ist_ein_duplikat_keine_zusammenfassung(
    tmp_path: Path,
) -> None:
    """Ohne exakte Folge 1..n ist nicht belegbar, dass es EIN Vorgang war."""
    zeilen = _retried_pair(0, attempts=2)
    zeilen[2]["attempt_count"] = 5
    zeilen[2]["retry_count"] = 4
    report = _evaluate(write_jsonl(tmp_path / "luecke.jsonl", zeilen))

    codes = {issue.code for issue in report.validation_issues}
    assert "DUPLICATE_SHADOW" in codes
    assert report.metrics["standard"].complete_pair_count == 0


# ---------------------------------------------------------------------------
# 31/32 — Provenienz nennt die Eingabe, nicht den Operator.
# ---------------------------------------------------------------------------


def test_der_bericht_verraet_keinen_posix_pfad(tmp_path: Path) -> None:
    verzeichnis = tmp_path / "home" / "kai" / "geheimprojekt"
    verzeichnis.mkdir(parents=True)
    pfad = write_jsonl(verzeichnis / "evidence.jsonl", [row("DIRECT"), row("SHADOW")])
    text = canonical_json(_evaluate(pfad))

    assert "geheimprojekt" not in text
    assert "/home/" not in text
    assert str(tmp_path.as_posix()) not in text
    assert "evidence.jsonl" in text, "der logische Name bleibt, der Pfad geht"


def test_der_bericht_verraet_keinen_windows_pfad(tmp_path: Path) -> None:
    pfad = write_jsonl(tmp_path / "evidence.jsonl", [row("DIRECT"), row("SHADOW")])
    text = canonical_json(_evaluate(pfad))

    assert "C:\\Users" not in text and "C:/Users" not in text
    assert "\\\\" not in text, "keine escapten Windows-Trenner"
    assert str(tmp_path) not in text
    for teil in tmp_path.parts[:-1]:
        if len(teil) > 3 and teil not in {"evidence.jsonl"}:
            assert teil not in text, teil


@pytest.mark.parametrize(
    "pfad",
    ["/home/sascha/kai/evidence.jsonl", "C:\\Users\\sascha\\kai\\evidence.jsonl"],
)
def test_auch_eine_unlesbare_eingabe_nennt_nur_den_dateinamen(pfad: str) -> None:
    loaded = load_evidence([Path(pfad)])

    (issue,) = loaded.issues
    assert issue.code == "INPUT_UNREADABLE"
    assert "sascha" not in issue.record_ref
    assert issue.record_ref.endswith("evidence.jsonl")
    assert all("sascha" not in key for key in loaded.input_files)


# ---------------------------------------------------------------------------
# 33 — Fehlende Qualitaet ist null, nicht 0, und kein READY.
# ---------------------------------------------------------------------------


def test_fehlende_qualitaet_erscheint_als_null_nicht_als_null_komma_null(
    tmp_path: Path,
) -> None:
    pfad = write_jsonl(tmp_path / "ohne.jsonl", [row("DIRECT"), row("SHADOW")])
    payload = _evaluate(pfad).to_dict()["metrics"]["standard"]

    assert payload["quality_status"] == "NOT_MEASURED"
    assert payload["quality_sample_count"] == 0
    for feld in (
        "quality_direct_mean",
        "quality_shadow_mean",
        "quality_delta_mean",
        "quality_delta_median",
    ):
        assert payload[feld] is None, feld
        assert payload[feld] != 0


def test_ohne_qualitaetsbeleg_gibt_es_kein_automatisches_ready(tmp_path: Path) -> None:
    """Sonst entscheidet ueber Reife, was niemand gemessen hat."""
    pfad = write_jsonl(tmp_path / "ohne.jsonl", [row("DIRECT"), row("SHADOW")])
    entscheidung = _evaluate(pfad).decisions["standard"]

    assert entscheidung.status is GraduationStatus.NOT_READY
    assert "QUALITY_NOT_MEASURED" in entscheidung.reasons
    assert entscheidung.primary_ready is False


def test_qualitaet_darf_beratend_sein_aber_nur_ausdruecklich(tmp_path: Path) -> None:
    """Die Ausnahme steht in der Politik und damit im Hash — nachweisbar gewollt."""
    pfad = write_jsonl(tmp_path / "ohne.jsonl", [row("DIRECT"), row("SHADOW")])
    entscheidung = _evaluate(pfad, require_quality_evidence=False).decisions["standard"]

    assert entscheidung.status is GraduationStatus.READY
    assert "QUALITY_NOT_MEASURED_ADVISORY" in entscheidung.reasons


def test_die_flachen_qualitaetsfelder_heissen_wie_vereinbart(tmp_path: Path) -> None:
    zeilen = [
        row("DIRECT", quality_score=0.5),
        row("SHADOW", quality_score=0.9),
    ]
    payload = _evaluate(write_jsonl(tmp_path / "q.jsonl", zeilen)).to_dict()
    metrics = payload["metrics"]["standard"]

    for feld in (
        "quality_status",
        "quality_sample_count",
        "quality_direct_mean",
        "quality_shadow_mean",
        "quality_delta_mean",
        "quality_delta_median",
        "shadow_better_count",
        "direct_better_count",
        "equal_count",
    ):
        assert feld in metrics, feld
    assert metrics["quality_status"] == "MEASURED"
    assert metrics["shadow_better_count"] == 1
    assert metrics["direct_better_count"] == metrics["equal_count"] == 0


# ---------------------------------------------------------------------------
# 34/35 — Determinismus, ohne den Zeitstempel mitzuzaehlen.
# ---------------------------------------------------------------------------


def test_der_erzeugungszeitpunkt_faellt_beim_vergleich_heraus(tmp_path: Path) -> None:
    pfad = write_jsonl(tmp_path / "e.jsonl", [row("DIRECT"), row("SHADOW")])
    frueh = evaluate(
        [pfad], GraduationPolicy(minimum_sample_count=1), proven_flags(), clock=lambda: NOW
    )
    spaet = evaluate(
        [pfad],
        GraduationPolicy(minimum_sample_count=1),
        proven_flags(),
        clock=lambda: datetime(2027, 1, 1, tzinfo=UTC),
    )

    assert canonical_json(frueh) != canonical_json(spaet), "der Zeitpunkt steht im Bericht"
    assert comparable_json(frueh) == comparable_json(spaet)
    assert "generated_at" not in comparable_json(frueh)


def test_dieselben_zeilen_in_anderer_reihenfolge_ergeben_dieselbe_auswertung(
    tmp_path: Path,
) -> None:
    zeilen = [row(side, number) for number in range(6) for side in ("DIRECT", "SHADOW")]
    # Dieselbe Datei, zweimal beschrieben: so unterscheidet sich ausschliesslich
    # die Reihenfolge. Zwei Dateinamen wuerden auch die Provenienz aendern —
    # und der Test wuerde dann etwas anderes messen als er behauptet.
    pfad = tmp_path / "e.jsonl"
    a = _evaluate(write_jsonl(pfad, zeilen))
    b = _evaluate(write_jsonl(pfad, list(reversed(zeilen))))

    assert comparable_json(a) == comparable_json(b)
    assert a.input_sha256 == b.input_sha256


# ---------------------------------------------------------------------------
# 36/37 — Ungueltig ist nicht dasselbe wie nicht bereit; belegt nicht dasselbe
#         wie erlaubt.
# ---------------------------------------------------------------------------


def test_ausfuehrungsautoritaet_im_schatten_ist_ungueltig_nicht_nur_unreif(
    tmp_path: Path,
) -> None:
    """Ein Schatten mit Autoritaet ist kein schwacher Beleg, sondern ein Widerspruch."""
    zeilen = [row("DIRECT", quality_score=0.8), row("SHADOW", quality_score=0.8)]
    zeilen[1]["execution_authority"] = True
    report = _evaluate(write_jsonl(tmp_path / "auth.jsonl", zeilen))

    codes = {issue.code for issue in report.validation_issues}
    assert "SHADOW_EXECUTION_AUTHORITY" in codes
    entscheidung = report.decisions["standard"]
    assert entscheidung.status is GraduationStatus.INVALID_EVIDENCE
    assert entscheidung.status is not GraduationStatus.NOT_READY
    assert entscheidung.primary_ready is False


def test_die_consensus_decke_ueberlebt_kaputte_datensaetze(tmp_path: Path) -> None:
    """Sonst hebt ausgerechnet unbrauchbare Evidenz die schaerfste Schranke auf."""

    def analyse(side: str, n: int) -> dict[str, Any]:
        return row(
            side,
            n,
            logical_route="reasoning",
            purpose="analysis",
            quality_score=0.8,
        )

    gut = [analyse(side, n) for n in range(2) for side in ("DIRECT", "SHADOW")]
    # Die einzige Consensus-Zeile ist unbrauchbar: ohne Zeitstempel faellt sie
    # aus der Auswertung. Die Route bleibt trotzdem eine Consensus-Route.
    kaputt = row(
        number=9, side="SHADOW", logical_route="reasoning", purpose="consensus", timestamp=None
    )
    report = _evaluate(write_jsonl(tmp_path / "c.jsonl", [*gut, kaputt]))

    assert report.decisions["reasoning"].consensus_route is True
    assert report.decisions["reasoning"].primary_ready is False
    assert report.to_dict()["primary_ready_routes"] == []


def test_consensus_bleibt_auch_bei_makelloser_evidenz_ohne_primary(tmp_path: Path) -> None:
    zeilen = [
        row("DIRECT", n, logical_route="reasoning", purpose="consensus", quality_score=0.9)
        for n in range(150)
    ]
    zeilen += [
        row("SHADOW", n, logical_route="reasoning", purpose="consensus", quality_score=0.95)
        for n in range(150)
    ]
    report = _evaluate(write_jsonl(tmp_path / "c.jsonl", zeilen), minimum=100)
    entscheidung = report.decisions["reasoning"]

    assert entscheidung.status is GraduationStatus.READY
    assert entscheidung.shadow_validated is True
    assert entscheidung.primary_ready is False
    assert report.to_dict()["decisions"]["reasoning"]["consensus_primary_allowed"] is False
    assert report.to_dict()["primary_ready_routes"] == []


# ---------------------------------------------------------------------------
# 38 — Kaputte Laufzeitbelege werden nicht zu einem Bestanden.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "roh",
    [
        {"off_mode_proven": "ja"},
        {"off_mode_proven": 1},
        {"rollback_proven": None},
        {"erfundenes_flag": True},
    ],
)
def test_unbrauchbare_laufzeitbelege_werden_abgelehnt(roh: dict[str, Any]) -> None:
    with pytest.raises(PolicyError):
        runtime_flags_from_dict(roh)


def test_fehlende_laufzeitbelege_sind_nicht_bewiesen(tmp_path: Path) -> None:
    """Eine leere Datei ist kein Beweis, sondern die Abwesenheit eines Beweises."""
    flags = runtime_flags_from_dict({})
    zeilen = [row("DIRECT", quality_score=0.8), row("SHADOW", quality_score=0.8)]
    report = evaluate(
        [write_jsonl(tmp_path / "e.jsonl", zeilen)],
        GraduationPolicy(minimum_sample_count=1),
        flags,
        clock=lambda: NOW,
    )
    entscheidung = report.decisions["standard"]

    assert entscheidung.status is GraduationStatus.NOT_READY
    assert "OFF_MODE_NOT_PROVEN" in entscheidung.reasons
    assert "ROLLBACK_NOT_PROVEN" in entscheidung.reasons
    assert entscheidung.primary_ready is False


# ---------------------------------------------------------------------------
# Praeregistrierung: die Schwellen sind eine Zusage, keine Stellschraube.
# ---------------------------------------------------------------------------


def test_die_praeregistrierte_politik_liegt_im_repo_und_ist_streng() -> None:
    roh = json.loads(Path("config/litellm_graduation_policy.json").read_text(encoding="utf-8"))

    assert roh["minimum_sample_count"] == 100
    assert roh["minimum_success_rate"] == 0.99
    assert roh["minimum_schema_valid_rate"] == 0.99
    for tor in (
        "require_off_mode_proven",
        "require_rollback_proven",
        "require_gateway_down_fallback_proven",
        "require_auth_no_retry_proven",
        "require_execution_gate_unchanged",
        "require_trading_gate_unchanged",
        "require_identity_observability",
        "require_quality_evidence",
    ):
        assert roh[tor] is True, tor
    assert roh["route_overrides"] == {}, "keine Route ist vorab ausgenommen"


def test_der_policy_hash_reagiert_auf_jede_lockerung() -> None:
    """Wer nachtraeglich lockert, bekommt einen anderen Hash — das ist der Zweck."""
    from scripts.litellm_shadow_eval.policy import policy_from_dict, policy_hash

    roh = json.loads(Path("config/litellm_graduation_policy.json").read_text(encoding="utf-8"))
    streng = policy_from_dict(roh)
    for feld, gelockert in (
        ("minimum_sample_count", 10),
        ("minimum_success_rate", 0.5),
        ("require_quality_evidence", False),
        ("require_identity_observability", False),
    ):
        locker = policy_from_dict({**roh, feld: gelockert})
        assert policy_hash(locker) != policy_hash(streng), feld


def test_die_politik_laesst_sich_aus_ihrer_eigenen_serialisierung_wiederherstellen() -> None:
    from scripts.litellm_shadow_eval.policy import policy_from_dict, policy_hash

    roh = json.loads(Path("config/litellm_graduation_policy.json").read_text(encoding="utf-8"))
    politik = policy_from_dict(roh)
    assert policy_hash(policy_from_dict(asdict(politik))) == policy_hash(politik)


# ---------------------------------------------------------------------------
# Das Werkzeug bleibt offline.
# ---------------------------------------------------------------------------


def test_das_paket_importiert_nichts_das_ins_netz_geht() -> None:
    """Ein Auswerter, der telefonieren kann, ist kein Auswerter mehr."""
    import ast

    verboten = {"httpx", "requests", "urllib", "socket", "http", "aiohttp", "openai", "litellm"}
    for datei in sorted(Path("scripts/litellm_shadow_eval").rglob("*.py")):
        baum = ast.parse(datei.read_text(encoding="utf-8"))
        namen: set[str] = set()
        for knoten in ast.walk(baum):
            if isinstance(knoten, ast.Import):
                namen |= {alias.name.split(".")[0] for alias in knoten.names}
            elif isinstance(knoten, ast.ImportFrom) and knoten.module:
                namen.add(knoten.module.split(".")[0])
        assert not (namen & verboten), (datei.name, sorted(namen & verboten))


def test_das_paket_haengt_nicht_an_der_produktion() -> None:
    """S6 liest Evidenz. Es darf die Control-Plane weder importieren noch aendern."""
    import ast

    for datei in sorted(Path("scripts/litellm_shadow_eval").rglob("*.py")):
        text = datei.read_text(encoding="utf-8")
        baum = ast.parse(text)
        for knoten in ast.walk(baum):
            module = None
            if isinstance(knoten, ast.ImportFrom) and knoten.module:
                module = knoten.module
            elif isinstance(knoten, ast.Import):
                module = knoten.names[0].name
            assert module is None or not module.startswith("app."), (datei.name, module)


# ---------------------------------------------------------------------------
# Vertragsprobe gegen den ECHTEN Schreiber -- ohne die Produktion zu aendern.
# ---------------------------------------------------------------------------


def test_der_leser_verdaut_was_kai_tatsaechlich_schreibt(tmp_path: Path) -> None:
    """Ein Auswerter, der seine eigene Evidenz nicht lesen kann, ist Zierde.

    Diese Probe ruft den ECHTEN Telemetrie-Schreiber auf, statt eine
    handgeschriebene Zeile zu erfinden, die zufaellig zum Leser passt. Die
    S5-Felder (`logical_route`, `transport`, `identity_proven`, ...) kommen aus
    #874 und werden hier ueberlagert; sobald #874 in der Mainline ist, faellt
    die Ueberlagerung weg und der Aufruf traegt sie selbst.

    Umgekehrt gilt die Richtung ausdruecklich NICHT: passt etwas nicht, wird
    der LESER angepasst, nie der Schreiber. S6 ist eine Brille, kein Eingriff.
    """
    from scripts.litellm_shadow_eval.loader import SUPPORTED_SCHEMA_VERSIONS, normalize_record

    from app.observability.llm_telemetry import record_llm_call

    pfad = tmp_path / "llm_telemetry.jsonl"
    record_llm_call(
        provider="openai",
        model="gpt-4o-mini",
        ok=True,
        latency_ms=12.0,
        role="shadow",
        correlation_id="corr-1",
        call_id="llmc_abc",
        purpose="analysis",
        attempt=1,
        outcome="success",
        path=pfad,
    )
    geschrieben = json.loads(pfad.read_text(encoding="utf-8").strip())
    assert geschrieben["schema_version"] in SUPPORTED_SCHEMA_VERSIONS, geschrieben["schema_version"]

    aus_s5 = {
        "logical_route": "standard",
        "mode": "shadow",
        "transport": "litellm",
        "requested_model_alias": "kai-standard",
        "actual_provider": "openai",
        "actual_model": "gpt-4o-mini",
        "identity_proven": True,
        "retry_count": 0,
        "input_tokens": 11,
        "output_tokens": 5,
        "cost_usd": 0.001,
        "cost_known": True,
        "schema_status": "valid",
        "execution_authority": False,
    }
    record, issues = normalize_record({**geschrieben, **aus_s5}, record_ref="1:t.jsonl:1")

    assert not issues, [issue.code for issue in issues]
    assert record is not None
    assert record.side.value == "SHADOW", "role=shadow ist die Schattenseite"
    assert record.logical_route == "standard"
    assert record.success is True, "der Schreiber sagt `ok`, der Leser versteht es"
    assert record.timestamp, "der Schreiber sagt `ts`, der Leser versteht es"
    assert record.cost_known is True and record.cost_usd == 0.001
    assert record.identity_proven is True
    assert record.schema_valid is True
    assert record.execution_authority is False
