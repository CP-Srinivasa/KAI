## Änderungsbericht

**Agent / Autor**: [Codex | Claude Code | Antigravity | Human]
**Branch**: `<branch-name>`
**Phase**: [P1 | P2 | P3 | P4 | P5 | P6]
**Task-Typ**: [feature | fix | refactor | test | spec | deploy]

### Was wurde geändert?
<!-- 2–5 Sätze: Was wurde implementiert oder behoben? -->

### Spec-Referenz
<!-- Welcher Abschnitt in PROJECT_SPEC.md oder AGENTS.md deckt diese Änderung ab? -->
- Referenz:

### Dateien erstellt
-

### Dateien geändert
-

---

## Quality Gates

- [ ] `pytest` grün (alle Tests bestanden)
- [ ] `ruff check .` grün (kein Lint-Fehler)
- [ ] Keine Secrets oder API-Keys im Code
- [ ] Keine nackten `dict` oder `Any` in öffentlichen Interfaces
- [ ] Failure-Pfade explizit behandelt
- [ ] `AGENTS.md` aktualisiert (falls Interface geändert)

---

## Risiken & Annahmen

<!-- Was könnte schiefgehen? Welche Annahmen wurden getroffen? -->
-

---

## Nächste TODOs

<!-- Was ist der logische nächste Schritt nach diesem PR? -->
- [ ]

---

## Testbefehl

```bash
pytest tests/unit/<datei>.py -v
```
