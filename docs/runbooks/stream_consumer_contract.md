# Stream-Consumer-Contract — kein neuer Strom ohne Abnehmer

**Status:** aktiv seit 2026-08-31 · **Herkunft:** Sprint G4 aus dem Master-Audit
`KMA-20260827` · **Gate:** `scripts/stream_consumer_ratchet.py` (CI-Job *Lint & Format Check*)

## Warum es das gibt

Das Master-Audit hat 22 Integrations-Gaps gefunden; **14 davon sind ein einziger
Defekt**: KAI baut Produzenten und nennt sie Systeme (R2-19). Fast jeder Strom hat
einen Schreiber, viele haben keinen Leser — und **niemand zahlt einen Preis, wenn
ein Strom stirbt** (R2-21). Belege:

- `telegram_webhook_rejections.jsonl`: 85 Schreibstellen, 0 Leser (A1-022)
- der Reconciler meldete **1.902-mal** gruen, ohne je etwas verglichen zu haben (A12-081)
- die Wachliste bewacht 11 Ausgaenge gegen 3 Eingaenge; 17 von 141 Stroemen haben
  ueberhaupt keinen Waechter
- `record_risk_gate_eval` hat einen Aufrufer und keinen Leser (A8-014)
- Folge: **fuenf Komponenten sind ausgefallen, ohne dass es jemand bemerkt hat** (R2-10)

Vierzehn Einzelfixes schliessen 14 Symptome. Dieses Gate schliesst die **Klasse**
— fuer den Zuwachs.

## Die Regel

Ein Stromname, der nicht in `scripts/stream_baseline.json` steht, ist nur
mergefaehig, wenn `config/stream_contracts.json` ihn deklariert:

| Feld | Prüfung |
|---|---|
| `reader` | Repo-relativer Modulpfad. Muss **existieren** und den Stromnamen **nennen**. Darf nicht `app/alerts/health_check.py` sein. |
| `failure_consequence` | Nicht leer, nicht „nichts". |
| `freshness_check` | Der Strom braucht eine Zeile in `_FRESHNESS_PER_FILE_MIN` (`app/alerts/health_check.py`). |
| `failure_would_be_noticed_by` | **Wer** merkt den Ausfall. „niemand" ⇒ `NEEDS_CONSUMER_FIRST`. |
| `time_to_notice` | **Nach welcher Zeit**. „nie" ⇒ `NEEDS_CONSUMER_FIRST`. |
| `decision_that_would_change` | **Welche Entscheidung** ohne den Strom anders ausfaellt. „keine" ⇒ `NEEDS_CONSUMER_FIRST`. |

Zusaetzlich strukturell: der Strom muss von **mindestens zwei Modulen** genannt
werden (die Freshness-Registry zaehlt nicht mit). Schreiber und Leser koennen
nicht dasselbe einzige Modul sein — genau das ist die Klasse
`telegram_webhook_rejections.jsonl`.

Ein Block, dessen drei Reifegrad-Felder „niemand / nie / keine" lauten, ist nicht
HARDEN-wuerdig, sondern **`NEEDS_CONSUMER_FIRST`**: erst der Abnehmer, dann die
Haertung. Dieselbe Vokabel gilt in der Funktionsmatrix des Audits.

## Ablauf beim Hinzufuegen eines Stroms

1. Leser bauen (eigenes Modul, wie `app/alerts/youtube_transcript_coverage.py`).
2. Zeile in `_FRESHNESS_PER_FILE_MIN` mit **gemessener** Schwelle
   (nicht aus einem Vorfall geraten — siehe `feedback_guard_thresholds_must_be_measured`).
3. Eintrag in `config/stream_contracts.json`.
4. `python scripts/stream_consumer_ratchet.py` lokal gruen.
5. Nach dem Merge optional `--update`, damit der Strom in die Baseline wandert.

## `INTENTIONALLY_INERT`

Absichtlich inerte Doktrin-Anker — nie ausgefuehrte LN-Verben, die
Kapital-Segmentierung, das Dritt-Prinzipal-Gate (R2-11) — fallen **nicht** unter
das Ratchet. Sie werden in `intentionally_inert` gefuehrt und im PR-Body
begruendet. Bindend dazu:

> Ein `INTENTIONALLY_INERT`-Anker darf in **keiner Reifegradaussage** als
> „vorhanden", „verdrahtet" oder „live" gezaehlt werden. Er ist eine Absicht,
> kein Mechanismus.

## Grenzen dieses Gates (bewusst benannt)

- **Kein Datenfluss-Beweis.** Gemessen werden Referenzen, nicht Lesevorgaenge.
  Ein Modul, das den Namen nennt und nie liest, kommt durch. Das Gate hebt den
  Boden, es beweist nicht den Konsum.
- **Kein rueckwirkender Zwang.** Die Baseline ist der Ist-Stand. Die Sanierung
  des Bestands waere ein 40-Sprint-Programm (R2-24) und ist ausdruecklich *nicht*
  Gegenstand.
- **Population ist Code-Wahrheit.** 110 im Code deklarierte Stroeme (108 Literale
  + 2 dynamische Familien). Das Audit zaehlte 141 Dateien *auf der Platte* und
  101 *Pfade im Code* — drei verschiedene Populationen, hier nicht vermischt.
- **Dynamische Namen** (f-String) werden als Familie `<modul>::*<suffix>` gefuehrt.
  Ein neuer dynamischer Schreiber faellt deshalb auf; welche konkreten Dateien er
  erzeugt, sieht das Gate nicht.
- **Umgehbar** durch `"".join([...])`-Konstruktionen. Das ist keine Sicherheits-,
  sondern eine Disziplin-Schranke.
