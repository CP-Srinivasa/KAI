#!/usr/bin/env python3
"""Windows desktop hub for KAI's independent developer reserves.

This is an operator tool, not part of KAI's inference runtime. It never imports
``app.ai`` and never changes provider routing or trading gates.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import functools
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

import kai_dev_workflow as workflow

HUB_VERSION = "0.4.0"

DEV_HOST = "127.0.0.1"
DEV_PORT = 4001
OLLAMA_PORT = 11434
PI_HOST = "192.168.178.23"
PI_USER = "ubuntu"
# OpenCode's system prompt plus a read file exceeds 16K tokens (Ollama
# truncated 16942 -> 16384 on 23.09.); both local start paths use the 64K
# model. Status and response probe check exactly this one (audit 27.09.:
# they checked the unused 16K model).
LOCAL_MODEL = "kai-qwen3-coder:30b-64k"
HERMES_LOCAL_MODEL = LOCAL_MODEL
OPENCODE_LOCAL_MODEL = LOCAL_MODEL
DEV_MODELS = {"kai-dev-economy", "kai-dev-code", "kai-dev-frontier"}
LOCK_TIMEOUT_S = 10.0
REMOTE_ENV = "/home/kai/ai_analyst_trading_bot/.env"
REMOTE_PROXY = "/home/kai/current/scripts/dev_reserve.sh"
REMOTE_PID_FILE = "/tmp/kai-dev-hub-proxy.pid"
STATE_ROOT = Path(
    os.environ.get("KAI_DEV_HUB_HOME", Path.home() / ".kai" / "developer-hub")
).expanduser()


class HubError(RuntimeError):
    """An actionable, safely displayable operator error."""


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=_creation_flag("CREATE_NO_WINDOW"),
    )


def _command(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates = {
        "ollama": [local / "Programs/Ollama/ollama.exe"],
        "hermes": [local / "hermes/bin/hermes.exe"],
        "kimi": [local / "Programs/Kimi/Kimi.exe"],
    }
    return next((str(path) for path in candidates.get(name, []) if path.is_file()), None)


def _opencode_executable() -> str | None:
    """The OpenCode binary itself, not its npm ``opencode.cmd`` wrapper.

    Behind the wrapper the client runs as a child of cmd.exe; if only the
    wrapper dies, opencode.exe keeps running as an orphan (23.09. acceptance).
    """
    wrapper = _command("opencode.cmd")
    if wrapper:
        native = Path(wrapper).parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
        return str(native) if native.is_file() else wrapper
    return _command("opencode")


def _port_open(port: int, *, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((DEV_HOST, port), timeout=timeout):
            return True
    except OSError:
        return False


def _git(repo: Path, *args: str) -> str:
    result = _run(["git", "-C", str(repo), *args])
    if result.returncode:
        raise HubError(f"Git-Fehler: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def _repo_root(value: str | None) -> Path:
    candidate = Path(value).expanduser().resolve() if value else Path(__file__).resolve().parents[1]
    required = (candidate / "AGENTS.md", candidate / "docs/AI_HANDOFF.md")
    if not all(path.is_file() for path in required):
        raise HubError(f"Kein KAI-Checkout mit AGENTS.md und docs/AI_HANDOFF.md: {candidate}")
    return candidate


def _state_dir() -> Path:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    return STATE_ROOT


def _creation_flag(name: str) -> int:
    return int(getattr(subprocess, name, 0))


def ensure_ollama() -> None:
    if _port_open(OLLAMA_PORT):
        return
    executable = _command("ollama")
    if not executable:
        raise HubError("Ollama ist nicht installiert oder nicht auffindbar.")
    log = (_state_dir() / "ollama.log").open("a", encoding="utf-8")
    subprocess.Popen(  # noqa: S603
        [executable, "serve"],
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=_creation_flag("CREATE_NO_WINDOW"),
    )
    for _ in range(40):
        if _port_open(OLLAMA_PORT):
            return
        time.sleep(0.25)
    raise HubError(f"Ollama antwortet nicht auf {DEV_HOST}:{OLLAMA_PORT}.")


def _ollama_models() -> set[str]:
    executable = _command("ollama")
    if not executable:
        return set()
    result = _run([executable, "list"], timeout=20)
    if result.returncode:
        return set()
    return {line.split()[0] for line in result.stdout.splitlines()[1:] if line.strip()}


def _ssh_base() -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=7",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
    ]


def _fetch_dev_key() -> str:
    # Only this dedicated key crosses SSH. It stays in memory and is inherited
    # by the launched client; it is never written to a file or log.
    code = (
        "from pathlib import Path; "
        f"p=Path('{REMOTE_ENV}'); n='LITELLM_DEV_MASTER_KEY'; "
        "v=[x.split('=',1)[1].strip().strip(chr(34)).strip(chr(39)) "
        "for x in p.read_text().splitlines() if x.startswith(n+'=')]; "
        "print(v[-1] if v and v[-1] else '')"
    )
    remote = f"python3 -c {shlex.quote(code)}"
    result = _run([*_ssh_base(), f"{PI_USER}@{PI_HOST}", remote], timeout=20)
    key = result.stdout.strip()
    if result.returncode or not key:
        detail = result.stderr.strip() or "dedizierter Dev-Key fehlt/ist leer"
        raise HubError(f"Dev-Key konnte nicht über SSH bezogen werden: {detail}")
    return key


def _remote_proxy_is_open() -> bool:
    code = (
        "import socket,sys; s=socket.socket(); s.settimeout(1); "
        f"sys.exit(0 if s.connect_ex(('127.0.0.1',{DEV_PORT})) == 0 else 1)"
    )
    remote = f"python3 -c {shlex.quote(code)}"
    result = _run([*_ssh_base(), f"{PI_USER}@{PI_HOST}", remote], timeout=15)
    return result.returncode == 0


def _cloud_state_file() -> Path:
    return _state_dir() / "cloud-tunnel.json"


def _save_cloud_pid(pid: int, mode: str) -> None:
    payload = {
        "pid": pid,
        "mode": mode,
        "started_at": datetime.now(UTC).isoformat(),
        "local_endpoint": f"http://{DEV_HOST}:{DEV_PORT}/v1",
    }
    _cloud_state_file().write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _verify_cloud_catalog(key: str) -> None:
    request = urllib.request.Request(
        f"http://{DEV_HOST}:{DEV_PORT}/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    last_error: Exception | None = None
    payload: dict[str, Any] | None = None
    # SSH can accept the forwarded socket a few seconds before LiteLLM has
    # finished loading its routes. Treat connection resets as startup state,
    # while authentication/catalog errors still fail closed below.
    for _ in range(30):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                payload = json.load(response)
            break
        except (OSError, ValueError, urllib.error.HTTPError) as exc:
            last_error = exc
            time.sleep(0.5)
    if payload is None:
        raise HubError(f"LiteLLM-Katalogprüfung fehlgeschlagen: {last_error}") from last_error
    models = {item.get("id") for item in payload.get("data", []) if isinstance(item, dict)}
    missing = DEV_MODELS - models
    if missing:
        raise HubError(f"LiteLLM-Katalog unvollständig; fehlt: {', '.join(sorted(missing))}")


def _local_inference_probe() -> dict[str, Any]:
    ensure_ollama()
    if LOCAL_MODEL not in _ollama_models():
        raise HubError(f"Lokales Modell fehlt: {LOCAL_MODEL}")
    request = urllib.request.Request(
        f"http://{DEV_HOST}:{OLLAMA_PORT}/api/generate",
        data=json.dumps(
            {
                "model": LOCAL_MODEL,
                "prompt": "Antworte nur mit KAI_LOCAL_OK",
                "stream": False,
                "options": {"temperature": 0, "num_predict": 16},
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            body = json.load(response)
    except (OSError, ValueError) as exc:
        raise HubError(f"Lokale Modellantwort fehlgeschlagen: {type(exc).__name__}") from exc
    answer = str(body.get("response", ""))
    if "KAI_LOCAL_OK" not in answer:
        raise HubError("Lokales Modell antwortete, aber nicht mit dem erwarteten Prüftext.")
    return {"route": "ollama-local", "model": LOCAL_MODEL, "response_proven": True}


def _cloud_inference_probe(key: str, route: str) -> dict[str, Any]:
    if route not in {"kai-dev-code", "kai-dev-economy"}:
        raise HubError("Diese Route ist nicht für automatische Proben freigegeben.")
    request = urllib.request.Request(
        f"http://{DEV_HOST}:{DEV_PORT}/v1/chat/completions",
        data=json.dumps(
            {
                "model": route,
                "max_tokens": 128,
                "messages": [{"role": "user", "content": "Antworte nur mit: ok"}],
            }
        ).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            body = json.load(response)
            headers = response.headers
    except urllib.error.HTTPError as exc:
        raise HubError(_diagnose_http_error(route, exc)) from exc
    except (OSError, ValueError) as exc:
        raise HubError(
            f"Dev-Route {route}: keine gültige Antwort ({type(exc).__name__}) — "
            "Tunnel/Dev-Proxy erreichbar? 'Cloud prüfen' startet beides neu."
        ) from exc
    # The body echoes the route alias; the provider model behind it is only in
    # LiteLLM's response headers. Identity means the latter.
    provider_model = headers.get("x-litellm-model-name")
    api_base = headers.get("x-litellm-model-api-base")
    cost_header = headers.get("x-litellm-response-cost")
    choices = body.get("choices") or []
    answer = choices[0].get("message", {}).get("content") if choices else None
    try:
        cost = float(cost_header) if cost_header is not None else None
    except ValueError:
        cost = None
    if not provider_model or not answer or cost is None or cost <= 0:
        missing = ", ".join(
            label
            for label, absent in (
                ("Modellidentität (x-litellm-model-name)", not provider_model),
                ("Antworttext", not answer),
                ("positive Kostenmessung", cost is None or cost <= 0),
            )
            if absent
        )
        raise HubError(f"Dev-Route {route}: FAIL_CLOSED; fehlt: {missing}.")
    return {
        "route": route,
        "model": provider_model,
        "api_base": api_base,
        "model_group": headers.get("x-litellm-model-group") or body.get("model"),
        "attempted_retries": headers.get("x-litellm-attempted-retries"),
        "attempted_fallbacks": headers.get("x-litellm-attempted-fallbacks"),
        "response_proven": True,
        "cost_usd": cost,
    }


_HTTP_HINTS = {
    400: "Anfrage oder Route abgelehnt — Routenname und Parameter prüfen",
    401: "Schlüssel fehlt oder ist falsch — LITELLM_DEV_MASTER_KEY auf der Pi prüfen",
    403: "Schlüssel ohne Berechtigung für diese Route",
    404: "Route unbekannt — ist sie in config/litellm_dev.yaml definiert?",
    408: "Zeitgrenze überschritten — Anbieter langsam oder nicht erreichbar",
    429: "Rate- oder Ausgabenlimit beim Anbieter erreicht",
}
_SECRETISH = re.compile(r"(sk-[A-Za-z0-9_\-*]{4,}|Bearer\s+\S+|[A-Fa-f0-9]{40,})")


def _diagnose_http_error(route: str, exc: urllib.error.HTTPError) -> str:
    """Actionable text for an HTTP failure; key-like fragments are masked."""
    try:
        detail = json.loads(exc.read() or b"{}").get("error", {}).get("message", "")
    except (OSError, ValueError, AttributeError):
        detail = ""
    detail = _SECRETISH.sub("***", str(detail)).replace("\n", " ")[:200]
    if "No connected db" in detail:
        # LiteLLM without a database reports a wrong key this way (issue #1000).
        return (
            f"Dev-Route {route}: HTTP {exc.code} — Schlüssel falsch (LiteLLM ohne Datenbank "
            "meldet das als 'No connected db', #1000) — LITELLM_DEV_MASTER_KEY auf der Pi prüfen."
        )
    hint = _HTTP_HINTS.get(
        exc.code,
        "Anbieterfehler — Anbieterstatus und Dev-Key-Guthaben prüfen"
        if exc.code >= 500
        else "unerwarteter Status",
    )
    return f"Dev-Route {route}: HTTP {exc.code} — {hint}." + (
        f" Proxy meldet: {detail}" if detail else ""
    )


def doctor(repo: Path, mode: str = "offline") -> dict[str, Any]:
    if mode not in {"offline", "local-inference", "cloud"}:
        raise HubError(f"Unbekannter Diagnosemodus: {mode}")
    report: dict[str, Any] = {
        "schema_version": 1,
        "hub_version": HUB_VERSION,
        "checked_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "repository": str(repo),
        "checks": {},
    }
    checks = report["checks"]
    if mode in {"offline", "local-inference"} and not _port_open(OLLAMA_PORT):
        try:
            ensure_ollama()
        except (HubError, OSError, subprocess.SubprocessError) as exc:
            checks["ollama_start_error"] = str(exc)
    checks["opencode_installed"] = bool(_opencode_executable())
    checks["hermes_installed"] = bool(_command("hermes"))
    checks["kimi_installed"] = bool(_command("kimi"))
    checks["ollama_online"] = _port_open(OLLAMA_PORT)
    models = _ollama_models() if checks["ollama_online"] else set()
    checks["opencode_local_model"] = OPENCODE_LOCAL_MODEL in models
    checks["hermes_local_model"] = HERMES_LOCAL_MODEL in models
    checks["handoff_chain"] = verify_handoffs()[0]
    checks["cloud_tunnel_open"] = _port_open(DEV_PORT)
    sessions = workflow.list_sessions(STATE_ROOT)
    checks["managed_workspaces"] = sum(not row.get("orphaned", False) for row in sessions)
    checks["orphaned_workspaces"] = sum(bool(row.get("orphaned")) for row in sessions)
    checks["local_automations"] = automation_inventory()
    if mode == "local-inference":
        checks["local_inference"] = _local_inference_probe()
        _save_local_response_proof(checks["local_inference"])
    elif mode == "cloud":
        key = start_cloud()
        checks["cloud_tunnel_open"] = _port_open(DEV_PORT)
        checks["dev_routes"] = [
            _cloud_inference_probe(key, route) for route in ("kai-dev-economy", "kai-dev-code")
        ]
    checks["local_prerequisites_ok"] = all(
        checks[name]
        for name in (
            "opencode_installed",
            "hermes_installed",
            "ollama_online",
            "opencode_local_model",
            "hermes_local_model",
            "handoff_chain",
        )
    )
    # Only the local-inference probe proves a generated answer; installed
    # clients and models are prerequisites, not proof (audit 27.09.).
    checks["local_response_proven"] = bool(checks.get("local_inference", {}).get("response_proven"))
    try:
        primary = workflow._primary_checkout(repo)
        checks["local_task_startable"] = (
            workflow.cached_remote_base(primary, STATE_ROOT) is not None
        )
    except (workflow.WorkflowError, OSError, subprocess.SubprocessError):
        checks["local_task_startable"] = False
    # Compatibility aliases (0.3.1/0.3.2 callers, Health-Task exit code). Both
    # mean prerequisites only, never a proven answer or a startable worktree.
    checks["local_inference_ready"] = checks["local_prerequisites_ok"]
    checks["offline_ready"] = checks["local_prerequisites_ok"]
    return report


def automation_inventory() -> dict[str, Any]:
    """Read local Task Scheduler state; Pi/systemd needs separate evidence."""
    if sys.platform != "win32":
        return {"available": False, "reason": "Windows Task Scheduler nicht verfügbar"}
    # Event 201 ("action completed") carries the action's exit code in
    # Properties[3]. Event 102 ("task completed") is written for failed runs
    # too and must not be read as success.
    script = (
        "$success=@{}; $failure=@{}; "
        "$events=Get-WinEvent -FilterHashtable "
        "@{LogName='Microsoft-Windows-TaskScheduler/Operational';Id=201;"
        "StartTime=(Get-Date).AddDays(-2)} -MaxEvents 2000 -ErrorAction SilentlyContinue; "
        "foreach($e in $events) { $n=([string]$e.Properties[0].Value).TrimStart('\\'); "
        "if($n -notlike 'KAI-*') { continue }; $rc=[int64]$e.Properties[3].Value; "
        "$slot=if($rc -eq 0){$success}else{$failure}; "
        "if(-not $slot.ContainsKey($n)) { $slot[$n]=$e.TimeCreated.ToString('o') } }; "
        "Get-ScheduledTask | Where-Object { $_.TaskName -like 'KAI-*' } | "
        "ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; "
        "$last = if ($i -and $i.LastRunTime) { $i.LastRunTime.ToString('o') } else { $null }; "
        "$next = if ($i -and $i.NextRunTime) { $i.NextRunTime.ToString('o') } else { $null }; "
        "$code=[int64]$i.LastTaskResult; "
        "$meaning=if($code -eq 0){'SUCCESS'}elseif($code -eq 267009){'RUNNING'}"
        "elseif($code -eq 267011){'NOT_RUN'}"
        "elseif($code -eq 2147946720){'MISSED_SCHEDULE'}else{'NONZERO_CHECK_LOG'}; "
        "[pscustomobject]@{name=$_.TaskName; state=[string]$_.State; "
        "owner=$_.Principal.UserId; last_run=$last; last_success=$success[$_.TaskName]; "
        "last_failure=$failure[$_.TaskName]; "
        "last_result=$code; last_result_hex=('0x{0:X8}' -f $code); "
        "last_result_meaning=$meaning; next_run=$next} } | "
        "ConvertTo-Json -Depth 3 -Compress"
    )
    executable = _command("powershell") or _command("pwsh")
    if not executable:
        return {"available": False, "reason": "PowerShell nicht gefunden"}
    result = _run([executable, "-NoProfile", "-NonInteractive", "-Command", script], timeout=25)
    if result.returncode:
        return {
            "available": False,
            "reason": "Aufgabenplanung antwortete nicht",
            "exit_code": result.returncode,
            "error_type": result.stderr.strip().splitlines()[-1][:160]
            if result.stderr.strip()
            else "unknown",
        }
    try:
        decoded = json.loads(result.stdout) if result.stdout.strip() else []
    except json.JSONDecodeError:
        return {"available": False, "reason": "Aufgabenplanung lieferte ungültige Daten"}
    tasks = decoded if isinstance(decoded, list) else [decoded]
    return {
        "available": True,
        "tasks": tasks,
        "last_success_history_window_days": 2,
        "failed_or_skipped": [
            row["name"]
            for row in tasks
            if int(row.get("last_result", 0)) not in (0, 267009, 267011)
        ],
        "pi_systemd": "NOT_CHECKED_BY_LOCAL_HUB",
        "codex_scheduled_tasks": "NOT_CHECKED_BY_LOCAL_HUB",
    }


def start_cloud() -> str:
    key = _fetch_dev_key()
    state_file = _cloud_state_file()
    if state_file.is_file() and not _port_open(DEV_PORT):
        # Leftover from a crash or reboot: reclaim a proxy this hub started
        # before a new start could mistake it for a foreign one.
        stop_cloud()
    if not _port_open(DEV_PORT):
        remote_open = _remote_proxy_is_open()
        no_command = ["-N"] if remote_open else []
        remote_command = (
            f"echo $$ > {shlex.quote(REMOTE_PID_FILE)}; exec bash {shlex.quote(REMOTE_PROXY)} proxy"
        )
        remote_args = [] if remote_open else ["sh", "-c", shlex.quote(remote_command)]
        log = (_state_dir() / "cloud-ssh.log").open("a", encoding="utf-8")
        process = subprocess.Popen(  # noqa: S603
            [
                *_ssh_base(),
                *no_command,
                "-o",
                "ExitOnForwardFailure=yes",
                "-L",
                f"{DEV_PORT}:{DEV_HOST}:{DEV_PORT}",
                f"{PI_USER}@{PI_HOST}",
                *remote_args,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=_creation_flag("CREATE_NO_WINDOW"),
        )
        # A running proxy whose PID file this hub wrote (e.g. orphaned since a
        # lost tunnel) is adopted, so the next stop ends it; a foreign one is not.
        owned = not remote_open or _remote_proxy_owned()
        _save_cloud_pid(process.pid, "proxy-and-tunnel" if owned else "tunnel-only")
        try:
            for _ in range(80):
                if process.poll() is not None:
                    raise HubError(
                        "SSH-Tunnel/Dev-Proxy wurde vorzeitig beendet. Siehe "
                        f"{_state_dir() / 'cloud-ssh.log'}"
                    )
                if _port_open(DEV_PORT):
                    break
                time.sleep(0.25)
            else:
                raise HubError(f"Dev-Proxy antwortet nicht auf {DEV_HOST}:{DEV_PORT}.")
            _verify_cloud_catalog(key)
        except HubError:
            # Started here, so cleaned up here: no orphaned tunnel or Pi proxy.
            stop_cloud()
            raise
        return key
    _verify_cloud_catalog(key)
    return key


def _process_image(pid: int) -> str | None:
    """Lower-case image name of the process running as ``pid``; None if none runs."""
    if pid <= 0:
        return None
    if sys.platform == "win32":
        result = _run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=10)
        for row in csv.reader(result.stdout.splitlines()):
            if len(row) > 1 and row[1].strip() == str(pid):
                return row[0].strip().casefold()
        return None
    if os.name == "nt":  # os.kill(pid, 0) would terminate the process on Windows
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass  # exists, owned by another user
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip().casefold()
    except OSError:
        return ""


def _pid_is_ssh(pid: int) -> bool:
    """True only if ``pid`` still is an ssh process (PIDs are reused)."""
    return sys.platform == "win32" and _process_image(pid) == "ssh.exe"


def stop_cloud() -> bool:
    """Stop what this hub started; also after a crash, reboot or dead tunnel.

    The state file is kept until both ends are verifiably down, so a later
    call can finish the cleanup (e.g. once the Pi is reachable again).
    """
    state_file = _cloud_state_file()
    if not state_file.is_file():
        return False
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        pid = int(state["pid"])
        mode = str(state.get("mode", ""))
    except (ValueError, KeyError, json.JSONDecodeError):
        return False
    if _pid_is_ssh(pid):
        result = _run(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=15)
        if result.returncode != 0:
            return False
    if mode == "proxy-and-tunnel" and not _stop_remote_proxy():
        return False
    state_file.unlink(missing_ok=True)
    return True


def _remote_proxy_owned() -> bool:
    """True if the Pi dev proxy runs under the PID this hub recorded."""
    code = (
        "from pathlib import Path; import sys; "
        f"p=Path('{REMOTE_PID_FILE}'); "
        "pid=int(p.read_text().strip() or 0) if p.is_file() else 0; "
        "q=Path(f'/proc/{pid}/cmdline'); "
        "c=q.read_bytes().replace(bytes([0]),b' ').decode() if pid and q.is_file() else ''; "
        f"sys.exit(0 if ('litellm_dev.yaml' in c and '--port {DEV_PORT}' in c) else 1)"
    )
    remote = f"python3 -c {shlex.quote(code)}"
    return _run([*_ssh_base(), f"{PI_USER}@{PI_HOST}", remote], timeout=15).returncode == 0


def _stop_remote_proxy() -> bool:
    """Stop the Pi dev proxy this hub started; True if it is gone afterwards.

    Remote exit 0 = stopped, 3 = already gone (stale PID file removed),
    1 = PID belongs to a foreign process (left alone), 255 = SSH failed.
    """
    code = (
        "from pathlib import Path; import os,signal,sys; "
        f"p=Path('{REMOTE_PID_FILE}'); "
        "pid=int(p.read_text().strip() or 0) if p.is_file() else 0; "
        "q=Path(f'/proc/{pid}/cmdline'); "
        "c=q.read_bytes().replace(bytes([0]),b' ').decode() if pid and q.is_file() else ''; "
        "p.unlink(missing_ok=True) if not c else None; "
        "sys.exit(3) if not c else None; "
        f"ok=('litellm_dev.yaml' in c and '--port {DEV_PORT}' in c); "
        "os.kill(pid,signal.SIGTERM) if ok else None; "
        "p.unlink(missing_ok=True) if ok else None; sys.exit(0 if ok else 1)"
    )
    remote = f"python3 -c {shlex.quote(code)}"
    result = _run([*_ssh_base(), f"{PI_USER}@{PI_HOST}", remote], timeout=15)
    return result.returncode in (0, 3)


def _opencode_local_config() -> Path:
    """Hub-owned OpenCode config fragment (merged via OPENCODE_CONFIG).

    Declares the loopback Ollama provider with the 64K model, so the operator's
    global OpenCode configuration stays untouched.
    """
    target = _state_dir() / "opencode-local.json"
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "ollama": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Ollama lokal (KAI Developer Hub)",
                "options": {"baseURL": f"http://{DEV_HOST}:{OLLAMA_PORT}/v1"},
                "models": {OPENCODE_LOCAL_MODEL: {"name": OPENCODE_LOCAL_MODEL}},
            }
        },
    }
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def launch_opencode(
    repo: Path, route: str, handoff: dict[str, Any] | None = None, *, take_over: bool = False
) -> None:
    session = workflow.require_session(repo, STATE_ROOT)
    _refuse_busy_workspace(repo, take_over)  # early: before tunnel and probe
    executable = _opencode_executable()
    if not executable:
        raise HubError("OpenCode ist nicht installiert oder nicht im PATH.")
    env = os.environ.copy()
    if route == "local":
        ensure_ollama()
        if OPENCODE_LOCAL_MODEL not in _ollama_models():
            raise HubError(f"Lokales Modell fehlt: {OPENCODE_LOCAL_MODEL}")
        env["OPENCODE_CONFIG"] = str(_opencode_local_config())
        model = f"ollama/{OPENCODE_LOCAL_MODEL}"
    elif route == "cloud":
        env["KAI_DEV_LITELLM_KEY"] = start_cloud()
        _cloud_inference_probe(env["KAI_DEV_LITELLM_KEY"], "kai-dev-code")
        model = "kai-litellm-dev/kai-dev-code"
    else:
        raise HubError(f"Unbekannte Route: {route}")
    env["KAI_AGENT_SURFACE"] = f"opencode-{route}"
    pack = workflow.context_pack(
        repo,
        STATE_ROOT,
        task=session["task"],
        handoff=handoff,
        compact=True,
    )
    prompt = (
        f"KAI-Aufgabe: {session['task']}. Hier ist das verifizierbare Kontextpaket "
        f"(Quelle: {pack.name}):\n\n{pack.read_text(encoding='utf-8')}\n\n"
        + (
            f"Bestätige die Übergabe-ID {handoff['handoff_id']} und die Challenge "
            f"{handoff['ack_challenge']}, beschreibe den offenen Schritt und nenne die "
            "mitgelieferten Quellen, auf die du dich stützt. "
            if handoff
            else ""
        )
        + "Beachte die Rollen und Grenzen. Warte vor schreibenden Änderungen auf die konkrete Aufgabe."
    )
    if len(prompt) > 28_000:
        raise HubError("Kontextpaket ist für den Windows-Startprompt zu groß.")
    _start_writer(
        repo,
        f"opencode-{route}",
        [executable, str(repo), "-m", model, "--prompt", prompt],
        handoff=handoff,
        session_id=session["session_id"],
        take_over=take_over,
        cwd=repo,
        env=env,
        creationflags=_creation_flag("CREATE_NEW_CONSOLE"),
    )


def _copy_to_clipboard(text: str) -> None:
    if sys.platform != "win32":
        return
    import tkinter as tk

    clipboard = tk.Tk()
    clipboard.withdraw()
    clipboard.clipboard_clear()
    clipboard.clipboard_append(text)
    clipboard.update()
    clipboard.destroy()


def launch_hermes(
    repo: Path, handoff: dict[str, Any] | None = None, *, take_over: bool = False
) -> None:
    session = workflow.require_session(repo, STATE_ROOT)
    _refuse_busy_workspace(repo, take_over)
    executable = _command("hermes")
    if not executable:
        raise HubError("Hermes ist nicht installiert.")
    env = os.environ.copy()
    ensure_ollama()
    if HERMES_LOCAL_MODEL not in _ollama_models():
        raise HubError(f"Hermes-64K-Modell fehlt: {HERMES_LOCAL_MODEL}")
    model = HERMES_LOCAL_MODEL
    env["CUSTOM_BASE_URL"] = f"http://{DEV_HOST}:{OLLAMA_PORT}/v1"
    env["CUSTOM_API_KEY"] = "ollama-local"
    env["HERMES_INFERENCE_MODEL"] = model
    env["HERMES_INFERENCE_PROVIDER"] = "custom"
    # Hermes' file/terminal tools resolve relative paths against TERMINAL_CWD,
    # not against --in; unset, read_file looked in the home directory.
    env["TERMINAL_CWD"] = str(repo)
    env["KAI_AGENT_SURFACE"] = "hermes-local"
    pack = workflow.context_pack(
        repo, STATE_ROOT, task=session["task"], handoff=handoff, compact=True
    )
    # Hermes TUI has no initial-prompt flag. Place the complete first prompt on
    # the clipboard, with a visible file fallback; the operator pastes it once.
    _copy_to_clipboard(pack.read_text(encoding="utf-8"))
    _start_writer(
        repo,
        "hermes-local",
        [
            executable,
            "--tui",
            "--provider",
            "custom",
            "--model",
            model,
            "--reasoning",
            "none",
            "--in",
            str(repo),
        ],
        handoff=handoff,
        session_id=session["session_id"],
        take_over=take_over,
        cwd=repo,
        env=env,
        creationflags=_creation_flag("CREATE_NEW_CONSOLE"),
    )


def context_pack(repo: Path, sources: Sequence[str] = ()) -> Path:
    session = workflow.require_session(repo, STATE_ROOT)
    return workflow.context_pack(repo, STATE_ROOT, task=session["task"], sources=list(sources))


def launch_kimi(repo: Path, handoff: dict[str, Any] | None = None) -> Path:
    # No writer lease: Kimi only receives the context pack and has no local
    # write access to the worktree (runbook), so it cannot be a second writer.
    workflow.require_session(repo, STATE_ROOT)
    executable = _command("kimi")
    if not executable:
        raise HubError("Kimi Desktop ist nicht installiert.")
    pack = Path(handoff["context_pack_path"]) if handoff else context_pack(repo)
    subprocess.Popen([executable], cwd=repo)  # noqa: S603
    if sys.platform == "win32":
        subprocess.Popen(["explorer.exe", "/select,", str(pack)])  # noqa: S603
    return pack


def launch_recipient(repo: Path, handoff: dict[str, Any]) -> str:
    target = str(handoff["to_agent"]).strip().casefold()
    if target in {"opencode", "opencode local", "opencode lokal"}:
        launch_opencode(repo, "local", handoff)
        return "OpenCode local"
    if target in {"opencode cloud", "opencode-cloud"}:
        launch_opencode(repo, "cloud", handoff)
        return "OpenCode cloud"
    if target in {"hermes", "hermes local", "hermes lokal"}:
        launch_hermes(repo, handoff)
        return "Hermes local (Kontextpaket aus Zwischenablage als ersten Prompt einfügen)"
    if target == "kimi":
        launch_kimi(repo, handoff)
        return "Kimi (Kontextpaket anhängen)"
    raise HubError("Empfänger unbekannt. OpenCode local/cloud, Hermes oder Kimi wählen.")


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _handoff_paths() -> tuple[Path, Path]:
    root = _state_dir() / "handoffs"
    root.mkdir(parents=True, exist_ok=True)
    return root / "ledger.jsonl", root / "CURRENT_HANDOFF.md"


def _lock_first_byte(handle: BinaryIO, *, release: bool = False) -> None:
    """Non-blocking lock (or release) of byte 0; busy raises BlockingIOError/PermissionError."""
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK if release else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN if release else fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextlib.contextmanager
def _file_lock(target: Path, label: str) -> Iterator[None]:
    """Exclusive cross-process lock for one read-check-write of ``target``.

    Without it two hub processes (UI and CLI, or two UIs) read the same state
    and both write onto it: the ledger's hash chain forks (audit 27.09.), or
    two clients get the same workspace. Same sidecar convention as
    ``app/core/file_lock.py`` in strict mode, but stdlib-only and with a
    bounded wait: the installed hub ships without ``app/``, and a hung peer
    must not block the operator indefinitely.
    """
    lock_path = target.with_name(target.name + ".lock")
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    with lock_path.open("a+b") as handle:
        while True:
            try:
                _lock_first_byte(handle)
                break
            except (BlockingIOError, PermissionError):
                if time.monotonic() >= deadline:
                    raise HubError(
                        f"{label} seit {LOCK_TIMEOUT_S:g} s von einem anderen "
                        f"Hub-Prozess gesperrt ({lock_path}); nichts geschrieben, bitte "
                        "erneut versuchen."
                    ) from None
                time.sleep(0.05)
        try:
            yield
        finally:
            _lock_first_byte(handle, release=True)


def _ledger_serialized[**P, R](operation: Callable[P, R]) -> Callable[P, R]:
    """Run a ledger-writing operation entirely under the ledger lock."""

    @functools.wraps(operation)
    def locked(*args: P.args, **kwargs: P.kwargs) -> R:
        with _file_lock(_handoff_paths()[0], "Übergabe-Ledger"):
            return operation(*args, **kwargs)

    return locked


@_ledger_serialized
def create_handoff(
    repo: Path,
    *,
    from_agent: str,
    to_agent: str,
    task: str,
    completed: str,
    open_items: str,
    assumptions: str,
    next_action: str,
    tests: str,
    sources: Sequence[str] = (),
) -> dict[str, Any]:
    session = workflow.require_session(repo, STATE_ROOT)
    if not from_agent.strip() or not to_agent.strip() or not task.strip():
        raise HubError("Von-Agent, An-Agent und Auftrag sind Pflichtfelder.")
    # Checked before snapshot and ledger: a refused source leaves no trace.
    context_sources = workflow.validate_sources(repo, sources)
    valid, reason = verify_handoffs()
    if not valid:
        raise HubError(f"Übergabekette ungültig: {reason}")
    ledger, current = _handoff_paths()
    previous = "GENESIS"
    if ledger.is_file():
        rows = [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        if rows:
            previous = json.loads(rows[-1])["payload_sha256"]
    status = _git(repo, "status", "--porcelain=v1")
    handoff_id = str(uuid.uuid4())
    saved = workflow.snapshot(repo, STATE_ROOT, handoff_id)
    payload: dict[str, Any] = {
        "schema_version": 2,
        "event": "handoff",
        "handoff_id": handoff_id,
        "session_id": session["session_id"],
        "ack_challenge": f"ACK:{uuid.uuid4().hex[:12]}",
        "snapshot_path": saved["path"],
        "snapshot_manifest_sha256": saved["manifest_sha256"],
        "created_at": datetime.now(UTC).isoformat(),
        "from_agent": from_agent.strip(),
        "to_agent": to_agent.strip(),
        "repository": str(repo),
        "branch": _git(repo, "branch", "--show-current") or "DETACHED",
        "head": _git(repo, "rev-parse", "HEAD"),
        "worktree_status": status.splitlines() if status else [],
        "task": task.strip(),
        "completed": completed.strip(),
        "open_items": open_items.strip(),
        "assumptions": assumptions.strip(),
        "next_action": next_action.strip(),
        "tests": tests.strip(),
        "context_sources": context_sources,
        "previous_sha256": previous,
    }
    payload["payload_sha256"] = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
    with ledger.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical_json(payload) + "\n")
    pack = workflow.context_pack(repo, STATE_ROOT, task=task, handoff=payload)
    payload["context_pack_path"] = str(pack)
    current.write_text(
        "\n".join(
            [
                "# KAI agent handoff",
                "",
                f"- ID: `{payload['handoff_id']}`",
                f"- From → To: **{payload['from_agent']} → {payload['to_agent']}**",
                f"- Branch / HEAD: `{payload['branch']}` / `{payload['head']}`",
                f"- Receipt SHA-256: `{payload['payload_sha256']}`",
                f"- Previous: `{payload['previous_sha256']}`",
                f"- Acknowledgement challenge: `{payload['ack_challenge']}`",
                f"- Session ID (workspace): `{payload['session_id']}`",
                f"- Task sources: {', '.join(context_sources) or '—'}",
                f"- Context pack: `{pack}`",
                f"- Recoverable snapshot: `{saved['path']}`",
                "",
                "## Task",
                payload["task"],
                "",
                "## Completed",
                payload["completed"] or "—",
                "",
                "## Open",
                payload["open_items"] or "—",
                "",
                "## Assumptions",
                payload["assumptions"] or "—",
                "",
                "## Next action",
                payload["next_action"] or "—",
                "",
                "## Tests",
                payload["tests"] or "—",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return payload


@_ledger_serialized
def acknowledge_handoff(
    repo: Path, *, handoff_id: str, agent: str, response: str
) -> dict[str, Any]:
    """Record a recipient's answer; this is evidence, not model authentication."""
    valid, reason = verify_handoffs()
    if not valid:
        raise HubError(f"Übergabekette ungültig: {reason}")
    ledger, _ = _handoff_paths()
    if not ledger.is_file():
        raise HubError("Keine Übergabe zum Bestätigen vorhanden.")
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    source = next(
        (
            row
            for row in rows
            if row.get("event", "handoff") == "handoff" and row.get("handoff_id") == handoff_id
        ),
        None,
    )
    if source is None or not source.get("ack_challenge"):
        raise HubError("Übergabe-ID fehlt oder stammt aus dem alten Schema ohne Challenge.")
    if any(row.get("event") == "ack" and row.get("handoff_id") == handoff_id for row in rows):
        raise HubError("Diese Übergabe wurde bereits bestätigt.")
    if any(row.get("event") == "supersede" and row.get("handoff_id") == handoff_id for row in rows):
        raise HubError("Diese Übergabe wurde bereits nachvollziehbar abgelöst.")
    if Path(source["repository"]).resolve() != repo.resolve():
        raise HubError("Übergabe gehört zu einem anderen Arbeitsbereich.")
    if agent.strip().casefold() != source["to_agent"].casefold():
        raise HubError("Bestätigender Agent ist nicht der eingetragene Empfänger.")
    response = response.strip()
    if len(response) < 50 or len(response) > 4000 or source["ack_challenge"] not in response:
        raise HubError(
            "Antwort muss die Challenge und eine verständliche Aufgabenübernahme enthalten."
        )
    # From schema 2 on the recipient must name the handoff itself; a session
    # ID in its place (the Kimi confusion) is not an acknowledgement.
    schema = 2 if int(source.get("schema_version", 1)) >= 2 else 1
    if schema >= 2 and handoff_id not in response:
        raise HubError("Antwort muss die Übergabe-ID nennen (nicht die Session-ID).")
    event: dict[str, Any] = {
        "schema_version": schema,
        "event": "ack",
        "handoff_id": handoff_id,
        "created_at": datetime.now(UTC).isoformat(),
        "from_agent": agent.strip(),
        "to_agent": source["from_agent"],
        "handoff_sha256": source["payload_sha256"],
        "ack_challenge": source["ack_challenge"],
        "response": response,
        "previous_sha256": rows[-1]["payload_sha256"],
    }
    event["payload_sha256"] = hashlib.sha256(_canonical_json(event).encode()).hexdigest()
    with ledger.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical_json(event) + "\n")
    return event


@_ledger_serialized
def supersede_handoff(
    handoff_id: str, reason: str, replaced_by: str | None = None
) -> dict[str, Any]:
    """Close a pending handoff with an append-only, independently verifiable event."""
    valid, detail = verify_handoffs()
    if not valid:
        raise HubError(f"Übergabekette ungültig: {detail}")
    reason = reason.strip()
    replaced_by = replaced_by.strip() if replaced_by else None
    if not reason:
        raise HubError("Grund für das Ablösen ist ein Pflichtfeld.")
    ledger, _ = _handoff_paths()
    if not ledger.is_file():
        raise HubError("Keine Übergabe zum Ablösen vorhanden.")
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    handoffs = {row["handoff_id"]: row for row in rows if row.get("event", "handoff") == "handoff"}
    source = handoffs.get(handoff_id)
    if source is None:
        raise HubError("Übergabe-ID ist unbekannt.")
    if any(
        row.get("event") in {"ack", "supersede"} and row.get("handoff_id") == handoff_id
        for row in rows
    ):
        raise HubError("Übergabe ist bereits bestätigt oder abgelöst.")
    if replaced_by:
        replacement = handoffs.get(replaced_by)
        if replacement is None or replaced_by == handoff_id:
            raise HubError("Ersatz-Übergabe ist unbekannt oder identisch.")
        if Path(replacement["repository"]).resolve() != Path(source["repository"]).resolve():
            raise HubError("Ersatz-Übergabe gehört zu einem anderen Arbeitsbereich.")
    event: dict[str, Any] = {
        "schema_version": 2,
        "event": "supersede",
        "handoff_id": handoff_id,
        "created_at": datetime.now(UTC).isoformat(),
        "reason": reason,
        "replaced_by": replaced_by,
        "handoff_sha256": source["payload_sha256"],
        "previous_sha256": rows[-1]["payload_sha256"],
    }
    event["payload_sha256"] = hashlib.sha256(_canonical_json(event).encode()).hexdigest()
    with ledger.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical_json(event) + "\n")
    return event


def handoff_state(repo: Path | None = None) -> dict[str, Any]:
    ledger, _ = _handoff_paths()
    if not ledger.is_file():
        return {"handoffs": 0, "acknowledged": 0, "superseded": [], "pending": []}
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    handoffs = [
        row
        for row in rows
        if row.get("event", "handoff") == "handoff"
        and (repo is None or Path(row["repository"]).resolve() == repo.resolve())
    ]
    acknowledged = {row["handoff_id"] for row in rows if row.get("event") == "ack"}
    superseded_events = {row["handoff_id"]: row for row in rows if row.get("event") == "supersede"}
    superseded = [
        {
            "handoff_id": row["handoff_id"],
            "reason": superseded_events[row["handoff_id"]]["reason"],
            "replaced_by": superseded_events[row["handoff_id"]].get("replaced_by"),
            "created_at": superseded_events[row["handoff_id"]]["created_at"],
        }
        for row in handoffs
        if row["handoff_id"] in superseded_events
    ]
    pending = [
        {
            "handoff_id": row["handoff_id"],
            "to_agent": row["to_agent"],
            "created_at": row["created_at"],
        }
        for row in handoffs
        if row["handoff_id"] not in acknowledged and row["handoff_id"] not in superseded_events
    ]
    return {
        "handoffs": len(handoffs),
        "acknowledged": sum(row["handoff_id"] in acknowledged for row in handoffs),
        "superseded": superseded,
        "pending": pending,
    }


def verify_handoffs() -> tuple[bool, str]:
    ledger, _ = _handoff_paths()
    if not ledger.is_file():
        return True, "Keine Übergaben vorhanden."
    previous = "GENESIS"
    count = 0
    handoffs: dict[str, dict[str, Any]] = {}
    acknowledged: set[str] = set()
    superseded: set[str] = set()
    for line_number, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            row = json.loads(line)
            claimed = row.pop("payload_sha256")
        except (json.JSONDecodeError, KeyError) as exc:
            return False, f"Zeile {line_number}: ungültiger Beleg ({exc})"
        actual = hashlib.sha256(_canonical_json(row).encode()).hexdigest()
        if claimed != actual:
            return False, f"Zeile {line_number}: SHA-256 stimmt nicht"
        if row.get("previous_sha256") != previous:
            return False, f"Zeile {line_number}: Kette unterbrochen"
        if row.get("event", "handoff") == "handoff":
            count += 1
            handoffs[row["handoff_id"]] = {**row, "payload_sha256": claimed}
        elif row.get("event") == "ack":
            source = handoffs.get(row.get("handoff_id", ""))
            if (
                source is None
                or row["handoff_id"] in acknowledged
                or row["handoff_id"] in superseded
                or row.get("handoff_sha256") != source["payload_sha256"]
                or row.get("ack_challenge") != source.get("ack_challenge")
                or row.get("from_agent", "").casefold() != source["to_agent"].casefold()
                or row.get("ack_challenge", "") not in row.get("response", "")
                or len(row.get("response", "")) < 50
                or (
                    int(row.get("schema_version", 1)) >= 2
                    and row["handoff_id"] not in row.get("response", "")
                )
            ):
                return False, f"Zeile {line_number}: ungültige Empfangsbestätigung"
            acknowledged.add(row["handoff_id"])
        elif row.get("event") == "supersede":
            source = handoffs.get(row.get("handoff_id", ""))
            replacement_id = row.get("replaced_by")
            replacement = handoffs.get(replacement_id) if replacement_id else None
            if (
                source is None
                or row["handoff_id"] in acknowledged
                or row["handoff_id"] in superseded
                or row.get("handoff_sha256") != source["payload_sha256"]
                or not str(row.get("reason", "")).strip()
                or replacement_id == row["handoff_id"]
                or (replacement_id and replacement is None)
                or (
                    replacement is not None
                    and Path(replacement["repository"]).resolve()
                    != Path(source["repository"]).resolve()
                )
            ):
                return False, f"Zeile {line_number}: ungültige Ablösung"
            superseded.add(row["handoff_id"])
        elif row.get("event") == "writer_takeover":
            if not (
                str(row.get("worktree", "")).strip()
                and str(row.get("new_client", "")).strip()
                and isinstance(row.get("previous_writer"), dict)
                and row["previous_writer"].get("pid")
            ):
                return False, f"Zeile {line_number}: ungültige Schreiberübernahme"
        else:
            return False, f"Zeile {line_number}: unbekannter Ereignistyp"
        previous = claimed
    return (
        True,
        f"{count} Übergabe(n), {len(acknowledged)} bestätigt, "
        f"{len(superseded)} abgelöst; Hash-Kette unverändert.",
    )


# --- Writer lease: one writing client per workspace (audit 27.09., point 4) ----


def _describe_writer(lease: dict[str, Any] | None) -> str:
    if not lease:
        return "keiner"
    text = (
        f"{lease['client']} (PID {lease['pid']}, Host {lease.get('host')}, "
        f"seit {lease.get('started_at')}"
    )
    return text + (f", Übergabe {lease['handoff_id']})" if lease.get("handoff_id") else ")")


class WriterBusyError(HubError):
    """A live client already writes this workspace."""

    def __init__(self, lease: dict[str, Any], hint: str | None = None) -> None:
        self.lease = lease
        super().__init__(
            f"Arbeitsbereich wird bereits bearbeitet: {_describe_writer(lease)}. "
            + (
                hint
                or "Diesen Client zuerst beenden oder ausdrücklich übernehmen (--take-over "
                "bzw. Bestätigung im Hub; die Übernahme wird im Übergabe-Ledger protokolliert)."
            )
        )


def _lease_path(worktree: Path) -> Path:
    key = hashlib.sha256(os.path.normcase(str(worktree.resolve())).encode()).hexdigest()[:16]
    root = _state_dir() / "writers"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{key}.json"


def _read_lease(path: Path) -> tuple[dict[str, Any] | None, bool]:
    """(lease, alive): alive while its process runs; another host cannot be checked."""
    try:
        lease = json.loads(path.read_text(encoding="utf-8"))
        pid = int(lease["pid"])
    except FileNotFoundError:
        return None, False
    except (OSError, ValueError, KeyError, TypeError):
        return {"client": "unlesbarer Lease", "pid": 0, "file": str(path)}, False
    if lease.get("host") != socket.gethostname():
        return lease, True  # fail closed: only an explicit take-over ends it
    image = _process_image(pid)
    # Same PID under another image name means the PID was reused: dead lease.
    return lease, image is not None and (not lease.get("image") or image == lease["image"])


def active_writer(worktree: Path) -> dict[str, Any] | None:
    """The live writer lease of ``worktree``; a lease ends when its client ends."""
    lease, alive = _read_lease(_lease_path(worktree))
    return lease if alive else None


def _refuse_busy_workspace(worktree: Path, take_over: bool) -> None:
    lease = active_writer(worktree)
    if lease and not take_over:
        raise WriterBusyError(lease)


def _log_writer_event(event: dict[str, Any]) -> None:
    line = {"ts": datetime.now(UTC).isoformat(), **event}
    with (_state_dir() / "writers" / "events.jsonl").open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")


@_ledger_serialized
def _record_writer_takeover(
    worktree: Path, previous: dict[str, Any], client: str
) -> dict[str, Any]:
    """An explicit take-over is an operator decision: it goes into the hash chain."""
    valid, reason = verify_handoffs()
    if not valid:
        raise HubError(f"Übergabekette ungültig: {reason}; Übernahme ohne Beleg verweigert.")
    ledger, _ = _handoff_paths()
    rows = (
        [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        if ledger.is_file()
        else []
    )
    event: dict[str, Any] = {
        "schema_version": 2,
        "event": "writer_takeover",
        "created_at": datetime.now(UTC).isoformat(),
        "worktree": str(worktree.resolve()),
        "previous_writer": previous,
        "new_client": client,
        "host": socket.gethostname(),
        "previous_sha256": json.loads(rows[-1])["payload_sha256"] if rows else "GENESIS",
    }
    event["payload_sha256"] = hashlib.sha256(_canonical_json(event).encode()).hexdigest()
    with ledger.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical_json(event) + "\n")
    return event


def _start_writer(
    worktree: Path,
    client: str,
    args: list[str],
    *,
    handoff: dict[str, Any] | None = None,
    session_id: str | None = None,
    take_over: bool = False,
    **popen: Any,
) -> subprocess.Popen[Any]:
    """Start a writing client and record it as the workspace's only writer.

    Check, start and lease write run under one cross-process lock, so two hub
    processes cannot both find the workspace free. A dead lease (client ended
    or crashed) is taken over and logged; a live one only with ``take_over``,
    recorded in the ledger. A take-over does not end the previous client.
    """
    path = _lease_path(worktree)
    with _file_lock(path, "Schreibersperre"):
        previous, alive = _read_lease(path)
        if previous is not None and alive:
            if not take_over:
                raise WriterBusyError(previous)
            _record_writer_takeover(worktree, previous, client)
        elif previous is not None:
            _log_writer_event(
                {
                    "event": "dead_lease_taken_over",
                    "worktree": str(worktree.resolve()),
                    "previous": previous,
                    "client": client,
                }
            )
        process = subprocess.Popen(args, **popen)  # noqa: S603
        lease = {
            "schema_version": 1,
            "worktree": str(worktree.resolve()),
            "client": client,
            "pid": process.pid,
            "image": _process_image(process.pid),
            "host": socket.gethostname(),
            "started_at": datetime.now(UTC).isoformat(),
            "session_id": session_id,
            "handoff_id": handoff.get("handoff_id") if handoff else None,
        }
        staging = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        staging.write_text(json.dumps(lease, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(staging, path)
    return process


# --- Restore: a snapshot back into a NEW worktree (audit 27.09., point 4) -------


def _ledger_handoffs() -> list[dict[str, Any]]:
    ledger, _ = _handoff_paths()
    if not ledger.is_file():
        return []
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line]
    return [row for row in rows if row.get("event", "handoff") == "handoff"]


def list_snapshots() -> list[dict[str, Any]]:
    return [
        {
            "handoff_id": row["handoff_id"],
            "created_at": row["created_at"],
            "from_to": f"{row['from_agent']} → {row['to_agent']}",
            "task": row["task"],
            "available": (STATE_ROOT / "snapshots" / row["handoff_id"] / "manifest.json").is_file(),
        }
        for row in reversed(_ledger_handoffs())
        if row.get("snapshot_manifest_sha256")
    ]


def restore(repo: Path, handoff_id: str, *, apply: bool = False) -> dict[str, Any]:
    """Dry run unless ``apply``; restores only into a new worktree and branch."""
    valid, reason = verify_handoffs()
    if not valid:
        raise HubError(f"Übergabekette ungültig: {reason}")
    source = next((row for row in _ledger_handoffs() if row["handoff_id"] == handoff_id), None)
    if source is None or not source.get("snapshot_manifest_sha256"):
        raise HubError("Keine Übergabe mit Snapshot unter dieser ID; 'restore' ohne ID listet sie.")
    manifest = workflow.verify_snapshot(STATE_ROOT, handoff_id, source["snapshot_manifest_sha256"])
    writer = active_writer(Path(source["repository"]))
    if apply and writer:
        raise WriterBusyError(writer, "Wiederherstellung erst, wenn dieser Client beendet ist.")
    plan = workflow.restore_snapshot(
        repo,
        STATE_ROOT,
        handoff_id,
        manifest,
        task=f"Wiederherstellung: {source['task']}",
        apply=apply,
    )
    plan.update(
        handoff_id=handoff_id,
        task=source["task"],
        from_to=f"{source['from_agent']} → {source['to_agent']}",
        created_at=source["created_at"],
        source_worktree=source["repository"],
        source_writer=writer,
    )
    return plan


def render_restore(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Snapshot der Übergabe {plan['handoff_id']} ({plan['from_to']}, {plan['created_at']})",
            f"Aufgabe: {plan['task']}",
            f"Hash-Prüfung: OK (Manifest = Übergabebeleg; Patch und {plan['files_checked']} "
            "Datei(en) geprüft)",
            f"Basis-Commit: {plan['base_head']}",
            "Änderungen an versionierten Dateien:",
            "\n".join(f"  {line.strip()}" for line in plan["tracked_stat"].splitlines())
            or "  keine",
            f"Unversionierte Dateien: {', '.join(plan['untracked']) or 'keine'}",
            "Byte-genau: "
            + (
                "ja" if plan["byte_exact"] else "nein (Snapshot vor 0.3.4, Zeilenenden je Checkout)"
            ),
            f"Schreiber im Quell-Arbeitsbereich: {_describe_writer(plan['source_writer'])}",
            f"Ziel (neu, der Quell-Arbeitsbereich bleibt unberührt): {plan['target_worktree']}",
            f"Ziel-Branch: {plan['target_branch']}",
            "WIEDERHERGESTELLT: im neuen Worktree weiterarbeiten."
            if plan["applied"]
            else f"TROCKENLAUF: nichts geändert. Ausführen: restore --handoff-id "
            f"{plan['handoff_id']} --apply",
        ]
    )


# --- Continue work: one plain-language status (audit 27.09., point 4) -----------


def _save_local_response_proof(result: dict[str, Any]) -> None:
    target = _state_dir() / "health" / "local-response.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    proof = {"checked_at": datetime.now(UTC).isoformat(), **result}
    target.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")


def _local_response_proof() -> dict[str, Any] | None:
    try:
        proof_file = STATE_ROOT / "health" / "local-response.json"
        proof = json.loads(proof_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    valid = proof.get("model") == LOCAL_MODEL and proof.get("response_proven") is True
    return proof if valid else None


def resume_report(repo: Path) -> dict[str, Any]:
    """What is needed to continue, from local evidence only (no Pi, no provider)."""
    sessions = [row for row in workflow.list_sessions(STATE_ROOT) if not row.get("orphaned")]
    target = repo.resolve()
    session = next(
        (row for row in sessions if Path(row["worktree"]).resolve() == target),
        sessions[0] if sessions else None,
    )
    if session is None:
        return {"session": None}
    worktree = Path(session["worktree"])
    ollama_online = _port_open(OLLAMA_PORT)
    proof = _local_response_proof()
    handoffs = [
        row for row in _ledger_handoffs() if Path(row["repository"]).resolve() == worktree.resolve()
    ]
    return {
        "session": {
            "session_id": session["session_id"],
            "task": session["task"],
            "worktree": str(worktree),
        },
        "branch": _git(worktree, "branch", "--show-current") or "DETACHED",
        "head": _git(worktree, "rev-parse", "--short", "HEAD"),
        "changed_files": len(_git(worktree, "status", "--porcelain=v1").splitlines()),
        "writer": active_writer(worktree),
        "local": {
            "model": LOCAL_MODEL,
            "ollama_online": ollama_online,
            "model_installed": LOCAL_MODEL in _ollama_models() if ollama_online else None,
            "response_proven": proof is not None,
            "response_proven_at": proof["checked_at"] if proof else None,
        },
        "cloud": {"tunnel_open": _port_open(DEV_PORT)},
        "pending_handoffs": handoff_state(worktree)["pending"],
        "next_action": handoffs[-1]["next_action"] if handoffs else None,
    }


def render_resume(report: dict[str, Any]) -> str:
    if report.get("session") is None:
        return "Keine offene Aufgabe: im Hub 'Neue Aufgabe' wählen oder 'restore' nutzen."
    local = report["local"]
    installed = {True: "installiert", False: "FEHLT"}.get(
        local["model_installed"], "unbekannt (Ollama offline)"
    )
    proven = (
        f"ja ({local['response_proven_at']})"
        if local["response_proven"]
        else "nein ('Lokal prüfen' bzw. doctor --mode local-inference ausführen)"
    )
    changed = report["changed_files"]
    lines = [
        f"Aufgabe: {report['session']['task']}",
        f"Arbeitsbereich: {report['session']['worktree']}",
        f"Arbeitsstand: Branch {report['branch']} @ {report['head']}, "
        + (f"{changed} geänderte Datei{'en' if changed > 1 else ''}" if changed else "sauber"),
        f"Aktiver Schreiber: {_describe_writer(report['writer'])}",
        f"Ersatz lokal: Modell {local['model']}: {installed}; Antwort bewiesen: {proven}",
        "Ersatz Cloud: Cloud-Tunnel: "
        + ("offen" if report["cloud"]["tunnel_open"] else "geschlossen")
        + " (Start und echte Antwortprüfung über 'Cloud prüfen')",
    ]
    lines += [
        f"Nächster Übergabeschritt: Übergabe {row['handoff_id']} an {row['to_agent']} wartet "
        "auf Bestätigung (Challenge im Kontextpaket)."
        for row in report["pending_handoffs"]
    ] or ["Nächster Übergabeschritt: keine offene Übergabe."]
    if report["next_action"]:
        lines.append(f"Zuletzt vereinbarter nächster Schritt: {report['next_action']}")
    return "\n".join(lines)


def status(repo: Path) -> dict[str, Any]:
    ollama_online = _port_open(OLLAMA_PORT)
    models = _ollama_models() if ollama_online else set()
    health_file = STATE_ROOT / "health" / "last.json"
    try:
        last_health = (
            json.loads(health_file.read_text(encoding="utf-8")) if health_file.is_file() else None
        )
    except (OSError, json.JSONDecodeError):
        last_health = None
    last_checks = last_health.get("checks", {}) if last_health else {}
    return {
        "hub_version": HUB_VERSION,
        "repository": str(repo),
        "branch": _git(repo, "branch", "--show-current") or "DETACHED",
        "head": _git(repo, "rev-parse", "HEAD"),
        "context_contract": (repo / "AGENTS.md").is_file()
        and (repo / "docs/AI_HANDOFF.md").is_file(),
        "opencode": bool(_opencode_executable()),
        "hermes": bool(_command("hermes")),
        "kimi": bool(_command("kimi")),
        "ollama_online": ollama_online,
        "local_model": LOCAL_MODEL,
        "local_model_installed": LOCAL_MODEL in models if ollama_online else None,
        "hermes_64k_model_installed": HERMES_LOCAL_MODEL in models if ollama_online else None,
        "litellm_tunnel_online": _port_open(DEV_PORT),
        "active_writer": _describe_writer(active_writer(repo)),
        "handoff_chain_valid": verify_handoffs()[0],
        "handoff_state": handoff_state(repo),
        "handoff_state_global": handoff_state(),
        "managed_workspaces": sum(
            not row.get("orphaned", False) for row in workflow.list_sessions(STATE_ROOT)
        ),
        "last_independent_check": last_health["checked_at"] if last_health else "NOT_RUN",
        # The hourly Health task runs doctor --mode offline: prerequisites only.
        # 0.3.2 files carry just the offline_ready alias.
        "last_prerequisites_ok": (
            last_checks.get("local_prerequisites_ok", last_checks.get("offline_ready"))
            if last_health
            else "NOT_RUN"
        ),
        "last_response_proven": (
            bool(last_checks.get("local_response_proven")) if last_health else "NOT_RUN"
        ),
    }


def run_ui(repo: Path) -> None:
    """Desktop window (0.4.0: Ollama-artig dunkel, KAI-Neon) in ``kai_dev_hub_ui``.

    The UI module only renders and calls back into this module; every guard
    (writer lease, ledger, model pin, offline base) stays here.
    """
    import kai_dev_hub_ui

    kai_dev_hub_ui.run_ui(sys.modules[__name__], repo)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=HUB_VERSION)
    parser.add_argument("--repo", help="KAI repository root (defaults to script parent)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ui")
    sub.add_parser("status")
    task_parser = sub.add_parser("new-task")
    task_parser.add_argument("--task", required=True)
    task_parser.add_argument(
        "--allow-offline", action="store_true", help="Explizit bestätigte Remote-Basis nutzen"
    )
    sub.add_parser("sessions")
    sub.add_parser("prune-sessions")
    sub.add_parser("automations")
    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument(
        "--mode", choices=("offline", "local-inference", "cloud"), default="offline"
    )
    doctor_parser.add_argument("--output", help="Explicit path for local status JSON")
    open_parser = sub.add_parser("open")
    open_parser.add_argument(
        "surface",
        choices=("opencode-local", "opencode-cloud", "hermes-local", "kimi"),
    )
    open_parser.add_argument(
        "--take-over",
        action="store_true",
        help="Lebenden Schreiber ausdrücklich ablösen (Ledger-Eintrag, alter Client läuft weiter)",
    )
    restore_parser = sub.add_parser("restore", help="Snapshot in NEUEN Worktree (Trockenlauf)")
    restore_parser.add_argument("--handoff-id", help="Ohne ID: Snapshots auflisten")
    restore_parser.add_argument("--apply", action="store_true", help="Wirklich wiederherstellen")
    restore_parser.add_argument("--json", action="store_true")
    continue_parser = sub.add_parser("continue", aliases=["resume"], help="Arbeit fortsetzen")
    continue_parser.add_argument("--json", action="store_true")
    sub.add_parser("start-cloud")
    sub.add_parser("stop-cloud")
    handoff = sub.add_parser("handoff")
    handoff.add_argument("--from-agent", required=True)
    handoff.add_argument("--to-agent", required=True)
    handoff.add_argument("--task", required=True)
    handoff.add_argument("--completed", default="")
    handoff.add_argument("--open-items", default="")
    handoff.add_argument("--assumptions", default="")
    handoff.add_argument("--next-action", default="")
    handoff.add_argument("--tests", default="")
    source_help = "Versionierte Quelldatei für das Kontextpaket (wiederholbar)"
    handoff.add_argument("--source", action="append", default=[], help=source_help)
    ack_parser = sub.add_parser("ack")
    ack_parser.add_argument("--handoff-id", required=True)
    ack_parser.add_argument("--agent", required=True)
    ack_parser.add_argument("--response-file", required=True)
    supersede_parser = sub.add_parser("supersede-handoff")
    supersede_parser.add_argument("--handoff-id", required=True)
    supersede_parser.add_argument("--reason", required=True)
    supersede_parser.add_argument("--replaced-by")
    sub.add_parser("verify-handoffs")
    pack_parser = sub.add_parser("context-pack")
    pack_parser.add_argument("--source", action="append", default=[], help=source_help)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        repo = _repo_root(args.repo)
        if args.command == "ui":
            run_ui(repo)
        elif args.command == "status":
            print(json.dumps(status(repo), indent=2, ensure_ascii=False))
        elif args.command == "new-task":
            print(
                json.dumps(
                    workflow.new_task(
                        repo, STATE_ROOT, args.task, allow_offline=args.allow_offline
                    ),
                    indent=2,
                    ensure_ascii=False,
                )
            )
        elif args.command == "sessions":
            print(json.dumps(workflow.list_sessions(STATE_ROOT), indent=2, ensure_ascii=False))
        elif args.command == "prune-sessions":
            print(
                json.dumps(workflow.prune_stale_sessions(STATE_ROOT), indent=2, ensure_ascii=False)
            )
        elif args.command == "automations":
            print(json.dumps(automation_inventory(), indent=2, ensure_ascii=False))
        elif args.command == "doctor":
            report = doctor(repo, args.mode)
            serialized = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
            if args.output:
                destination = Path(args.output).expanduser().resolve()
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(serialized, encoding="utf-8")
            print(serialized, end="")
            return 0 if report["checks"]["offline_ready"] or args.mode == "cloud" else 1
        elif args.command == "open":
            if args.surface.startswith("opencode-"):
                route = args.surface.removeprefix("opencode-")
                launch_opencode(repo, route, take_over=args.take_over)
            elif args.surface == "hermes-local":
                launch_hermes(repo, take_over=args.take_over)
            else:
                print(launch_kimi(repo))
        elif args.command == "restore":
            if not args.handoff_id:
                rows = list_snapshots()
                print(
                    json.dumps(rows, indent=2, ensure_ascii=False)
                    if args.json
                    else "\n".join(
                        f"{row['handoff_id']}  {row['created_at']}  {row['from_to']}  "
                        f"{row['task']}" + ("" if row["available"] else "  [SNAPSHOT FEHLT]")
                        for row in rows
                    )
                    or "Keine Snapshots vorhanden."
                )
            else:
                plan = restore(repo, args.handoff_id, apply=args.apply)
                print(
                    json.dumps(plan, indent=2, ensure_ascii=False)
                    if args.json
                    else render_restore(plan)
                )
        elif args.command in {"continue", "resume"}:
            report = resume_report(repo)
            print(
                json.dumps(report, indent=2, ensure_ascii=False)
                if args.json
                else render_resume(report)
            )
        elif args.command == "start-cloud":
            start_cloud()
            print("KAI_DEV_CLOUD_READY endpoint=http://127.0.0.1:4001/v1 catalog=VERIFIED")
        elif args.command == "stop-cloud":
            print("STOPPED" if stop_cloud() else "NOT_MANAGED_OR_ALREADY_STOPPED")
        elif args.command == "handoff":
            result = create_handoff(
                repo,
                from_agent=args.from_agent,
                to_agent=args.to_agent,
                task=args.task,
                completed=args.completed,
                open_items=args.open_items,
                assumptions=args.assumptions,
                next_action=args.next_action,
                tests=args.tests,
                sources=args.source,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "ack":
            response = Path(args.response_file).read_text(encoding="utf-8")
            print(
                json.dumps(
                    acknowledge_handoff(
                        repo, handoff_id=args.handoff_id, agent=args.agent, response=response
                    ),
                    indent=2,
                    ensure_ascii=False,
                )
            )
        elif args.command == "supersede-handoff":
            print(
                json.dumps(
                    supersede_handoff(args.handoff_id, args.reason, args.replaced_by),
                    indent=2,
                    ensure_ascii=False,
                )
            )
        elif args.command == "verify-handoffs":
            valid, message = verify_handoffs()
            print(message)
            return 0 if valid else 1
        elif args.command == "context-pack":
            print(context_pack(repo, args.source))
    except (HubError, workflow.WorkflowError, OSError, subprocess.SubprocessError) as exc:
        print(f"KAI_DEV_HUB_ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
