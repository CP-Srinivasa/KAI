# Runtime-Identität — welcher Code läuft wirklich? (STAB-02)

**Befund 25.08.2026 (live):** `kai-server` (MainPID 2736616) lief seit 18.08. 22:30:09 auf
`79e6fca7`. Der Checkout stand auf `52145cc1` (23 Commits weiter), die Mainline auf `2ebf13d4`
(27). Vier Fast-Forward-Merges am 20./21.08. hatten den Checkout bewegt, ohne den Prozess neu
zu starten. Timer und CLI-Prozesse luden bereits neuen Code, der Server hielt alte Module im
Speicher — und `/health` sagte `{"status":"ok","version":"0.1.0"}`.

Lehre (18.08., Monitoring): ein gesunder Ausgang beweist keinen aktuellen Code. Der **Abstand**
zwischen Prozess und Checkout ist selbst der Befund.

## Wie prüfe ich, welcher Code läuft?

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok","version":"0.1.0",
#  "runtime_commit":"<40 hex>",   # Commit, mit dem der Prozess gestartet wurde
#  "checkout_commit":"<40 hex>",  # Commit, der jetzt im Checkout liegt
#  "drift_commits":0,             # Commits, die der Checkout voraus ist (None = nicht messbar)
#  "started_at_utc":"…",
#  "uptime_s":…,
#  "lock_changed":false}          # requirements.lock seit Prozessstart geändert?
```

`drift_commits > 0` heißt: der Code liegt auf der Platte, läuft aber nicht. `None` heißt
**nicht messbar** — das ist nie „aktuell".

Quelle im Prozess: `app/core/runtime_identity.py`. Die Identität wird **einmal beim Start**
eingefroren (nie pro Request) und als Artefakt `artifacts/runtime/runtime_identity.json`
abgelegt (atomar, Schema `runtime_identity/v1`). Der Checkout wird pro Anfrage billig aus
`.git/HEAD` + Ref-Datei gelesen (auch verknüpfte Worktrees, `packed-refs`); `git rev-list`
läuft nur, wenn sich der Checkout seit dem letzten Mal bewegt hat.

## Drei Konsumenten, eine Regel

`evaluate_runtime_drift()` ist die einzige Bewertungsfunktion (keine doppelt implementierte
Invariante — Lehre 21.08.):

| Zustand | Ergebnis |
|---|---|
| `drift_commits == 0` | nichts |
| `drift > 0`, Checkout seit < 60 min auf dem neuen Commit | nichts (Deploy unterwegs) |
| `drift > 0`, seit ≥ 60 min | **warning** |
| `drift > 0`, seit ≥ 24 h | **critical** |
| `drift` nicht messbar | **warning** („aktuell" ist unbelegt) |
| `lock_changed == true` | **warning** — `pip install -e .` + Restart |

„Seit" = mtime der Ref-Datei, die der ff-Merge neu schreibt; ist sie nicht messbar, gilt die
Prozess-Uptime als Untergrenze.

1. **`/health`** — Felder oben, fail-soft.
2. **Health-Check-Timer** (`kai alerts health-check`, eigener Prozess): liest das Artefakt,
   Komponente `runtime_identity`. Fehlt das Artefakt auf der Pi ⇒ warning (Server älter als
   STAB-02 oder nie gestartet).
3. **Deploy-Urteil** (`scripts/pi_deploy_step.sh` → `scripts/lib/pi_deploy_verdict.sh`): liest
   den `/health`-**Body** nach dem Deploy.

| Token | Urteil | Bedeutung |
|---|---|---|
| — | — | `runtime_commit == checkout` — Nachbedingung erfüllt |
| `RUNTIME_DRIFT_AFTER_RESTART:<n>` | **DEPLOY_FAILED** | Restart lief, Prozess meldet trotzdem den alten Commit — der Restart hat den Code nicht geladen |
| `RUNTIME_STALE_NO_RESTART:<n>` | DEPLOY_HOLD | kein Restart angefordert; Code liegt im Checkout, läuft nicht |
| `RUNTIME_IDENTITY_UNKNOWN` | DEPLOY_HOLD | `/health` ohne `runtime_commit` (Server vor STAB-02) |

## Betriebsfolge

- Die Felder erscheinen auf der Pi **erst nach dem nächsten Restart** (Deploy-Fenster STAB-07,
  `kai_deploy.sh --restart kai-server`, mit Freeze-Guard, Beweis und Rückweg). Bis dahin liefert
  der laufende Server das alte `/health` — und der Deploy meldet korrekt `RUNTIME_IDENTITY_UNKNOWN`.
- Ein `git pull` ohne Restart ist ab jetzt sichtbar: nach 60 min als Health-Befund, sofort in
  `/health`.
- `lock_changed` erinnert an `pip install -e .` (Lock-Änderung ⇒ Abhängigkeiten im Prozess
  veraltet, Lehre 18.08.).
