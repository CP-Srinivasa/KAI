"""Fail-safe environment configuration owned by the AI control plane."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ai.retry import DEFAULT_MAX_ATTEMPTS, MAX_ATTEMPTS_CEILING

#: Die Stufen, die LiteLLM als `reasoning_effort` annimmt.
ERLAUBTE_DENKSTUFEN = frozenset({"minimal", "low", "medium", "high"})


def _strip_secret(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


class InferenceSettings(BaseSettings):
    """``KAI_INFERENCE_*`` is a namespace, never a second control plane."""

    model_config = SettingsConfigDict(
        env_prefix="KAI_INFERENCE_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    mode_ceiling: str = Field(default="off")
    route_modes: dict[str, str] = Field(default_factory=dict)
    route_aliases: dict[str, str] = Field(
        default_factory=lambda: {
            "bulk": "kai-bulk",
            "standard": "kai-standard",
            "reasoning": "kai-reasoning",
            "critical": "kai-critical",
            "stt": "kai-stt",
        }
    )
    #: Logische Route -> Denkbudget in Token. LEER heisst: kein Parameter, also
    #: unveraendertes Verhalten des Modells.
    #:
    #: Am 2026-09-08 auf kai-pi5 gemessen, `gemini/gemini-2.5-flash`, derselbe
    #: Prompt:
    #:
    #:   ohne Parameter   381 Denk-Token, 15 Text-Token, 0,0009957 USD, 3632 ms
    #:   Budget 128       104 Denk-Token, 45 Text-Token, 0,0003782 USD, 2354 ms
    #:   Budget 0           0 Denk-Token, 42 Text-Token, 0,0001107 USD,  772 ms
    #:
    #: Der Faktor zwischen "denken" und "nicht denken" ist 9, bei fuenffacher
    #: Geschwindigkeit -- und die Antwort wurde dabei laenger, nicht kuerzer.
    #: Ob sie BESSER war, sagt diese Messung nicht; das ist der Grund, warum es
    #: ein Regler ist und keine Vorgabe.
    #:
    #: Bewusst ein Token-Budget und keine Stufe: `reasoning_effort="low"` wurde
    #: in derselben Messung durchgereicht und blieb wirkungslos (380 statt 381
    #: Denk-Token). Wer es setzte, glaubte zu sparen und sparte nichts.
    #:
    #: ACHTUNG, seit 2026-09-09 gilt fuer `gemini/gemini-3.6-flash` das
    #: GEGENTEIL, auf kai-pi5 ueber den laufenden Proxy gemessen:
    #:
    #:   ohne Parameter                876 Denk-Token, 8353 ms
    #:   Budget 0   (dieser Regler)    867 Denk-Token, 6526 ms  <- WIRKUNGSLOS
    #:   reasoning_effort="low"        499 Denk-Token, 6366 ms
    #:   reasoning_effort="minimal"      0 Denk-Token, 1301 ms
    #:
    #: Der Grund ist die Form, nicht die Zahl: `thinking.budget_tokens` ist die
    #: Anthropic-Schreibweise, und LiteLLM 1.99.0 uebersetzt sie fuer Gemini
    #: nicht. Belegt ist das nicht am Token-Zaehler, sondern am Statuscode --
    #: derselbe Wert `0` gibt DIREKT gegen Google HTTP 400 ("invalid argument",
    #: `gemini-3.6-flash` kann Denken nicht abschalten), ueber LiteLLM aber 200
    #: mit unveraendertem Denkaufwand. Ein Parameter, der eine Ablehnung
    #: ausloesen MUESSTE und keine ausloest, ist nie angekommen.
    #:
    #: Der Regler bleibt: wo der Transport `thinking` nativ traegt, wirkt er.
    #: Fuer Gemini ist `route_reasoning_effort` der Weg.
    route_reasoning_budget: dict[str, int] = Field(default_factory=dict)
    #: Logische Route -> Denkstufe. LEER heisst: kein Parameter.
    #:
    #: Zweiter Regler und nicht Ersatz, weil es zwei Dialekte sind und nicht
    #: zwei Meinungen ueber dieselbe Zahl: ein Token-Budget ist nachpruefbar,
    #: eine Stufe ist ein Versprechen des Anbieters. Eine Umrechnung zwischen
    #: beiden waere geraten -- `minimal` ist kein bestimmter Token-Wert.
    #:
    #: Auf `gemini/gemini-3.6-flash` ist `minimal` der Faktor 10,9 bei
    #: dreifacher Geschwindigkeit (0,0017607 -> 0,0001620 USD je Aufruf,
    #: 4508 -> 1493 ms), bei gleicher Ausgabelaenge und in 4 von 4 Laeufen
    #: schemagueltigem JSON. Ob die Antwort BESSER ist, sagt das nicht -- auch
    #: das hier ist ein Regler und keine Vorgabe.
    route_reasoning_effort: dict[str, str] = Field(default_factory=dict)
    litellm_base_url: str = Field(default="http://127.0.0.1:4000")
    litellm_api_key: str = Field(default="", repr=False)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    # Die Obergrenze wird nicht zweitgeschrieben: sie gehoert der Retry-Politik.
    max_attempts: int = Field(default=DEFAULT_MAX_ATTEMPTS, ge=1, le=MAX_ATTEMPTS_CEILING)
    backoff_base_seconds: float = Field(default=0.25, ge=0.0, le=10.0)
    backoff_max_seconds: float = Field(default=2.0, ge=0.0, le=30.0)
    jitter_max_seconds: float = Field(default=0.1, ge=0.0, le=5.0)

    _strip_api_key = field_validator("litellm_api_key", mode="before")(_strip_secret)

    @field_validator("route_reasoning_effort")
    @classmethod
    def _stufen_muessen_bekannt_sein(cls, wert: dict[str, str]) -> dict[str, str]:
        """Ein Tippfehler wuerde sonst still zu "kein Regler".

        Das ist genau die Klasse Fehler, gegen die dieses Feld ueberhaupt
        entstanden ist: ein Parameter, den niemand annimmt, und eine Rechnung,
        die trotzdem laeuft.
        """
        unbekannt = sorted(set(wert.values()) - ERLAUBTE_DENKSTUFEN)
        if unbekannt:
            raise ValueError(
                f"unbekannte Denkstufe(n): {unbekannt} — erlaubt ist {sorted(ERLAUBTE_DENKSTUFEN)}"
            )
        return wert


__all__ = ["ERLAUBTE_DENKSTUFEN", "InferenceSettings"]
