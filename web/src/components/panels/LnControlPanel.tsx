// @data-source: /dashboard/api/lightning + POST /dashboard/api/ln/value-action
import { useState } from "react";
import { ShieldAlert, ShieldCheck, Play, Send } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { LiveDot } from "@/components/ui/LiveDot";
import { lnValueAction, type LightningStatus, type LnActionResult } from "@/lib/api";
import type { AsyncState } from "@/lib/useApi";

// Steuer-Cockpit für die gegatete Wert-Schicht (Sprint 5). Zeigt EHRLICH den
// Kill-Switch-Zustand (pay_enabled) und erlaubt Plan-Vorschau (Policy-Verdikt +
// inerter Zustand) + B-005-Confirm-Ausführung. Alles bleibt inert, solange
// pay_enabled=false (Node wird nie berührt). Kein Service-Token (Email-Allowlist).

// ADR 0018 §12: keysend/send_coins/open_channel/close_channel sind entfallen.
// Sie standen im Menü, obwohl die Regelkette sie mit unsupported_action abgelehnt
// hätte — ein Eintrag, der nur existiert, um abgelehnt zu werden, sieht aus wie
// eine Fähigkeit, die man nur freischalten müsste.
const ACTIONS = ["create_invoice", "pay_invoice"] as const;

function decisionTone(d?: string): "pos" | "warn" | "neg" | "muted" {
  if (d === "receive_gate") return "pos";
  if (d === "payment_control_plane") return "warn";
  if (d === "denied") return "neg";
  return "muted";
}

/** Der Lightning-Status kommt von der Seite herein: die Node-Seite holt ihn
 *  EINMAL fuer Lightning-Karte und LN-Steuerung (2026-09-17, SP-8 / T6 —
 *  vorher je ein eigener Poller auf denselben Endpunkt). */
export function LnControlPanel({ status: polling }: { status: AsyncState<LightningStatus> }) {
  const ln = polling.state === "ready" ? polling.data : null;
  const payOn = ln?.pay_enabled === true;

  const [action, setAction] = useState<(typeof ACTIONS)[number]>("create_invoice");
  const [valueSat, setValueSat] = useState("1000");
  const [memo, setMemo] = useState("");
  const [paymentRequest, setPaymentRequest] = useState("");
  const [purpose, setPurpose] = useState("operator_pay_invoice");
  const [hotp, setHotp] = useState<string>("");
  const [idemKey, setIdemKey] = useState(() => crypto.randomUUID());
  const [result, setResult] = useState<LnActionResult | null>(null);
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState(false);

  const changeInput = (update: () => void) => {
    update();
    setResult(null);
    setError("");
    setHotp("");
    setIdemKey(crypto.randomUUID());
  };

  const run = async (execute: boolean) => {
    setBusy(true);
    setError("");
    let params: Record<string, unknown>;
    if (action === "create_invoice") {
      if (!/^[1-9]\d*$/.test(valueSat) || !Number.isSafeInteger(Number(valueSat))) {
        setError("Betrag muss eine positive ganze Sat-Zahl sein");
        setBusy(false);
        return;
      }
      params = { value_sat: Number(valueSat), memo };
    } else {
      if (!paymentRequest.trim() || !purpose.trim()) {
        setError("Rechnung und Zweck sind erforderlich");
        setBusy(false);
        return;
      }
      params = { payment_request: paymentRequest.trim(), purpose: purpose.trim() };
    }
    if (execute && (!result?.plan_hash || result.mode !== "plan")) {
      setError("Bitte zuerst den aktuellen Vorgang prüfen");
      setBusy(false);
      return;
    }
    try {
      const confirm = execute && result?.plan_hash
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
        subtitle="Vorschau → prüfen → freigeben · PaymentService für Zahlungen"
        right={
          <div className="flex items-center gap-2">
            <LiveDot state={polling.state} generatedAt={ln ? ln.generated_at : null} staleAfterMs={90_000} downAfterMs={240_000} />
            {ln == null ? null : payOn ? (
              <Badge tone="warn" dot>
                <ShieldAlert size={10} /> pay_enabled AN
              </Badge>
            ) : (
              <Badge tone="pos" dot>
                <ShieldCheck size={10} /> Kill-Switch AN (Senden aus)
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
            <span className="font-semibold text-warn">pay_enabled=true</span> — Zahlungen können
            innerhalb der Payment-Regelkette ausgeführt werden. Die Freigabe bindet den geprüften Plan.
          </span>
        ) : (
          <span>
            <span className="font-semibold text-pos">Sendepfad gesperrt</span> (
            <span className="font-mono">pay_enabled=false</span>). Das Erstellen von Rechnungen
            hängt separat vom Empfangs-Gate ab.
          </span>
        )}
      </div>

      <div className="mt-3 space-y-2">
        <div className="flex flex-wrap gap-2">
          <select
            aria-label="Aktion"
            value={action}
            onChange={(e) => {
              changeInput(() => setAction(e.target.value as (typeof ACTIONS)[number]));
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
        {action === "create_invoice" ? (
          <div className="flex flex-wrap gap-2">
            <label className="text-2xs text-fg-muted">
              Betrag in sat
              <input
                type="number"
                min="1"
                step="1"
                value={valueSat}
                onChange={(e) => changeInput(() => setValueSat(e.target.value))}
                className="mt-1 block w-32 rounded-sm border border-line-subtle bg-bg-2 px-2 py-1 text-xs text-fg"
              />
            </label>
            <label className="text-2xs text-fg-muted">
              Memo
              <input
                value={memo}
                onChange={(e) => changeInput(() => setMemo(e.target.value))}
                className="mt-1 block w-64 rounded-sm border border-line-subtle bg-bg-2 px-2 py-1 text-xs text-fg"
              />
            </label>
          </div>
        ) : (
          <div className="space-y-2">
            <label className="block text-2xs text-fg-muted">
              Lightning-Rechnung
              <textarea
                value={paymentRequest}
                onChange={(e) => changeInput(() => setPaymentRequest(e.target.value))}
                rows={2}
                spellCheck={false}
                className="mt-1 w-full rounded-sm border border-line-subtle bg-bg-2/60 px-2 py-1 font-mono text-2xs text-fg"
              />
            </label>
            <label className="block text-2xs text-fg-muted">
              Zweck
              <input
                value={purpose}
                onChange={(e) => changeInput(() => setPurpose(e.target.value))}
                className="mt-1 w-full rounded-sm border border-line-subtle bg-bg-2 px-2 py-1 text-xs text-fg"
              />
            </label>
          </div>
        )}

        {result?.policy && (
          <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2 space-y-1">
            <div className="flex items-center gap-2 text-2xs">
              <span className="text-fg-subtle">Ausführungspfad:</span>
              <Badge tone={decisionTone(result.policy.decision)}>{result.policy.decision}</Badge>
              <span className="text-fg-muted">{result.policy.reason}</span>
            </div>
            {result.plan && (
              <div className="font-mono text-2xs text-fg-subtle">
                Plan-Zustand: <span className="text-fg">{result.plan.state ?? result.plan.status ?? "unbekannt"}</span>
                {result.plan.detail ? ` (${result.plan.detail})` : ""}
                {result.plan.amount_sat != null ? ` · ${result.plan.amount_sat} sat` : ""}
                {result.plan.fee_limit_sat != null ? ` · Gebührengrenze ${result.plan.fee_limit_sat} sat` : ""}
                {result.plan.mode ? ` · ${result.plan.mode}` : ""}
              </div>
            )}
            {result.plan_hash && (
              <div className="font-mono text-2xs text-fg-subtle break-all">
                plan_hash: {result.plan_hash.slice(0, 24)}…
              </div>
            )}
          </div>
        )}

        {result?.mode === "plan" && result.plan_hash && result.policy &&
          result.policy.decision !== "denied" &&
          result.plan?.state !== "disabled" && result.plan?.state !== "error" &&
          result.plan?.status !== "unavailable" &&
          !(action === "pay_invoice" && (result.plan?.mode === "shadow" || (!payOn && result.plan?.mode === "live"))) && (
          <div className="rounded-sm border border-warn/25 bg-warn/5 px-2.5 py-2 space-y-1.5">
            <div className="text-2xs text-fg-muted">
              {action === "pay_invoice"
                ? "Zahlung freigeben (HOTP, wenn vom Dienst verlangt):"
                : "Rechnung nach geprüfter Vorschau erstellen:"}
            </div>
            <div className="flex flex-wrap gap-2">
              {action === "pay_invoice" && (
                <label className="text-2xs text-fg-muted">
                  HOTP-Freigabe
                  <input
                    type="password"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    value={hotp}
                    onChange={(e) => setHotp(e.target.value)}
                    className="mt-1 block w-28 rounded-sm border border-line-subtle bg-bg-2 px-2 py-1 text-xs font-mono text-fg"
                  />
                </label>
              )}
              <button
                onClick={() => run(true)}
                disabled={busy}
                className="flex items-center gap-1 rounded-sm border border-warn/40 bg-warn/10 px-2.5 py-1 text-xs text-warn disabled:opacity-50"
              >
                <Send size={11} /> {result.plan?.mode === "simulation" ? "Simulieren" : "Ausführen"}
              </button>
            </div>
          </div>
        )}

        {result?.mode === "execute" && result.result && (
          <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2 font-mono text-2xs">
            Ergebnis: <span className="text-fg">{result.result.state ?? result.result.status ?? "unbekannt"}</span>
            {result.result.detail ? ` (${result.result.detail})` : ""}
            {result.result.replayed ? " · bereits bekannt" : ""}
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
