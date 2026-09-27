#!/usr/bin/env bash
# scripts/dependency_audit.sh — EIN Ort fuer die Dependency-Audits.
#
# Aufrufer: PR-CI (Pflicht-Checks "Security Scan" -> python, "Frontend Build" -> web)
# und der Nachtlauf .github/workflows/dependency-audit.yml (all). Vorher lief
# pip-audit nur in der PR-CI und web/ hatte GAR KEIN Audit (Audit 27.09.2026):
# eine neue CVE in einem unveraenderten Paket fiel erst auf, wenn ein fremder PR
# daran scheiterte.
#
# Aufruf:  bash scripts/dependency_audit.sh [python|web|all]   (Default: all)
# Exit:    0 = sauber · 1 = mindestens ein Audit meldet Befunde · 2 = falscher Aufruf
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

mode="${1:-all}"
case "$mode" in
    python | web | all) ;;
    *) echo "Aufruf: $0 [python|web|all]" >&2; exit 2 ;;
esac

# Ausnahmen NUR mit Begruendung und nur hier:
# - CVE-2026-3219 (no fix) und CVE-2026-6357 (fix pending in pip 26.1) betreffen pip
#   selbst -- Build-Werkzeug, keine Laufzeit-Abhaengigkeit von KAI.
PIP_IGNORE=(--ignore-vuln CVE-2026-3219 --ignore-vuln CVE-2026-6357)

# CI: `pip install pip-audit` legt das Kommando in den PATH; lokal oft nur das Modul.
PIP_AUDIT=(pip-audit)
command -v pip-audit >/dev/null 2>&1 || PIP_AUDIT=(python -m pip_audit)

rc=0
if [ "$mode" = python ] || [ "$mode" = all ]; then
    echo "== pip-audit requirements.lock"
    "${PIP_AUDIT[@]}" -r requirements.lock "${PIP_IGNORE[@]}" || rc=1
    # Gehashter Pi-Transportbaum (aarch64): nur Versionen gegen die Advisory-DB.
    echo "== pip-audit requirements-transport.lock"
    "${PIP_AUDIT[@]}" -r requirements-transport.lock --no-deps --disable-pip || rc=1
fi
if [ "$mode" = web ] || [ "$mode" = all ]; then
    # Laufzeit-Pakete des SPA (landen im ausgelieferten Bundle): ab "high".
    echo "== npm audit web (Laufzeit, ab high)"
    (cd web && npm audit --omit=dev --audit-level=high) || rc=1
    # Build-/Test-Werkzeuge (vite, vitest, ...): laufen nur auf Laptop/CI, blockieren ab "critical".
    echo "== npm audit web (Build/Test, ab critical)"
    (cd web && npm audit --audit-level=critical) || rc=1
fi
exit "$rc"
