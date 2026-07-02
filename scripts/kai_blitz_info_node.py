#!/usr/bin/env python3
"""kai_blitz_info — read-only RaspiBlitz snapshot as ONE JSON object.

Forced-command target for the KAI dashboard mirror panel: takes no input,
changes nothing, every field is best-effort (missing -> null) so a partial
node never breaks the caller.
"""

import json
import subprocess
import time


def run(cmd: str, timeout: int = 20) -> str:
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip()
    except Exception:
        return ""


def run_json(cmd: str, timeout: int = 20):
    out = run(cmd, timeout)
    try:
        return json.loads(out)
    except Exception:
        return {}


def read_file(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""


info = {"ts": int(time.time()), "hostname": run("hostname")}

# RaspiBlitz version (raspiblitz.info is admin-readable)
rb = read_file("/home/admin/raspiblitz.info")
for line in rb.splitlines():
    if line.startswith("codeVersion="):
        info["raspiblitz_version"] = line.split("=", 1)[1].strip().strip("'\"")

# system
try:
    up_s = float(read_file("/proc/uptime").split()[0])
    info["uptime_seconds"] = int(up_s)
except Exception:
    info["uptime_seconds"] = None
try:
    info["load"] = [float(x) for x in read_file("/proc/loadavg").split()[:3]]
except Exception:
    info["load"] = None
try:
    info["cpu_temp_c"] = round(int(read_file("/sys/class/thermal/thermal_zone0/temp")) / 1000, 1)
except Exception:
    info["cpu_temp_c"] = None
try:
    mem = {}
    for line in read_file("/proc/meminfo").splitlines():
        k, v = line.split(":", 1)
        mem[k] = int(v.strip().split()[0])
    info["mem_total_mb"] = mem.get("MemTotal", 0) // 1024
    info["mem_available_mb"] = mem.get("MemAvailable", 0) // 1024
except Exception:
    info["mem_total_mb"] = info["mem_available_mb"] = None
try:
    import shutil

    du = shutil.disk_usage("/mnt/disk_storage")
    info["disk_total_gb"] = round(du.total / 1e9, 1)
    info["disk_used_pct"] = round(du.used / du.total * 100, 1)
except Exception:
    info["disk_total_gb"] = info["disk_used_pct"] = None

# bitcoind
bci = run_json("bitcoin-cli getblockchaininfo")
bni = run_json("bitcoin-cli getnetworkinfo")
info["bitcoind"] = {
    "version": (bni.get("subversion") or "").strip("/") or None,
    "chain": bci.get("chain"),
    "blocks": bci.get("blocks"),
    "headers": bci.get("headers"),
    "sync_pct": round(float(bci.get("verificationprogress", 0)) * 100, 2) if bci else None,
    "peers": bni.get("connections"),
}

# lnd
gi = run_json("lncli getinfo")
wb = run_json("lncli walletbalance")
cb = run_json("lncli channelbalance")
pc = run_json("lncli pendingchannels")
fr = run_json("lncli feereport")
info["lnd"] = {
    "version": gi.get("version"),
    "alias": gi.get("alias"),
    "pubkey": gi.get("identity_pubkey"),
    "uris": gi.get("uris") or [],
    "synced_to_chain": gi.get("synced_to_chain"),
    "synced_to_graph": gi.get("synced_to_graph"),
    "block_height": gi.get("block_height"),
    "peers": gi.get("num_peers"),
    "active_channels": gi.get("num_active_channels"),
    "pending_channels": gi.get("num_pending_channels"),
    "wallet_confirmed_sat": int(wb.get("confirmed_balance", 0) or 0) if wb else None,
    "wallet_unconfirmed_sat": int(wb.get("unconfirmed_balance", 0) or 0) if wb else None,
    "channel_local_sat": int((cb.get("local_balance") or {}).get("sat", 0) or 0) if cb else None,
    "channel_remote_sat": int((cb.get("remote_balance") or {}).get("sat", 0) or 0) if cb else None,
    "pending_open": [
        {
            "remote_pubkey": (c.get("channel") or {}).get("remote_node_pub"),
            "capacity_sat": int((c.get("channel") or {}).get("capacity", 0) or 0),
            "channel_point": (c.get("channel") or {}).get("channel_point"),
        }
        for c in (pc.get("pending_open_channels") or [])
    ],
    "fee_report": {
        "day_sat": int(fr.get("day_fee_sum", 0) or 0) if fr else None,
        "week_sat": int(fr.get("week_fee_sum", 0) or 0) if fr else None,
        "month_sat": int(fr.get("month_fee_sum", 0) or 0) if fr else None,
    },
}

print(json.dumps(info, separators=(",", ":")))
