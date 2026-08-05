# ACTIVE_CLAIMS — wo die Claims wirklich stehen

**Das Register liegt nicht hier, sondern in `KAI-mirror/ACTIVE_CLAIMS.md`.**

Diese Datei entstand am 2026-08-05 mit #634 aus der Beobachtung, dass die
Claims-light-Regel auf eine Datei verwies, die es im Repo nicht gab. Die
Beobachtung war halb richtig: im Repo gab es sie nicht — im Arbeitsmirror schon,
mit über fünfzig geführten Zeilen, Leases und der am 12.07. nachgezogenen Regel
für OPS-Claims. #634 hat damit ein **zweites** Register angelegt, statt das erste
zu finden.

Zwei Register sind schlechter als eines, das man suchen muss: Ein Claim schützt
nur, wenn alle Spuren in dieselbe Liste schauen. Deshalb steht hier ein Verweis
und kein Duplikat.

## Protokoll (Kurzfassung — verbindlich ist die Fassung im Mirror)

1. **Vor** dem Worktree: Zeile in `KAI-mirror/ACTIVE_CLAIMS.md` eintragen, Scope
   als Pfade, nicht als Thema. Lease max. 24 h, `expires_at` ist Pflicht.
2. Aktive Claims auf Scope-Überlappung lesen. Überlappung heißt STOPP und
   koordinieren, nicht danebenarbeiten.
3. **Vor** dem Ship zusätzlich `gh pr list --state open` gegen den eigenen
   Dateiscope prüfen. Ein auto-merge-armierter Fremd-PR auf denselben Dateien ist
   ein Rennen um die Mainline, kein Konflikt, den man später in Ruhe auflöst.
4. Bei Merge, Abbruch **oder Session-Ende** schließen. Abgelaufene Zeilen werden
   als `expired` markiert, nie gelöscht.
5. Die Claim-Pflicht gilt auch für OPS — Deploys, Restarts, Backups, Restores,
   Gate-Auswertungen. Betriebskollisionen sind teurer als doppelte Entwicklung.

## Warum das ernst zu nehmen ist

Am 2026-08-05 entstanden **drei** Parallelbauten an einem Tag: #630 und #632 am
C1-Evaluator neben #628/#633, und #636 am W0-P1-Freshness-Gate neben dem
umfassenderen #638. Jeder hatte ein bezahltes Voll-Gate hinter sich, bevor die
Kollision überhaupt sichtbar wurde; #636 war zusätzlich auto-merge-armiert.

Der Grund ist strukturell, nicht Pech: Geordnete Backlogs — ADR-0016-Wellen,
Post-C1-Fahrplan — führen zwei Sessions zuverlässig auf **dasselbe nächste
Paket**. Kollision ist dort der Normalfall, den man verhindern muss, nicht der
Zufall, auf den man reagiert.

Wird eigene Arbeit verdrängt: erst den Diff des überlebenden PRs lesen, dann
reagieren. Bei #638 war der fremde Stand tatsächlich breiter — nichts zu retten.
Bei #632 fehlte genau ein Test, der einzeln nachgezogen wurde (#634).
