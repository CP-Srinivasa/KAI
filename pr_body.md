## Änderungsbericht

Wöchentlicher automatischer Lock-File-Refresh via 
`uv pip compile pyproject.toml --extra dev --universal --upgrade`.

## Quality Gates

- [x] Lock-File regeneriert (403 Zeilen)
- [ ] CI grün (lint+test+security+type-check)
- [ ] Operator-Review pip-audit-Output

## Risiken

Bei MAL-/CVE-Funden: PR rot lassen, NICHT mergen. Manuelles
Triage: pyproject.toml-Exclude (`!=<version>`) für betroffene
Library setzen, Lock-File regenerieren.

## Nächste TODOs

- Operator reviewed pip-audit-Output (siehe `audit_output.txt`)
- Bei Sauberkeit: merge → Pi-Deploy

## Testbefehl

```bash
uv pip compile pyproject.toml --extra dev --universal -o requirements.lock
pip-audit -r requirements.lock
```

## pip-audit Output

```
```
