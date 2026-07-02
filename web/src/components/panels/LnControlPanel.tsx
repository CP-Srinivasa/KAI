// @data-source: /dashboard/api/lightning + POST /dashboard/api/ln/value-action
import { useState } from "react";
import { ShieldAlert, ShieldCheck, Play, Send } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { LiveDot } from "@/components/ui/LiveDot";
import {
  fetchLightningStatus,
  lnValueAction,
  type LightningStatus,
  type LnActionResult,
} from "@/lib/api";
import { usePolling } from "@/lib/usePolling";

// Steuer-Cockpit für die gegatete Wert-Schicht (Sprint 5). Zeigt EHRLICH den
// Kill-Switch-Zustand (pay_enabled) und erlaubt Plan-Vorschau (Policy-Verdikt +
// inerter Zustand) + B-005-Confirm-Ausführung. Alles bleibt inert, solange
// pay_enabled=false (Node wird nie berührt). Kein Service-Token (Email-Allowlist).

const POLL_MS = 60_000;
const ACTIONS = [
  "create_invoice",
  "pay_invoice",
  "keysend",
  "send_coins",
  "open_channel",
  "close_channel",
] as const;

const PARAM_HINT: Record<string, string> = {
  create_invoice: '{"value_sat": 1000, "memo": "test"}',
  pay_invoice: '{"payment_request": "lnbc..."}',
  keysend: '{"dest_pubkey_hex": "02..", "amt_sat": 1000}',
  send_coins: '{"addr": "bc1q..", "amount_sat": 1000}',
  open_channel: '{"node_pubkey_hex": "02..", "local_funding_sat": 50000}',
  close_channel: '{"funding_txid": "..", "output_index": 0}',
};

function decisionTone(d?: string): "pos" | "warn" | "neg" | "muted" {
  if (d === "auto_execute") return "pos";
  if (d === "needs_confirm") return "warn";
  if (d === "denied") return "neg";
  return "muted";
}

export function LnControlPanel() {
  const polling = usePolling<LightningStatus>((s) => fetchLightningStatus(s), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const ln = polling.state === "ready" ? polling.data : null;
  const payOn = ln?.pay_enabled === true;

  const [action, setAction] = useState<string>("create_invoice");
  const [paramsText, setParamsText] = useState<string>(PARAM_HINT["create_invoice"]);
  const [hotp, setHotp] = useState<string>("");
  const [idemKey, setIdemKey] = useState<string>("");
  const [result, setResult] = useState<LnActionResult | null>(null);
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState(false);

  const run = async (execute: boolean) => {
    setBusy(true);
    setError("");
    let params: Record<string, unknown>;
    try {
      params = JSON.parse(paramsText || "{}");
    } catch {
      setError("Params sind kein gültiges JSON");
      setBusy(false);
      return;
    }
    try {
      const confirm =
        execute && result?.plan_hash
          ? { hotp, plan_hash: result.plan_hash, idempotency_key: idemKey }
          : undefined;
      const r = await lnValueAction({ action, params, ...(confirm ? { confirm } : {}) });
      setResult(r);
    } catch (e) {
      setError((e as Error).message || "Fehler");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card padded className="overflow-hidden">
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            <Send size={14} className="text-ai shrink-0" />
            LN-Steuerung (Wert-Schicht)
          </span>
        }
        subtitle="Plan → Policy → Confirm · inert bis pay_enabled · read+control"
        right={
          <div className="flex items-center gap-2">
            <LiveDot state={polling.state} generatedAt={ln ? ln.generated_at : null} staleAfterMs={90_000} downAfterMs={240_000} />
            {ln == null ? null : payOn ? (
              <Badge tone="warn" dot>
                <ShieldAlert size={10} /> pay_enabled AN
              </Badge>
            ) : (
              <Badge tone="pos" dot>
                <ShieldCheck size={10} /> Kill-Switch AN (inert)
              </Badge>
            )}
          </div>
        }
      />

      <div
        className={
          payOn
            ? "rounded-sm border border-warn/40 bg-warn/10 px-3 py-2 text-2xs text-fg-muted"
            : "rounded-sm border border-pos/30 bg-pos/5 px-3 py-2 text-2xs text-fg-muted"
        }
      >
        {payOn ? (
          <span>
            <span className="font-semibold text-warn">pay_enabled=true</span> — kapital-wirksame
            Aktionen KÖNNEN ausführen (innerhalb der Policy-Envelopes). Confirm = HOTP + Plan-Hash.
          </span>
        ) : (
          <span>
            <span className="font-semibold text-pos">Kill-Switch aktiv</span> (
            <span className="font-mono">pay_enabled=false</span>): jede Ausführung bleibt{" "}
            <span className="font-mono">disabled</span> — die Node wird nie berührt. Vorschau (Plan)
            zeigt trotzdem das Policy-Verdikt.
          </span>
        )}
      </div>

      <div className="mt-3 space-y-2">
        <div className="flex flex-wrap gap-2">
          <select
            value={action}
            onChange={(e) => {
              setAction(e.target.value);
              setParamsText(PARAM_HINT[e.target.value] ?? "{}");
              setResult(null);
            }}
            className="rounded-sm border border-line-subtle bg-bg-2 px-2 py-1 text-xs font-mono text-fg"
          >
            {ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          <button
            onClick={() => run(false)}
            disabled={busy}
            className="flex items-center gap-1 rounded-sm border border-ai/40 bg-ai/10 px-2.5 py-1 text-xs text-ai disabled:opacity-50"
          >
            <Play size={11} /> Plan
          </button>
        </div>
        <textarea
          value={paramsText}
          onChange={(e) => setParamsText(e.target.value)}
          rows={2}
          spellCheck={false}
          className="w-full rounded-sm border border-line-subtle bg-bg-2/60 px-2 py-1 font-mono text-2xs text-fg"
        />

        {result?.policy && (
          <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2 space-y-1">
            <div className="flex items-center gap-2 text-2xs">
              <span className="text-fg-subtle">Policy:</span>
              <Badge tone={decisionTone(result.policy.decision)}>{result.policy.decision}</Badge>
              <span className="text-fg-muted">{result.policy.reason}</span>
            </div>
            {result.plan && (
              <div className="font-mono text-2xs text-fg-subtle">
                Plan-Zustand: <span className="text-fg">{result.plan.state}</span>
                {result.plan.detail ? ` (${result.plan.detail})` : ""}
              </div>
            )}
            {result.plan_hash && (
              <div className="font-mono text-2xs text-fg-subtle break-all">
                plan_hash: {result.plan_hash.slice(0, 24)}…
              </div>
            )}
          </div>
        )}

        {result?.mode === "plan" && result.policy && result.policy.decision !== "denied" && (
          <div className="rounded-sm border border-warn/25 bg-warn/5 px-2.5 py-2 space-y-1.5">
            <div className="text-2xs text-fg-muted">
              Ausführen{result.policy.decision === "needs_confirm" ? " (Confirm nötig: HOTP)" : " (auto)"}:
            </div>
            <div className="flex flex-wrap gap-2">
              {result.policy.decision === "needs_confirm" && (
                <>
                  <input
                    value={hotp}
                    onChange={(e) => setHotp(e.target.value)}
                    placeholder="HOTP"
                    className="w-24 rounded-sm border border-line-subtle bg-bg-2 px-2 py-1 text-xs font-mono"
                  />
                  <input
                    value={idemKey}
                    onChange={(e) => setIdemKey(e.target.value)}
                    placeholder="idempotency-key"
                    className="w-40 rounded-sm border border-line-subtle bg-bg-2 px-2 py-1 text-xs font-mono"
                  />
                </>
              )}
              <button
                onClick={() => run(true)}
                disabled={busy}
                className="flex items-center gap-1 rounded-sm border border-warn/40 bg-warn/10 px-2.5 py-1 text-xs text-warn disabled:opacity-50"
              >
                <Send size={11} /> Ausführen
              </button>
            </div>
          </div>
        )}

        {result?.mode === "execute" && result.result && (
          <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2 font-mono text-2xs">
            Ergebnis: <span className="text-fg">{result.result.state}</span>
            {result.result.detail ? ` (${result.result.detail})` : ""}
          </div>
        )}

        {error && (
          <div className="rounded-sm border border-neg/30 bg-neg/5 px-2.5 py-1.5 text-2xs font-mono text-neg">
            {error}
          </div>
        )}
      </div>
    </Card>
  );
}
