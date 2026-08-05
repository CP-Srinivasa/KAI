# ACTIVE_CLAIMS

Wer arbeitet gerade woran. Eine Zeile pro laufendem Arbeitsbereich — **bevor**
ein Worktree entsteht, ein Gate gefahren oder deployt wird.

Der Zweck ist nicht Buchhaltung, sondern Kollisionsvermeidung: an KAI arbeiten
mehrere Agenten-Sessions plus Codex parallel auf derselben Mainline. Zwei
Sessions, die unabhängig dieselbe Datei anfassen, produzieren einen Konflikt,
den erst der Merge sichtbar macht — meistens nachdem beide Seiten ihr Voll-Gate
schon bezahlt haben. Ein sichtbarer Claim kostet eine Zeile und spart den
doppelten Lauf. Derselbe Mechanismus hat am 2026-08-05 gefehlt: #630/#632 und
#628/#633 entstanden zweimal nebeneinander am selben C1-Evaluator.

Diese Datei ist bewusst **nicht** CI-erzwungen. Ein Gate, das man mit einer
leeren Zeile passiert, misst nichts; die Disziplin trägt sich über die
Sichtbarkeit oder gar nicht.

## Protokoll

1. **Vor** dem Worktree: Zeile unter „Aktiv" eintragen (Scope so eng wie
   möglich — Pfade, nicht Themen).
2. Prüfen, ob dort schon jemand steht. Überlappung → anderen Scope wählen oder
   den bestehenden Claim übernehmen, nicht danebenarbeiten.
3. Nach Merge **oder** Abbruch: Zeile entfernen. Ein Claim, der einen
   verwaisten Branch überlebt, ist schlimmer als kein Claim — er sperrt
   Arbeitsbereiche, an denen niemand mehr sitzt.

Ein Claim ist eine Absichtserklärung, kein Besitz. Er sagt „hier läuft etwas",
nicht „Finger weg".

## Aktiv

| Seit (UTC) | Wer | Scope (Pfade) | Branch / PR |
|---|---|---|---|
| — | — | — | — |

## Beispiel (nicht aktiv)

```
| 2026-08-05 11:26 | Claude/Session | tests/unit/test_c1_payment_branch_eval.py, ACTIVE_CLAIMS.md | fix/c1-ledger-shape-claims-20260805 |
```
