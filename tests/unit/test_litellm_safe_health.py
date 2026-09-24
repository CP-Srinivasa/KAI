"""LiteLLM health checks must remain free of inference and provider cost."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SMOKE = REPO / "scripts" / "pi_litellm_smoke.sh"


def _find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/bin/bash.exe",
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/usr/bin/bash.exe",
        ):
            if candidate.is_file():
                return str(candidate)
    return shutil.which("bash")


def _bash_path(path: Path) -> str:
    value = path.as_posix()
    if os.name == "nt" and len(value) > 2 and value[1:3] == ":/":
        return "/" + value[0].lower() + value[2:]
    return value


_BASH = _find_bash()


def _without_comments(path: Path) -> str:
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


#: ``/health`` not continued by a sub-path, word or file-name character. The
#: first version used ``[?'\"\\s]`` in a raw string, which matched a literal
#: backslash and ``s`` instead of whitespace -- ``curl $URL/health | jq`` slipped
#: through. Negative lookahead covers every terminator at once.
BARE_HEALTH = re.compile(r"/health(?![/\w.-])")

#: Files that mention LiteLLM or its ports and still contain a bare ``/health``
#: that is NOT a LiteLLM call, with the exact number of tolerated hits. A second
#: hit in the same file fails the guard again.
KNOWN_NON_LITELLM_HEALTH = {
    # Docstring about kai-server's own /health vs. the runtime probe.
    "app/alerts/process_runtime_probe.py": 1,
}


@pytest.mark.parametrize(
    "caller",
    [
        "curl -s http://127.0.0.1:4000/health | jq .",
        "curl -s http://127.0.0.1:4000/health\nnext",
        "curl http://127.0.0.1:4001/health",
        'url = f"{base}/health"',
        "requests.get(base + '/health')",
        "curl $URL/health)",
    ],
)
def test_guard_pattern_catches_bare_health_callers(caller: str) -> None:
    assert BARE_HEALTH.search(caller)


@pytest.mark.parametrize(
    "caller",
    ["$URL/health/liveliness", "$URL/health/readiness", "$URL/healthz", "/health_check"],
)
def test_guard_pattern_allows_passive_probes(caller: str) -> None:
    assert BARE_HEALTH.search(caller) is None


def test_litellm_callers_never_use_costly_bare_health() -> None:
    """Bare ``/health`` makes provider calls; only passive probes are allowed."""
    candidates: list[Path] = []
    for root in (REPO / "scripts", REPO / "app", REPO / "deploy"):
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".sh", ".service", ".timer"}:
                continue
            text = _without_comments(path)
            lowered = text.lower()
            if "litellm" in lowered or "4000" in text or "4001" in text:
                candidates.append(path)

    violations = []
    for path in candidates:
        rel = path.relative_to(REPO).as_posix()
        hits = len(BARE_HEALTH.findall(_without_comments(path)))
        if hits > KNOWN_NON_LITELLM_HEALTH.get(rel, 0):
            violations.append(f"{rel} ({hits})")
    assert violations == [], (
        "LiteLLM bare /health can invoke configured providers; use "
        f"/health/liveliness or /health/readiness instead: {violations}"
    )


def test_known_exceptions_are_still_needed() -> None:
    """An exception whose file no longer has the hit must be removed."""
    for rel, allowed in KNOWN_NON_LITELLM_HEALTH.items():
        hits = len(BARE_HEALTH.findall(_without_comments(REPO / rel)))
        assert hits == allowed, f"{rel}: {hits} Treffer, Ausnahme erlaubt {allowed}"


@pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")
def test_smoke_is_read_only_and_checks_expected_statuses(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "curl.calls"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$KAI_TEST_CURL_CALLS"\n'
        'case "$*" in\n'
        "  *'/health/liveliness'*) printf 200 ;;\n"
        "  *'/v1/models'*) printf 401 ;;\n"
        "  *) printf 599 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{_bash_path(fake_bin)}:/usr/bin:/bin",
        "KAI_TEST_CURL_CALLS": _bash_path(calls),
        "KAI_LITELLM_SMOKE_CURL": _bash_path(fake_curl),
    }

    result = subprocess.run(
        [_BASH, _bash_path(SMOKE)], capture_output=True, text=True, env=env, check=False
    )

    assert result.returncode == 0, result.stderr
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert sum("/v1/models" in line for line in lines) == 2
    assert any(
        "Authorization: Bearer kai-intentionally-invalid-smoke-key" in line for line in lines
    )
    assert all("chat/completions" not in line for line in lines)
    assert all(BARE_HEALTH.search(line) is None for line in lines)


@pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")
def test_smoke_fails_when_auth_contract_changes(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        "  *'/health/liveliness'*) printf 200 ;;\n"
        "  *) printf 500 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{_bash_path(fake_bin)}:/usr/bin:/bin",
        "KAI_LITELLM_SMOKE_CURL": _bash_path(fake_curl),
    }

    result = subprocess.run(
        [_BASH, _bash_path(SMOKE)], capture_output=True, text=True, env=env, check=False
    )

    assert result.returncode == 1
    assert "expected=401 actual=500" in result.stderr
