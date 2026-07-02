// @data-source: /dashboard/api/ln/ops
import { ScrollText, ShieldCheck, Power, Lock } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { LiveDot } from "@/components/ui/LiveDot";
import { fetchLnOps, type LnOps, type LnOp } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";

// Wert-Schicht-Ops-Audit-Trail (read-only). Jede gegatete Wert-Schicht-Aktion
// (Plan + Ausführung) landet tamper-evident in artifacts/ln_ops_ledger.jsonl,
// täglich L3-OTS-verankert. Writer + Wert-Schicht (U1–U5) sind GEBAUT; der Trail
// bleibt leer, bis eine Aktion bei receive_enabled/pay_enabled erfolgt (Default
// false = inert). Kein schreibender/kapitalrelevanter Pfad im Default.

const POLL_MS = 60_000;

const asStr = (v: unknown): string => (v == null ? "—" : typeof v === "string" ? v : JSON.stringify(v));

function OpRow({ op }: { op: LnOp }) {
  const action = asStr(op.action ?? op.type);
  const state = asStr(op.state ?? op.status);
  const ts = asStr(op.ts ?? op.timestamp);
  return (
    <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-mono text-2xs text-fg">{action}</span>
        <Badge tone="muted">{state}</Badge>
      </div>
      <div className="mt-0.5 font-mono text-2xs text-fg-subtle">{ts}</div>
    </div>
  );
}

export function LnOpsAuditPanel() {
  const polling = usePolling<LnOps>((signal) => fetchLnOps(signal), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const data = polling.state === "ready" ? polling.data : null;

  const badge =
    data == null ? null : data.count > 0 ? (
      <Badge tone="info" dot>
        <ShieldCheck size={10} /> {data.count} Aktionen
      </Badge>
    ) : (
      <Badge tone="muted" dot>
        <Power size={10} /> keine Aktionen
      </Badge>
    );

  return (
    <Card padded className="overflow-hidden">
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            <ScrollText size={14} className="text-info shrink-0" />
            Wert-Schicht-Audit-Trail
          </span>
        }
        subtitle="ln_ops_ledger · read-only · L3-OTS-verankerbar"
        right={
          <div className="flex items-center gap-2">
            <LiveDot
              state={polling.state}
              generatedAt={data ? data.generated_at : null}
              staleAfterMs={90_000}
              downAfterMs={240_000}
            />
            {badge}
          </div>
        }
      />

      {polling.state === "loading" && (
        <div className="py-4 text-center text-xs text-fg-subtle">Lade Audit-Trail …</div>
      )}

      {polling.state === "error" && (
        <div className="rounded-sm border border-neg/30 bg-neg/5 px-3 py-2 text-2xs font-mono text-neg">
          Endpoint nicht erreichbar ({polling.error.kind}) ·{" "}
          <span className="font-mono">/dashboard/api/ln/ops</span>
        </div>
      )}

      {data != null && data.count === 0 && (
        <div className="rounded-sm border border-warn/25 bg-warn/5 px-3 py-2.5 text-2xs text-fg-muted leading-relaxed">
          <span className="flex items-center gap-1.5 font-semibold text-warn">
            <Lock size={11} /> Wert-Schicht gebaut · inert
          </span>
          <p className="mt-1">
            Noch keine Wert-Schicht-Aktion ausgeführt (Default inert:{" "}
            <span className="font-mono">receive_enabled</span> + <span className="font-mono">pay_enabled</span>{" "}
            false). Empfangen ist kapitalfrei vom Senden entkoppelt; Senden/Channels bleiben hinter dem
            Kapital-Gate. Sobald eine gegatete Aktion erfolgt (Plan/Ausführung), erscheint sie hier mit
            Status — tamper-evident und täglich OTS-verankert.
          </p>
        </div>
      )}

      {data != null && data.count > 0 && (
        <div className="space-y-1.5">
          {data.ops.map((op, i) => (
            <OpRow key={i} op={op} />
          ))}
        </div>
      )}
    </Card>
  );
}
