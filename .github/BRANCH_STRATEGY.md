# Branch-Strategie — KAI Platform

## Schutzregeln (in GitHub Settings einrichten)

### `main` ist geschützt:
- Kein direkter Push erlaubt
- PR required vor Merge
- Status Checks müssen grün sein: `lint`, `test`
- Mindestens 1 Approval (Operator)

**Setup → GitHub → Settings → Branches → Add rule → `main`:**
```
✅ Require a pull request before merging
✅ Require status checks to pass before merging
   → Status checks: "Lint & Format Check", "Tests"
✅ Require branches to be up to date before merging
✅ Do not allow bypassing the above settings
```

---

## Branch-Namenskonvention

```
<agent>/<phase>/<kurzer-beschreibender-name>

Beispiele:
  codex/p2/rss-scheduler
  codex/p2/news-api-adapter
  claude/p2/storage-session
  claude/p3/llm-provider-openai
  antigravity/deploy/docker-compose-prod
  fix/ci-ruff-error
  hotfix/settings-validation
```

| Prefix | Agent / Zweck |
|---|---|
| `codex/` | OpenAI Codex — Implementierung |
| `claude/` | Claude Code — Architektur, Multi-File |
| `antigravity/` | Google Antigravity — Workflows, Deploy |
| `fix/` | Bugfix (jeder Agent) |
| `hotfix/` | Kritischer Fix direkt nach Produktion |
| `docs/` | Nur Dokumentation |

---

## Workflow pro Agent

### OpenAI Codex
```
1. Branch erstellen:  git checkout -b codex/p2/<name>
2. Implementieren
3. Tests + Lint lokal prüfen: pytest && ruff check .
4. Commit mit Report im Body
5. PR öffnen mit ausgefülltem PR-Template
6. CI muss grün sein vor Merge
```

### Claude Code
```
1. Branch erstellen:  git checkout -b claude/p2/<name>
2. Architektur umsetzen (Interface zuerst, dann Implementation)
3. Betroffene AGENTS.md aktualisieren
4. Tests + Lint prüfen
5. PR mit Architekturentscheidung im Änderungsbericht
```

### Google Antigravity
```
1. Branch erstellen:  git checkout -b antigravity/<ziel>/<name>
2. Workflow / Skills / MCP-Konfiguration
3. PR mit Workflow-Beschreibung und Validierungsnachweis
```

---

## Commit-Format

```
<typ>(<modul>): <kurze Beschreibung>

<optionaler Body mit Erklärung>

Agent: [Codex | Claude Code | Antigravity | Human]
Refs: <Task-ID oder TASKLIST.md Abschnitt>
```

Typen: `feat` · `fix` · `refactor` · `test` · `docs` · `ci` · `chore`

Beispiele:
```
feat(ingestion): add RSS scheduler with APScheduler
fix(api): handle missing source_type in /sources endpoint
test(enrichment): add deduplicator edge case for empty title
docs(agents): update ingestion AGENTS.md with scheduler interface
```

---

## PR-Merge-Regel

- **Squash and Merge** bevorzugt (saubere History auf main)
- Branch nach Merge löschen
- Kein Force-Push auf main
