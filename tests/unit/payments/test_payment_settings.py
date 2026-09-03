"""Boot-Guards des Payment Control Plane (ADR 0018 §11).

Die Fragen hier sind nicht "laedt Pydantic ein Env", sondern: kann eine
Fehlkonfiguration einen Send freischalten, den niemand gewollt hat? Vier
Vorbedingungen fuer LIVE (Environment, Kill-Switch, Macaroon, HOTP-Seed) und
eine Kollision, die auch in SHADOW den Boot abbricht.

Warum die Scope-Kollision haerter ist als sie aussieht: zeigen
``APP_LN_MACAROON_PATH`` und ``APP_LN_INVOICE_MACAROON_PATH`` auf dieselbe
Datei, dann traegt jeder READ-Pfad die Rechte des Invoice-Scopes. Die
Aufteilung in Capabilities (W0/PR-A) waere damit rueckgaengig gemacht — nicht
sichtbar im Code, sondern nur in zwei Env-Zeilen.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.errors import ConfigurationError
from app.core.lightning_settings import LightningSettings
from app.core.payment_settings import PaymentSettings, validate_payment_boot


@pytest.fixture
def provisioned(tmp_path: Path) -> dict[str, Path]:
    macaroon = tmp_path / "payment.macaroon"
    macaroon.write_bytes(b"\x02\x01\x03payment")
    invoice = tmp_path / "invoice.macaroon"
    invoice.write_bytes(b"\x02\x01\x03invoice")
    read = tmp_path / "readonly.macaroon"
    read.write_bytes(b"\x02\x01\x03read")
    seed = tmp_path / "hotp_seed.b32"
    seed.write_text("JBSWY3DPEHPK3PXP", encoding="ascii")
    return {"payment": macaroon, "invoice": invoice, "read": read, "seed": seed}


def ln_settings(paths: dict[str, Path], **overrides: object) -> LightningSettings:
    base: dict[str, object] = {
        "enabled": True,
        "tls_cert_path": str(paths["read"]),
        "macaroon_path": str(paths["read"]),
        "invoice_macaroon_path": str(paths["invoice"]),
        "payment_macaroon_path": str(paths["payment"]),
        "hotp_seed_path": str(paths["seed"]),
        "pay_enabled": True,
    }
    base.update(overrides)
    return LightningSettings(**base)  # type: ignore[arg-type]


def payment_settings(**overrides: object) -> PaymentSettings:
    base: dict[str, object] = {"mode": "simulation"}
    base.update(overrides)
    return PaymentSettings(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #


def test_default_mode_is_simulation() -> None:
    assert PaymentSettings().mode == "simulation"


def test_default_journal_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der CODE-Default, nicht der Test-Redirect.

    ``tests/conftest.py`` biegt das Geld-Journal fuer die ganze Suite auf tmp
    um (sonst schriebe jeder App-Boot in die nie rotierte Produktivdatei).
    Dieser Test prueft, was ohne Umgebung gilt — also muss er die Umgebung
    ausdruecklich entfernen.
    """
    monkeypatch.delenv("APP_PAYMENT_JOURNAL_PATH", raising=False)
    assert PaymentSettings().journal_path == "artifacts/payments/payment_journal.jsonl"


def test_defaults_are_fail_closed_amounts() -> None:
    cfg = PaymentSettings()
    assert cfg.per_payment_max_sat > 0
    assert cfg.daily_hard_cap_sat >= cfg.per_payment_max_sat
    assert cfg.fee_limit_default_ppm > 0
    assert cfg.fee_limit_max_sat > 0
    assert cfg.approval_threshold_sat > 0
    assert cfg.max_inflight_window_s > 0


def test_default_destination_allowlist_is_empty_and_that_denies() -> None:
    """Eine leere Allowlist ist keine offene Tuer, sondern eine geschlossene."""
    assert PaymentSettings().destination_allowlist_hashes == ()


def test_env_prefix_is_app_payment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PAYMENT_PER_PAYMENT_MAX_SAT", "4242")
    assert PaymentSettings().per_payment_max_sat == 4242


def test_csv_fields_are_parsed_and_normalised() -> None:
    cfg = payment_settings(
        destination_allowlist=f" {'a' * 64}, {'B' * 64} ,",
        purposes_allowed="data_subscription, api_credit ,",
    )
    assert cfg.destination_allowlist_hashes == ("a" * 64, "b" * 64)
    assert cfg.purposes_allowed_set == frozenset({"data_subscription", "api_credit"})


def test_daily_cap_below_per_payment_max_is_refused() -> None:
    with pytest.raises(ValueError, match="daily_hard_cap_sat"):
        payment_settings(per_payment_max_sat=1000, daily_hard_cap_sat=500)


def test_negative_amounts_are_refused() -> None:
    with pytest.raises(ValueError):
        payment_settings(per_payment_max_sat=-1)


def test_agent_limits_example_file_exists_and_parses() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    path = repo_root / PaymentSettings().agent_limits_path
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    agents = data["agents"]
    assert agents, "die Beispieldatei braucht mindestens einen Agenten"
    for limits in agents.values():
        assert limits["max_amount_sat"] > 0
        assert limits["daily_max_sat"] >= limits["max_amount_sat"]
        assert limits["purposes"]


# --------------------------------------------------------------------------- #
# validate_payment_boot
# --------------------------------------------------------------------------- #


def test_simulation_boots_without_any_lightning_credential(tmp_path: Path) -> None:
    validate_payment_boot(
        payment_settings(mode="simulation"),
        app_env="development",
        lightning=LightningSettings(),
    )


def test_unknown_mode_is_refused_at_construction() -> None:
    with pytest.raises(ValueError):
        payment_settings(mode="armed")


def test_live_requires_production_environment(provisioned: dict[str, Path]) -> None:
    with pytest.raises(ConfigurationError, match="APP_ENV"):
        validate_payment_boot(
            payment_settings(mode="live"),
            app_env="development",
            lightning=ln_settings(provisioned),
        )


def test_live_requires_pay_enabled(provisioned: dict[str, Path]) -> None:
    with pytest.raises(ConfigurationError, match="APP_LN_PAY_ENABLED"):
        validate_payment_boot(
            payment_settings(mode="live"),
            app_env="production",
            lightning=ln_settings(provisioned, pay_enabled=False),
        )


def test_live_requires_an_existing_payment_macaroon(
    provisioned: dict[str, Path], tmp_path: Path
) -> None:
    with pytest.raises(ConfigurationError, match="payment macaroon"):
        validate_payment_boot(
            payment_settings(mode="live"),
            app_env="production",
            lightning=ln_settings(provisioned, payment_macaroon_path=str(tmp_path / "nope")),
        )


def test_live_refuses_a_payment_macaroon_configured_only_as_hex(
    provisioned: dict[str, Path],
) -> None:
    """Ein Hex-Macaroon im Env ist ein Secret im Prozessumfeld — LIVE will die Datei."""
    with pytest.raises(ConfigurationError, match="payment macaroon"):
        validate_payment_boot(
            payment_settings(mode="live"),
            app_env="production",
            lightning=ln_settings(provisioned, payment_macaroon_path="", payment_macaroon_hex="ab"),
        )


def test_live_requires_an_existing_hotp_seed(provisioned: dict[str, Path], tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="HOTP"):
        validate_payment_boot(
            payment_settings(mode="live"),
            app_env="production",
            lightning=ln_settings(provisioned, hotp_seed_path=str(tmp_path / "missing.b32")),
        )


def test_live_requires_a_positive_default_fee_limit(provisioned: dict[str, Path]) -> None:
    with pytest.raises(ConfigurationError, match="FEE_LIMIT_DEFAULT_PPM"):
        validate_payment_boot(
            payment_settings(mode="live", fee_limit_default_ppm=0),
            app_env="production",
            lightning=ln_settings(provisioned),
        )


def test_live_boots_when_every_precondition_holds(provisioned: dict[str, Path]) -> None:
    validate_payment_boot(
        payment_settings(mode="live"),
        app_env="production",
        lightning=ln_settings(provisioned),
    )


@pytest.mark.parametrize("mode", ["simulation", "shadow", "live"])
def test_scope_collision_aborts_in_every_mode(provisioned: dict[str, Path], mode: str) -> None:
    """ADR §11: ``macaroon_path == invoice_macaroon_path`` ist immer ein Abbruch.

    Auch in SIMULATION — die Kollision beschreibt die Rechte auf der Platte,
    nicht das, was dieser Prozess gerade vorhat.
    """
    collided = ln_settings(provisioned, invoice_macaroon_path=str(provisioned["read"]))
    with pytest.raises(ConfigurationError, match="scope collision"):
        validate_payment_boot(payment_settings(mode=mode), app_env="production", lightning=collided)


def test_scope_collision_is_ignored_when_both_paths_are_empty() -> None:
    """Zwei leere Pfade sind keine Kollision, sondern eine unbestueckte Anlage."""
    validate_payment_boot(
        payment_settings(mode="simulation"),
        app_env="development",
        lightning=LightningSettings(),
    )


def test_shadow_needs_no_payment_macaroon(provisioned: dict[str, Path], tmp_path: Path) -> None:
    """SHADOW liest nur — es darf ohne Sende-Credential booten."""
    validate_payment_boot(
        payment_settings(mode="shadow"),
        app_env="development",
        lightning=ln_settings(
            provisioned, payment_macaroon_path="", pay_enabled=False, hotp_seed_path=""
        ),
    )


def test_journal_path_resolves_to_an_absolute_path(tmp_path: Path) -> None:
    cfg = payment_settings(journal_path="artifacts/payments/payment_journal.jsonl")
    resolved = cfg.resolved_journal_path()
    assert resolved.is_absolute()
    assert resolved.name == "payment_journal.jsonl"


def test_absolute_journal_path_is_kept(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere" / "payment_journal.jsonl"
    cfg = payment_settings(journal_path=str(target))
    assert cfg.resolved_journal_path() == target


def test_payment_settings_are_not_declared_in_core_settings() -> None:
    """ADR §2: keine Zeile in ``app/core/settings.py``."""
    repo_root = Path(__file__).resolve().parents[3]
    source = (repo_root / "app" / "core" / "settings.py").read_text(encoding="utf-8")
    assert "PaymentSettings" not in source
    assert "APP_PAYMENT_" not in source


def test_lifespan_calls_the_payment_guard_after_the_lightning_guard() -> None:
    """ADR §11: die Reihenfolge ist Teil der Zusage, nicht Geschmack.

    Der Payment-Guard setzt auf einem Transport auf, den der Lightning-Guard
    bereits als vertrauenswuerdig erwiesen hat (TLS-Anker, Credential je
    aktivierter Capability). Umgekehrt wuerde er ueber eine Konfiguration
    urteilen, die gleich darauf verworfen wird.
    """
    repo_root = Path(__file__).resolve().parents[3]
    source = (repo_root / "app" / "api" / "main.py").read_text(encoding="utf-8")
    ln_call = source.index("validate_lightning_boot(settings.lightning)")
    pay_call = source.index("validate_payment_boot(")
    assert ln_call < pay_call
