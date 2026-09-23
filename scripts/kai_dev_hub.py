#!/usr/bin/env python3
"""Windows desktop hub for KAI's independent developer reserves.

This is an operator tool, not part of KAI's inference runtime. It never imports
``app.ai`` and never changes provider routing or trading gates.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import queue
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import kai_dev_workflow as workflow

HUB_VERSION = "0.3.1"

DEV_HOST = "127.0.0.1"
DEV_PORT = 4001
OLLAMA_PORT = 11434
PI_HOST = "192.168.178.23"
PI_USER = "ubuntu"
LOCAL_MODEL = "kai-qwen3-coder:30b-16k"
HERMES_LOCAL_MODEL = "kai-qwen3-coder:30b-64k"
# OpenCode's system prompt plus a read file exceeds 16K tokens (Ollama
# truncated 16942 -> 16384 on 23.09.); coding sessions use the 64K model.
OPENCODE_LOCAL_MODEL = HERMES_LOCAL_MODEL
DEV_MODELS = {"kai-dev-economy", "kai-dev-code", "kai-dev-frontier"}
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
    checks["opencode_installed"] = bool(_command("opencode.cmd") or _command("opencode"))
    checks["hermes_installed"] = bool(_command("hermes"))
    checks["kimi_installed"] = bool(_command("kimi"))
    checks["ollama_online"] = _port_open(OLLAMA_PORT)
    models = _ollama_models() if checks["ollama_online"] else set()
    checks["opencode_local_model"] = OPENCODE_LOCAL_MODEL in models
    checks["hermes_local_model"] = HERMES_LOCAL_MODEL in models
    checks["handoff_chain"] = verify_handoffs()[0]
    checks["cloud_tunnel_open"] = _port_open(DEV_PORT)
    checks["managed_workspaces"] = len(workflow.list_sessions(STATE_ROOT))
    checks["local_automations"] = automation_inventory()
    if mode == "local-inference":
        checks["local_inference"] = _local_inference_probe()
    elif mode == "cloud":
        key = start_cloud()
        checks["cloud_tunnel_open"] = _port_open(DEV_PORT)
        checks["dev_routes"] = [
            _cloud_inference_probe(key, route) for route in ("kai-dev-economy", "kai-dev-code")
        ]
    checks["offline_ready"] = all(
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


def _pid_is_ssh(pid: int) -> bool:
    """True only if ``pid`` still is an ssh process (PIDs are reused)."""
    if sys.platform != "win32":
        return False
    result = _run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=10)
    for row in csv.reader(result.stdout.splitlines()):
        if len(row) > 1 and row[1].strip() == str(pid):
            return row[0].strip().casefold() == "ssh.exe"
    return False


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


def launch_opencode(repo: Path, route: str, handoff: dict[str, Any] | None = None) -> None:
    session = workflow.require_session(repo, STATE_ROOT)
    executable = _command("opencode.cmd") or _command("opencode")
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
    subprocess.Popen(  # noqa: S603
        [executable, str(repo), "-m", model, "--prompt", prompt],
        cwd=repo,
        env=env,
        creationflags=_creation_flag("CREATE_NEW_CONSOLE"),
    )


def launch_hermes(repo: Path, handoff: dict[str, Any] | None = None) -> None:
    session = workflow.require_session(repo, STATE_ROOT)
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
    first_prompt = pack.read_text(encoding="utf-8")
    if sys.platform == "win32":
        import tkinter as tk

        clipboard = tk.Tk()
        clipboard.withdraw()
        clipboard.clipboard_clear()
        clipboard.clipboard_append(first_prompt)
        clipboard.update()
        clipboard.destroy()
    subprocess.Popen(  # noqa: S603
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
        cwd=repo,
        env=env,
        creationflags=_creation_flag("CREATE_NEW_CONSOLE"),
    )


def context_pack(repo: Path, sources: Sequence[str] = ()) -> Path:
    session = workflow.require_session(repo, STATE_ROOT)
    return workflow.context_pack(repo, STATE_ROOT, task=session["task"], sources=list(sources))


def launch_kimi(repo: Path, handoff: dict[str, Any] | None = None) -> Path:
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


def handoff_state(repo: Path | None = None) -> dict[str, Any]:
    ledger, _ = _handoff_paths()
    if not ledger.is_file():
        return {"handoffs": 0, "acknowledged": 0, "pending": []}
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    handoffs = [
        row
        for row in rows
        if row.get("event", "handoff") == "handoff"
        and (repo is None or Path(row["repository"]).resolve() == repo.resolve())
    ]
    acknowledged = {row["handoff_id"] for row in rows if row.get("event") == "ack"}
    pending = [
        {
            "handoff_id": row["handoff_id"],
            "to_agent": row["to_agent"],
            "created_at": row["created_at"],
        }
        for row in handoffs
        if row["handoff_id"] not in acknowledged
    ]
    return {
        "handoffs": len(handoffs),
        "acknowledged": len(handoffs) - len(pending),
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
        else:
            return False, f"Zeile {line_number}: unbekannter Ereignistyp"
        previous = claimed
    return True, f"{count} Übergabe(n), {len(acknowledged)} bestätigt; Hash-Kette unverändert."


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
    return {
        "hub_version": HUB_VERSION,
        "repository": str(repo),
        "branch": _git(repo, "branch", "--show-current") or "DETACHED",
        "head": _git(repo, "rev-parse", "HEAD"),
        "context_contract": (repo / "AGENTS.md").is_file()
        and (repo / "docs/AI_HANDOFF.md").is_file(),
        "opencode": bool(_command("opencode.cmd") or _command("opencode")),
        "hermes": bool(_command("hermes")),
        "kimi": bool(_command("kimi")),
        "ollama_online": ollama_online,
        "local_model_installed": LOCAL_MODEL in models if ollama_online else None,
        "hermes_64k_model_installed": HERMES_LOCAL_MODEL in models if ollama_online else None,
        "litellm_tunnel_online": _port_open(DEV_PORT),
        "handoff_chain_valid": verify_handoffs()[0],
        "handoff_state": handoff_state(repo),
        "handoff_state_global": handoff_state(),
        "managed_workspaces": len(workflow.list_sessions(STATE_ROOT)),
        "last_independent_check": last_health["checked_at"] if last_health else "NOT_RUN",
        "last_independent_result": (
            last_health.get("checks", {}).get("offline_ready") if last_health else "NOT_RUN"
        ),
    }


def _handoff_dialog(repo: Path, parent: Any) -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    dialog = tk.Toplevel(parent)
    dialog.title("Neue KAI-Übergabe")
    fields = [
        ("from_agent", "Von Agent", "OpenCode"),
        ("to_agent", "An Agent", "Hermes"),
        ("task", "Auftrag", ""),
        ("completed", "Erledigt", ""),
        ("open_items", "Offen", ""),
        ("assumptions", "Annahmen", ""),
        ("next_action", "Nächster Schritt", ""),
        ("tests", "Tests", ""),
        ("sources", f"Quelldateien (je Zeile, max. {workflow.MAX_SOURCE_FILES})", ""),
    ]
    widgets: dict[str, Any] = {}
    for row, (name, label, initial) in enumerate(fields):
        ttk.Label(dialog, text=label).grid(row=row, column=0, sticky="nw", padx=8, pady=4)
        widget = tk.Text(dialog, height=1 if row < 2 else 3, width=64)
        widget.insert("1.0", initial)
        widget.grid(row=row, column=1, padx=8, pady=4)
        widgets[name] = widget

    def save(*, start_recipient: bool = False) -> None:
        values = {name: widget.get("1.0", "end").strip() for name, widget in widgets.items()}
        sources = values.pop("sources").splitlines()
        try:
            result = create_handoff(repo, sources=sources, **values)
        except (HubError, workflow.WorkflowError) as exc:
            messagebox.showerror("Übergabe nicht gespeichert", str(exc), parent=dialog)
            return
        messagebox.showinfo(
            "Übergabe gespeichert",
            f"Beleg: {result['payload_sha256']}\nKontext: {result['context_pack_path']}\n"
            "Die Antwort des Empfängers mit Challenge danach im Hub bestätigen.",
            parent=dialog,
        )
        dialog.destroy()
        if start_recipient:

            def worker() -> None:
                try:
                    launch_recipient(repo, result)
                except (HubError, workflow.WorkflowError, OSError) as exc:
                    error_message = str(exc)
                    parent.after(
                        0,
                        lambda: messagebox.showerror(
                            "Empfängerstart fehlgeschlagen", error_message, parent=parent
                        ),
                    )

            threading.Thread(target=worker, daemon=True).start()

    ttk.Button(dialog, text="Nur sichern", command=save).grid(
        row=len(fields), column=0, sticky="e", padx=8, pady=10
    )
    ttk.Button(
        dialog, text="Sichern + Empfänger starten", command=lambda: save(start_recipient=True)
    ).grid(row=len(fields), column=1, sticky="e", padx=8, pady=10)


def _ack_dialog(repo: Path, parent: Any) -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    dialog = tk.Toplevel(parent)
    dialog.title("Empfang und Verständnis bestätigen")
    fields = (
        ("handoff_id", "Übergabe-ID"),
        ("agent", "Empfänger"),
        ("response", "Antwort des Empfängers"),
    )
    widgets: dict[str, Any] = {}
    for index, (key, label) in enumerate(fields):
        ttk.Label(dialog, text=label).grid(row=index, column=0, sticky="nw", padx=8, pady=4)
        widget = tk.Text(dialog, height=8 if key == "response" else 1, width=68)
        widget.grid(row=index, column=1, padx=8, pady=4)
        widgets[key] = widget

    def save() -> None:
        try:
            event = acknowledge_handoff(
                repo,
                **{key: widget.get("1.0", "end").strip() for key, widget in widgets.items()},
            )
        except HubError as exc:
            messagebox.showerror("Bestätigung fehlgeschlagen", str(exc), parent=dialog)
            return
        messagebox.showinfo(
            "Empfang dokumentiert", f"Bestätigung: {event['payload_sha256']}", parent=dialog
        )
        dialog.destroy()

    ttk.Button(dialog, text="Antwort prüfen und speichern", command=save).grid(
        row=len(fields), column=1, sticky="e", padx=8, pady=10
    )


def run_ui(repo: Path) -> None:
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk

    root = tk.Tk()
    root.title(f"KAI Developer Hub {HUB_VERSION} — unabhängige Reserve")
    root.geometry("840x650")
    active: dict[str, Path] = {"repo": repo}
    ttk.Label(root, text=f"KAI Developer Hub {HUB_VERSION}", font=("Segoe UI", 20, "bold")).pack(
        pady=(18, 4)
    )
    ttk.Label(
        root,
        text="Eine Aufgabe = ein eigener Arbeitsbereich. Modelle bleiben für schreibende Sitzungen fest.",
    ).pack(pady=(0, 14))

    picker = ttk.Combobox(root, state="readonly", width=95)
    picker.pack(padx=16, fill="x")
    picker_rows: dict[str, Path] = {}

    def refresh_sessions() -> None:
        rows = workflow.list_sessions(STATE_ROOT)
        picker_rows.clear()
        for row in rows:
            label = f"{row['task']}  [{row['branch']}]"
            picker_rows[label] = Path(row["worktree"])
        picker["values"] = list(picker_rows)
        if active["repo"] not in picker_rows.values() and rows:
            active["repo"] = Path(rows[0]["worktree"])
        for label, path in picker_rows.items():
            if path == active["repo"]:
                picker.set(label)
                break

    def choose_session(_event: Any) -> None:
        chosen = picker_rows.get(picker.get())
        if chosen:
            active["repo"] = chosen
            refresh()

    picker.bind("<<ComboboxSelected>>", choose_session)
    status_box = tk.Text(root, height=17, width=96, state="disabled", font=("Consolas", 10))
    status_box.pack(padx=16, fill="x")
    ui_events: queue.SimpleQueue[tuple[str, Any, bool]] = queue.SimpleQueue()

    def show_error(exc: Exception) -> None:
        messagebox.showerror("KAI Developer Hub", str(exc), parent=root)

    def show_report(value: Any) -> None:
        dialog = tk.Toplevel(root)
        dialog.title("KAI Diagnosebericht")
        dialog.geometry("720x520")
        frame = ttk.Frame(dialog)
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        scrollbar = ttk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")
        content = tk.Text(frame, wrap="word", yscrollcommand=scrollbar.set)
        content.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=content.yview)
        content.insert("1.0", json.dumps(value, indent=2, ensure_ascii=False))
        content.configure(state="disabled")
        ttk.Button(dialog, text="Schließen", command=dialog.destroy).pack(pady=(0, 12))

    def refresh() -> None:
        try:
            refresh_sessions()
            values = status(active["repo"])
            lines = [
                f"{key:25} {'OK' if value is True else 'FEHLT' if value is False else value}"
                for key, value in values.items()
            ]
            valid, chain = verify_handoffs()
            lines.append(f"{'handoff_receipts':25} {'OK' if valid else 'FEHLT'} — {chain}")
        except Exception as exc:  # UI boundary
            lines = [f"STATUS-FEHLER: {exc}"]
        status_box.configure(state="normal")
        status_box.delete("1.0", "end")
        status_box.insert("1.0", "\n".join(lines))
        status_box.configure(state="disabled")

    def pump_events() -> None:
        try:
            while True:
                kind, value, report = ui_events.get_nowait()
                if kind == "error":
                    show_error(value)
                elif kind == "result" and report and value is not None:
                    show_report(value)
                refresh()
        except queue.Empty:
            pass
        root.after(150, pump_events)

    def action(func: Any, *, report: bool = False) -> None:
        def worker() -> None:
            try:
                ui_events.put(("result", func(), report))
            except Exception as exc:  # UI boundary
                ui_events.put(("error", exc, False))

        threading.Thread(target=worker, daemon=True).start()

    def active_workspace() -> Path:
        workflow.require_session(active["repo"], STATE_ROOT)
        return active["repo"]

    def new_session() -> None:
        task = simpledialog.askstring(
            "Neue KAI-Aufgabe", "Was soll bearbeitet werden?", parent=root
        )
        if task:

            def create() -> None:
                row = workflow.new_task(repo, STATE_ROOT, task)
                active["repo"] = Path(row["worktree"])

            action(create)

    buttons = ttk.Frame(root)
    buttons.pack(pady=16)
    ttk.Button(buttons, text="Neue Aufgabe / Worktree", command=new_session).grid(
        row=0, column=0, padx=6, pady=6
    )
    ttk.Button(
        buttons,
        text="OpenCode lokal (offline)",
        command=lambda: action(lambda: launch_opencode(active_workspace(), "local")),
    ).grid(row=0, column=1, padx=6, pady=6)
    ttk.Button(
        buttons,
        text="OpenCode Cloud-Reserve",
        command=lambda: action(lambda: launch_opencode(active_workspace(), "cloud")),
    ).grid(row=0, column=2, padx=6, pady=6)
    ttk.Button(
        buttons,
        text="Hermes lokal (offline, 64K)",
        command=lambda: action(lambda: launch_hermes(active_workspace())),
    ).grid(row=0, column=3, padx=6, pady=6)
    ttk.Button(
        buttons,
        text="Kimi + Kontextpaket",
        command=lambda: action(lambda: launch_kimi(active_workspace()), report=True),
    ).grid(row=1, column=0, padx=6, pady=6)
    ttk.Button(
        buttons,
        text="Übergabe + Snapshot",
        command=lambda: _handoff_dialog(active_workspace(), root),
    ).grid(row=1, column=1, padx=6, pady=6)
    ttk.Button(
        buttons,
        text="Empfang bestätigen",
        command=lambda: _ack_dialog(active_workspace(), root),
    ).grid(row=1, column=2, padx=6, pady=6)
    ttk.Button(
        buttons,
        text="Cloud-Tunnel stoppen",
        command=lambda: action(stop_cloud),
    ).grid(row=1, column=3, padx=6, pady=6)
    ttk.Button(
        buttons,
        text="Lokal prüfen",
        command=lambda: action(lambda: doctor(active["repo"], "local-inference"), report=True),
    ).grid(row=2, column=0, padx=6, pady=6)
    ttk.Button(
        buttons,
        text="Cloud prüfen",
        command=lambda: action(lambda: doctor(active["repo"], "cloud"), report=True),
    ).grid(row=2, column=1, padx=6, pady=6)
    ttk.Button(
        buttons, text="Automationen", command=lambda: action(automation_inventory, report=True)
    ).grid(row=2, column=2, padx=6, pady=6)
    ttk.Button(buttons, text="Status aktualisieren", command=refresh).grid(
        row=2, column=3, padx=6, pady=6
    )
    ttk.Label(
        root,
        text=(
            "Kimi benötigt den Anhang aus dem Kontextpaket. Nach einer Übergabe die Antwort "
            "des Empfängers mit Challenge dokumentieren; bis dahin bleibt sie offen."
        ),
        wraplength=700,
    ).pack(padx=18, pady=8)
    refresh()
    root.after(150, pump_events)
    root.mainloop()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=HUB_VERSION)
    parser.add_argument("--repo", help="KAI repository root (defaults to script parent)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ui")
    sub.add_parser("status")
    task_parser = sub.add_parser("new-task")
    task_parser.add_argument("--task", required=True)
    sub.add_parser("sessions")
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
                    workflow.new_task(repo, STATE_ROOT, args.task), indent=2, ensure_ascii=False
                )
            )
        elif args.command == "sessions":
            print(json.dumps(workflow.list_sessions(STATE_ROOT), indent=2, ensure_ascii=False))
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
                launch_opencode(repo, args.surface.removeprefix("opencode-"))
            elif args.surface == "hermes-local":
                launch_hermes(repo)
            else:
                print(launch_kimi(repo))
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
