#!/usr/bin/env python3
"""Macaroon-Neuaufbau: Bestandsaufnahme, Abbruchbedingungen und Bilanzvergleich (nur lesend).

Laeuft als admin auf dem RaspiBlitz. Ruft ausschliesslich LESENDE Befehle auf
(getinfo, walletbalance, channelbalance, listchannels, pendingchannels,
listpayments, listmacaroonids, Account-/Swap-Listen). Kein Sende-, Kanal- oder
On-Chain-Befehl -- Operator-Bedingung 29.09.2026: kein Satoshi darf sich bewegen.

  macrot_snap.py snapshot <datei>      Bestand als JSON sichern
  macrot_snap.py guards <datei>        Bestand sichern UND Abbruchbedingungen pruefen (Exit 1 = ABBRUCH)
  macrot_snap.py diff <vorher> <nachher>   Bilanz vergleichen (Exit 2 = BEFUND)
"""

from __future__ import annotations

import json
import subprocess
import sys

LIT = ["--rpcserver=localhost:8443", "--tlscertpath=/mnt/hdd/app-data/.lit/tls.cert"]


def run(cmd: list[str], *, as_json: bool = True):
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}: {(res.stderr or res.stdout).strip()[:200]}")
    return json.loads(res.stdout) if as_json else res.stdout


def try_run(snap: dict, key: str, fn):
    try:
        snap[key] = fn()
    except Exception as exc:  # noqa: BLE001 -- fehlt ein Wert, steht der Fehler im Bestand
        snap[key] = {"error": str(exc)}


def sat(v) -> int:
    return int(v or 0)


BOLTZ_DB = "/home/bitcoin/.boltz/boltz.db"
# boltz-client SwapState: 0 PENDING, 1 SUCCESSFUL, 2 ERROR, 3 SERVER_ERROR, 4 REFUNDED,
# 5 ABANDONED. Abgeschlossen sind nur 1/4/5 -- ERROR-Zustaende koennen noch einen
# Refund brauchen und zaehlen deshalb als offen (Abbruch statt Annahme).
BOLTZ_FINAL_STATES = (1, 4, 5)


def boltz_open_swaps_from_db(path: str = BOLTZ_DB) -> int:
    """Offene Swaps direkt aus boltz.db (nur lesend), falls boltzd kein Backend hat."""
    code = (
        "import sqlite3,sys\n"
        f"con=sqlite3.connect('file:{path}?mode=ro',uri=True)\n"
        "n=0\n"
        "for t in ('swaps','reverseSwaps','chainSwaps'):\n"
        f"    n+=con.execute(f'select count(*) from {{t}} where state not in {BOLTZ_FINAL_STATES}')"
        ".fetchone()[0]\n"
        "print(n)\n"
    )
    res = subprocess.run(
        ["sudo", "-u", "bitcoin", "python3", "-c", code], capture_output=True, text=True, timeout=60
    )
    if res.returncode != 0:
        raise RuntimeError(f"boltz.db nicht lesbar: {(res.stderr or '').strip()[:160]}")
    return int(res.stdout.strip())


def boltz_state() -> dict:
    """Boltz fuer die Abbruchbedingungen -- drei Faelle, jeder belegt statt angenommen.

    - boltzd gestoppt: kein Swap kann laufen, offene Altvorgaenge trotzdem aus boltz.db.
    - boltzd laeuft und antwortet: wie gehabt ueber boltzcli.
    - boltzd laeuft ohne Backend (Boltz hat den Dienst am 03.08.2026 eingestellt):
      boltzcli sagt nur "unavailable", also offene Swaps aus boltz.db lesen.
    """
    active = (
        subprocess.run(["systemctl", "is-active", "--quiet", "boltzd"], timeout=30).returncode == 0
    )
    if active:
        try:
            d = run(["sudo", "-u", "bitcoin", "boltzcli", "listswaps", "--pending", "--json"])
        except RuntimeError:
            d = None
        if d is not None:
            # "autoswap status" endet mit rc != 0, wenn Autoswap nie eingerichtet wurde -- Text zaehlt.
            res = subprocess.run(
                ["sudo", "-u", "bitcoin", "boltzcli", "autoswap", "status"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            auto = ((res.stdout or "") + (res.stderr or "")).strip().splitlines() or ["(leer)"]
            return {
                "mode": "boltzcli",
                "pending_swaps": len(d.get("allSwaps") or []),
                "autoswap": auto[0][:80],
            }
    mode = "boltz.db (boltzd ohne Backend)" if active else "boltz.db (boltzd gestoppt)"
    return {
        "mode": mode,
        "pending_swaps": boltz_open_swaps_from_db(),
        "autoswap": "not configured (kein Backend)",
    }


def take() -> dict:
    s: dict = {}

    def info():
        d = run(["lncli", "getinfo"])
        return {
            "pubkey": d["identity_pubkey"],
            "synced_to_chain": d["synced_to_chain"],
            "block_height": d["block_height"],
            "active_channels": d["num_active_channels"],
        }

    def wallet():
        d = run(["lncli", "walletbalance"])
        keys = ("total_balance", "confirmed_balance", "unconfirmed_balance", "locked_balance")
        return {k: sat(d.get(k)) for k in keys}

    def chanbal():
        d = run(["lncli", "channelbalance"])
        return {
            k: sat((d.get(k) or {}).get("sat"))
            for k in (
                "local_balance",
                "remote_balance",
                "unsettled_local_balance",
                "pending_open_local_balance",
            )
        }

    def channels():
        d = run(["lncli", "listchannels"])["channels"]
        return [
            {
                "chan_point": c["channel_point"],
                "capacity": sat(c["capacity"]),
                "local": sat(c["local_balance"]),
                "remote": sat(c["remote_balance"]),
                "unsettled": sat(c.get("unsettled_balance")),
                "pending_htlcs": len(c.get("pending_htlcs") or []),
                "active": bool(c.get("active")),
            }
            for c in d
        ]

    def pending():
        d = run(["lncli", "pendingchannels"])
        return {
            "pending_open": len(d.get("pending_open_channels") or []),
            "pending_closing": len(d.get("pending_closing_channels") or []),
            "waiting_close": len(d.get("waiting_close_channels") or []),
            "force_closing": [
                {
                    "chan_point": c["channel"]["channel_point"],
                    "limbo": sat(c.get("limbo_balance")),
                    "recovered": sat(c.get("recovered_balance")),
                    "pending_htlcs": len(c.get("pending_htlcs") or []),
                }
                for c in d.get("pending_force_closing_channels") or []
            ],
        }

    def payments():
        p = run(["lncli", "listpayments", "--include_incomplete", "--max_payments", "100"])[
            "payments"
        ]
        return {"checked": len(p), "in_flight": sum(x.get("status") == "IN_FLIGHT" for x in p)}

    def accounts():
        d = run(["sudo", "-u", "lit", "litcli", "accounts", "list"])
        return [
            {"id": a["id"], "label": a.get("label", ""), "balance": sat(a.get("current_balance"))}
            for a in d.get("accounts") or []
        ]

    def boltz():
        return boltz_state()

    def loop():
        params = run(["sudo", "-u", "lit", "loop", *LIT, "getparams"])
        swaps = run(["sudo", "-u", "lit", "loop", *LIT, "listswaps"]).get("swaps") or []
        open_ = [x for x in swaps if x.get("state") not in ("SUCCESS", "FAILED")]
        return {"autoloop": bool(params.get("autoloop")), "open_swaps": len(open_)}

    def pool():
        accs = run(["sudo", "-u", "lit", "pool", *LIT, "accounts", "list"]).get("accounts") or []
        return {"open_accounts": sum(a.get("state") != "CLOSED" for a in accs)}

    def macids():
        return run(["lncli", "listmacaroonids"]).get("root_key_ids") or []

    for key, fn in (
        ("info", info),
        ("wallet", wallet),
        ("channelbalance", chanbal),
        ("channels", channels),
        ("pending", pending),
        ("payments", payments),
        ("accounts", accounts),
        ("boltz", boltz),
        ("loop", loop),
        ("pool", pool),
        ("macaroon_ids", macids),
    ):
        try_run(s, key, fn)
    return s


def guard_reasons(s: dict) -> list[str]:
    r: list[str] = []
    for key, val in s.items():
        if isinstance(val, dict) and "error" in val:
            r.append(f"{key} nicht lesbar: {val['error']}")
    if r:
        return r
    if not s["info"]["synced_to_chain"]:
        r.append("lnd ist nicht mit der Chain synchron")
    p = s["pending"]
    for k in ("pending_open", "pending_closing", "waiting_close"):
        if p[k]:
            r.append(f"{p[k]} Kanal/Kanaele in {k}")
    for fc in p["force_closing"]:
        if fc["pending_htlcs"]:
            r.append(
                f"Force-Close {fc['chan_point'][:12]}… hat {fc['pending_htlcs']} offene HTLCs (Fristen!)"
            )
    for c in s["channels"]:
        if c["pending_htlcs"] or c["unsettled"]:
            r.append(f"Kanal {c['chan_point'][:12]}… hat offene HTLCs/unsettled")
    if s["payments"]["in_flight"]:
        r.append(f"{s['payments']['in_flight']} Zahlung(en) IN_FLIGHT")
    if s["boltz"]["pending_swaps"]:
        r.append(f"{s['boltz']['pending_swaps']} offene Boltz-Swaps")
    auto = s["boltz"]["autoswap"].lower()
    if "not configured" not in auto and "disabled" not in auto:
        r.append(f"Boltz-Autoswap nicht eindeutig aus: {s['boltz']['autoswap']}")
    if s["loop"]["autoloop"]:
        r.append("Autoloop ist an")
    if s["loop"]["open_swaps"]:
        r.append(f"{s['loop']['open_swaps']} offene Loop-Swaps")
    if s["pool"]["open_accounts"]:
        r.append(f"{s['pool']['open_accounts']} offene Pool-Accounts")
    if [a for a in s["accounts"] if a["label"] == "kai"] == []:
        r.append("litd-Account 'kai' nicht gefunden")
    return r


def summary(s: dict) -> str:
    w, cb = s["wallet"], s["channelbalance"]
    fc = sum(x["limbo"] for x in s["pending"]["force_closing"])
    kai = [a["balance"] for a in s["accounts"] if a["label"] == "kai"]
    return (
        f"On-Chain {w['total_balance']} sat (bestaetigt {w['confirmed_balance']}) · "
        f"Kanaele {len(s['channels'])} ({s['info']['active_channels']} aktiv), lokal {cb['local_balance']} sat · "
        f"Force-Close-Anzeige {fc} sat · KAI-Budget {kai[0] if kai else '?'} sat"
    )


def diff(a: dict, b: dict) -> list[str]:
    out: list[str] = []
    if a["info"]["pubkey"] != b["info"]["pubkey"]:
        out.append("BEFUND: Node-Identitaet verschieden")
    for k in ("total_balance", "confirmed_balance", "unconfirmed_balance", "locked_balance"):
        if a["wallet"][k] != b["wallet"][k]:
            out.append(f"BEFUND: On-Chain {k} {a['wallet'][k]} -> {b['wallet'][k]}")
    ca = {c["chan_point"]: c for c in a["channels"]}
    cb = {c["chan_point"]: c for c in b["channels"]}
    if set(ca) != set(cb):
        out.append(f"BEFUND: Kanalliste verschieden (vorher {sorted(ca)}, nachher {sorted(cb)})")
    for cp in set(ca) & set(cb):
        x, y = ca[cp], cb[cp]
        if x["capacity"] != y["capacity"]:
            out.append(f"BEFUND: Kapazitaet {cp[:12]}… {x['capacity']} -> {y['capacity']}")
        if y["local"] < x["local"]:
            out.append(
                f"BEFUND: lokales Guthaben {cp[:12]}… {x['local']} -> {y['local']} (Abnahme)"
            )
        elif y["local"] > x["local"]:
            out.append(
                f"HINWEIS: lokales Guthaben {cp[:12]}… +{y['local'] - x['local']} sat (Routing-Gebuehr/Eingang)"
            )
    fa = {f["chan_point"]: f["limbo"] for f in a["pending"]["force_closing"]}
    fb = {f["chan_point"]: f["limbo"] for f in b["pending"]["force_closing"]}
    if fa != fb:
        out.append(f"BEFUND: Force-Close-Anzeige {fa} -> {fb}")
    ka = [x["balance"] for x in a["accounts"] if x["label"] == "kai"]
    kb = [x["balance"] for x in b["accounts"] if x["label"] == "kai"]
    if ka != kb:
        out.append(f"BEFUND: KAI-Budget {ka} -> {kb}")
    return out


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] in ("snapshot", "guards"):
        s = take()
        with open(sys.argv[2], "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=1, sort_keys=True)
        if sys.argv[1] == "snapshot":
            broken = [k for k, v in s.items() if isinstance(v, dict) and "error" in v]
            print(
                "BESTAND:", summary(s) if not broken else f"unvollstaendig, nicht lesbar: {broken}"
            )
            return 1 if broken else 0
        reasons = guard_reasons(s)
        if reasons:
            for x in reasons:
                print("ABBRUCH:", x)
            return 1
        print("BESTAND:", summary(s))
        print("ABBRUCHBEDINGUNGEN: keine erfuellt -- GO")
        return 0
    if len(sys.argv) == 4 and sys.argv[1] == "diff":
        a = json.load(open(sys.argv[2], encoding="utf-8"))
        b = json.load(open(sys.argv[3], encoding="utf-8"))
        print("VORHER: ", summary(a))
        print("NACHHER:", summary(b))
        lines = diff(a, b)
        for x in lines:
            print(x)
        if any(x.startswith("BEFUND") for x in lines):
            print("BILANZ: ABWEICHUNG -- sofort melden")
            return 2
        print("BILANZ: KEIN VERLUST (nur Zuwachs, siehe HINWEIS)" if lines else "BILANZ: IDENTISCH")
        return 0
    print(__doc__)
    return 64


if __name__ == "__main__":
    sys.exit(main())
