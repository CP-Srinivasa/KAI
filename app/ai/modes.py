"""Modus je Route: wer ist autoritativ, und wer läuft nur mit.

ADR 0017 legt drei Modi fest und eine Regel, die wichtiger ist als die Modi
selbst: **``PRIMARY`` wird nie global aktiviert.** Graduation erfolgt pro Route
und bleibt Operator-Entscheidung.

    OFF      der bestehende direkte KAI-Pfad ist autoritativ; LiteLLM ist nicht beteiligt
    SHADOW   der direkte Pfad bleibt autoritativ; LiteLLM läuft parallel MIT
             ``execution_authority = False``
    PRIMARY  LiteLLM ist autoritativer Transport für GENAU DIESE Route; der
             direkte Provider bleibt kontrollierter Fallback

Der Defekt, gegen den diese Datei steht, hiess im ersten Anlauf „implizite
Production-Aktivierung": ein globaler Schalter, der beim Umlegen jede Route
mitnahm, auch die nie gemessenen. Hier kann ein globaler Wert deshalb nur noch
**deckeln**, niemals anheben. Wer ``PRIMARY`` will, muss die Route einzeln
nennen.

Rein: keine Uhr, kein I/O, keine Settings-Kenntnis. Die Herkunft der Werte —
Env, Settings, Datei — entscheidet der Aufrufer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Final, Literal, get_args

from app.ai.routes import ROUTES, Route

Mode = Literal["off", "shadow", "primary"]

MODES: Final[tuple[Mode, ...]] = get_args(Mode)

#: Aufsteigende Wirkmächtigkeit. ``off`` < ``shadow`` < ``primary``.
_RANK: Final[dict[Mode, int]] = {"off": 0, "shadow": 1, "primary": 2}

#: Ohne jede Konfiguration ist nichts an. Das ist die einzige Voreinstellung,
#: bei der ein Konfigurationsfehler nicht zu einer Aktivierung führt.
DEFAULT_MODE: Final[Mode] = "off"


def is_mode(value: object) -> bool:
    return isinstance(value, str) and value in MODES


def parse_mode(value: object, *, default: Mode = DEFAULT_MODE) -> Mode:
    """Ein Konfigurationswert wird zum Modus — Unbekanntes fällt auf *default*.

    Fail-closed by default: ein Tippfehler im Env darf keine Route aktivieren.
    """
    if isinstance(value, str):
        candidate = value.strip().lower()
        for mode in MODES:
            if candidate == mode:
                return mode
    return default


def resolve_mode(
    route: Route,
    *,
    per_route: Mapping[str, object] | None = None,
    ceiling: object = DEFAULT_MODE,
) -> Mode:
    """Der wirksame Modus einer Route.

    ``ceiling`` ist die globale Obergrenze und wirkt ausschliesslich als Deckel:
    sie kann eine Route herunterstufen, aber niemals hochstufen. ``primary``
    entsteht nur, wenn die Route SELBST auf ``primary`` steht — ein globales
    ``primary`` allein aktiviert nichts.

    Damit ist der Weg nach oben immer eine benannte Einzelentscheidung, der Weg
    nach unten aber ein einziger Schalter: im Zwischenfall genügt ein globales
    ``off``, um alles gleichzeitig stillzulegen, ohne jede Route anzufassen.
    """
    cap = parse_mode(ceiling)
    wanted = parse_mode((per_route or {}).get(route), default=DEFAULT_MODE)
    return wanted if _RANK[wanted] <= _RANK[cap] else cap


def graduated_routes(
    *, per_route: Mapping[str, object] | None = None, ceiling: object = DEFAULT_MODE
) -> tuple[Route, ...]:
    """Die Routen, die tatsächlich auf ``primary`` stehen — sortiert, deduped."""
    return tuple(
        r for r in ROUTES if resolve_mode(r, per_route=per_route, ceiling=ceiling) == "primary"
    )


def has_execution_authority(mode: Mode) -> bool:
    """Darf das Ergebnis dieses Modus etwas bewirken?

    Nur ``primary``. ``shadow`` laeuft mit und wird gemessen, aber sein Ergebnis
    erreicht keinen Aufrufer — das ist der ganze Sinn des Schattens, und es ist
    die Zusicherung, die einen Shadow-Betrieb ueberhaupt risikofrei macht.
    """
    return mode == "primary"


def unknown_route_keys(per_route: Mapping[str, object] | None) -> tuple[str, ...]:
    """Konfigurierte Schluessel, die keine Route sind.

    Ein Tippfehler in ``reasonning`` wuerde sonst schweigend nichts tun — und
    der Operator glaubte, er haette graduiert. Der Aufrufer entscheidet, ob das
    ein Warnbefund oder ein Startabbruch ist.
    """
    if not per_route:
        return ()
    known: Iterable[str] = ROUTES
    return tuple(sorted(k for k in per_route if k not in set(known)))


__all__ = [
    "DEFAULT_MODE",
    "MODES",
    "Mode",
    "graduated_routes",
    "has_execution_authority",
    "is_mode",
    "parse_mode",
    "resolve_mode",
    "unknown_route_keys",
]
