"""Waechter fuer die Transkript-Abdeckung der YouTube-Ingestion.

Vier Monate lang lief die YouTube-Pipeline puenktlich alle zwei Stunden, sammelte
Videos ein und lieferte den eigentlichen Inhalt nie: ``fetch_transcript`` rief eine
Methode, die es in ``youtube-transcript-api`` 1.x nicht mehr gab, fing den
``AttributeError`` selbst ab und gab ``None`` zurueck. Der Aufruf im Cron-Skript
endet auf ``>/dev/null 2>&1`` und protokolliert nur bei Exit != 0 — Exit war 0.
Ergebnis, am 2026-08-28 gemessen: **0 von 2584** Dokumenten hatten je einen
Transkript-Text. Der Eingang war gruen, der Inhalt leer.

Dieser Waechter schaut deshalb nicht, **ob** Dokumente ankommen (das tut bereits
``_check_document_ingest``), sondern **ob sie Inhalt tragen**.

Alle Schwellen sind gemessen, nicht gesetzt (Stichproben vom 2026-08-28 auf dem Pi):

* Beschreibungstexte: n=2584, max **143** Zeichen, p99 139 — nie laenger.
* Kuerzestes echtes Transkript in einer 30er-Stichprobe: **315** Zeichen.
  ⇒ ``TRANSCRIPT_MIN_CHARS = 200`` liegt ueber jeder beobachteten Beschreibung
  und unter jedem beobachteten Transkript.
* Trefferquote ueber 30 zuletzt eingesammelte Videos: **25/30 = 83,3 %**.
  Die 5 Fehlschlaege lagen vollstaendig bei zwei nicht-englischen Kanaelen
  (``Trader sanju`` 0/3, ``DAY TRADER తెలుగు`` 0/2) — ``_PREFERRED_LANGUAGES``
  ist ``["en", "de"]``, die liefern legitim nichts.

Genau deshalb reicht eine Gesamtquote nicht. Eine Kanalliste mit vielen
fremdsprachigen Kanaelen druecke sie legitim, ein echter Ausfall druecke sie
ebenso — die Aggregatzahl allein kann beides nicht unterscheiden. Die Zerlegung
je Kanal ist hier kein Beiwerk, sondern der Unterscheider:

* **jeder** Kanal trocken  ⇒ Ausfall (``blackout``) — so sah der Vorfall aus,
* **einzelne** Kanaele trocken, andere liefern ⇒ Sprach-Artefakt, benannt in der
  Meldung samt Quote ohne diese Kanaele (leave-one-out).

Nachtrag 2026-08-31 — zwei eigene Befunde, live widerlegt:

1. **Die Handlungsanweisung war erledigt und falsch.** Der ``blackout``-Text
   schrieb „fetch_transcript gegen die installierte youtube-transcript-api
   pruefen". Genau das war am 27./28.08. repariert (#792), der Vertragstest
   daneben ist gruen — die Meldung schickte den Operator vier Tage lang auf
   eine Spur, die schon abgearbeitet war. Ein Waechter, der ein Ergebnis
   verlangt, das bereits vorliegt, kostet Vertrauen, nicht nur Zeit.
2. **Die Schwellen-Begruendung oben stimmt nicht mehr.** „Beschreibungstexte:
   max 143 Zeichen" galt fuer den API-Schnipsel. Seit die Uploads aus dem
   Atom-Feed kommen, liefert der die VOLLE Beschreibung: am 2026-08-31 waren
   **47 von 76** Beschreibungen der letzten 30 Tage >= 1000 Zeichen, die
   laengste 4976. ``TRANSCRIPT_MIN_CHARS = 200`` trennt also nichts mehr. Es
   rettet nur, dass ``text_source`` heute explizit ``"description"`` sagt —
   der Laengen-Rueckfall (``_YT_COVERAGE_SQL_BY_LENGTH``, SQLite ohne JSON1)
   wuerde 69 dieser 76 Beschreibungen als Transkript zaehlen und die Wache
   **still gruen** melden. Deshalb ist der Rueckfall jetzt als degradiert
   gekennzeichnet, statt so zu tun, als messe er dasselbe.

Statt einer Anweisung nennt die Meldung jetzt den **aufgezeichneten Grund**
(``youtube_meta.transcript_status``): ``transcripts_disabled`` und
``none_found`` verlangen nichts, ``error:IpBlocked`` verlangt Warten, und ein
Code-Fehler traegt seinen Ausnahmetyp. Vorher stand der Grund nirgends — die
einzige Diagnose waere eine neue Anfrage an YouTube gewesen, also die Handlung,
die den IP-Block ausgeloest hat.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Ab dieser Laenge kann ``raw_text`` keine Video-Beschreibung mehr sein.
TRANSCRIPT_MIN_CHARS = 200

#: Beobachtungsfenster in Stunden. Der Lauf greift ~alle 2 h; 24 h ueberdeckt
#: auch eine ruhige Nacht ohne neue Uploads.
COVERAGE_WINDOW_HOURS = 24

#: Unterhalb dieser Quote wird gemeldet. Gemessene Basisrate 83,3 % — 40 % laesst
#: reichlich Luft fuer einen ungewoehnlichen Kanal-Mix und faengt trotzdem jeden
#: Einbruch, der diesen Namen verdient.
COVERAGE_WARN_RATIO = 0.40

#: Unter dieser Stichprobengroesse ist eine Quote nicht aussagekraeftig.
COVERAGE_MIN_SAMPLE = 10

#: Bei exakt null Transkripten genuegt eine kleine Stichprobe — der historische
#: Ausfall war 0 %, und 0 % ist auch bei n=3 kein Zufall des Kanal-Mixes.
BLACKOUT_MIN_SAMPLE = 3


@dataclass(frozen=True)
class ChannelCoverage:
    """Transkript-Abdeckung eines einzelnen Kanals im Fenster."""

    channel: str
    total: int
    with_transcript: int

    @property
    def ratio(self) -> float:
        return self.with_transcript / self.total if self.total else 0.0

    @property
    def is_dry(self) -> bool:
        """Kein einziges Transkript — Kandidat fuer ein Sprach-Artefakt."""
        return self.total > 0 and self.with_transcript == 0


@dataclass(frozen=True)
class CoverageVerdict:
    """Aggregat **und** Zerlegung — nie das eine ohne das andere."""

    total: int
    with_transcript: int
    by_channel: tuple[ChannelCoverage, ...]
    status: str  # "no_population" | "ok" | "low" | "blackout"
    #: Aufgezeichnete Gruende aus ``youtube_meta.transcript_status``, absteigend.
    #: Leer bei Zeilen aus der Zeit vor dem Feld — dann faellt die Meldung auf
    #: den ehrlichen Satz „kein Grund aufgezeichnet" zurueck, nicht auf eine
    #: Vermutung.
    by_status: tuple[tuple[str, int], ...] = ()
    #: True, wenn nur die schwaechere Laengenmessung moeglich war (SQLite ohne
    #: JSON1). Die Meldung sagt das dann dazu — eine Quote aus einer Heuristik,
    #: die Beschreibungen nicht mehr von Transkripten trennt, darf sich nicht
    #: wie eine Messung lesen.
    degraded_length_proxy: bool = False

    @property
    def ratio(self) -> float | None:
        return self.with_transcript / self.total if self.total else None

    @property
    def dry_channels(self) -> tuple[ChannelCoverage, ...]:
        return tuple(c for c in self.by_channel if c.is_dry)

    @property
    def ratio_excluding_dry(self) -> float | None:
        """Leave-one-out: die Quote, wenn die trockenen Kanaele draussen bleiben.

        Liegt sie hoch, ist eine gedrueckte Gesamtquote ein Kanal-Mix-Effekt und
        kein Ausfall. Ist sie ``None``, liefert kein einziger Kanal — dann ist es
        einer.
        """
        rest = [c for c in self.by_channel if not c.is_dry]
        total = sum(c.total for c in rest)
        if not total:
            return None
        return sum(c.with_transcript for c in rest) / total

    @property
    def is_healthy(self) -> bool:
        return self.status in ("ok", "no_population")


def classify_coverage(
    channels: list[ChannelCoverage],
    *,
    warn_ratio: float = COVERAGE_WARN_RATIO,
    min_sample: int = COVERAGE_MIN_SAMPLE,
    blackout_min_sample: int = BLACKOUT_MIN_SAMPLE,
    by_status: tuple[tuple[str, int], ...] = (),
    degraded_length_proxy: bool = False,
) -> CoverageVerdict:
    """Ein Urteil aus der Kanal-Zerlegung — reine Funktion, ohne DB und ohne Uhr.

    ``by_status`` urteilt NICHT mit: die Gruende erklaeren einen Ausfall, sie
    definieren ihn nicht. Ein Kanal ohne Untertitel bleibt ein Kanal ohne
    Transkript — die Quote darf sich davon nicht schoenrechnen lassen.
    """
    ordered = tuple(sorted(channels, key=lambda c: (-c.total, c.channel)))
    total = sum(c.total for c in ordered)
    with_transcript = sum(c.with_transcript for c in ordered)
    reasons = tuple(sorted(by_status, key=lambda kv: (-kv[1], kv[0])))

    if total == 0:
        # Keine erreichbare Population — das ist kein Befund, sondern Nacht.
        # Ob ueberhaupt noch Dokumente ankommen, beantwortet _check_document_ingest.
        return CoverageVerdict(0, 0, ordered, "no_population", reasons, degraded_length_proxy)

    if with_transcript == 0 and total >= blackout_min_sample:
        return CoverageVerdict(total, 0, ordered, "blackout", reasons, degraded_length_proxy)

    ratio = with_transcript / total
    if total >= min_sample and ratio < warn_ratio:
        return CoverageVerdict(
            total, with_transcript, ordered, "low", reasons, degraded_length_proxy
        )

    return CoverageVerdict(total, with_transcript, ordered, "ok", reasons, degraded_length_proxy)


def render_message(verdict: CoverageVerdict, *, window_hours: int = COVERAGE_WINDOW_HOURS) -> str:
    """Meldetext: Aggregat, Zerlegung und Konzentration in einem Satzblock."""
    ratio = verdict.ratio
    quote = "keine Population" if ratio is None else f"{ratio:.0%}"
    head = (
        f"YouTube-Transkripte: {verdict.with_transcript}/{verdict.total} Videos der letzten "
        f"{window_hours}h tragen einen Transkript-Text ({quote})"
    )

    top = ", ".join(f"{c.channel} {c.with_transcript}/{c.total}" for c in verdict.by_channel[:6])
    parts = [head, f"je Kanal: {top}"]
    if len(verdict.by_channel) > 6:
        parts[-1] += f" (+{len(verdict.by_channel) - 6} weitere)"

    dry = verdict.dry_channels
    if verdict.status == "blackout":
        parts.append(
            "KEIN einziger Kanal liefert — das ist kein Sprach-Artefakt, sondern ein Ausfall"
        )
        parts.append(_render_reasons(verdict))
    elif dry:
        rest = verdict.ratio_excluding_dry
        parts.append(
            f"trocken: {', '.join(c.channel for c in dry)}"
            + (
                f" — ohne diese {rest:.0%}, also Kanal-Mix und kein Ausfall"
                if rest is not None
                else ""
            )
        )
    if verdict.degraded_length_proxy:
        parts.append(
            "ACHTUNG: nur Laengen-Heuristik verfuegbar (SQLite ohne JSON1) — seit dem "
            "Feed-Umbau sind Beschreibungen regelmaessig laenger als die Schwelle, "
            "diese Quote ist eine Obergrenze und kein Nachweis"
        )
    return "; ".join(parts)


#: Der Platzhalter, den die SQL fuer Zeilen ohne ``transcript_status`` liefert.
#: Er ist KEIN Grund — steht nur er da, ist gar keiner aufgezeichnet.
NO_REASON_RECORDED = "(nicht aufgezeichnet)"


def _render_reasons(verdict: CoverageVerdict) -> str:
    """Der aufgezeichnete Grund — oder das ehrliche Eingestaendnis, dass keiner da ist."""
    named_reasons = [kv for kv in verdict.by_status if kv[0] != NO_REASON_RECORDED]
    if not named_reasons:
        return (
            "kein Grund aufgezeichnet (youtube_meta.transcript_status leer) — "
            "Ursache aus den Ingest-Logs belegen, NICHT durch neue Abrufe messen "
            "(das hat am 2026-08-28 den IP-Block ausgeloest)"
        )
    named = ", ".join(f"{reason} {count}x" for reason, count in named_reasons[:4])
    missing = next((c for r, c in verdict.by_status if r == NO_REASON_RECORDED), 0)
    tail = f" (+{missing} Zeilen ohne Grund)" if missing else ""
    return f"aufgezeichneter Grund: {named}{tail}"
