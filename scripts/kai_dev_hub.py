#!/usr/bin/env python3
"""Windows desktop hub for KAI's independent developer reserves.

This is an operator tool, not part of KAI's inference runtime. It never imports
``app.ai`` and never changes provider routing or trading gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEV_HOST = "127.0.0.1"
DEV_PORT = 4001
OLLAMA_PORT = 11434
PI_HOST = "192.168.178.23"
PI_USER = "ubuntu"
LOCAL_MODEL = "kai-qwen3-coder:30b-16k"
HERMES_LOCAL_MODEL = "kai-qwen3-coder:30b-64k"
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


def start_cloud() -> str:
    key = _fetch_dev_key()
    if not _port_open(DEV_PORT):
        remote_open = _remote_proxy_is_open()
        no_command = ["-N"] if remote_open else []
        remote_command = (
            f"echo $$ > {shlex.quote(REMOTE_PID_FILE)}; "
            f"exec bash {shlex.quote(REMOTE_PROXY)} proxy"
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
        _save_cloud_pid(process.pid, "tunnel-only" if remote_open else "proxy-and-tunnel")
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
            process.terminate()
            raise HubError(f"Dev-Proxy antwortet nicht auf {DEV_HOST}:{DEV_PORT}.")
    _verify_cloud_catalog(key)
    return key


def stop_cloud() -> bool:
    state_file = _cloud_state_file()
    if not state_file.is_file():
        return False
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        pid = int(state["pid"])
        mode = str(state.get("mode", ""))
    except (ValueError, KeyError, json.JSONDecodeError):
        return False
    result = _run(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=15)
    if result.returncode == 0:
        if mode == "proxy-and-tunnel" and not _stop_remote_proxy():
            return False
        state_file.unlink(missing_ok=True)
        return True
    return False


def _stop_remote_proxy() -> bool:
    code = (
        "from pathlib import Path; import os,signal,sys; "
        f"p=Path('{REMOTE_PID_FILE}'); "
        "pid=int(p.read_text().strip()) if p.is_file() else 0; "
        "c=Path(f'/proc/{pid}/cmdline').read_bytes().replace(bytes([0]),b' ').decode() "
        "if pid and Path(f'/proc/{pid}/cmdline').is_file() else ''; "
        f"ok=('litellm_dev.yaml' in c and '--port {DEV_PORT}' in c); "
        "os.kill(pid,signal.SIGTERM) if ok else None; "
        "p.unlink(missing_ok=True) if ok else None; sys.exit(0 if ok else 1)"
    )
    remote = f"python3 -c {shlex.quote(code)}"
    return _run([*_ssh_base(), f"{PI_USER}@{PI_HOST}", remote], timeout=15).returncode == 0


def launch_opencode(repo: Path, route: str) -> None:
    executable = _command("opencode.cmd") or _command("opencode")
    if not executable:
        raise HubError("OpenCode ist nicht installiert oder nicht im PATH.")
    env = os.environ.copy()
    if route == "local":
        ensure_ollama()
        if LOCAL_MODEL not in _ollama_models():
            raise HubError(f"Lokales Modell fehlt: {LOCAL_MODEL}")
        model = f"ollama/{LOCAL_MODEL}"
    elif route == "cloud":
        env["KAI_DEV_LITELLM_KEY"] = start_cloud()
        model = "kai-litellm-dev/kai-dev-code"
    else:
        raise HubError(f"Unbekannte Route: {route}")
    env["KAI_AGENT_SURFACE"] = f"opencode-{route}"
    subprocess.Popen(  # noqa: S603
        [executable, str(repo), "-m", model],
        cwd=repo,
        env=env,
        creationflags=_creation_flag("CREATE_NEW_CONSOLE"),
    )


def launch_hermes(repo: Path) -> None:
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
    env["KAI_AGENT_SURFACE"] = "hermes-local"
    subprocess.Popen(  # noqa: S603
        [
            executable,
            "--tui",
            "--provider",
            "custom",
            "--model",
            model,
            "--in",
            str(repo),
        ],
        cwd=repo,
        env=env,
        creationflags=_creation_flag("CREATE_NEW_CONSOLE"),
    )


def context_pack(repo: Path) -> Path:
    branch = _git(repo, "branch", "--show-current") or "DETACHED"
    head = _git(repo, "rev-parse", "HEAD")
    dirty = _git(repo, "status", "--short") or "clean"
    text = f"""# KAI context pack for an external assistant

Generated: {datetime.now(UTC).isoformat()}
Repository: {repo}
Branch: {branch}
HEAD: {head}
Worktree status:
```
{dirty}
```

Read these files before advising or changing anything:
1. `{repo / 'AGENTS.md'}`
2. `{repo / 'CLAUDE.md'}`
3. `{repo / 'docs/AI_HANDOFF.md'}`
4. `{repo / 'ARCHITECTURE.md'}`

Rules: do not read `.env` or credentials; do not deploy, merge, push, change
trading gates, or work in the shared main checkout. For code changes use a new
git worktree and provide a structured handoff. `app/ai` remains KAI's sole AI
control-plane; LiteLLM is transport below it.
"""
    target = _state_dir() / "KAI_CONTEXT_FOR_KIMI.md"
    target.write_text(text, encoding="utf-8")
    return target


def launch_kimi(repo: Path) -> Path:
    executable = _command("kimi")
    if not executable:
        raise HubError("Kimi Desktop ist nicht installiert.")
    pack = context_pack(repo)
    subprocess.Popen([executable], cwd=repo)  # noqa: S603
    if sys.platform == "win32":
        subprocess.Popen(["explorer.exe", "/select,", str(pack)])  # noqa: S603
    return pack


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
) -> dict[str, Any]:
    ledger, current = _handoff_paths()
    previous = "GENESIS"
    if ledger.is_file():
        rows = [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        if rows:
            previous = json.loads(rows[-1])["payload_sha256"]
    status = _git(repo, "status", "--porcelain=v1")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "handoff_id": str(uuid.uuid4()),
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
        "previous_sha256": previous,
    }
    if not payload["from_agent"] or not payload["to_agent"] or not payload["task"]:
        raise HubError("Von-Agent, An-Agent und Auftrag sind Pflichtfelder.")
    payload["payload_sha256"] = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
    with ledger.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical_json(payload) + "\n")
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


def verify_handoffs() -> tuple[bool, str]:
    ledger, _ = _handoff_paths()
    if not ledger.is_file():
        return True, "Keine Übergaben vorhanden."
    previous = "GENESIS"
    count = 0
    for count, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            row = json.loads(line)
            claimed = row.pop("payload_sha256")
        except (json.JSONDecodeError, KeyError) as exc:
            return False, f"Zeile {count}: ungültiger Beleg ({exc})"
        actual = hashlib.sha256(_canonical_json(row).encode()).hexdigest()
        if claimed != actual:
            return False, f"Zeile {count}: SHA-256 stimmt nicht"
        if row.get("previous_sha256") != previous:
            return False, f"Zeile {count}: Kette unterbrochen"
        previous = claimed
    return True, f"{count} Übergabe(n) kryptografisch verkettet und unverändert."


def status(repo: Path) -> dict[str, Any]:
    ollama_online = _port_open(OLLAMA_PORT)
    models = _ollama_models() if ollama_online else set()
    return {
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
    ]
    widgets: dict[str, Any] = {}
    for row, (name, label, initial) in enumerate(fields):
        ttk.Label(dialog, text=label).grid(row=row, column=0, sticky="nw", padx=8, pady=4)
        widget = tk.Text(dialog, height=1 if row < 2 else 3, width=64)
        widget.insert("1.0", initial)
        widget.grid(row=row, column=1, padx=8, pady=4)
        widgets[name] = widget

    def save() -> None:
        try:
            result = create_handoff(
                repo,
                **{name: widget.get("1.0", "end").strip() for name, widget in widgets.items()},
            )
        except HubError as exc:
            messagebox.showerror("Übergabe nicht gespeichert", str(exc), parent=dialog)
            return
        messagebox.showinfo(
            "Übergabe gespeichert",
            f"Beleg: {result['payload_sha256']}\n{_handoff_paths()[1]}",
            parent=dialog,
        )
        dialog.destroy()

    ttk.Button(dialog, text="Verkettet speichern", command=save).grid(
        row=len(fields), column=1, sticky="e", padx=8, pady=10
    )


def run_ui(repo: Path) -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("KAI Developer Hub — unabhängige Reserve")
    root.geometry("760x520")
    ttk.Label(root, text="KAI Developer Hub", font=("Segoe UI", 20, "bold")).pack(pady=(18, 4))
    ttk.Label(
        root,
        text="Explizite, gepinnte Sitzungen — kein stiller Providerwechsel, kein Deploy.",
    ).pack(pady=(0, 14))
    status_box = tk.Text(root, height=13, width=88, state="disabled", font=("Consolas", 10))
    status_box.pack(padx=16, fill="x")

    def show_error(exc: Exception) -> None:
        messagebox.showerror("KAI Developer Hub", str(exc), parent=root)

    def refresh() -> None:
        try:
            values = status(repo)
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

    def action(func: Any) -> None:
        def worker() -> None:
            try:
                func()
            except Exception as exc:  # UI boundary
                root.after(0, show_error, exc)
            finally:
                root.after(0, refresh)

        threading.Thread(target=worker, daemon=True).start()

    buttons = ttk.Frame(root)
    buttons.pack(pady=16)
    ttk.Button(
        buttons,
        text="OpenCode lokal (offline)",
        command=lambda: action(lambda: launch_opencode(repo, "local")),
    ).grid(row=0, column=0, padx=6, pady=6)
    ttk.Button(
        buttons,
        text="OpenCode Cloud-Reserve",
        command=lambda: action(lambda: launch_opencode(repo, "cloud")),
    ).grid(row=0, column=1, padx=6, pady=6)
    ttk.Button(
        buttons,
        text="Hermes lokal (offline, 64K)",
        command=lambda: action(lambda: launch_hermes(repo)),
    ).grid(row=0, column=2, padx=6, pady=6)
    ttk.Button(
        buttons,
        text="Kimi + Kontextpaket",
        command=lambda: action(lambda: launch_kimi(repo)),
    ).grid(row=1, column=0, padx=6, pady=6)
    ttk.Button(
        buttons,
        text="Neue prüfbare Übergabe",
        command=lambda: _handoff_dialog(repo, root),
    ).grid(row=1, column=1, padx=6, pady=6)
    ttk.Button(
        buttons,
        text="Cloud-Tunnel stoppen",
        command=lambda: action(stop_cloud),
    ).grid(row=1, column=2, padx=6, pady=6)
    ttk.Button(buttons, text="Status aktualisieren", command=refresh).grid(
        row=2, column=2, padx=6, pady=6
    )
    ttk.Label(
        root,
        text=(
            "Kimi erhält ein aktuelles Kontextpaket, bleibt aber eine Beratungsoberfläche ohne "
            "garantierten lokalen Schreibzugriff. OpenCode und Hermes starten direkt im KAI-Checkout."
        ),
        wraplength=700,
    ).pack(padx=18, pady=8)
    refresh()
    root.mainloop()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="KAI repository root (defaults to script parent)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ui")
    sub.add_parser("status")
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
    sub.add_parser("verify-handoffs")
    sub.add_parser("context-pack")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        repo = _repo_root(args.repo)
        if args.command == "ui":
            run_ui(repo)
        elif args.command == "status":
            print(json.dumps(status(repo), indent=2, ensure_ascii=False))
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
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "verify-handoffs":
            valid, message = verify_handoffs()
            print(message)
            return 0 if valid else 1
        elif args.command == "context-pack":
            print(context_pack(repo))
    except (HubError, OSError, subprocess.SubprocessError) as exc:
        print(f"KAI_DEV_HUB_ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
