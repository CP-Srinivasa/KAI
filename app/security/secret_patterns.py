"""Ein Musterkatalog fuer GitHub-Token — fuer CI-Wache UND Laufzeit-Redaction.

WARUM DIESES MODUL EXISTIERT (2026-09-03)

Am 2026-09-01 landete ein ``gho_``-Token der GitHub CLI (Scopes ``repo``,
``workflow``) in einem Sitzungsprotokoll. Die Aufarbeitung deckte auf, dass das
Repository zwei voneinander unabhaengige GitHub-Muster fuehrte — und **beide**
dieselbe Luecke hatten:

    scripts/secret_guard.py     CI-Wache          ghp_ only  (behoben in #849)
    app/audit/sanitization.py   Laufzeit-Redaction ghp_|gh[osr]_{36}

Dass zweimal dasselbe fehlte, ist kein Zufall: zwei Kataloge, die dasselbe
beschreiben sollen, driften. Der eine wurde in #849 korrigiert, der andere blieb
zurueck — mit einem Kommentar darueber ("ghp_…, gho_…, ghs_…, etc."), der bereits
mehr behauptete, als das Muster darunter leistete.

Deshalb gibt es ab hier **einen** Katalog. Wer ein Praefix ergaenzt, ergaenzt es
fuer beide Waechter, oder fuer keinen.

STDLIB-ONLY, UND ZWAR ABSICHTLICH
---------------------------------
``scripts/secret_guard.py`` laeuft im CI-Hygiene-Job, und der installiert
nichts — kein ``pip install -r requirements.lock``, kein ``pip install -e .``.
Dieses Modul darf daher ausschliesslich die Standardbibliothek benutzen. Der
Importpfad traegt: ``app/__init__.py`` ist leer, ``app/security/__init__.py``
enthaelt nur einen Docstring.

ZWEI FORMEN, EINE QUELLE
------------------------
Die beiden Verbraucher brauchen dasselbe Wissen in unterschiedlicher Gestalt:

* Die CI-Wache meldet **pro Praefix** einen sprechenden Typ ("GitHub OAuth
  token"), damit im Bericht steht, welche Klasse gefunden wurde.
* Der Laufzeit-Sanitizer ersetzt durch **eine** Marke ``[REDACTED:github_token]``
  und braucht daher genau ein Muster. Zusaetzlich Wortgrenzen, weil er auf
  Fliesstext arbeitet und nicht auf Zeilen einer Datei.

Beides wird hier aus denselben :class:`TokenShape`-Eintraegen abgeleitet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

__all__ = [
    "GITHUB_TOKEN_SHAPES",
    "TokenShape",
    "github_combined_regex",
    "github_patterns",
]


@dataclass(frozen=True)
class TokenShape:
    """Ein Token-Format: sprechender Name, Praefix, Regex fuer den Rest."""

    label: str
    prefix: str
    body: str

    def as_regex(self) -> str:
        """Ungebundene Form — fuer den zeilenweisen Dateiscan der CI-Wache."""
        return re.escape(self.prefix) + self.body


#: Alle sechs Praefixe, die GitHub vergibt.
#:
#: Zur Laenge ``{20,}`` statt ``{36}``: GitHub hat die Tokenlaenge ueber die
#: Jahre veraendert, und fine-grained PATs sind deutlich laenger. Ein exaktes
#: ``{36}`` ist eine Wette auf ein Format, das der Emittent jederzeit aendern
#: darf — und eine verlorene Wette heisst hier: kein Alarm, keine Redaction.
#:
#: Das steht in scheinbarem Widerspruch zur Vorsichtsregel in
#: ``app/audit/sanitization.py`` ("lieber ein verpasstes Geheimnis als
#: Ueber-Redaction"). Der Widerspruch ist keiner: jene Regel wurde fuer das
#: **praefixlose** 40-Zeichen-AWS-Muster geschrieben (Neo-F-004), das auf
#: SHA-1-Hashes und Commit-IDs anschlaegt. Hier traegt das Praefix die
#: Trennschaerfe — ``ghs_`` gefolgt von 20+ alphanumerischen Zeichen ist kein
#: Text, der versehentlich entsteht.
GITHUB_TOKEN_SHAPES: Final[tuple[TokenShape, ...]] = (
    TokenShape("GitHub Personal Access Token", "ghp_", r"[A-Za-z0-9]{20,}"),
    TokenShape("GitHub OAuth token", "gho_", r"[A-Za-z0-9]{20,}"),
    TokenShape("GitHub server-to-server token", "ghs_", r"[A-Za-z0-9]{20,}"),
    TokenShape("GitHub user-to-server token", "ghu_", r"[A-Za-z0-9]{20,}"),
    TokenShape("GitHub refresh token", "ghr_", r"[A-Za-z0-9]{20,}"),
    TokenShape("GitHub fine-grained PAT", "github_pat_", r"[A-Za-z0-9_]{20,}"),
)


def github_patterns() -> tuple[tuple[str, str], ...]:
    """``(Typ, Regex)`` pro Praefix — die Form, die die CI-Wache meldet."""
    return tuple((shape.label, shape.as_regex()) for shape in GITHUB_TOKEN_SHAPES)


def github_combined_regex() -> str:
    """Eine Alternation mit Wortgrenzen — die Form fuer den Laufzeit-Sanitizer.

    Wortgrenzen sind hier noetig und in der CI-Wache nicht: der Sanitizer sieht
    Fliesstext, in dem ein Token mitten im Satz stehen kann. ``\b`` vorne
    verhindert, dass ``notagho_...`` als Treffer zaehlt; ``\b`` hinten haelt
    den Treffer am Wortende, damit die Marke nicht mitten in ein Wort schneidet.
    """
    return "|".join(rf"\b{shape.as_regex()}\b" for shape in GITHUB_TOKEN_SHAPES)
