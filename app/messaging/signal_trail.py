"""Signal Trail Visualizer for Telegram and Operator CLI.

Searches through the pipeline JSONL files to reconstruct the exact path
and state of a TradingView signal through the 9 Bridge/Execution gates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records = []
    if not path.exists():
        return records
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return records


def find_matching_signal_data(query_id: str, artifacts_dir: Path) -> dict[str, Any]:
    """Search for the signal across multiple audit files to piece together its trail."""
    query = query_id.strip()

    data: dict[str, Any] = {
        "query_id": query,
        "ingress": None,
        "auth": None,
        "replay": None,
        "eligibility": None,
        "promotion": None,
        "approval": None,
        "intent": None,
        "routing": None,
        "lifecycle": None,
        "symbol": None,
        "direction": None,
    }

    # 1. Search Signal Ingress Audit
    ingress_records = load_jsonl_records(artifacts_dir / "tradingview_signal_audit.jsonl")
    for r in reversed(ingress_records):
        sig_id = r.get("signal_id") or r.get("document_id")
        corr_id = r.get("correlation_id")
        if query in (sig_id, corr_id) or (sig_id and query in sig_id) or (corr_id and query in corr_id):
            data["ingress"] = r
            data["symbol"] = r.get("symbol")
            data["direction"] = r.get("direction")
            break

    # 2. Search Pending/Promoted Decisions
    pending_records = load_jsonl_records(artifacts_dir / "tradingview_pending_signals.jsonl")
    for r in reversed(pending_records):
        sig_id = r.get("signal_id") or r.get("document_id")
        corr_id = r.get("correlation_id")
        if query in (sig_id, corr_id) or (sig_id and query in sig_id) or (corr_id and query in corr_id):
            data["eligibility"] = r
            break

    promoted_records = load_jsonl_records(artifacts_dir / "tradingview_promoted_signals.jsonl")
    for r in reversed(promoted_records):
        sig_id = r.get("signal_id") or r.get("document_id")
        corr_id = r.get("correlation_id")
        if query in (sig_id, corr_id) or (sig_id and query in sig_id) or (corr_id and query in corr_id):
            data["promotion"] = r
            break

    # 3. Search Operator Review / Decision Journal
    decision_records = load_jsonl_records(artifacts_dir / "decision_journal.jsonl")
    for r in reversed(decision_records):
        sig_id = r.get("signal_id") or r.get("document_id")
        corr_id = r.get("correlation_id")
        if query in (sig_id, corr_id) or (sig_id and query in sig_id) or (corr_id and query in corr_id):
            data["approval"] = r
            break

    # 4. Search Alert Audit
    alert_records = load_jsonl_records(artifacts_dir / "alert_audit.jsonl")
    for r in reversed(alert_records):
        doc_id = r.get("document_id")
        if query in (doc_id, "") or (doc_id and query in doc_id):
            data["intent"] = r
            if not data["symbol"] and r.get("affected_assets"):
                data["symbol"] = r.get("affected_assets")[0]
            if not data["direction"] and r.get("sentiment_label"):
                data["direction"] = r.get("sentiment_label")
            break

    # 5. Search Paper Execution Audit (Lifecycle & Routing)
    exec_records = load_jsonl_records(artifacts_dir / "paper_execution_audit.jsonl")
    matching_execs = []
    for r in reversed(exec_records):
        sig_id = r.get("signal_id") or r.get("document_id")
        corr_id = r.get("correlation_id")
        if query in (sig_id, corr_id) or (sig_id and query in sig_id) or (corr_id and query in corr_id):
            matching_execs.append(r)
            if not data["symbol"] and r.get("symbol"):
                data["symbol"] = r.get("symbol")
            if not data["direction"] and r.get("direction"):
                data["direction"] = r.get("direction")

    if matching_execs:
        # Sort so we have the lifecycle chronologically
        matching_execs.reverse()
        data["routing"] = matching_execs[0]
        data["lifecycle"] = matching_execs  # Keep the whole chain

    return data


def format_signal_trail_message(query_id: str, artifacts_dir: Path) -> str:
    """Reconstruct and format the visual gate-trail for Telegram."""
    if not query_id.strip():
        # Get list of recent signals from ingress
        ingress_records = load_jsonl_records(artifacts_dir / "tradingview_signal_audit.jsonl")
        if not ingress_records:
            return "Keine kürzlichen Signale gefunden. Bitte eine Signal ID angeben (z. B. `/trail SIG-20260415-BTCUSDT-001`)."

        recent = []
        seen = set()
        for r in reversed(ingress_records):
            sig_id = r.get("signal_id") or r.get("document_id")
            if sig_id and sig_id not in seen:
                seen.add(sig_id)
                symbol = r.get("symbol", "unknown")
                dir_str = r.get("direction", "")
                ts = r.get("timestamp", "")[:19].replace("T", " ")
                recent.append(f"• `{sig_id}` ({symbol} {dir_str}) @ {ts}")
                if len(recent) >= 5:
                    break

        recent_str = "\n".join(recent)
        return (
            f"*Kürzliche TradingView Signale:*\n\n"
            f"{recent_str}\n\n"
            f"Nutze `/trail <signal_id>` für Details zum Gate-Durchlauf."
        )

    data = find_matching_signal_data(query_id, artifacts_dir)

    if not data["ingress"] and not data["intent"] and not data["routing"]:
        return f"Kein Signal für `{query_id}` in den Audit-Logs gefunden."

    # Parse details
    sig_id = query_id
    if data["ingress"]:
        sig_id = data["ingress"].get("signal_id") or data["ingress"].get("document_id") or query_id

    symbol = data["symbol"] or "unknown"
    direction = (data["direction"] or "unknown").upper()

    # Reconstruct the 9 Gates
    lines = []
    lines.append(f"*KAI Signal Trail: {sig_id}*")
    lines.append(f"Asset: `{symbol}` · Direction: `{direction}`\n")

    # Gate 1: Ingress
    if data["ingress"]:
        ts = data["ingress"].get("timestamp", "")[:19].replace("T", " ")
        lines.append(f"1️⃣ *Ingress:* ✅ Empfangen @ {ts}")
    else:
        lines.append("1️⃣ *Ingress:* ⚪ Nicht erfasst")

    # Gate 2: Auth / Provenance
    if data["ingress"]:
        auth_ok = data["ingress"].get("auth_valid", True)
        method = data["ingress"].get("auth_method", "HMAC")
        if auth_ok:
            lines.append(f"2️⃣ *Auth/Provenance:* ✅ Valide ({method})")
        else:
            lines.append("2️⃣ *Auth/Provenance:* ❌ Signatur ungültig")
    else:
        lines.append("2️⃣ *Auth/Provenance:* ⚪ Unbekannt")

    # Gate 3: Replay-Guard
    if data["ingress"]:
        is_replay = data["ingress"].get("is_replay", False)
        if not is_replay:
            lines.append("3️⃣ *Replay-Guard:* ✅ Eindeutig (Neu)")
        else:
            lines.append("3️⃣ *Replay-Guard:* ❌ Replay-Versuch blockiert")
    else:
        lines.append("3️⃣ *Replay-Guard:* ⚪ Unbekannt")

    # Gate 4: Eligibility
    if data["eligibility"] or data["ingress"]:
        rec = data["eligibility"] or data["ingress"]
        eligible = rec.get("directional_eligible", True)
        if eligible:
            lines.append("4️⃣ *Eligibility:* ✅ Berechtigt")
        else:
            reason = rec.get("directional_block_reason", "Nicht berechtigt")
            lines.append(f"4️⃣ *Eligibility:* ❌ Blockiert ({reason})")
    else:
        lines.append("4️⃣ *Eligibility:* ⚪ Unbekannt")

    # Gate 5: Promotion
    if data["promotion"]:
        lines.append("5️⃣ *Promotion:* ✅ Promoted")
    elif data["ingress"] and data["ingress"].get("directional_eligible") is False:
        lines.append("5️⃣ *Promotion:* ❌ Übersprungen (Not Eligible)")
    else:
        lines.append("5️⃣ *Promotion:* ⚪ Ausstehend")

    # Gate 6: Operator Approval
    if data["approval"]:
        decision = data["approval"].get("decision", "pending").lower()
        operator = data["approval"].get("operator", "System")
        ts = data["approval"].get("timestamp", "")[:19].replace("T", " ")
        if decision == "approved":
            lines.append(f"6️⃣ *Operator-Approval:* ✅ Akzeptiert von {operator} @ {ts}")
        elif decision == "rejected":
            lines.append(f"6️⃣ *Operator-Approval:* ❌ Abgelehnt von {operator} @ {ts}")
        else:
            lines.append("6️⃣ *Operator-Approval:* ⏳ Ausstehend")
    else:
        lines.append("6️⃣ *Operator-Approval:* ⏳ Ausstehend")

    # Gate 7: Executable Intent
    if data["intent"]:
        corr_id = data["intent"].get("document_id") or "unbekannt"
        lines.append(f"7️⃣ *Executable Intent:* ✅ Generiert (`{corr_id[:12]}`)")
    else:
        lines.append("7️⃣ *Executable Intent:* ⚪ Ausstehend")

    # Gate 8: Execution Routing
    if data["routing"]:
        event = data["routing"].get("event_type", "order_created")
        lines.append(f"8️⃣ *Execution Routing:* ✅ Geroutet ({event})")
    else:
        lines.append("8️⃣ *Execution Routing:* ⚪ Ausstehend")

    # Gate 9: Lifecycle State (16-State SSoT)
    if data["lifecycle"]:
        # Find the latest state from the execution audit chain
        latest_event = data["lifecycle"][-1]
        evt_type = latest_event.get("event_type", "unknown")

        emoji_map = {
            "position_closed": "🏁 Geschlossen (Closed)",
            "position_opened": "🟢 Offen (Filled)",
            "order_filled": "🟢 Gefüllt (Filled)",
            "order_created": "⏳ Platziert (Created)",
            "position_adjusted": "🔄 Angepasst (Adjusted)",
            "order_rejected": "❌ Abgelehnt (Rejected)",
            "order_expired": "⏳ Abgelaufen (Expired)",
        }
        status_text = emoji_map.get(evt_type, f"🔄 {evt_type}")
        ts = latest_event.get("timestamp_utc", "")[:19].replace("T", " ")

        lines.append(f"9️⃣ *Lifecycle:* {status_text} @ {ts}")

        # Details of the trail
        lines.append("\n*Historischer Ablauf:*")
        for idx, ev in enumerate(data["lifecycle"]):
            etype = ev.get("event_type")
            pnl = ev.get("pnl")
            pnl_str = f" (PnL: {pnl:+.2f}%)" if pnl is not None else ""
            lines.append(f"  {idx+1}. `{etype}`{pnl_str}")
    else:
        lines.append("9️⃣ *Lifecycle:* ⚪ Inaktiv")

    return "\n".join(lines)
