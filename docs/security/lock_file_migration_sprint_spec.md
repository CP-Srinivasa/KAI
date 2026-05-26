# Lock-File-Migration Sprint-Spec

**Status:** Spec-Only · **Owner:** TBD · **Sprint-Aufwand:** ~1 Tag · **Trigger-Tag:** offen

---

## 1. Problem

`pyproject.toml` listet 43 Dependencies, davon 42 mit ausschließlich `>=`-Lower-Bounds, 1 mit Exclude (`fastapi>=0.115.0,!=0.136.3`). Bei jedem fresh `pip install -e ".[dev]"` (CI bei jedem PR-Run) zieht der Resolver die **LATEST verfügbare Version** jeder Dep.

**Konsequenz:** Wenn eine Dep kompromittiert wird (wie `fastapi==0.136.3` mit MAL-2026-4750, siehe Memory `kai_fastapi_mal_2026_4750_incident`), passiert beim nächsten `pip install` das gleiche wie 2026-05-26 — Hidden-Dep-Injection in unsere CI/Pi-Production.

**Exposure-Klasse:** Jede Library mit `>=`-Pin ist potentiell next-MAL-2026-4750.

## 2. Lösung

Lock-File-Pattern für Deploy-Pipeline:

```
pyproject.toml       (library spec — bleibt offen `>=`)
       ↓
   pip-compile (oder uv pip compile)
       ↓
requirements.lock    (deterministisch, vollständig, gehashed)
       ↓
CI install        : pip install -r requirements.lock
pip-audit         : pip-audit -r requirements.lock
Pi-Production install: pip install -r requirements.lock
```

**Eigenschaft:** Jeder neue Dep-Bump (latest fastapi 0.136.4) erscheint zuerst in einem **dedizierten Lock-Update-PR**. CI für diesen PR triggert pip-audit gegen das neue Lock-File. Wenn `MAL-*` aufschlägt → PR rot → Bump-PR nicht gemerged → andere Feature-PRs unbetroffen.

## 3. Tooling-Wahl

### Option A — `pip-tools` (Standard)
- Pros: pre-installed in vielen CI-Stacks, gut dokumentiert, von Python-Packaging-WG empfohlen
- Cons: langsamer als `uv` (~5x), kein async parallelism
- Install: `pip install pip-tools`
- Compile: `pip-compile pyproject.toml -o requirements.lock --extra dev`

### Option B — `uv` (FastAPI-Team-empfohlen, schneller)
- Pros: 10-100x schneller, modernerer Resolver, single-binary, growing ecosystem
- Cons: relativ jung (v0.5+), noch nicht in allen Distros
- Install: `pip install uv`
- Compile: `uv pip compile pyproject.toml --extra dev -o requirements.lock`

**Empfehlung:** `uv` — Speed-Gewinn ist signifikant bei wöchentlichen Lock-Updates + täglichem pip-audit.

## 4. Sprint-Plan (1 Tag)

### Phase 1 — Lock-File generieren (1h)
1. `uv` lokal + auf Pi installieren
2. `uv pip compile pyproject.toml --extra dev -o requirements.lock` lokal
3. Verifikation: `pip install -r requirements.lock` in fresh venv funktioniert + 2744+ Tests grün
4. `requirements.lock` committen (~3000 Zeilen, deterministisch)

### Phase 2 — CI umstellen (2h)
1. `.github/workflows/ci.yml` editieren:
   - `lint`-Job: `pip install -r requirements.lock --no-deps && pip install -e . --no-deps`
   - `test`-Job: gleiches
   - `security`-Job: `pip-audit -r requirements.lock`
2. Lokaler Trockenlauf via `act` (optional)
3. Test-PR pushen + CI grün

### Phase 3 — Pi-Deploy umstellen (1h)
1. `reference_pi_deploy_after_pyproject_change`-Memo aktualisieren:
   ```bash
   ssh ubuntu@192.168.178.23 'cd /home/ubuntu/ai_analyst_trading_bot && \
     git fetch origin && git merge --ff-only && \
     .venv/bin/pip install -r requirements.lock --break-system-packages && \
     sudo bash scripts/pi_install_systemd.sh --reactivate'
   ```
2. Dry-Run auf Pi
3. Smoke

### Phase 4 — Auto-Update-Workflow (2h)
1. Wöchentlicher GitHub-Workflow `lock-update.yml`:
   ```yaml
   on:
     schedule: [{cron: '0 4 * * 1'}]  # Mo 04:00 UTC
     workflow_dispatch:
   jobs:
     update:
       steps:
         - uses: actions/checkout@v4
         - run: pip install uv
         - run: uv pip compile pyproject.toml --extra dev -o requirements.lock --upgrade
         - run: pip-audit -r requirements.lock --ignore-vuln CVE-2026-3219 --ignore-vuln CVE-2026-6357
         - uses: peter-evans/create-pull-request@v6
           with:
             title: "chore(deps): weekly lock-file update"
             body: "Automated lock-file refresh. Review pip-audit output in CI for MAL/CVE findings."
             branch: chore/lock-update-${{ github.run_number }}
   ```
2. PR-Template + reviewer ist Operator

### Phase 5 — Doku + Memory (2h)
1. `docs/security/lock_file_workflow.md` als Operator-Doc
2. Memory-Update: `kai_supply_chain_lockfile_followup` → "DONE"
3. CLAUDE.md erwähnt Lock-File-Workflow im Dependencies-Bereich
4. Cross-Link in `reference_pi_deploy_after_pyproject_change`

## 5. Risiken

| Risiko | Mitigation |
|---|---|
| Lock-File ist 3000+ Zeilen, scary diff | PR-Template-Anweisung: "Nur pip-audit + Test-Status reviewen, nicht line-by-line" |
| `uv`-Bug bricht resolver | Fallback `pip-tools` als Alternative ready |
| Wöchentlicher Update-PR-Stau wenn Operator nicht reviewt | PR auto-close nach 14 Tagen + neuer auto-open am nächsten Mo |
| Lock-File bricht beim Pi-arm64 (Wheel-Mismatch) | `uv pip compile --python-platform linux-aarch64` für Pi-spezifisches Lock-File |
| Native-Deps (cryptography, pydantic-core) builden langsam beim Pi-pip-install | Vorab-gebuildete Wheels per `--prefer-binary` |

## 6. Stop-Conditions / Rollback

- Lock-File macht Pi-Install >5min langsamer → `--prefer-binary`-Flag oder Fallback auf pyproject-only
- Lock-Update-PR-Stau >3 unresolved → Wöchentlich → Monatlich verlangsamen
- pip-audit-Cron-Job-False-Positives >5/Woche → Ignore-Liste verbreitern + Memo

## 7. Erfolgsmessung

**Quantitativ:**
- 0 Supply-Chain-Incidents über 30 Tage nach Migration
- pip-audit-Daily-Run findet 0 nicht-allowlisted MAL-/CVE-Funde
- Lock-Update-PR-Merge-Rate >=80% innerhalb 7 Tagen

**Qualitativ:**
- Operator-Quote: "Ich kann jeden Lock-Update in 60s reviewen"
- Keine Notwendigkeit mehr für ad-hoc `!=0.136.3`-Excludes in `pyproject.toml`

## 8. Cross-References

- Memory `kai_supply_chain_lockfile_followup` — Initial-Vorschlag 2026-05-26
- Memory `kai_fastapi_mal_2026_4750_incident` — Auslöser
- Memory `kai_pi_security_backlog_20260526` — aktuelle Floor-Pin-Approach (Lock-File ersetzt das langfristig)
- Memory `feedback_pip_audit_check_mal_prefix` — MAL-Präfix-Pattern (CI nach Migration einfacher)
- Memory `reference_pi_deploy_after_pyproject_change` — wird durch Phase-3 aktualisiert
- `pyproject.toml` Z.17-58 — current spec
- `.github/workflows/ci.yml` Z.18-93 — current CI

## 9. Pflichtformat-Compliance (KAI Master Directive §11)

### Vorschlag
Lock-File-Migration für Deploy-Pipeline

### Warum jetzt?
2026-05-26 MAL-2026-4750 fastapi-Incident zeigte: 43 `>=`-Pins = 43 potentielle Supply-Chain-Attack-Vektoren. Aktueller Patch-Pfad (manuelles `!=`-Exclude pro Incident) skaliert nicht.

### Erwarteter Nutzen
Klassen-Lösung statt Einzelpatches. Jeder Bump erscheint isoliert in dediziertem PR. CI fängt MAL-/CVE-Pattern vor Merge.

### Datenquellen / Systeme
`pyproject.toml`, `requirements.lock`, `.github/workflows/ci.yml`, `.github/workflows/lock-update.yml` (neu), `scripts/pi_install_*.sh`.

### Umsetzungsweg
5 Phasen (siehe §4). Sequenziell, ~1 Tag.

### Parallel möglich?
Ja teilweise — Phase 1+2 (CI) kann parallel zu Phase 3 (Pi) laufen. Phase 4 (auto-update) braucht 1+2+3 done.

### Aufwand
Realistisch: 1 Tag (~8h Operator-Zeit Spread). Minimal: 4h wenn `uv`-Setup glatt läuft. Kritisch: 2 Tage wenn Pi-arm64-Wheel-Issues auftreten.

### Risiken
Siehe §5. Wichtigster: Pi-arm64-Wheel-Mismatch — mitigierbar via `--python-platform linux-aarch64` oder Fallback auf pyproject-only-install.

### Priorität
**P1** — nicht akut blockend (Floor-Pins decken aktuelle CVEs), aber Klassen-Härtung gegen nächste MAL-Incident dringend.

---

**Spec-Status:** Bereit für Operator-Sign-off. Bei Sign-off → eigener Sprint-Auftrag + neuer Branch `sprint/lock-file-migration`.
