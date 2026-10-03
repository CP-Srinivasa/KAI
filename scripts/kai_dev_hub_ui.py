#!/usr/bin/env python3
"""Oberfläche des KAI Developer Hub (ab 0.4.0).

Ollama-artige, dunkle Arbeitsfläche: links die Arbeitsbereiche wie Chats,
in der Mitte eine Engine-Auswahl mit Fähigkeits-Symbolen und eine
Startleiste, rechts der Systemzustand. Darüber liegt KAIs 80er-Neon-Schicht
(Synthwave-Horizont, Glitch-Monogramm, Glow in den KAI-Tönen aus
``web/src/styles/kai.tokens.css``).

Nur Darstellung: jede Aktion ruft unverändert die geprüften Funktionen aus
``kai_dev_hub`` auf. Routing, Schreibersperre, Übergabe-Ledger und
Modell-Pinning bleiben dort. Der obere Teil ist reine Logik (Katalog,
Statuschips, Farben, Monogramm-Raster) und ohne Tk testbar; ``run_ui`` baut
das Fenster.
"""

from __future__ import annotations

import contextlib
import json
import queue
import re
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

# --- Farben und Symbole --------------------------------------------------------

# KAI-Neon (kai.tokens.css) auf einer Ollama-dunklen Fläche.
PALETTE: Final[dict[str, str]] = {
    "bg0": "#05070A",
    "bg1": "#0A0D13",
    "bg2": "#10141C",
    "bg3": "#161B26",
    "bg4": "#1E2431",
    "line": "#1A2030",
    "line_strong": "#2A3344",
    "fg": "#EAF6FF",
    "fg_muted": "#9AA8BC",
    "fg_subtle": "#5D6A7E",
    "cyan": "#00E5FF",
    "blue": "#2F7DFF",
    "magenta": "#FF2BD6",
    "violet": "#8B5CF6",
    "green": "#00FFA3",
    "orange": "#FF6B00",
    "red": "#FF1744",
}

STATE_TONES: Final[dict[str, str]] = {
    "ok": "green",
    "fail": "red",
    "warn": "orange",
    "info": "cyan",
    "idle": "fg_subtle",
    "unknown": "fg_subtle",
}

# Name -> (Segoe-Fluent-Icons-Codepunkt, Unicode-Ersatz ohne Symbolschrift).
ICONS: Final[dict[str, tuple[str, str]]] = {
    "add": ("", "+"),
    "cancel": ("", "x"),
    "attach": ("", "@"),
    "check": ("", "v"),
    "chevron": ("", "v"),
    "clipboard": ("", "#"),
    "cloud": ("", "~"),
    "code": ("", "{}"),
    "document": ("", "="),
    "error": ("", "x"),
    "eye": ("", "o"),
    "folder": ("", ">"),
    "gears": ("", "*"),
    "handoff": ("", "<>"),
    "heart": ("", "+"),
    "history": ("", "<"),
    "info": ("", "i"),
    "laptop": ("", "[]"),
    "lock": ("", "#"),
    "moon": ("", "("),
    "offline": ("", "-"),
    "package": ("", "#"),
    "pc": ("", "[]"),
    "pen": ("", "/"),
    "play": ("", ">"),
    "power": ("", "o"),
    "pulse": ("", "~"),
    "refresh": ("", "@"),
    "robot": ("", "&"),
    "shield": ("", "#"),
    "tools": ("", "%"),
    "warning": ("", "!"),
}


def blend(base: str, top: str, amount: float) -> str:
    """Mix two ``#rrggbb`` colours; ``amount`` 0 = base, 1 = top."""
    amount = min(1.0, max(0.0, amount))
    low = [int(base[index : index + 2], 16) for index in (1, 3, 5)]
    high = [int(top[index : index + 2], 16) for index in (1, 3, 5)]
    return "#" + "".join(
        f"{round(a + (b - a) * amount):02x}" for a, b in zip(low, high, strict=True)
    )


def tone(state: str) -> str:
    return PALETTE[STATE_TONES.get(state, "fg_subtle")]


# --- Engines und Fähigkeiten (Ollama-Auswahl) ------------------------------------


@dataclass(frozen=True)
class Capability:
    icon: str
    label: str
    hint: str
    badge: str | None = None  # Textmarke statt Symbol, z. B. "64K"


CAPABILITIES: Final[dict[str, Capability]] = {
    "offline": Capability("offline", "Offline", "Läuft ohne Internet auf diesem Rechner (Ollama)."),
    "cloud": Capability(
        "cloud", "Cloud", "Braucht Netz: Anbieter bzw. LiteLLM-Dev-Proxy der Pi (:4001)."
    ),
    "code": Capability("code", "Schreibt Code", "Schreibender Client: belegt die Schreibersperre."),
    "tools": Capability("tools", "Werkzeuge", "Dateien, Terminal und Tests im Arbeitsbereich."),
    "ctx64k": Capability("package", "64K Kontext", "Kontextfenster 64K Token.", badge="64K"),
    "context": Capability("attach", "Kontextpaket", "Bekommt das verifizierbare KAI-Kontextpaket."),
    "clipboard": Capability(
        "clipboard", "Zwischenablage", "Erster Prompt liegt in der Zwischenablage: einmal einfügen."
    ),
    "readonly": Capability(
        "eye", "Nur lesen", "Kein Schreibzugriff auf den Arbeitsbereich, keine Schreibersperre."
    ),
    "pinned": Capability("lock", "Modell gepinnt", "Das Modell bleibt für die ganze Sitzung fest."),
}


@dataclass(frozen=True)
class Engine:
    key: str
    name: str
    variant: str
    model: str
    icon: str
    tone: str
    capabilities: tuple[str, ...]
    summary: str
    writes: bool

    @property
    def label(self) -> str:
        return f"{self.name} · {self.variant}"


def engine_catalog(local_model: str, cloud_model: str = "kai-dev-code") -> tuple[Engine, ...]:
    """The four start paths of the hub, in Ollama's picker order."""
    return (
        Engine(
            "opencode-local",
            "OpenCode",
            "lokal",
            local_model,
            "laptop",
            "cyan",
            ("offline", "code", "tools", "ctx64k", "context", "pinned"),
            "Offline auf diesem Rechner; Ollama startet bei Bedarf.",
            True,
        ),
        Engine(
            "opencode-cloud",
            "OpenCode",
            "Cloud-Reserve",
            f"{cloud_model} · LiteLLM :4001",
            "cloud",
            "blue",
            ("cloud", "code", "tools", "context", "pinned"),
            "Öffnet den Tunnel zur Pi und prüft vorher eine echte Antwort.",
            True,
        ),
        Engine(
            "hermes-local",
            "Hermes",
            "lokal",
            local_model,
            "robot",
            "green",
            ("offline", "code", "tools", "ctx64k", "clipboard", "pinned"),
            "Agent im Terminal; Kontextpaket aus der Zwischenablage einfügen.",
            True,
        ),
        Engine(
            "kimi",
            "Kimi",
            "Desktop",
            "Kontextpaket als Anhang",
            "moon",
            "magenta",
            ("cloud", "readonly", "context"),
            "Öffnet Kimi und markiert das Kontextpaket im Explorer.",
            False,
        ),
    )


def engine_readiness(key: str, values: Mapping[str, Any]) -> tuple[str, str]:
    """(state, text) for the dot on an engine row, from ``kai_dev_hub.status``."""
    if not values:
        return "unknown", "Status wird geladen …"
    if key in {"opencode-local", "opencode-cloud"} and not values.get("opencode"):
        return "fail", "OpenCode ist nicht installiert."
    if key == "hermes-local" and not values.get("hermes"):
        return "fail", "Hermes ist nicht installiert."
    if key == "kimi":
        if values.get("kimi"):
            return "ok", "Kimi Desktop gefunden."
        return "fail", "Kimi Desktop ist nicht installiert."
    if key == "opencode-cloud":
        if values.get("litellm_tunnel_online"):
            return "ok", "Cloud-Tunnel ist offen."
        return "idle", "Tunnel öffnet sich beim Start."
    field = "hermes_64k_model_installed" if key == "hermes-local" else "local_model_installed"
    model = values.get("local_model", "lokales Modell")
    installed = values.get(field)
    if installed is True:
        return "ok", f"{model} ist bereit."
    if installed is False:
        return "fail", f"{model} fehlt in Ollama."
    return "idle", "Ollama ist aus und startet beim Start."


# --- Systemzustand -------------------------------------------------------------


@dataclass(frozen=True)
class Chip:
    key: str
    icon: str
    label: str
    state: str  # ok | fail | warn | info | idle | unknown
    detail: str


def short_time(value: Any) -> str:
    if not isinstance(value, str) or not value or value == "NOT_RUN":
        return "nie"
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.strftime("%d.%m. %H:%M")


def _flag(value: Any, *, missing: str = "fail") -> str:
    if value is True:
        return "ok"
    if value is False:
        return missing
    return "unknown"


def writer_name(described: Any) -> str:
    text = str(described or "keiner")
    return text.split(" (", 1)[0]


def status_chips(values: Mapping[str, Any], chain: tuple[bool, str] | None = None) -> list[Chip]:
    """Operator-readable rows for the system panel; never invents a value."""
    if not values:
        return [Chip("loading", "pulse", "Status", "unknown", "wird geladen …")]
    handoffs = values.get("handoff_state") or {}
    pending = len(handoffs.get("pending") or [])
    writer = writer_name(values.get("active_writer"))
    installed = {"ok": "installiert", "fail": "fehlt", "unknown": "unbekannt"}
    model_state = _flag(values.get("local_model_installed"))
    proven = values.get("last_response_proven")
    chain_ok = values.get("handoff_chain_valid") if chain is None else chain[0]
    chain_text = chain[1] if chain else ("gültig" if chain_ok else "gebrochen")
    chips = [
        Chip(
            "ollama_online",
            "pc",
            "Ollama",
            _flag(values.get("ollama_online"), missing="idle"),
            "online" if values.get("ollama_online") else "aus · startet bei Bedarf",
        ),
        Chip(
            "local_model_installed",
            "package",
            "Modell 64K",
            model_state,
            {"ok": "bereit", "fail": "fehlt", "unknown": "Ollama aus"}[model_state],
        ),
        *(
            Chip(key, icon, label, _flag(values.get(key)), installed[_flag(values.get(key))])
            for key, icon, label in (
                ("opencode", "code", "OpenCode"),
                ("hermes", "robot", "Hermes"),
                ("kimi", "moon", "Kimi"),
            )
        ),
        Chip(
            "litellm_tunnel_online",
            "cloud",
            "Cloud-Tunnel",
            _flag(values.get("litellm_tunnel_online"), missing="idle"),
            "offen" if values.get("litellm_tunnel_online") else "geschlossen",
        ),
        Chip(
            "context_contract",
            "document",
            "Kontextvertrag",
            _flag(values.get("context_contract")),
            "AGENTS.md + AI_HANDOFF.md" if values.get("context_contract") else "fehlt",
        ),
        Chip("handoff_chain_valid", "shield", "Übergabekette", _flag(chain_ok), chain_text),
        Chip(
            "pending_handoffs",
            "handoff",
            "Offene Übergaben",
            "warn" if pending else "ok",
            f"{pending} offen" if pending else "keine",
        ),
        Chip("active_writer", "pen", "Schreiber", "idle" if writer == "keiner" else "info", writer),
        Chip(
            "last_prerequisites_ok",
            "heart",
            "Health-Task",
            _flag(values.get("last_prerequisites_ok")),
            f"zuletzt {short_time(values.get('last_independent_check'))}",
        ),
        Chip(
            "last_response_proven",
            "check",
            "Antwort bewiesen",
            _flag(proven, missing="warn"),
            "ja" if proven is True else "nein · Lokal prüfen" if proven is False else "nie geprüft",
        ),
        Chip(
            "managed_workspaces",
            "folder",
            "Arbeitsbereiche",
            "info",
            str(values.get("managed_workspaces", 0)),
        ),
    ]
    return chips


# --- Arbeitsbereiche -----------------------------------------------------------


@dataclass(frozen=True)
class SessionRow:
    title: str
    subtitle: str
    path: Path | None
    icon: str
    hint: str


def session_rows(rows: Sequence[Mapping[str, Any]]) -> list[SessionRow]:
    result: list[SessionRow] = []
    for row in rows:
        task = str(row.get("task") or "Ohne Titel")
        if row.get("orphaned"):
            reason = str(row.get("orphan_reason") or "unbekannt")
            result.append(SessionRow(task, f"VERWAIST: {reason}", None, "warning", reason))
            continue
        branch = str(row.get("branch") or "")
        offline = row.get("base_mode") == "offline-cache"
        subtitle = branch + ("  [OFFLINE-BASIS]" if offline else "")
        hint = f"{row.get('worktree', '')}" + (
            "\nOffline-Basis: aus dem letzten verifizierten Remote-Stand." if offline else ""
        )
        result.append(
            SessionRow(
                task, subtitle, Path(row["worktree"]), "offline" if offline else "folder", hint
            )
        )
    return result


def choose_active(current: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    """Keep the chosen workspace if it is still valid, else the newest valid one."""
    valid = [Path(row["worktree"]) for row in rows if not row.get("orphaned")]
    if not valid or current in valid:
        return current
    return valid[0]


def workspace_summary(values: Mapping[str, Any], resume: Mapping[str, Any]) -> tuple[str, str, str]:
    """(title, meta line, next step) for the hero banner."""
    session = resume.get("session") if resume else None
    if not session:
        return (
            "Kein Arbeitsbereich gewählt",
            "Strg+N legt eine neue Aufgabe mit eigenem Worktree an.",
            "",
        )
    changed = int(resume.get("changed_files") or 0)
    state = "sauber" if not changed else f"{changed} geänderte Datei{'en' if changed > 1 else ''}"
    head = str(resume.get("head") or values.get("head") or "")[:8]
    branch = resume.get("branch") or values.get("branch") or "?"
    writer = writer_name(values.get("active_writer"))
    meta = f"{branch}  ·  @{head}  ·  {state}  ·  Schreiber: {writer}"
    return str(session["task"]), meta, str(resume.get("next_action") or "")


def handoff_hint(resume: Mapping[str, Any]) -> str:
    pending = (resume or {}).get("pending_handoffs") or []
    if pending:
        first = pending[0]
        more = f" (+{len(pending) - 1} weitere)" if len(pending) > 1 else ""
        return (
            f"Übergabe {str(first['handoff_id'])[:8]} an {first['to_agent']} wartet auf "
            f"Bestätigung{more}: Antwort mit Challenge unter 'Empfang bestätigen' prüfen."
        )
    return "Keine offene Übergabe. Kimi braucht den Anhang aus dem Kontextpaket."


# --- KAI-Monogramm als Raster (für Fenster-Symbol und Logo, kantengeglättet) ----

# Pfade aus brand/kai-mark.svg (viewBox 128).
KAI_MARK: Final[tuple[tuple[tuple[float, float], ...], ...]] = (
    ((22, 18), (38, 18), (38, 110), (22, 110)),
    ((38, 64), (96, 6), (110, 22), (52, 80)),
    ((38, 64), (82, 110), (66, 110), (30, 72)),
)


def _inside(x: float, y: float, polygon: Sequence[tuple[float, float]]) -> bool:
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def mark_coverage(size: int, samples: int = 3) -> list[list[float]]:
    """Antialiased coverage 0..1 of the KAI monogram on a ``size`` x ``size`` grid."""
    scale = 128 / size
    offsets = [(index + 0.5) / samples for index in range(samples)]
    total = samples * samples
    grid: list[list[float]] = []
    for row in range(size):
        line: list[float] = []
        for col in range(size):
            hits = sum(
                any(_inside((col + dx) * scale, (row + dy) * scale, part) for part in KAI_MARK)
                for dy in offsets
                for dx in offsets
            )
            line.append(hits / total)
        grid.append(line)
    return grid


def mark_pixels(
    size: int, background: str, layers: Sequence[tuple[str, int, int, float]]
) -> list[list[str]]:
    """Composite (colour, dx, dy, alpha) layers of the monogram: the glitch look."""
    coverage = mark_coverage(size, samples=2 if size >= 64 else 3)
    pixels = [[background] * size for _ in range(size)]
    for colour, dx, dy, alpha in layers:
        for row in range(size):
            source_row = row - dy
            if not 0 <= source_row < size:
                continue
            for col in range(size):
                source_col = col - dx
                if 0 <= source_col < size and coverage[source_row][source_col] > 0:
                    pixels[row][col] = blend(
                        pixels[row][col], colour, coverage[source_row][source_col] * alpha
                    )
    return pixels


# --- Tk-Bausteine (Import erst zur Laufzeit) -----------------------------------


class Theme:
    """Fonts, DPI scale and glyph lookup for one Tk root."""

    def __init__(self, root: Any) -> None:
        from tkinter import font as tkfont

        self.tkfont = tkfont
        self.scale = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
        families = set(tkfont.families(root))

        def first(*names: str) -> str:
            return next((name for name in names if name in families), "TkDefaultFont")

        self.icon_family = next(
            (name for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if name in families),
            None,
        )
        self.display = first("Orbitron", "Segoe UI Variable Display", "Segoe UI")
        self.body = first("Inter", "Segoe UI Variable Text", "Segoe UI")
        self.mono = first("Cascadia Mono", "JetBrains Mono", "Consolas", "Courier New")
        self._fonts: dict[tuple[str, int, str], Any] = {}

    def px(self, value: float) -> int:
        return int(round(value * self.scale))

    def font(self, kind: str, size: int, weight: str = "normal") -> Any:
        family = {
            "display": self.display,
            "body": self.body,
            "mono": self.mono,
            "icon": self.icon_family or self.body,
        }[kind]
        key = (family, size, weight)
        if key not in self._fonts:
            self._fonts[key] = self.tkfont.Font(family=family, size=size, weight=weight)
        return self._fonts[key]

    def glyph(self, name: str) -> str:
        fluent, fallback = ICONS.get(name, ("", "?"))
        return fluent if self.icon_family else fallback

    def fit(self, font: Any, text: str, width: int) -> str:
        if width <= 0 or font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"


def round_rect(
    canvas: Any, x1: float, y1: float, x2: float, y2: float, radius: float, **options: Any
) -> int:
    radius = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = (
        x1 + radius, y1, x1 + radius, y1, x2 - radius, y1, x2 - radius, y1, x2, y1,
        x2, y1 + radius, x2, y1 + radius, x2, y2 - radius, x2, y2 - radius, x2, y2,
        x2 - radius, y2, x2 - radius, y2, x1 + radius, y2, x1 + radius, y2, x1, y2,
        x1, y2 - radius, x1, y2 - radius, x1, y1 + radius, x1, y1 + radius, x1, y1,
    )  # fmt: skip
    return int(canvas.create_polygon(points, smooth=True, splinesteps=12, **options))


def glow_outline(
    canvas: Any,
    theme: Theme,
    box: tuple[float, float, float, float],
    radius: float,
    colour: str,
    base: str,
    *,
    rings: int = 3,
) -> None:
    x1, y1, x2, y2 = box
    for ring in range(rings, 0, -1):
        grow = theme.px(ring * 1.4)
        round_rect(
            canvas,
            x1 - grow,
            y1 - grow,
            x2 + grow,
            y2 + grow,
            radius + grow,
            fill="",
            outline=blend(base, colour, 0.34 - ring * 0.09),
            width=max(1, theme.px(1)),
        )


def branch_symbol(canvas: Any, theme: Theme, x: float, y: float, colour: str) -> None:
    """Git-branch mark (trunk, fork, three nodes); Segoe has no branch glyph."""
    unit = theme.px(1)
    width = max(1, round(1.4 * unit))
    top, bottom = y - 6 * unit, y + 6 * unit
    canvas.create_line(x + 3 * unit, top, x + 3 * unit, bottom, fill=colour, width=width)
    canvas.create_line(
        x + 10 * unit, top + 2 * unit, x + 10 * unit, y - unit, x + 3 * unit, y + 3 * unit,
        fill=colour, width=width, smooth=True,
    )  # fmt: skip
    for node_x, node_y in ((x + 3 * unit, top), (x + 3 * unit, bottom), (x + 10 * unit, top)):
        radius = 2 * unit
        canvas.create_oval(
            node_x - radius, node_y - radius, node_x + radius, node_y + radius,
            fill=PALETTE["bg0"], outline=colour, width=width,
        )  # fmt: skip


def glow_dot(canvas: Any, theme: Theme, x: float, y: float, colour: str, base: str) -> None:
    radius = theme.px(3.5)
    for factor, amount in ((2.6, 0.10), (1.8, 0.24), (1.0, 1.0)):
        size = radius * factor
        canvas.create_oval(
            x - size, y - size, x + size, y + size, fill=blend(base, colour, amount), outline=""
        )


class Tooltip:
    def __init__(self, root: Any, theme: Theme) -> None:
        self.root = root
        self.theme = theme
        self.window: Any = None
        self.pending: Any = None

    def schedule(self, text: str, x: int, y: int) -> None:
        self.hide()
        self.pending = self.root.after(380, lambda: self.show(text, x, y))

    def show(self, text: str, x: int, y: int) -> None:
        import tkinter as tk

        self.pending = None
        window = tk.Toplevel(self.root)
        window.overrideredirect(True)
        with contextlib.suppress(tk.TclError):
            window.attributes("-topmost", True)
        frame = tk.Frame(window, bg=PALETTE["line_strong"], padx=1, pady=1)
        frame.pack()
        tk.Label(
            frame,
            text=text,
            bg=PALETTE["bg4"],
            fg=PALETTE["fg"],
            font=self.theme.font("body", 9),
            justify="left",
            wraplength=self.theme.px(320),
            padx=self.theme.px(9),
            pady=self.theme.px(6),
        ).pack()
        window.geometry(f"+{x + self.theme.px(12)}+{y + self.theme.px(14)}")
        self.window = window

    def hide(self) -> None:
        if self.pending is not None:
            self.root.after_cancel(self.pending)
            self.pending = None
        if self.window is not None:
            self.window.destroy()
            self.window = None

    def bind(self, widget: Any, text: Callable[[], str] | str, tag: str | None = None) -> None:
        def enter(event: Any) -> None:
            value = text() if callable(text) else text
            if value:
                self.schedule(value, event.x_root, event.y_root)

        def leave(_event: Any) -> None:
            self.hide()

        if tag is None:
            widget.bind("<Enter>", enter, add="+")
            widget.bind("<Leave>", leave, add="+")
        else:
            # Ersetzen statt anhaengen: Tags ueberleben das Neuzeichnen.
            widget.tag_bind(tag, "<Enter>", enter)
            widget.tag_bind(tag, "<Leave>", leave)


class NeonButton:
    """Rounded canvas button: ``ghost`` (Ollama-flach), ``primary`` (Neon), ``icon``."""

    def __init__(
        self,
        parent: Any,
        theme: Theme,
        *,
        text: str,
        command: Callable[[], None],
        bg: str,
        icon: str | None = None,
        variant: str = "ghost",
        accent: str = "cyan",
        height: int = 34,
        align: str = "center",
        tooltip: Tooltip | None = None,
        tip: str = "",
    ) -> None:
        import tkinter as tk

        self.theme = theme
        self.text = text
        self.icon = icon
        self.command = command
        self.bg = bg
        self.variant = variant
        self.accent = PALETTE[accent]
        self.align = align
        self.hover = False
        self.pressed = False
        self.text_font = theme.font("body", 9, "bold" if variant == "primary" else "normal")
        self.icon_font = theme.font("icon", 11)
        pad = theme.px(14)
        width = (
            theme.px(height)
            if variant == "icon"
            else pad * 2
            + self.text_font.measure(text)
            + (self.icon_font.measure(theme.glyph(icon)) + theme.px(9) if icon else 0)
        )
        self.canvas = tk.Canvas(
            parent,
            width=width,
            height=theme.px(height),
            bg=bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.canvas.bind("<Configure>", lambda _e: self.draw())
        self.canvas.bind("<Enter>", lambda _e: self._set(hover=True))
        self.canvas.bind("<Leave>", lambda _e: self._set(hover=False, pressed=False))
        self.canvas.bind("<ButtonPress-1>", lambda _e: self._set(pressed=True))
        self.canvas.bind("<ButtonRelease-1>", self._release)
        if tooltip is not None and (tip or variant == "icon"):
            tooltip.bind(self.canvas, tip or text)
        self.draw()

    def _set(self, **state: bool) -> None:
        for key, value in state.items():
            setattr(self, key, value)
        self.draw()

    def _release(self, event: Any) -> None:
        inside = 0 <= event.x <= self.canvas.winfo_width() and 0 <= event.y <= (
            self.canvas.winfo_height()
        )
        self._set(pressed=False)
        if inside:
            self.command()

    def draw(self) -> None:
        canvas, theme = self.canvas, self.theme
        canvas.delete("all")
        width = max(canvas.winfo_width(), int(canvas.cget("width")))
        if canvas.winfo_width() > 1:
            width = canvas.winfo_width()
        height = int(canvas.cget("height"))
        margin = theme.px(2)
        box = (margin, margin, width - margin, height - margin)
        radius = theme.px(9)
        if self.variant == "primary":
            fill = blend(self.bg, self.accent, 0.30 if self.hover else 0.18)
            outline = self.accent
            text_colour = PALETTE["fg"]
            icon_colour = self.accent if not self.hover else PALETTE["fg"]
        elif self.variant == "icon":
            fill = PALETTE["bg3"] if self.hover else self.bg
            outline = PALETTE["line_strong"] if self.hover else self.bg
            text_colour = PALETTE["fg"]
            icon_colour = self.accent if self.hover else PALETTE["fg_muted"]
        else:
            fill = PALETTE["bg3"] if self.hover else PALETTE["bg2"]
            outline = blend(PALETTE["line_strong"], self.accent, 0.5 if self.hover else 0.0)
            text_colour = (
                PALETTE["fg"] if self.hover else blend(PALETTE["fg_muted"], PALETTE["fg"], 0.6)
            )
            icon_colour = self.accent if self.hover else PALETTE["fg_muted"]
        if self.pressed:
            fill = blend(fill, self.accent, 0.12)
        round_rect(canvas, *box, radius, fill=fill, outline=outline, width=max(1, theme.px(1)))
        middle = height / 2
        glyph = theme.glyph(self.icon) if self.icon else ""
        if self.variant == "icon":
            canvas.create_text(width / 2, middle, text=glyph, font=self.icon_font, fill=icon_colour)
            return
        icon_width = self.icon_font.measure(glyph) + theme.px(9) if glyph else 0
        content = icon_width + self.text_font.measure(self.text)
        x = theme.px(14) if self.align == "left" else max(theme.px(10), (width - content) / 2)
        if glyph:
            canvas.create_text(
                x, middle, text=glyph, font=self.icon_font, fill=icon_colour, anchor="w"
            )
        label = theme.fit(self.text_font, self.text, int(width - x - icon_width - theme.px(10)))
        canvas.create_text(
            x + icon_width, middle, text=label, font=self.text_font, fill=text_colour, anchor="w"
        )


def section_label(parent: Any, theme: Theme, text: str, bg: str) -> Any:
    import tkinter as tk

    return tk.Label(
        parent,
        text=" ".join(text.upper()),
        bg=bg,
        fg=PALETTE["fg_subtle"],
        font=theme.font("body", 7, "bold"),
        anchor="w",
    )


# --- Fenster ------------------------------------------------------------------


def _enable_dpi_awareness() -> None:
    """Sharp text on 4K/200 %: without it Windows bitmap-stretches the window."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]


def _dark_title_bar(window: Any) -> None:
    """Windows 11: dark caption in KAI colours; silently ignored elsewhere."""
    if sys.platform != "win32":
        return
    import ctypes

    def colorref(colour: str) -> int:
        red, green, blue = (int(colour[index : index + 2], 16) for index in (1, 3, 5))
        return red | (green << 8) | (blue << 16)

    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())  # type: ignore[attr-defined]
        dwm = ctypes.windll.dwmapi  # type: ignore[attr-defined]
        for attribute, value in (
            (20, 1),  # DWMWA_USE_IMMERSIVE_DARK_MODE
            (35, colorref(PALETTE["bg1"])),  # DWMWA_CAPTION_COLOR
            (34, colorref(blend(PALETTE["bg1"], PALETTE["cyan"], 0.35))),  # BORDER_COLOR
            (36, colorref(PALETTE["fg"])),  # DWMWA_TEXT_COLOR
        ):
            data = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))
    except (AttributeError, OSError):
        return


def _photo(root: Any, pixels: list[list[str]]) -> Any:
    import tkinter as tk

    image = tk.PhotoImage(master=root, width=len(pixels[0]), height=len(pixels))
    image.put(" ".join("{" + " ".join(row) + "}" for row in pixels))
    return image


class HubWindow:
    """The main window. Everything that touches Git, Ollama or the Pi runs off the UI thread."""

    def __init__(self, hub: Any, repo: Path, root: Any) -> None:
        self.hub = hub
        self.repo = repo
        self.root = root
        self.active = repo
        self.theme = Theme(root)
        self.tooltip = Tooltip(root, self.theme)
        self.engines = engine_catalog(hub.LOCAL_MODEL)
        self.selected = self.engines[0].key
        self.values: dict[str, Any] = {}
        self.resume: dict[str, Any] = {}
        self.chain: tuple[bool, str] | None = None
        self.events: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()
        self.refreshing = False
        self.refresh_again = False
        self.toast_job: Any = None
        self.images: list[Any] = []
        self._build()
        root.after(120, self._pump)
        self.refresh()

    # -- Infrastruktur -----------------------------------------------------------

    def post(self, callback: Callable[[], None]) -> None:
        self.events.put(callback)

    def _pump(self) -> None:
        try:
            while True:
                callback = self.events.get_nowait()
                try:
                    callback()
                except Exception as exc:  # UI boundary
                    self.fail(exc)
        except queue.Empty:
            pass
        self.root.after(120, self._pump)

    def run_background(
        self,
        func: Callable[[], Any],
        *,
        busy: str | None = None,
        done: str | None = None,
        on_result: Callable[[Any], None] | None = None,
        report: str | None = None,
    ) -> None:
        if busy:
            self.toast(busy, "info", sticky=True)

        def worker() -> None:
            try:
                result = func()
            except Exception as exc:  # UI boundary
                self.post(lambda exc=exc: self.fail(exc))
                return

            def finish() -> None:
                if on_result is not None:
                    on_result(result)
                if done:
                    self.toast(done, "ok")
                elif busy:
                    self.toast("Fertig.", "ok")
                if report and result is not None:
                    self.show_report(report, result)
                self.refresh()

            self.post(finish)

        threading.Thread(target=worker, daemon=True).start()

    def refresh(self) -> None:
        if self.refreshing:
            self.refresh_again = True
            return
        self.refreshing = True
        hub, current = self.hub, self.active

        def worker() -> None:
            try:
                rows = hub.workflow.list_sessions(hub.STATE_ROOT)
                target = choose_active(current, rows)
                values = hub.status(target)
                chain = hub.verify_handoffs()
                resume = hub.resume_report(target)
            except Exception as exc:  # UI boundary
                self.post(lambda exc=exc: self._status_failed(exc))
                return
            self.post(lambda: self._apply_status(rows, target, values, chain, resume))

        threading.Thread(target=worker, daemon=True).start()

    def _status_done(self) -> None:
        self.refreshing = False
        if self.refresh_again:
            self.refresh_again = False
            self.refresh()

    def _status_failed(self, exc: Exception) -> None:
        self.toast(f"STATUS-FEHLER: {exc}", "fail", sticky=True)
        self._status_done()

    def _apply_status(
        self,
        rows: list[dict[str, Any]],
        target: Path,
        values: dict[str, Any],
        chain: tuple[bool, str],
        resume: dict[str, Any],
    ) -> None:
        self.active, self.values, self.chain, self.resume = target, values, chain, resume
        self._render_sessions(session_rows(rows))
        for row in self.engine_rows:
            row["state"] = engine_readiness(row["engine"].key, values)
        self._draw_engines()
        self._render_chips(status_chips(values, chain))
        self._draw_hero()
        self._draw_composer()
        self.hint.configure(text=handoff_hint(resume))
        self._status_done()

    # -- Aufbau ------------------------------------------------------------------

    def _build(self) -> None:
        import tkinter as tk

        root, theme = self.root, self.theme
        root.title(f"KAI Developer Hub {self.hub.HUB_VERSION}")
        root.configure(bg=PALETTE["bg0"])
        root.geometry(f"{theme.px(1280)}x{theme.px(880)}")
        root.minsize(theme.px(1120), theme.px(800))
        icon = _photo(
            root,
            mark_pixels(
                64,
                PALETTE["bg1"],
                [(PALETTE["cyan"], -2, 0, 0.9), (PALETTE["magenta"], 2, 0, 0.9),
                 (PALETTE["fg"], 0, 0, 1.0)],
            ),
        )  # fmt: skip
        self.images.append(icon)
        root.iconphoto(True, icon)
        root.option_add("*Font", theme.font("body", 10))

        sidebar = tk.Frame(root, bg=PALETTE["bg1"], width=theme.px(268))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Frame(root, bg=PALETTE["line"], width=max(1, theme.px(1))).pack(side="left", fill="y")
        main = tk.Frame(root, bg=PALETTE["bg0"])
        main.pack(side="left", fill="both", expand=True)

        self._build_sidebar(sidebar)
        self.hero = tk.Canvas(
            main, height=theme.px(128), bg=PALETTE["bg0"], highlightthickness=0, bd=0
        )
        self.hero.pack(fill="x")
        self.hero.bind("<Configure>", lambda _e: self._draw_hero())

        self.toast_bar = tk.Frame(main, bg=PALETTE["bg1"], height=theme.px(34))
        self.toast_bar.pack(side="bottom", fill="x")
        self.toast_bar.pack_propagate(False)
        self.toast_icon = tk.Label(
            self.toast_bar, bg=PALETTE["bg1"], font=theme.font("icon", 10), padx=theme.px(6)
        )
        self.toast_icon.pack(side="left", padx=(theme.px(18), 0))
        self.toast_text = tk.Label(
            self.toast_bar, bg=PALETTE["bg1"], font=theme.font("body", 9), anchor="w"
        )
        self.toast_text.pack(side="left", fill="x", expand=True)
        tk.Frame(main, bg=PALETTE["line"], height=max(1, theme.px(1))).pack(side="bottom", fill="x")

        body = tk.Frame(main, bg=PALETTE["bg0"])
        body.pack(fill="both", expand=True, padx=theme.px(28), pady=(theme.px(4), theme.px(16)))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, minsize=theme.px(330))
        body.grid_rowconfigure(0, weight=1)
        center = tk.Frame(body, bg=PALETTE["bg0"])
        center.grid(row=0, column=0, sticky="nsew", padx=(0, theme.px(24)))
        side = tk.Frame(
            body,
            bg=PALETTE["bg1"],
            highlightthickness=max(1, theme.px(1)),
            highlightbackground=PALETTE["line"],
        )
        side.grid(row=0, column=1, sticky="nsew")
        self._build_center(center)
        self._build_system(side)
        self.toast_idle()
        root.bind("<Control-n>", lambda _e: self.new_session())
        root.bind("<F5>", lambda _e: self.refresh())
        root.bind("<Return>", lambda _e: self.start_engine())

    def _build_sidebar(self, sidebar: Any) -> None:
        import tkinter as tk

        theme = self.theme
        logo = tk.Canvas(
            sidebar, height=theme.px(92), bg=PALETTE["bg1"], highlightthickness=0, bd=0
        )
        logo.pack(fill="x")
        size = theme.px(44)
        shift = max(1, theme.px(1.5))
        mark = _photo(
            self.root,
            mark_pixels(
                size,
                PALETTE["bg1"],
                [(PALETTE["cyan"], -shift, 0, 0.95), (PALETTE["magenta"], shift, 0, 0.95),
                 (PALETTE["fg"], 0, 0, 1.0)],
            ),
        )  # fmt: skip
        self.images.append(mark)
        logo.create_image(theme.px(22), theme.px(46), image=mark, anchor="w")
        x = theme.px(76)
        wordmark = theme.font("display", 17, "bold")
        for colour, dx in ((PALETTE["cyan"], -shift), (PALETTE["magenta"], shift)):
            logo.create_text(
                x + dx, theme.px(38), text="KAI", font=wordmark, fill=colour, anchor="w"
            )
        logo.create_text(x, theme.px(38), text="KAI", font=wordmark, fill=PALETTE["fg"], anchor="w")
        logo.create_text(
            x + theme.px(1),
            theme.px(62),
            text="DEVELOPER HUB",
            font=theme.font("display", 7),
            fill=PALETTE["cyan"],
            anchor="w",
        )
        version = f"v{self.hub.HUB_VERSION}"
        vx = x + wordmark.measure("KAI") + theme.px(12)
        mono = theme.font("mono", 7)
        round_rect(
            logo,
            vx,
            theme.px(29),
            vx + mono.measure(version) + theme.px(14),
            theme.px(47),
            theme.px(9),
            fill=blend(PALETTE["bg1"], PALETTE["magenta"], 0.14),
            outline=blend(PALETTE["bg1"], PALETTE["magenta"], 0.55),
        )
        logo.create_text(
            vx + theme.px(7),
            theme.px(38),
            text=version,
            font=mono,
            fill=PALETTE["magenta"],
            anchor="w",
        )

        new_button = NeonButton(
            sidebar,
            theme,
            text="Neue Aufgabe",
            icon="add",
            command=self.new_session,
            bg=PALETTE["bg1"],
            variant="primary",
            accent="cyan",
            height=40,
            align="left",
            tooltip=self.tooltip,
            tip="Eigener Worktree von der frischen Remote-Basis (Strg+N)",
        )
        new_button.canvas.pack(fill="x", padx=theme.px(16), pady=(0, theme.px(18)))

        header = tk.Frame(sidebar, bg=PALETTE["bg1"])
        header.pack(fill="x", padx=theme.px(20))
        section_label(header, theme, "Arbeitsbereiche", PALETTE["bg1"]).pack(side="left")

        holder = tk.Frame(sidebar, bg=PALETTE["bg1"])
        holder.pack(fill="both", expand=True, padx=theme.px(10), pady=(theme.px(6), 0))
        self.session_canvas = tk.Canvas(
            holder, bg=PALETTE["bg1"], highlightthickness=0, bd=0, yscrollincrement=4
        )
        self.session_canvas.pack(fill="both", expand=True)
        self.session_frame = tk.Frame(self.session_canvas, bg=PALETTE["bg1"])
        window = self.session_canvas.create_window(0, 0, window=self.session_frame, anchor="nw")
        self.session_frame.bind(
            "<Configure>",
            lambda _e: self.session_canvas.configure(scrollregion=self.session_canvas.bbox("all")),
        )
        self.session_canvas.bind(
            "<Configure>",
            lambda event: self.session_canvas.itemconfigure(window, width=event.width),
        )

        def wheel(event: Any) -> None:
            if self.session_frame.winfo_height() > self.session_canvas.winfo_height():
                self.session_canvas.yview_scroll(int(-event.delta / 40), "units")

        self.session_canvas.bind("<Enter>", lambda _e: self.root.bind_all("<MouseWheel>", wheel))
        self.session_canvas.bind("<Leave>", lambda _e: self.root.unbind_all("<MouseWheel>"))
        self.session_widgets: list[Any] = []
        self._render_sessions([])

        footer = tk.Frame(sidebar, bg=PALETTE["bg1"])
        footer.pack(fill="x", side="bottom", padx=theme.px(20), pady=theme.px(14))
        tk.Label(
            footer,
            text="Strg+N neu  ·  F5 Status  ·  Enter Start",
            bg=PALETTE["bg1"],
            fg=PALETTE["fg_subtle"],
            font=theme.font("body", 8),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            footer,
            text="Unabhängige Reserve · kein Zugriff auf app/ai",
            bg=PALETTE["bg1"],
            fg=blend(PALETTE["fg_subtle"], PALETTE["violet"], 0.45),
            font=theme.font("body", 8),
            anchor="w",
        ).pack(fill="x", pady=(theme.px(3), 0))

    def _render_sessions(self, rows: list[SessionRow]) -> None:
        import tkinter as tk

        theme = self.theme
        self.tooltip.hide()
        for widget in self.session_widgets:
            widget.destroy()
        self.session_widgets = []
        if not rows:
            label = tk.Label(
                self.session_frame,
                text="Noch keine Aufgabe.\nStrg+N legt die erste an.",
                bg=PALETTE["bg1"],
                fg=PALETTE["fg_subtle"],
                font=theme.font("body", 9),
                justify="left",
                anchor="w",
            )
            label.pack(fill="x", padx=theme.px(10), pady=theme.px(8))
            self.session_widgets.append(label)
            return
        for row in rows:
            canvas = tk.Canvas(
                self.session_frame,
                height=theme.px(52),
                bg=PALETTE["bg1"],
                highlightthickness=0,
                bd=0,
                cursor="hand2" if row.path else "arrow",
            )
            canvas.pack(fill="x", pady=theme.px(1))
            state = {"hover": False}

            def draw(canvas: Any = canvas, row: SessionRow = row, state: Any = state) -> None:
                canvas.delete("all")
                width = canvas.winfo_width()
                height = int(canvas.cget("height"))
                active = row.path is not None and row.path == self.active
                if active or state["hover"]:
                    round_rect(
                        canvas,
                        theme.px(2),
                        theme.px(2),
                        width - theme.px(2),
                        height - theme.px(2),
                        theme.px(9),
                        fill=PALETTE["bg3"] if active else PALETTE["bg2"],
                        outline="",
                    )
                if active:
                    canvas.create_rectangle(
                        theme.px(2),
                        theme.px(14),
                        theme.px(5),
                        height - theme.px(14),
                        fill=PALETTE["cyan"],
                        outline="",
                    )
                colour = (
                    PALETTE["orange"]
                    if row.path is None
                    else PALETTE["cyan"]
                    if active
                    else PALETTE["fg_subtle"]
                )
                canvas.create_text(
                    theme.px(18),
                    height / 2,
                    text=theme.glyph(row.icon),
                    font=theme.font("icon", 11),
                    fill=colour,
                    anchor="w",
                )
                title_font = theme.font("body", 10, "bold" if active else "normal")
                sub_font = theme.font("mono", 7)
                room = width - theme.px(54)
                canvas.create_text(
                    theme.px(44),
                    height / 2 - theme.px(9),
                    text=theme.fit(title_font, row.title, room),
                    font=title_font,
                    fill=PALETTE["fg"]
                    if active
                    else blend(PALETTE["fg_muted"], PALETTE["fg"], 0.5),
                    anchor="w",
                )
                canvas.create_text(
                    theme.px(44),
                    height / 2 + theme.px(10),
                    text=theme.fit(sub_font, row.subtitle, room),
                    font=sub_font,
                    fill=PALETTE["orange"] if row.path is None else PALETTE["fg_subtle"],
                    anchor="w",
                )

            def hover(on: bool, draw: Callable[[], None] = draw, state: Any = state) -> None:
                state["hover"] = on
                draw()

            canvas.bind("<Configure>", lambda _e, draw=draw: draw())
            canvas.bind("<Enter>", lambda _e, hover=hover: hover(True), add="+")
            canvas.bind("<Leave>", lambda _e, hover=hover: hover(False), add="+")
            if row.path is not None:
                canvas.bind("<Button-1>", lambda _e, path=row.path: self._activate(path))
            self.tooltip.bind(canvas, row.hint)
            self.session_widgets.append(canvas)

    def _build_center(self, center: Any) -> None:
        import tkinter as tk

        theme = self.theme
        head = tk.Frame(center, bg=PALETTE["bg0"])
        head.pack(fill="x", pady=(theme.px(6), theme.px(8)))
        section_label(head, theme, "Engine", PALETTE["bg0"]).pack(side="left")
        tk.Label(
            head,
            text="eine pro Sitzung · Modell bleibt gepinnt",
            bg=PALETTE["bg0"],
            fg=PALETTE["fg_subtle"],
            font=theme.font("body", 8),
        ).pack(side="left", padx=theme.px(10))
        legend = tk.Label(
            head,
            text=theme.glyph("info"),
            bg=PALETTE["bg0"],
            fg=PALETTE["fg_subtle"],
            font=theme.font("icon", 10),
            cursor="question_arrow",
        )
        legend.pack(side="right")
        self.tooltip.bind(
            legend,
            "Symbole:\n"
            + "\n".join(
                f"{capability.badge or theme.glyph(capability.icon)}  {capability.label}"
                for capability in CAPABILITIES.values()
            ),
        )

        self.engine_rows: list[dict[str, Any]] = []
        for engine in self.engines:
            canvas = tk.Canvas(
                center,
                height=theme.px(66),
                bg=PALETTE["bg0"],
                highlightthickness=0,
                bd=0,
                cursor="hand2",
            )
            canvas.pack(fill="x", pady=theme.px(3))
            row: dict[str, Any] = {
                "engine": engine,
                "canvas": canvas,
                "hover": False,
                "state": ("unknown", "Status wird geladen …"),
            }
            self.engine_rows.append(row)
            canvas.bind("<Configure>", lambda _e, row=row: self._draw_engine(row))
            canvas.bind("<Enter>", lambda _e, row=row: self._hover_engine(row, True))
            canvas.bind("<Leave>", lambda _e, row=row: self._hover_engine(row, False))
            canvas.bind("<Button-1>", lambda _e, key=engine.key: self._select(key))
            canvas.bind("<Double-Button-1>", lambda _e, key=engine.key: self.start_engine(key))

        self.composer = tk.Canvas(
            center, height=theme.px(84), bg=PALETTE["bg0"], highlightthickness=0, bd=0
        )
        self.composer.pack(fill="x", pady=(theme.px(14), theme.px(6)))
        self.composer.bind("<Configure>", lambda _e: self._draw_composer())

        head = tk.Frame(center, bg=PALETTE["bg0"])
        head.pack(fill="x", pady=(theme.px(16), theme.px(8)))
        section_label(head, theme, "Übergabe & Verlauf", PALETTE["bg0"]).pack(side="left")
        grid = tk.Frame(center, bg=PALETTE["bg0"])
        grid.pack(fill="x")
        actions = (
            ("Übergabe + Snapshot", "handoff", self.handoff_dialog,
             "Auftrag, Stand und Quellen sichern; optional Empfänger starten"),
            ("Empfang bestätigen", "check", self.ack_dialog,
             "Antwort des Empfängers mit Challenge prüfen und dokumentieren"),
            ("Arbeit fortsetzen", "play", self.show_resume,
             "Was zum Weitermachen nötig ist, nur aus lokalen Belegen"),
            ("Snapshot wiederherstellen", "history", self.restore_dialog,
             "Gesicherten Stand in einen NEUEN Worktree zurückholen"),
        )  # fmt: skip
        for index, (text, icon, command, tip) in enumerate(actions):
            button = NeonButton(
                grid,
                theme,
                text=text,
                icon=icon,
                command=command,
                bg=PALETTE["bg0"],
                accent="violet",
                height=40,
                align="left",
                tooltip=self.tooltip,
                tip=tip,
            )
            button.canvas.grid(
                row=index // 2, column=index % 2, sticky="ew", padx=theme.px(3), pady=theme.px(3)
            )
        grid.grid_columnconfigure(0, weight=1, uniform="actions")
        grid.grid_columnconfigure(1, weight=1, uniform="actions")
        self.hint = tk.Label(
            center,
            text="",
            bg=PALETTE["bg0"],
            fg=PALETTE["fg_subtle"],
            font=theme.font("body", 9),
            justify="left",
            anchor="w",
            wraplength=theme.px(600),
        )
        self.hint.pack(fill="x", pady=(theme.px(10), 0))
        center.bind(
            "<Configure>",
            lambda event: self.hint.configure(wraplength=max(theme.px(200), event.width)),
        )

    def _build_system(self, side: Any) -> None:
        import tkinter as tk

        theme = self.theme
        head = tk.Frame(side, bg=PALETTE["bg1"])
        head.pack(fill="x", padx=theme.px(16), pady=(theme.px(14), theme.px(6)))
        section_label(head, theme, "System", PALETTE["bg1"]).pack(side="left")
        NeonButton(
            head,
            theme,
            text="Status aktualisieren (F5)",
            icon="refresh",
            command=self.refresh,
            bg=PALETTE["bg1"],
            variant="icon",
            height=28,
            tooltip=self.tooltip,
        ).canvas.pack(side="right")
        self.chip_frame = tk.Frame(side, bg=PALETTE["bg1"])
        self.chip_frame.pack(fill="x", padx=theme.px(8))
        self.chip_widgets: list[Any] = []
        self._render_chips(status_chips({}))

        tools = tk.Frame(side, bg=PALETTE["bg1"])
        tools.pack(fill="x", side="bottom", padx=theme.px(14), pady=theme.px(14))
        section_label(tools, theme, "Diagnose", PALETTE["bg1"]).pack(
            fill="x", pady=(0, theme.px(6))
        )
        diagnostics = (
            ("Lokal prüfen", "pulse", "green", self.check_local,
             "Echte Antwort vom 64K-Modell über Ollama beweisen"),
            ("Cloud prüfen", "cloud", "blue", self.check_cloud,
             "Tunnel öffnen und echte Antwort über den Dev-Proxy prüfen"),
            ("Automationen", "gears", "violet", self.show_automations,
             "Geplante Aufgaben dieses Rechners und ihr letztes Ergebnis"),
            ("Cloud-Tunnel stoppen", "power", "red", self.stop_cloud,
             "Eigenen SSH-Tunnel und Proxy des Hubs beenden"),
        )  # fmt: skip
        grid = tk.Frame(tools, bg=PALETTE["bg1"])
        grid.pack(fill="x")
        grid.grid_columnconfigure(0, weight=1, uniform="diagnose")
        grid.grid_columnconfigure(1, weight=1, uniform="diagnose")
        labels = {"Cloud-Tunnel stoppen": "Tunnel stoppen"}
        for index, (text, icon, accent, command, tip) in enumerate(diagnostics):
            NeonButton(
                grid,
                theme,
                text=labels.get(text, text),
                icon=icon,
                command=command,
                bg=PALETTE["bg1"],
                accent=accent,
                height=36,
                align="left",
                tooltip=self.tooltip,
                tip=f"{text}: {tip}",
            ).canvas.grid(
                row=index // 2, column=index % 2, sticky="ew", padx=theme.px(2), pady=theme.px(2)
            )

    def _render_chips(self, chips: list[Chip]) -> None:
        import tkinter as tk

        theme = self.theme
        self.tooltip.hide()
        for widget in self.chip_widgets:
            widget.destroy()
        self.chip_widgets = []
        for chip in chips:
            canvas = tk.Canvas(
                self.chip_frame, height=theme.px(29), bg=PALETTE["bg1"], highlightthickness=0, bd=0
            )
            canvas.pack(fill="x")

            def draw(canvas: Any = canvas, chip: Chip = chip) -> None:
                canvas.delete("all")
                width = canvas.winfo_width()
                middle = int(canvas.cget("height")) / 2
                colour = tone(chip.state)
                glow_dot(canvas, theme, theme.px(16), middle, colour, PALETTE["bg1"])
                canvas.create_text(
                    theme.px(34),
                    middle,
                    text=theme.glyph(chip.icon),
                    font=theme.font("icon", 10),
                    fill=PALETTE["fg_muted"],
                    anchor="w",
                )
                label_font = theme.font("body", 9)
                canvas.create_text(
                    theme.px(58),
                    middle,
                    text=chip.label,
                    font=label_font,
                    fill=PALETTE["fg"],
                    anchor="w",
                )
                detail_font = theme.font("mono", 8)
                room = width - theme.px(66) - label_font.measure(chip.label) - theme.px(14)
                canvas.create_text(
                    width - theme.px(12),
                    middle,
                    text=theme.fit(detail_font, chip.detail, room),
                    font=detail_font,
                    fill=colour if chip.state in {"fail", "warn"} else PALETTE["fg_subtle"],
                    anchor="e",
                )

            canvas.bind("<Configure>", lambda _e, draw=draw: draw())
            self.tooltip.bind(canvas, f"{chip.label}: {chip.detail}")
            self.chip_widgets.append(canvas)

    # -- Zeichnen ----------------------------------------------------------------

    def engine(self, key: str) -> Engine:
        return next(engine for engine in self.engines if engine.key == key)

    def _select(self, key: str) -> None:
        self.selected = key
        self._draw_engines()
        self._draw_composer()

    def _hover_engine(self, row: dict[str, Any], on: bool) -> None:
        row["hover"] = on
        self._draw_engine(row)

    def _draw_engines(self) -> None:
        for row in self.engine_rows:
            self._draw_engine(row)

    def _draw_engine(self, row: dict[str, Any]) -> None:
        canvas, engine, theme = row["canvas"], row["engine"], self.theme
        canvas.delete("all")
        width, height = canvas.winfo_width(), int(canvas.cget("height"))
        if width <= 1:
            return
        accent = PALETTE[engine.tone]
        selected = engine.key == self.selected
        margin = theme.px(5)
        box = (margin, margin, width - margin, height - margin)
        radius = theme.px(14)
        if selected:
            glow_outline(canvas, theme, box, radius, accent, PALETTE["bg0"])
        round_rect(
            canvas,
            *box,
            radius,
            fill=PALETTE["bg3"] if selected or row["hover"] else PALETTE["bg2"],
            outline=accent
            if selected
            else PALETTE["line_strong"]
            if row["hover"]
            else PALETTE["line"],
            width=max(1, theme.px(1)),
        )
        middle = height / 2
        tile = theme.px(40)
        tx = margin + theme.px(12)
        round_rect(
            canvas,
            tx,
            middle - tile / 2,
            tx + tile,
            middle + tile / 2,
            theme.px(10),
            fill=blend(PALETTE["bg2"], accent, 0.16 if selected else 0.09),
            outline=blend(PALETTE["bg2"], accent, 0.45 if selected else 0.18),
        )
        canvas.create_text(
            tx + tile / 2,
            middle,
            text=theme.glyph(engine.icon),
            font=theme.font("icon", 15),
            fill=accent,
        )
        name_font = theme.font("body", 11, "bold")
        nx = tx + tile + theme.px(14)
        canvas.create_text(
            nx, middle - theme.px(10), text=engine.name, font=name_font, fill=PALETTE["fg"],
            anchor="w",
        )  # fmt: skip
        pill_font = theme.font("body", 7, "bold")
        px1 = nx + name_font.measure(engine.name) + theme.px(9)
        px2 = px1 + pill_font.measure(engine.variant) + theme.px(14)
        round_rect(
            canvas,
            px1,
            middle - theme.px(19),
            px2,
            middle - theme.px(2),
            theme.px(8),
            fill=blend(PALETTE["bg2"], accent, 0.14),
            outline="",
        )
        canvas.create_text(
            px1 + theme.px(7),
            middle - theme.px(10.5),
            text=engine.variant,
            font=pill_font,
            fill=accent,
            anchor="w",
        )

        # Rechts: Bereitschaft, davor die Fähigkeits-Symbole (wie Ollamas Badges).
        state, text = row["state"]
        dot_x = width - margin - theme.px(22)
        glow_dot(canvas, theme, dot_x, middle, tone(state), PALETTE["bg3"])
        dot_tag = f"dot-{engine.key}"
        canvas.create_oval(
            dot_x - theme.px(10),
            middle - theme.px(10),
            dot_x + theme.px(10),
            middle + theme.px(10),
            outline="",
            fill="",
            tags=(dot_tag,),
        )
        self.tooltip.bind(canvas, lambda row=row: row["state"][1], tag=dot_tag)
        badge = theme.px(27)
        gap = theme.px(5)
        cx = dot_x - theme.px(22)
        badge_font = theme.font("mono", 7, "bold")
        for key in reversed(engine.capabilities):
            capability = CAPABILITIES[key]
            tag = f"cap-{engine.key}-{key}"
            x2, x1 = cx, cx - badge
            round_rect(
                canvas,
                x1,
                middle - badge / 2,
                x2,
                middle + badge / 2,
                theme.px(7),
                fill=PALETTE["bg1"],
                outline=PALETTE["line_strong"] if selected else PALETTE["line"],
                tags=(tag,),
            )
            canvas.create_text(
                (x1 + x2) / 2,
                middle,
                text=capability.badge or theme.glyph(capability.icon),
                font=badge_font if capability.badge else theme.font("icon", 10),
                fill=accent if selected else PALETTE["fg_muted"],
                tags=(tag,),
            )
            self.tooltip.bind(canvas, f"{capability.label}: {capability.hint}", tag=tag)
            cx = x1 - gap
        model_font = theme.font("mono", 8)
        canvas.create_text(
            nx,
            middle + theme.px(11),
            text=theme.fit(model_font, engine.model, int(cx - nx - theme.px(10))),
            font=model_font,
            fill=PALETTE["fg_subtle"],
            anchor="w",
        )

    def _draw_composer(self) -> None:
        canvas, theme = self.composer, self.theme
        canvas.delete("all")
        width, height = canvas.winfo_width(), int(canvas.cget("height"))
        if width <= 1:
            return
        engine = self.engine(self.selected)
        accent = PALETTE[engine.tone]
        margin = theme.px(5)
        box = (margin, margin, width - margin, height - margin)
        radius = theme.px(22)
        glow_outline(canvas, theme, box, radius, PALETTE["violet"], PALETTE["bg0"], rings=2)
        round_rect(
            canvas,
            *box,
            radius,
            fill=PALETTE["bg2"],
            outline=blend(PALETTE["line_strong"], PALETTE["violet"], 0.35),
            width=max(1, theme.px(1)),
        )
        middle = height / 2
        # Engine-Pille links (wie Ollamas Modellwahl in der Eingabeleiste).
        pill_font = theme.font("body", 9, "bold")
        icon_font = theme.font("icon", 11)
        x1 = margin + theme.px(14)
        x2 = (
            x1
            + theme.px(12)
            + icon_font.measure(theme.glyph(engine.icon))
            + theme.px(8)
            + pill_font.measure(engine.label)
            + theme.px(8)
            + icon_font.measure(theme.glyph("chevron"))
            + theme.px(12)
        )
        round_rect(
            canvas,
            x1,
            middle - theme.px(17),
            x2,
            middle + theme.px(17),
            theme.px(17),
            fill=blend(PALETTE["bg2"], accent, 0.12),
            outline=blend(PALETTE["bg2"], accent, 0.5),
            tags=("pill",),
        )
        x = x1 + theme.px(12)
        canvas.create_text(
            x, middle, text=theme.glyph(engine.icon), font=icon_font, fill=accent, anchor="w",
            tags=("pill",),
        )  # fmt: skip
        x += icon_font.measure(theme.glyph(engine.icon)) + theme.px(8)
        canvas.create_text(
            x, middle, text=engine.label, font=pill_font, fill=PALETTE["fg"], anchor="w",
            tags=("pill",),
        )  # fmt: skip
        x += pill_font.measure(engine.label) + theme.px(8)
        canvas.create_text(
            x, middle, text=theme.glyph("chevron"), font=theme.font("icon", 8),
            fill=PALETTE["fg_muted"], anchor="w", tags=("pill",),
        )  # fmt: skip
        canvas.tag_bind("pill", "<Button-1>", self._engine_menu)
        canvas.tag_bind("pill", "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
        canvas.tag_bind("pill", "<Leave>", lambda _e: canvas.configure(cursor=""))

        # Start-Knopf rechts (Ollamas Senden-Kreis, hier mit Neon-Ringen).
        button = theme.px(23)
        bx = width - margin - theme.px(16) - button
        for factor, amount in ((1.55, 0.10), (1.28, 0.22)):
            canvas.create_oval(
                bx - button * factor,
                middle - button * factor,
                bx + button * factor,
                middle + button * factor,
                fill=blend(PALETTE["bg2"], accent, amount),
                outline="",
                tags=("start",),
            )
        canvas.create_oval(
            bx - button,
            middle - button,
            bx + button,
            middle + button,
            fill=accent,
            outline="",
            tags=("start",),
        )
        canvas.create_text(
            bx + theme.px(1),
            middle,
            text=theme.glyph("play"),
            font=theme.font("icon", 13),
            fill=PALETTE["bg0"],
            tags=("start",),
        )
        canvas.tag_bind("start", "<Button-1>", lambda _e: self.start_engine())
        canvas.tag_bind("start", "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
        canvas.tag_bind("start", "<Leave>", lambda _e: canvas.configure(cursor=""))
        self.tooltip.bind(canvas, lambda: f"{engine.label} starten (Enter)", tag="start")

        task = (self.resume.get("session") or {}).get("task") if self.resume else None
        text_font = theme.font("body", 9)
        if task:
            line = f"Startet in »{task}«. {engine.summary}"
        else:
            line = "Erst einen Arbeitsbereich wählen oder mit Strg+N anlegen."
        room = int(bx - button * 1.6 - x2 - theme.px(24))
        canvas.create_text(
            x2 + theme.px(16),
            middle,
            text=theme.fit(text_font, line, room),
            font=text_font,
            fill=PALETTE["fg_muted"] if task else PALETTE["fg_subtle"],
            anchor="w",
        )

    def _engine_menu(self, event: Any) -> None:
        import tkinter as tk

        menu = tk.Menu(
            self.root,
            tearoff=False,
            bg=PALETTE["bg3"],
            fg=PALETTE["fg"],
            activebackground=blend(PALETTE["bg3"], PALETTE["cyan"], 0.22),
            activeforeground=PALETTE["fg"],
            bd=0,
            font=self.theme.font("body", 10),
        )
        for engine in self.engines:
            menu.add_command(
                label=f"{engine.label}   ·   {engine.model}",
                command=lambda key=engine.key: self._select(key),
            )
        menu.tk_popup(event.x_root, event.y_root)

    def _draw_hero(self) -> None:
        canvas, theme = self.hero, self.theme
        canvas.delete("all")
        width, height = canvas.winfo_width(), int(canvas.cget("height"))
        if width <= 1:
            return
        base = PALETTE["bg0"]
        # Neon-Kante oben: Cyan -> Violett -> Magenta.
        steps = 48
        for index in range(steps):
            t = index / (steps - 1)
            colour = (
                blend(PALETTE["cyan"], PALETTE["violet"], t * 2)
                if t < 0.5
                else blend(PALETTE["violet"], PALETTE["magenta"], (t - 0.5) * 2)
            )
            canvas.create_rectangle(
                width * index / steps,
                0,
                width * (index + 1) / steps + 1,
                max(2, theme.px(2)),
                fill=colour,
                outline="",
            )
        # Synthwave-Horizont rechts: Sonne mit Streifen, perspektivisches Gitter.
        horizon = height * 0.60
        start = width * 0.46
        vanish = width * 0.80
        sun = theme.px(34)
        top = int(horizon - sun)
        for y in range(top, int(horizon)):
            depth = (y - top) / max(1, horizon - top)
            band = int((horizon - y) / max(1, theme.px(4)))
            if depth > 0.45 and band % 2 == 1:
                continue
            half = max(0.0, sun**2 - (horizon - y) ** 2) ** 0.5
            colour = blend(PALETTE["magenta"], PALETTE["orange"], depth)
            canvas.create_line(vanish - half, y, vanish + half, y, fill=blend(base, colour, 0.55))
        lines = 7
        for index in range(1, lines + 1):
            t = (index / lines) ** 1.8
            y = horizon + (height - horizon) * t
            for segment in range(6):
                x1 = start + (width - start) * segment / 6
                x2 = start + (width - start) * (segment + 1) / 6
                fade = min(1.0, (segment + 1) / 3)
                canvas.create_line(
                    x1, y, x2, y, fill=blend(base, PALETTE["violet"], (0.12 + 0.22 * t) * fade)
                )
        canvas.create_line(
            start, horizon, width, horizon, fill=blend(base, PALETTE["magenta"], 0.5)
        )
        spread = theme.px(64)
        for index in range(-14, 15):
            bottom = vanish + index * spread
            x_top = vanish + index * theme.px(5)
            if bottom < start:
                if x_top <= start:
                    continue
                # Linie an der Gitterkante kappen.
                ratio = (x_top - start) / (x_top - bottom)
                bottom_y = horizon + (height - horizon) * ratio
                canvas.create_line(
                    x_top, horizon, start, bottom_y, fill=blend(base, PALETTE["violet"], 0.16)
                )
                continue
            canvas.create_line(
                x_top, horizon, bottom, height, fill=blend(base, PALETTE["violet"], 0.24)
            )

        title, meta, _next = workspace_summary(self.values, self.resume)
        x = theme.px(32)
        canvas.create_text(
            x,
            theme.px(30),
            text="A R B E I T S B E R E I C H",
            font=theme.font("body", 7, "bold"),
            fill=PALETTE["cyan"],
            anchor="w",
        )
        title_font = theme.font("body", 18, "bold")
        canvas.create_text(
            x,
            theme.px(60),
            text=theme.fit(title_font, title, int(vanish - sun - x - theme.px(28))),
            font=title_font,
            fill=PALETTE["fg"],
            anchor="w",
        )
        branch_symbol(canvas, theme, x, theme.px(94), PALETTE["magenta"])
        meta_font = theme.font("mono", 8)
        offset = theme.px(18)
        canvas.create_text(
            x + offset,
            theme.px(94),
            text=theme.fit(meta_font, meta, int(start - x - offset)),
            font=meta_font,
            fill=PALETTE["fg_muted"],
            anchor="w",
        )

    # -- Rückmeldungen -----------------------------------------------------------

    def toast(self, text: str, state: str = "info", *, sticky: bool = False) -> None:
        icon = {"ok": "check", "fail": "error", "warn": "warning"}.get(state, "info")
        colour = tone(state) if state != "idle" else PALETTE["fg_subtle"]
        self.toast_icon.configure(text=self.theme.glyph(icon), fg=colour)
        self.toast_text.configure(
            text=text, fg=PALETTE["fg"] if state in {"ok", "fail", "warn"} else PALETTE["fg_muted"]
        )
        if self.toast_job is not None:
            self.root.after_cancel(self.toast_job)
            self.toast_job = None
        if not sticky:
            self.toast_job = self.root.after(8000, self.toast_idle)

    def toast_idle(self) -> None:
        self.toast_job = None
        self.toast_icon.configure(text=self.theme.glyph("pulse"), fg=PALETTE["fg_subtle"])
        self.toast_text.configure(
            text="Bereit. Engine wählen, Enter startet im gewählten Arbeitsbereich.",
            fg=PALETTE["fg_subtle"],
        )

    def fail(self, exc: BaseException) -> None:
        self.toast(str(exc).splitlines()[0] if str(exc) else type(exc).__name__, "fail")
        self.show_message("KAI Developer Hub", str(exc) or type(exc).__name__, state="fail")

    # -- Dialoge (dunkel, modal) --------------------------------------------------

    def _dialog(self, title: str, *, width: int = 560) -> tuple[Any, Any]:
        import tkinter as tk

        theme = self.theme
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title(title)
        dialog.configure(bg=PALETTE["bg1"])
        dialog.transient(self.root)
        dialog.resizable(True, True)
        tk.Frame(dialog, bg=PALETTE["violet"], height=max(2, theme.px(2))).pack(fill="x")
        body = tk.Frame(dialog, bg=PALETTE["bg1"], padx=theme.px(22), pady=theme.px(18))
        body.pack(fill="both", expand=True)
        dialog.minsize(theme.px(width), 1)
        return dialog, body

    def _present(self, dialog: Any) -> None:
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_reqheight()) // 3
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.deiconify()
        _dark_title_bar(dialog)
        dialog.grab_set()
        dialog.focus_force()

    def _text_widget(self, parent: Any, *, height: int, mono: bool = False) -> Any:
        import tkinter as tk

        return tk.Text(
            parent,
            height=height,
            bg=PALETTE["bg2"],
            fg=PALETTE["fg"],
            insertbackground=PALETTE["cyan"],
            selectbackground=blend(PALETTE["bg2"], PALETTE["cyan"], 0.35),
            relief="flat",
            highlightthickness=max(1, self.theme.px(1)),
            highlightbackground=PALETTE["line_strong"],
            highlightcolor=PALETTE["cyan"],
            font=self.theme.font("mono" if mono else "body", 9 if mono else 10),
            wrap="word",
            padx=self.theme.px(10),
            pady=self.theme.px(8),
            undo=True,
        )

    def _button_row(
        self, parent: Any, buttons: Sequence[tuple[str, str, str, Callable[[], None]]]
    ) -> None:
        import tkinter as tk

        row = tk.Frame(parent, bg=PALETTE["bg1"])
        row.pack(fill="x", pady=(self.theme.px(16), 0))
        for text, icon, variant, command in reversed(buttons):
            NeonButton(
                row,
                self.theme,
                text=text,
                icon=icon,
                command=command,
                bg=PALETTE["bg1"],
                variant=variant,
                height=36,
            ).canvas.pack(side="right", padx=(self.theme.px(8), 0))

    def show_message(self, title: str, text: str, *, state: str = "info") -> None:
        import tkinter as tk

        theme = self.theme
        dialog, body = self._dialog(title)
        head = tk.Frame(body, bg=PALETTE["bg1"])
        head.pack(fill="x")
        icon = {"ok": "check", "fail": "error", "warn": "warning"}.get(state, "info")
        tk.Label(
            head,
            text=theme.glyph(icon),
            bg=PALETTE["bg1"],
            fg=tone(state),
            font=theme.font("icon", 16),
        ).pack(side="left", padx=(0, theme.px(12)))
        tk.Label(
            head,
            text=title,
            bg=PALETTE["bg1"],
            fg=PALETTE["fg"],
            font=theme.font("body", 12, "bold"),
        ).pack(side="left")
        box = self._text_widget(body, height=min(18, max(3, text.count("\n") + 2)))
        box.insert("1.0", text)
        box.configure(state="disabled", bg=PALETTE["bg1"], highlightthickness=0)
        box.pack(fill="both", expand=True, pady=(theme.px(12), 0))
        self._button_row(body, [("Schließen", "check", "primary", dialog.destroy)])
        dialog.bind("<Return>", lambda _e: dialog.destroy())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        self._present(dialog)
        dialog.wait_window()

    def ask_yes_no(self, title: str, text: str, *, state: str = "warn") -> bool:
        import tkinter as tk

        theme = self.theme
        answer = {"value": False}
        dialog, body = self._dialog(title)
        head = tk.Frame(body, bg=PALETTE["bg1"])
        head.pack(fill="x")
        tk.Label(
            head,
            text=theme.glyph("warning" if state == "warn" else "info"),
            bg=PALETTE["bg1"],
            fg=tone(state),
            font=theme.font("icon", 16),
        ).pack(side="left", padx=(0, theme.px(12)))
        tk.Label(
            head,
            text=title,
            bg=PALETTE["bg1"],
            fg=PALETTE["fg"],
            font=theme.font("body", 12, "bold"),
        ).pack(side="left")
        box = self._text_widget(body, height=min(18, max(3, text.count("\n") + 2)))
        box.insert("1.0", text)
        box.configure(state="disabled", bg=PALETTE["bg1"], highlightthickness=0)
        box.pack(fill="both", expand=True, pady=(theme.px(12), 0))

        def choose(value: bool) -> None:
            answer["value"] = value
            dialog.destroy()

        self._button_row(
            body,
            [
                ("Abbrechen", "cancel", "ghost", lambda: choose(False)),
                ("Ja, fortfahren", "check", "primary", lambda: choose(True)),
            ],
        )
        dialog.bind("<Escape>", lambda _e: choose(False))
        self._present(dialog)
        dialog.wait_window()
        return answer["value"]

    def ask_text(
        self,
        title: str,
        prompt: str,
        *,
        initial: str = "",
        choices: Sequence[tuple[str, str]] = (),
    ) -> str | None:
        import tkinter as tk

        theme = self.theme
        answer: dict[str, str | None] = {"value": None}
        dialog, body = self._dialog(title)
        tk.Label(
            body, text=title, bg=PALETTE["bg1"], fg=PALETTE["fg"], font=theme.font("body", 12, "bold"),
            anchor="w",
        ).pack(fill="x")  # fmt: skip
        tk.Label(
            body, text=prompt, bg=PALETTE["bg1"], fg=PALETTE["fg_muted"], font=theme.font("body", 9),
            anchor="w", justify="left",
        ).pack(fill="x", pady=(theme.px(4), theme.px(10)))  # fmt: skip
        entry = tk.Entry(
            body,
            bg=PALETTE["bg2"],
            fg=PALETTE["fg"],
            insertbackground=PALETTE["cyan"],
            relief="flat",
            highlightthickness=max(1, theme.px(1)),
            highlightbackground=PALETTE["line_strong"],
            highlightcolor=PALETTE["cyan"],
            font=theme.font("body", 11),
        )
        entry.insert(0, initial)
        entry.pack(fill="x", ipady=theme.px(7))
        if choices:
            listing = tk.Listbox(
                body,
                height=min(8, len(choices)),
                bg=PALETTE["bg2"],
                fg=PALETTE["fg_muted"],
                selectbackground=blend(PALETTE["bg2"], PALETTE["cyan"], 0.3),
                selectforeground=PALETTE["fg"],
                relief="flat",
                highlightthickness=0,
                font=theme.font("mono", 8),
                activestyle="none",
            )
            for _value, label in choices:
                listing.insert("end", label)
            listing.pack(fill="x", pady=(theme.px(10), 0))

            def pick(_event: Any) -> None:
                picked = listing.curselection()
                if picked:
                    entry.delete(0, "end")
                    entry.insert(0, choices[picked[0]][0])

            listing.bind("<<ListboxSelect>>", pick)

        def accept() -> None:
            answer["value"] = entry.get().strip() or None
            dialog.destroy()

        self._button_row(
            body,
            [
                ("Abbrechen", "cancel", "ghost", dialog.destroy),
                ("Übernehmen", "check", "primary", accept),
            ],
        )
        dialog.bind("<Return>", lambda _e: accept())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        self._present(dialog)
        entry.focus_set()
        dialog.wait_window()
        return answer["value"]

    def show_report(self, title: str, value: Any) -> None:
        import tkinter as tk

        theme = self.theme
        dialog, body = self._dialog(title, width=760)
        tk.Label(
            body, text=title, bg=PALETTE["bg1"], fg=PALETTE["fg"], font=theme.font("body", 12, "bold"),
            anchor="w",
        ).pack(fill="x")  # fmt: skip
        frame = tk.Frame(body, bg=PALETTE["bg1"])
        frame.pack(fill="both", expand=True, pady=(theme.px(10), 0))
        content = self._text_widget(frame, height=26, mono=True)
        scrollbar = tk.Scrollbar(frame, command=content.yview)
        scrollbar.pack(side="right", fill="y")
        content.configure(yscrollcommand=scrollbar.set, wrap="none")
        content.pack(side="left", fill="both", expand=True)
        text = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
        content.insert("1.0", text)
        for tag, pattern, colour in (
            ("key", r'"[^"\n]+"(?=\s*:)', PALETTE["cyan"]),
            ("true", r"\btrue\b", PALETTE["green"]),
            ("false", r"\bfalse\b", PALETTE["red"]),
            ("null", r"\bnull\b", PALETTE["fg_subtle"]),
        ):
            content.tag_configure(tag, foreground=colour)
            for number, line in enumerate(text.splitlines(), start=1):
                for match in re.finditer(pattern, line):
                    content.tag_add(tag, f"{number}.{match.start()}", f"{number}.{match.end()}")
        content.configure(state="disabled")
        self._button_row(body, [("Schließen", "check", "primary", dialog.destroy)])
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        self._present(dialog)

    def _form(
        self,
        title: str,
        intro: str,
        fields: Sequence[tuple[str, str, str, int]],
    ) -> tuple[Any, Any, dict[str, Any]]:
        import tkinter as tk

        theme = self.theme
        dialog, body = self._dialog(title, width=680)
        tk.Label(
            body, text=title, bg=PALETTE["bg1"], fg=PALETTE["fg"], font=theme.font("body", 12, "bold"),
            anchor="w",
        ).pack(fill="x")  # fmt: skip
        tk.Label(
            body, text=intro, bg=PALETTE["bg1"], fg=PALETTE["fg_muted"], font=theme.font("body", 9),
            anchor="w", justify="left", wraplength=theme.px(640),
        ).pack(fill="x", pady=(theme.px(4), theme.px(8)))  # fmt: skip
        grid = tk.Frame(body, bg=PALETTE["bg1"])
        grid.pack(fill="both", expand=True)
        grid.grid_columnconfigure(1, weight=1)
        widgets: dict[str, Any] = {}
        for row, (name, label, initial, height) in enumerate(fields):
            tk.Label(
                grid, text=label, bg=PALETTE["bg1"], fg=PALETTE["fg_muted"],
                font=theme.font("body", 9), anchor="nw", justify="left", wraplength=theme.px(150),
            ).grid(row=row, column=0, sticky="nw", padx=(0, theme.px(12)), pady=theme.px(4))  # fmt: skip
            widget = self._text_widget(grid, height=height)
            widget.insert("1.0", initial)
            widget.grid(row=row, column=1, sticky="ew", pady=theme.px(4))
            widgets[name] = widget
        return dialog, body, widgets

    # -- Aktionen ----------------------------------------------------------------

    def _activate(self, path: Path | None) -> None:
        if path is None:
            return
        self.active = path
        self._draw_hero()
        self.refresh()

    def workspace(self) -> Path:
        self.hub.workflow.require_session(self.active, self.hub.STATE_ROOT)
        return self.active

    def new_session(self) -> None:
        hub = self.hub
        task = self.ask_text(
            "Neue KAI-Aufgabe",
            "Was soll bearbeitet werden? Der Hub legt dafür einen eigenen Worktree an.",
        )
        if not task:
            return

        def create() -> Path | None:
            try:
                row = hub.workflow.new_task(self.repo, hub.STATE_ROOT, task)
            except hub.workflow.OfflineBaseRequired as required:
                self.post(lambda required=required: self._accept_offline(task, required))
                return None
            return Path(row["worktree"])

        def created(path: Path | None) -> None:
            if path is not None:
                self.toast(f"Arbeitsbereich für »{task}« angelegt.", "ok")
                self._activate(path)

        self.run_background(
            create, busy="Hole frische Remote-Basis und lege Worktree an …", on_result=created
        )

    def _accept_offline(self, task: str, required: Any) -> None:
        hub = self.hub
        if not required.last_remote_sha or not required.fetched_at:
            self.fail(required)
            return
        cached = hub.workflow.cached_remote_base(
            hub.workflow._primary_checkout(self.repo), hub.STATE_ROOT
        )
        if cached is None:
            self.fail(required)
            return
        hours = int(cached["age_s"]) // 3600
        if self.ask_yes_no(
            "Offline-Basis bestätigen",
            f"Offline-Start von {required.last_remote_sha[:8]} (Stand vor {hours} h)?",
        ):
            self.run_background(
                lambda: Path(
                    hub.workflow.new_task(self.repo, hub.STATE_ROOT, task, allow_offline=True)[
                        "worktree"
                    ]
                ),
                busy="Lege Worktree auf der Offline-Basis an …",
                done="Arbeitsbereich mit OFFLINE-BASIS angelegt.",
                on_result=self._activate,
            )

    def start_engine(self, key: str | None = None) -> None:
        if key:
            self._select(key)
        engine = self.engine(self.selected)
        hub, target = self.hub, self.active

        def workspace() -> Path:
            hub.workflow.require_session(target, hub.STATE_ROOT)
            return target

        if engine.key == "kimi":
            self.run_background(
                lambda: hub.launch_kimi(workspace()),
                busy="Öffne Kimi und baue das Kontextpaket …",
                on_result=lambda pack: self.show_message(
                    "Kimi gestartet",
                    f"Kontextpaket im Explorer markiert:\n{pack}\n\nIn Kimi als Anhang hinzufügen.",
                ),
                done="Kimi gestartet.",
            )
            return
        launchers: dict[str, Callable[[bool], Any]] = {
            "opencode-local": lambda take_over: hub.launch_opencode(
                workspace(), "local", take_over=take_over
            ),
            "opencode-cloud": lambda take_over: hub.launch_opencode(
                workspace(), "cloud", take_over=take_over
            ),
            "hermes-local": lambda take_over: hub.launch_hermes(workspace(), take_over=take_over),
        }
        self.start_writer(engine, launchers[engine.key])

    def start_writer(self, engine: Engine, start: Callable[[bool], Any]) -> None:
        """Start a writing client; a busy workspace asks before taking it over."""
        self.toast(f"Starte {engine.label} …", "info", sticky=True)
        done = f"{engine.label} gestartet: die Konsole öffnet sich."
        if engine.key == "hermes-local":
            done += " Kontextpaket liegt in der Zwischenablage."

        def worker() -> None:
            try:
                start(False)
            except self.hub.WriterBusyError as exc:
                self.post(lambda exc=exc: self._ask_takeover(exc, start, done))
                return
            except Exception as exc:  # UI boundary
                self.post(lambda exc=exc: self.fail(exc))
                return
            self.post(lambda: (self.toast(done, "ok"), self.refresh()))

        threading.Thread(target=worker, daemon=True).start()

    def _ask_takeover(self, exc: Exception, start: Callable[[bool], Any], done: str) -> None:
        if self.ask_yes_no(
            "Arbeitsbereich belegt",
            f"{exc}\n\nTrotzdem übernehmen? Die Übernahme wird im Übergabe-Ledger "
            "protokolliert; der bisherige Client wird NICHT beendet.",
        ):
            self.run_background(lambda: start(True), busy="Übernehme Arbeitsbereich …", done=done)
        else:
            self.toast(
                "Start abgebrochen: Arbeitsbereich bleibt beim bisherigen Schreiber.", "warn"
            )

    def check_local(self) -> None:
        target = self.active
        self.run_background(
            lambda: self.hub.doctor(target, "local-inference"),
            busy="Prüfe lokale Inferenz (echte Antwort vom 64K-Modell) …",
            report="Lokal prüfen",
        )

    def check_cloud(self) -> None:
        target = self.active
        self.run_background(
            lambda: self.hub.doctor(target, "cloud"),
            busy="Prüfe Cloud-Reserve über den Tunnel …",
            report="Cloud prüfen",
        )

    def show_automations(self) -> None:
        self.run_background(
            self.hub.automation_inventory, busy="Lese Automationen …", report="Automationen"
        )

    def stop_cloud(self) -> None:
        self.run_background(
            self.hub.stop_cloud,
            busy="Stoppe Cloud-Tunnel …",
            on_result=lambda stopped: self.toast(
                "Cloud-Tunnel gestoppt." if stopped else "Kein Hub-Tunnel aktiv.", "ok"
            ),
            done=None,
        )

    def show_resume(self) -> None:
        target = self.active
        self.run_background(
            lambda: self.hub.render_resume(self.hub.resume_report(target)),
            busy="Sammle lokale Belege …",
            on_result=lambda text: self.show_message("Arbeit fortsetzen", text),
        )

    def restore_dialog(self) -> None:
        hub = self.hub
        try:
            snapshots = [row for row in hub.list_snapshots() if row["available"]]
        except Exception as exc:  # UI boundary
            self.fail(exc)
            return
        handoff_id = self.ask_text(
            "Snapshot wiederherstellen",
            "Übergabe-ID des Snapshots (Liste: CLI 'restore'). Ziel ist immer ein NEUER Worktree.",
            initial=snapshots[0]["handoff_id"] if snapshots else "",
            choices=[
                (
                    row["handoff_id"],
                    f"{row['handoff_id'][:8]}  {short_time(row['created_at'])}  "
                    f"{row['from_to']}  {row['task']}",
                )
                for row in snapshots
            ],
        )
        if not handoff_id:
            return
        try:
            plan = hub.restore(self.repo, handoff_id)
        except Exception as exc:  # UI boundary
            self.fail(exc)
            return
        if self.ask_yes_no(
            "In NEUEN Worktree wiederherstellen?", hub.render_restore(plan), state="info"
        ):
            self.run_background(
                lambda: Path(hub.restore(self.repo, handoff_id, apply=True)["target_worktree"]),
                busy="Stelle Snapshot in neuem Worktree her …",
                done="Snapshot wiederhergestellt.",
                on_result=self._activate,
            )

    def handoff_dialog(self) -> None:
        hub = self.hub
        try:
            repo = self.workspace()
        except Exception as exc:  # UI boundary
            self.fail(exc)
            return
        dialog, body, widgets = self._form(
            "Neue KAI-Übergabe",
            "Sichert Auftrag, Stand und Quelldateien als Snapshot mit Beleg. Der Empfänger "
            "bestätigt danach mit der Challenge aus dem Kontextpaket.",
            [
                ("from_agent", "Von Agent", "OpenCode", 1),
                ("to_agent", "An Agent", "Hermes", 1),
                ("task", "Auftrag", "", 2),
                ("completed", "Erledigt", "", 2),
                ("open_items", "Offen", "", 2),
                ("assumptions", "Annahmen", "", 2),
                ("next_action", "Nächster Schritt", "", 2),
                ("tests", "Tests", "", 2),
                (
                    "sources",
                    f"Quelldateien (je Zeile, max. {hub.workflow.MAX_SOURCE_FILES})",
                    "",
                    3,
                ),
            ],
        )

        def save(*, start_recipient: bool = False) -> None:
            values = {name: widget.get("1.0", "end").strip() for name, widget in widgets.items()}
            sources = values.pop("sources").splitlines()
            try:
                result = hub.create_handoff(repo, sources=sources, **values)
            except (hub.HubError, hub.workflow.WorkflowError) as exc:
                self.show_message("Übergabe nicht gespeichert", str(exc), state="fail")
                return
            dialog.destroy()
            self.show_message(
                "Übergabe gespeichert",
                f"Beleg: {result['payload_sha256']}\nKontext: {result['context_pack_path']}\n"
                "Die Antwort des Empfängers mit Challenge danach im Hub bestätigen.",
                state="ok",
            )
            if start_recipient:
                self.run_background(
                    lambda: hub.launch_recipient(repo, result),
                    busy=f"Starte Empfänger {result.get('to_agent', '')} …",
                    on_result=lambda started: self.toast(f"Gestartet: {started}", "ok"),
                )
            else:
                self.refresh()

        self._button_row(
            body,
            [
                ("Abbrechen", "cancel", "ghost", dialog.destroy),
                ("Nur sichern", "document", "ghost", save),
                ("Sichern + Empfänger starten", "play", "primary",
                 lambda: save(start_recipient=True)),
            ],
        )  # fmt: skip
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        self._present(dialog)

    def ack_dialog(self) -> None:
        hub = self.hub
        try:
            repo = self.workspace()
        except Exception as exc:  # UI boundary
            self.fail(exc)
            return
        pending = (self.resume.get("pending_handoffs") or []) if self.resume else []
        dialog, body, widgets = self._form(
            "Empfang und Verständnis bestätigen",
            "Die Antwort muss die Übergabe-ID und die Challenge aus dem Kontextpaket zitieren.",
            [
                ("handoff_id", "Übergabe-ID", pending[0]["handoff_id"] if pending else "", 1),
                ("agent", "Empfänger", pending[0]["to_agent"] if pending else "", 1),
                ("response", "Antwort des Empfängers", "", 9),
            ],
        )

        def save() -> None:
            try:
                event = hub.acknowledge_handoff(
                    repo,
                    **{key: widget.get("1.0", "end").strip() for key, widget in widgets.items()},
                )
            except hub.HubError as exc:
                self.show_message("Bestätigung fehlgeschlagen", str(exc), state="fail")
                return
            dialog.destroy()
            self.show_message(
                "Empfang dokumentiert", f"Bestätigung: {event['payload_sha256']}", state="ok"
            )
            self.refresh()

        self._button_row(
            body,
            [
                ("Abbrechen", "cancel", "ghost", dialog.destroy),
                ("Antwort prüfen und speichern", "shield", "primary", save),
            ],
        )
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        self._present(dialog)


def build_ui(hub: Any, repo: Path) -> HubWindow:
    import tkinter as tk

    _enable_dpi_awareness()
    root = tk.Tk()
    window = HubWindow(hub, repo, root)
    _dark_title_bar(root)
    return window


def run_ui(hub: Any, repo: Path) -> None:
    build_ui(hub, repo).root.mainloop()
