# Kontrast der Neon-Tokens — Messung 2026-09-16

**Quelle:** `web/src/index.css` @ `84ce1e26`, Blöcke `:root` (Light) und `.dark`. Rechnung nach WCAG 2.1 (relative Luminanz, Kontrastverhältnis), Skript im Sitzungs-Scratchpad `contrast_tokens.py` (reine Arithmetik, keine Abhängigkeiten).
**Anlass:** Facelift-Plan v2.1 §7 / DALI-Finding F-024 — im Code steht seit 2026-05-08 „Sub-WCAG-Body wird bewusst akzeptiert (Vibe vor Lesbarkeit)", gemessen war es nie. Diese Datei liefert die Zahl für den Risikoregister-Eintrag (nach dem Deploy 17./18.09., eigener Doku-PR).

Schwellen: ✓ ≥ 4,5:1 (AA Fließtext) · ◐ ≥ 3:1 (AA großer Text ≥ 18 pt bzw. 14 pt fett, UI-Komponenten und Grafik) · ✗ < 3:1.

## Dark (Standard-Theme, Betrieb)

| Token | Hex | bg-0 | bg-1 (Karten) | bg-2 | bg-3 |
|---|---|---|---|---|---|
| fg | #E8ECF3 | 16,30 ✓ | 15,43 ✓ | 14,56 ✓ | 13,47 ✓ |
| fg-muted | #9CA5B8 | 7,81 ✓ | 7,39 ✓ | 6,97 ✓ | 6,45 ✓ |
| fg-subtle | #6D778A | 4,28 ◐ | 4,05 ◐ | 3,82 ◐ | 3,54 ◐ |
| accent | #2F7DFF | 5,06 ✓ | 4,79 ✓ | 4,52 ✓ | 4,18 ◐ |
| pos | #00FFA3 | 14,56 ✓ | 13,78 ✓ | 13,00 ✓ | 12,03 ✓ |
| neg | #FF1744 | 5,02 ✓ | 4,75 ✓ | 4,48 ◐ | 4,15 ◐ |
| warn | #FF5A1E | 6,19 ✓ | 5,86 ✓ | 5,53 ✓ | 5,12 ✓ |
| info | #00E5FF | 12,56 ✓ | 11,88 ✓ | 11,21 ✓ | 10,37 ✓ |
| ai | #8B5CF6 | 4,56 ✓ | 4,32 ◐ | 4,07 ◐ | 3,77 ◐ |

## Light

| Token | Hex | bg-0 | bg-1 (Karten) | bg-2 | bg-3 |
|---|---|---|---|---|---|
| fg | #121720 | 16,90 ✓ | 17,96 ✓ | 17,52 ✓ | 16,47 ✓ |
| fg-muted | #5C6676 | 5,46 ✓ | 5,81 ✓ | 5,66 ✓ | 5,32 ✓ |
| fg-subtle | #8C96A5 | 2,81 ✗ | 2,99 ✗ | 2,92 ✗ | 2,74 ✗ |
| accent | #2382FA | 3,50 ◐ | 3,72 ◐ | 3,63 ◐ | 3,41 ◐ |
| pos | #00D787 | 1,79 ✗ | 1,90 ✗ | 1,85 ✗ | 1,74 ✗ |
| neg | #EB1E4B | 4,10 ◐ | 4,36 ◐ | 4,25 ◐ | 3,99 ◐ |
| warn | #F04B05 | 3,46 ◐ | 3,68 ◐ | 3,58 ◐ | 3,37 ◐ |
| info | #00B4DC | 2,31 ✗ | 2,45 ✗ | 2,39 ✗ | 2,25 ✗ |
| ai | #845AF0 | 4,20 ◐ | 4,47 ◐ | 4,36 ◐ | 4,09 ◐ |

## Befund

- **Dark ist in Ordnung.** Alle Fließtext-Töne erreichen AA; `fg-subtle` (Hilfstexte, Labels in 11 px) liegt mit 3,5–4,3:1 nur auf AA-Large-Niveau und wird im Dashboard überwiegend in 11-px-Schrift benutzt — das ist der einzige reale Dark-Mode-Befund. `neg` und `ai` fallen auf bg-2/bg-3 knapp unter 4,5:1, werden dort aber als Badge-Text mit eigenem Hintergrund (`bg-neg/10` etc.) gerendert, nicht als Fließtext.
- **Light ist das Problem.** `pos` (1,8:1) und `info` (2,3:1) sind auf allen Flächen unter 3:1, also auch als UI-Komponente nicht AA-fähig; `fg-subtle` (2,7–3,0:1) ebenfalls. Betroffen sind damit genau die Statusfarben „ok/erreicht" und „Live/Daten", die das Dashboard als Bedeutungsträger benutzt. Weil jeder Status zusätzlich Text oder Symbol trägt (Sprint-Regel v2.1), geht keine Information verloren, aber die farbige Lesbarkeit ist im Light-Theme nicht gegeben.
- **Einordnung:** Der Betrieb läuft im Dark-Theme (Default `class="dark"` in `index.html`). Der Kommentar „Vibe vor Lesbarkeit" beschreibt also faktisch nur den Light-Modus zutreffend.

## Vorschlag für das Risikoregister (nach Deploy)

- Risiko: „Statusfarben im Light-Theme unter WCAG 3:1 (`pos` 1,8:1, `info` 2,3:1, `fg-subtle` 2,8:1)". Einstufung niedrig (Dark ist Default, Status nie nur Farbe). Gegenmaßnahme optional: Light-Werte für `pos` auf ≈ #00A868 und `info` auf ≈ #0086A8 abdunkeln (beide dann ≥ 3,5:1 auf bg-1), `fg-subtle` Light auf ≈ #6F7A8A (≈ 4,5:1). Kein Handlungszwang.
- Dark: `fg-subtle` für 11-px-Hilfstexte auf ≈ #7C8699 heben (≈ 5:1 auf bg-1) — kleine Änderung, sichtbar ruhiger; Entscheidung DALI/Operator.
