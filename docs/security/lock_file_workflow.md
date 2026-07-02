# Lock-File Workflow

**Status:** Live (seit `sprint/lock-file-migration` 2026-05-27)
**Trigger-Sprint:** [`lock_file_migration_sprint_spec.md`](./lock_file_migration_sprint_spec.md) (commit `ee18dc44`)
**DS-Item:** DS-20260527-V5

---

## Was es ist

`requirements.lock` ist ein deterministisch generiertes Lock-File mit **exakten Pins** für alle 200+ direkten und transitiven Dependencies. CI, Pi-Production-Install und lokale Test-Envs nutzen das Lock-File, nicht `pyproject.toml` direkt.

`pyproject.toml` bleibt offen-spec (`>=`-Lower-Bounds plus gezielte `!=`-Excludes wie `fastapi>=0.115.0,!=0.136.3`). Die Lock-File pinnt eine konkrete Version-Kombination, die zu einem Zeitpunkt durch `uv pip compile --universal` aufgelöst wurde.

## Warum

Jeder neue Bump erscheint in einem **dedizierten Lock-Update-PR** (siehe `.github/workflows/lock-update.yml`). CI für diesen PR triggert `pip-audit -r requirements.lock`. MAL-/CVE-Funde rot-en den Bump-PR, **andere Feature-PRs bleiben davon unbetroffen**. Vorher (mit `>=`-Pins ohne Lock-File) hätte jedes `pip install` in jedem PR die LATEST Version gezogen → MAL-2026-4750-Klasse-Incident hätte die ganze CI verseucht.

## Generierung

```bash
pip install uv
uv pip compile pyproject.toml --extra dev --universal -o requirements.lock
```

- `--universal` = cross-platform Lock-File mit `; sys_platform == ...`-Markers (Windows-only-Deps wie `pywin32`, `colorama` werden konditional).
- `--upgrade` = wöchentlicher Bump in der Auto-Update-Workflow-Action.
- `--extra dev` = inklusive Dev-Dependencies (pytest, ruff, mypy, etc.).

## Audit

```bash
pip install pip-audit
pip-audit -r requirements.lock \
  --ignore-vuln CVE-2026-3219 \
  --ignore-vuln CVE-2026-6357
```

Die zwei ignorierten CVEs betreffen `pip` selbst, das Build-Tool, nicht eine Project-Dependency.

## Lokale Entwicklung

- **Dev-Setup** (Workstation, einmalig je Branch): `pip install -e ".[dev]"` — nutzt `pyproject.toml`-Range-Pins, weniger streng als Lock-File. OK für interaktive Iteration.
- **Reproduktion eines konkreten CI-Fehlers**: `pip install -r requirements.lock --no-cache-dir && pip install -e . --no-deps`. Reproduziert exakt das CI-Env.
- **Neue Dependency hinzufügen**: 1) Eintrag in `pyproject.toml` ergänzen, 2) `uv pip compile pyproject.toml --extra dev --universal -o requirements.lock`, 3) beide Files committen.

## CI

Alle Jobs (`lint`, `test`, `security`, `type-check`) installieren via:

```yaml
- run: pip install --no-cache-dir -r requirements.lock
- run: pip install -e . --no-deps
```

Security-Job läuft `pip-audit -r requirements.lock` direkt (kein Install-Detour).

## Pi-Production-Deploy

Wenn die Lock-File-Migration auf der Pi ausgerollt wird (Phase 3 aus Sprint-Spec):

```bash
ssh ubuntu@192.168.178.23
cd /home/ubuntu/ai_analyst_trading_bot
git fetch origin && git merge --ff-only
.venv/bin/pip install --no-cache-dir -r requirements.lock
sudo bash scripts/pi_install_systemd.sh --reactivate
```

`requirements.lock` ist universal — funktioniert auf aarch64 (Pi 5) wie auf x86_64 (CI runners).

## Auto-Update-Workflow

`.github/workflows/lock-update.yml` läuft **Montag 04:00 UTC** und manuell via `workflow_dispatch`:

1. Regeneriert `requirements.lock` mit `--upgrade` (zieht jeweils Latest, soweit `pyproject.toml`-Range erlaubt).
2. Läuft `pip-audit` gegen die neue Lock-File.
3. Öffnet einen PR mit:
   - Standard-KAI-PR-Template (Änderungsbericht, Quality Gates, Risiken, Nächste TODOs, Testbefehl) — sonst rot-Lint via `pr-check`-Job
   - pip-audit-Output als Markdown-Code-Block im PR-Body

Operator-Review:
- pip-audit clean + CI grün → merge → Pi-Deploy
- pip-audit zeigt MAL-/CVE-Findung → PR rot lassen, NICHT mergen
  - Falls Findung in einer Library, die kein Fix-Release hat: Exclude in `pyproject.toml` (`!=<malware-version>`) + Lock-File regenerieren

## Stop-Conditions / Rollback

Aus Sprint-Spec §6:
- Lock-File macht Pi-Install >5min langsamer → `--prefer-binary`-Flag oder Fallback auf pyproject-only-install
- Lock-Update-PR-Stau >3 unresolved → Wöchentlich → Monatlich verlangsamen
- pip-audit-False-Positives >5/Woche → Ignore-Liste verbreitern + Memo

## Cross-References

- Spec: [`lock_file_migration_sprint_spec.md`](./lock_file_migration_sprint_spec.md)
- Memory: `kai_fastapi_mal_2026_4750_incident` (Auslöser)
- Memory: `kai_supply_chain_lockfile_followup` (Initial-Vorschlag)
- CI-Config: `.github/workflows/ci.yml`
- Auto-Update: `.github/workflows/lock-update.yml`
- Pi-Deploy-Memo: `reference_pi_deploy_after_pyproject_change`
