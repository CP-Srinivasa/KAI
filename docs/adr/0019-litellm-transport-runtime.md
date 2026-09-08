# ADR 0019 — Die LiteLLM-Runtime ist ein eigener Artefaktbaum, kein Teil des KAI-Release

- **Status:** **ACCEPTED — BINDEND** (Operator-Entscheid 2026-09-08).
  Freigegeben ist die **Architekturentscheidung**, nicht ihre Ausfuehrung: der
  Transport-Baum darf gebaut werden, ein LiteLLM-Dienst darf weiterhin nicht
  starten. Siehe „Status der Umsetzung" am Ende.
- **Datum:** 2026-09-08
- **Betroffen:** `deploy/systemd/kai-litellm.service`, `scripts/pi_make_release.sh`, `requirements.lock`, `pyproject.toml`, `/health`, der Backup-Vertrag in `deploy/bin/standby_to_usb.sh`
- **Präzisiert:** [ADR 0017](0017-ai-control-plane-and-litellm-transport.md) § Deployment. Ersetzt nichts, hebt nichts auf.
- **Ändert nicht:** `app/ai` bleibt alleinige Control-Plane. Diese Ergänzung betrifft ausschließlich, **wo das Transport-Binary liegt** — nicht, wer entscheidet.

## Kontext: eine Zusage, die die Implementierung nicht halten kann

ADR 0017 sagt, LiteLLM sei „austauschbar, ohne dass die Governance mitwandert".
Am 2026-09-07 hat sich gezeigt, dass die vorgesehene Implementierung diese
Zusage nicht einlösen kann. Der erste Bau eines Release mit dem Extra scheiterte
nicht an einem Werkzeugfehler, sondern an einem echten Konflikt:

```
litellm 1.99.0 depends on openai<3.0.0 and >=2.20.0
The user requested (constraint) openai==3.6.0
ERROR: ResolutionImpossible
```

`requirements.lock` pinnt `openai==3.6.0` — die Version, mit der die direkten
Provider und damit der produktive AI-Core laufen. Die LiteLLM-1.x-Reihe verlangt
`<3.0.0`. Aus den Wheel-Metadaten auf PyPI, direkt gelesen:

| Version | `Requires-Dist` |
|---|---|
| 1.99.0 | `openai>=2.20.0,<3.0.0` |
| 1.100.0 | `openai>=2.20.0,<3.0.0` |
| 1.101.0rc1 | `openai>=2.20.0,<3.0.0` |
| 1.101.0.dev2 | `openai>=2.20.0,<3.0.0` |

Auch die Vorab- und Entwicklungsschiene hält an `<3.0.0` fest. Es gibt keine
Version, auf die man warten oder ausweichen könnte, und ein Lock-Refresh
vergrößert den Abstand, statt ihn zu schließen — der offene #885 hebt `openai`
auf 3.8.0.

**Damit steht die Zusage aus ADR 0017 gegen ihre eigene Umsetzung.** Ein
Transport, der bestimmt, welche OpenAI-Version der Kern fahren darf, ist nicht
austauschbar; er ist der Kern mit einem anderen Namen. Die drei denkbaren Wege:

1. **`openai` im Lockfile zurücknehmen.** Verändert den risikoreicheren Teil des
   Systems, damit ein optionaler Transport passt. Vom Operator abgelehnt, und zu
   Recht: die Richtung ist verkehrt.
2. **LiteLLM zurückstellen.** Löst nichts, verschiebt nur. Die nächste Version
   verlangt dasselbe.
3. **Den Dependency-Vertrag trennen.** Gegenstand dieses ADR.

## Der Befund, der Weg 3 überhaupt möglich macht

**KAI importiert `litellm` nirgends als Python-Paket.** Nachgemessen über
`app/` und `scripts/`: kein einziges `import litellm`. Der gesamte Verkehr läuft
über `httpx` gegen `http://127.0.0.1:4000` — die OpenAI-kompatible HTTP-API des
Proxys. Der einzige Import in `app/ai/runtime.py` ist
`app.integrations.litellm.provider`, also KAIs **eigener** Code.

LiteLLM ist damit für KAI kein Paket, sondern ein Dienst hinter einem Socket.
Dass sein Binary bisher im Release-venv liegen sollte, ist eine Konvention,
keine technische Notwendigkeit — und genau diese Konvention zieht den
Dependency-Vertrag eines austauschbaren Transports in den Kern.

Die einzige Kopplung steht in einer Zeile:

```
ExecStart=… /home/kai/current/.venv/bin/litellm --config … --host 127.0.0.1 --port 4000
```

## Entscheidung (vorgeschlagen)

Die LiteLLM-Runtime wird ein **eigener versiegelter Artefaktbaum** neben dem
KAI-Release, mit eigener Provenance und eigenem Dependency-Vertrag.

### Warum das keine zweite Deployment-Welt ist

ADR 0017 verbietet ausdrücklich eine „zweite Deployment-Welt". Dieser Einwand
ist ernst zu nehmen, denn genau daran ist der Donor-Branch gescheitert. Der
Unterschied:

| Der abgelehnte Donor | Dieser Vorschlag |
|---|---|
| zweite **Control-Plane** (`app/inference/`) mit eigenem Routing, eigener Telemetrie, eigener Policy | keine Control-Plane. `app/ai` entscheidet weiterhin allein. |
| eigenes Deployment-**Modell** neben dem Release-Modell | **dasselbe** Modell, auf einen zweiten Baum angewandt: versiegelt, gehasht, attestiert, rollbackfähig |
| zweite Wahrheit über denselben Sachverhalt | eine Wahrheit über einen **anderen** Sachverhalt — was der Transport ist, nicht was KAI tut |

Zwei Artefaktbäume sind keine zwei Welten, solange sie **denselben Regeln**
folgen und **verschiedene Dinge** beschreiben. Der KAI-Release beantwortet „mit
welchem Code läuft KAI"; die Transport-Runtime beantwortet „mit welchem Binary
läuft der Proxy". Diese Fragen heute in einen Baum zu zwingen, erzeugt genau die
Vermischung, gegen die ADR 0017 antritt.

### Die vier harten Grenzen

1. **`app/ai` bleibt alleinige Control-Plane.** Die Transport-Runtime enthält
   keinen KAI-Code, keine Routing-Entscheidung, keine Policy. Sie enthält ein
   Binary und dessen Abhängigkeiten.
2. **Der KAI-Release bleibt allein zuständig für Core-Abhängigkeiten.**
   `requirements.lock` beschreibt weiterhin vollständig, womit KAI läuft. Er
   enthält `litellm` nicht — weder heute noch nach diesem ADR.
3. **Keine Trading- oder Execution-Authority.** Die Transport-Runtime kann
   keinen Handel auslösen, keinen Modus ändern und keine Graduierung bewirken.
   Sie beantwortet HTTP-Anfragen.
4. **Ihre Identität erscheint in Health, Telemetrie und Backup** — als Angabe
   *über einen Transport*, nicht als zweite Laufzeit-Wahrheit über KAI.

## Was verbindlich festzulegen ist

### 1. Speicherort und Layout

```
/home/ubuntu/transport/litellm/<version>-<deps8>/
    .venv/                    das Binary und seine Abhaengigkeiten
    transport.json            Manifest (siehe 2)
    requirements.lock         der EIGENE Lock dieses Baums
```

Bewusst **nicht** unter `/home/ubuntu/releases/`: Releases sind KAI-Releases,
und `pi_activate_release.sh --keep N` räumt dort auf. Ein Transport-Baum, den
die Release-Rotation mitnimmt, wäre ein Ausfall aus einem Grund, der ihn nichts
angeht.

Die Config (`litellm.yaml`) bleibt im **KAI-Release** — sie beschreibt KAIs
Routen-Aliase und gehört zu KAIs Identität, nicht zur Transport-Identität.

### 2. Manifest, Hash, Identität

`transport.json` führt mindestens:

```json
{
  "schema": "kai_transport/v1",
  "transport": "litellm",
  "version": "1.99.0",
  "spec": "litellm[proxy]==1.99.0",
  "requirements_lock_sha256": "…",
  "dependency_manifest_sha256": "…",
  "python_version": "3.12.3",
  "binary_path": "/home/ubuntu/transport/litellm/<id>/.venv/bin/litellm",
  "created_at_utc": "…",
  "builder_version": "pi_make_transport/1"
}
```

`dependency_manifest_sha256` wird **wie beim Release** aus `pip freeze` gebildet
und zur Prüfzeit gegen den venv gehalten (dieselbe Funktion aus #900, nicht eine
zweite Implementierung). `<deps8>` im Pfad kommt aus diesem Hash — nicht aus der
Version, denn zwei Bauten derselben Version können verschiedene transitive
Auflösungen tragen. Das ist dieselbe Lehre wie `<SHA>-<tree8>` und
`<SHA>+<profil>-<specs8>`: nicht „was war gemeint", sondern „was ist drin".

**Kein `repo_sha`.** Die Transport-Runtime hat keinen KAI-Commit — ihn zu
führen, wäre eine Behauptung über eine Verbindung, die nicht existiert.

### 3. Unit-Startpfad

```
ExecStart=/home/kai/current/.venv/bin/python -m app.cli.main trading runtime-exec \
    --unit %n --repo /home/kai/current \
    --transport /home/ubuntu/transport/litellm/current \
    -- /home/ubuntu/transport/litellm/current/.venv/bin/litellm \
       --config /home/kai/current/config/litellm.yaml --host 127.0.0.1 --port 4000
```

Der `runtime-exec`-Wrapper bleibt aus dem KAI-Release: er attestiert, **welcher
KAI-Stand diesen Dienst gestartet hat**. Neu ist `--transport`, das zusätzlich
die Transport-Identität attestiert. Ein Prozessmarker, der nur einen der beiden
Bäume nennt, wäre die halbe Wahrheit.

`…/litellm/current` ist ein Symlink, atomar umgeschaltet — dasselbe Muster wie
`/home/kai/current`.

### 4. Fehlende oder nicht attestierte Transport-Runtime

**Fail-closed für den Dienst, fail-open für KAI.** Das ist die entscheidende
Asymmetrie dieses ADR:

| Zustand | Folge |
|---|---|
| `…/litellm/current` fehlt | Unit startet **nicht**, `TRANSPORT_RUNTIME_MISSING`, Exit ≠ 0 |
| Symlink zeigt ins Leere | Unit startet nicht, `TRANSPORT_RUNTIME_DANGLING` |
| `transport.json` fehlt oder unlesbar | Unit startet nicht, `TRANSPORT_MANIFEST_MISSING` |
| venv weicht vom Manifest ab | Unit startet nicht, `TRANSPORT_DEPENDENCY_DRIFT` |
| Unit läuft nicht | **KAI läuft normal weiter**, SHADOW-Aufrufe scheitern und werden als Fehlversuch telemetriert |

Die letzte Zeile ist keine Kulanz, sondern eine bereits belegte Eigenschaft:
seit #893 reißt ein ausgefallenes Gateway den Aufruf nicht mehr mit, und der
Altpfad behält die Antwort, die er ohnehin hatte. Ein Wrapper, der bei fehlender
Transport-Runtime KAI mitnähme, würde diese Arbeit rückgängig machen.

### 5. Darstellung in `/health`

Ein **eigener Block**, nicht in `runtime_provenance` hineingemischt:

```json
"transport": {
  "litellm": {
    "present": true,
    "version": "1.99.0",
    "dependency_manifest_sha256": "…",
    "attested": true,
    "service_active": true,
    "endpoint": "127.0.0.1:4000"
  }
}
```

`present: false` ist **kein** CRITICAL, solange kein Modus ihn braucht — sonst
entstünde derselbe unschließbare Alarm wie der heutige
`process_runtime_marker: DEPLOY_HOLD — 5 von 6`, der eine Unit erwartet, die es
nicht geben kann. Die Erwartung folgt dem Modus, nicht der Unit-Liste.

`attested: false` bei `present: true` ist dagegen ein Befund: dann liegt etwas
da, das nicht ist, was es behauptet.

### 6. Backup und Restore

`standby_to_usb.sh` sichert im Modus `system` zusätzlich die **aufgelöste**
Transport-Runtime, einschließlich `.venv` und `transport.json` — mit derselben
Begründung wie beim Release-venv: ohne sie ist der Restore ein Quelltext, der
Netz und Paketquellen bräuchte, und zwar für Pakete, die im Lockfile gar nicht
stehen.

**Fail-closed, aber modusabhängig:** Fehlt die Transport-Runtime, während
`kai-litellm.service` enabled ist, endet das Backup mit
`TRANSPORT_RUNTIME_MISSING` — analog zu `ACTIVE_RELEASE_MISSING`. Ist der Dienst
nicht enabled, ist ihr Fehlen kein Befund. Ein Backup, das etwas verlangt, das
der Betrieb nicht braucht, wird abgeschaltet statt repariert.

Die Archiv-Inventarisierung weist die Transport-Runtime als eigenen Posten aus.

### 7. Upgrade und Rollback

Ein Upgrade ist ein **neuer Baum**, kein `pip install --upgrade`. Nachträglich
in einen versiegelten venv zu installieren, verlöre `dependency_manifest_sha256`
gegen den Inhalt — seit #900 verhindert das den Dienststart, und das ist
beabsichtigt.

```
bauen  ->  verifizieren  ->  current-Symlink umschalten  ->  Dienst neu starten
```

Rollback ist das Umschalten des Symlinks auf den vorherigen Baum. Aufbewahrung
mindestens zwei Bäume; die Rotation ist **getrennt** von der Release-Rotation
und darf einen Baum nicht entfernen, auf den ein lebender Prozessmarker zeigt.

Ein Transport-Wechsel erzwingt **keinen** KAI-Release. Das ist der eigentliche
Gewinn dieses ADR: heute erzwingt jede Transport-Änderung einen neuen KAI-Baum
samt Neustart aller fünf Dauerläufer.

### 8. Was ein defekter Transport niemals darf

Belegt, nicht zugesagt — die Nachweise existieren bereits:

| Zusage | Nachweis |
|---|---|
| Ein ausgefallenes Gateway nimmt dem Altpfad nicht die Antwort | `test_ein_ausgefallenes_gateway_nimmt_dem_altpfad_nicht_die_antwort` (#893) |
| Ein werfender Transport wird zur Spur, nicht zum Abbruch | `test_ein_transportfehler_wird_zur_spur_statt_zum_abbruch` |
| Auch ein kaputter Client-Aufbau bleibt im Schatten | `test_auch_ein_kaputter_client_aufbau_bleibt_im_schatten` |
| SHADOW ersetzt die Antwort nie | `test_shadow_chat_liefert_die_direkte_antwort_trotz_abweichendem_litellm` |
| OFF ruft den Transport überhaupt nicht | fünf Aufrufer einzeln, `test_s5_control_plane_invariants.py` |

Dieses ADR fügt dem nichts hinzu; es hält fest, dass die Entkopplung diese
Eigenschaften **nicht** anfasst. Ein Transport in einem eigenen Baum ist für
`app/ai` derselbe Socket wie zuvor.

## Was dieses ADR bewusst offenlässt

- **Ob der Transport-Baum vom Pi selbst gebaut wird oder anderswo.** Beides ist
  mit dem Manifest verträglich; der Bau auf dem Pi ist der kürzere Weg und
  braucht kein Netz zur Laufzeit.
- **Ob weitere Transporte demselben Muster folgen.** Das Layout sieht es vor
  (`/home/ubuntu/transport/<name>/`), dieses ADR entscheidet es nicht.
- **Den Zeitpunkt.** Der operative Status bleibt unverändert, bis diese
  Ergänzung entschieden **und** umgesetzt ist.

## Was vor der Annahme geprueft wurde

Der Operator hat die Annahme davon abhaengig gemacht, dass die in § 8
genannten Tests den Direktpfad **tatsaechlich** schuetzen — nicht, dass sie
zitiert werden. Ein ADR, das Nachweise nennt, die es nicht gibt, waere genau
die Zusage ohne Deckung, gegen die die halbe Arbeit dieses Sprints steht.

Am 2026-09-08 gegen `958b9f59` geprueft: alle fuenf Belege existieren, laufen
und sind gruen.

| Beleg | Datei |
|---|---|
| `test_ein_ausgefallenes_gateway_nimmt_dem_altpfad_nicht_die_antwort` | `tests/unit/test_shadow_replay.py` |
| `test_ein_transportfehler_wird_zur_spur_statt_zum_abbruch` | `tests/unit/test_shadow_replay.py` |
| `test_auch_ein_kaputter_client_aufbau_bleibt_im_schatten` | `tests/unit/test_shadow_replay.py` |
| `test_shadow_chat_liefert_die_direkte_antwort_trotz_abweichendem_litellm` | `tests/unit/test_s5_control_plane_invariants.py` |
| fuenf `test_off_*`-Faelle, je Aufrufer einzeln | `tests/unit/test_s5_control_plane_invariants.py` |

`9 passed`. Die Isolation, auf der die Fail-open-Haelfte dieses ADR beruht, ist
damit belegt und nicht behauptet.

## Konsequenzen

**Positiv:** Der Dependency-Vertrag des Kerns wird von dem des Transports
getrennt — die Austauschbarkeit aus ADR 0017 wird von einer Zusage zu einer
Eigenschaft. `openai` bleibt auf 3.6.0 und kann dem Lock-Refresh folgen, ohne
dass ein optionaler Transport mitredet. Ein Transport-Upgrade erzwingt keinen
KAI-Release und keinen Neustart der fünf Dauerläufer.

**Negativ:** Ein zweiter versiegelter Baum bedeutet ein zweites Manifest, eine
zweite Rotation und einen zweiten Posten im Backup. Das ist echter Aufwand, und
er fällt an, obwohl heute genau ein Transport existiert. Die Alternative wäre,
den Kern an einen austauschbaren Bestandteil zu binden — teurer, nur später.

**Rückrollbarkeit:** Vollständig. Ohne Transport-Runtime verhält sich KAI wie
heute: `kai-litellm.service` existiert nicht, `OFF` ist der Zustand, die direkten
Provider tragen den Betrieb. Es gibt nichts zu migrieren und nichts
zurückzubauen — nur einen Baum, den man nicht anlegt.

## Status der Umsetzung

| | |
|---|---|
| ADR entschieden | **ACCEPTED 2026-09-08** |
| Transport-Implementierung | **zulaessig** |
| `pi_make_transport.sh` | nicht gebaut |
| Unit auf Transport-Pfad umgestellt | nein |
| `/health`-Block | nicht gebaut |
| Backup-Vertrag erweitert | nein |
| Release 3 mit eingebettetem LiteLLM | **NO** |
| `kai-litellm.service` | **NO** |
| `PRIMARY` | **NO** |
| Pi-Cutover | **HOLD** |
