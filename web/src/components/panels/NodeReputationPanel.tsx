// @data-source: /dashboard/api/ln/reputation
import type { ReactNode } from "react";
import { Gauge, ShieldCheck, ShieldAlert, Power, Users, Network, Coins, Activity } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { LiveDot } from "@/components/ui/LiveDot";
import { fetchLnReputation, type LnReputation } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";

// Node-Reputation-Telemetrie (read-only, default-off). Zeigt EHRLICH das
// aufgezeichnete Fenster: Uptime% (Anteil erreichbarer Ticks — null ohne Daten,
// kein erfundener 100%-Wert), letzte Konnektivität (Peers/Channels/Sync) und den
// Routing-Income-Trend. `unavailable`-Ticks zählen mit (Downtime = Reputation).
// Kein schreibender/kapitalrelevanter Pfad. Ersetzt den Roadmap-Platzhalter.

const POLL_MS = 60_000;

const fmtSats = (sat: number) => `${sat.toLocaleString("de-DE")} sats`;
const fmtFee = (sat: number | null) => (sat == null ? "n/v" : fmtSats(sat));

function Stat({ icon, label, value, tone }: { icon: ReactNode; label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2">
      <div className="flex items-center gap-1 text-2xs text-fg-subtle">
        {icon} {label}
      </div>
      <div className={`mt-0.5 font-mono tabular-nums text-sm ${tone ?? "text-fg"}`}>{value}</div>
    </div>
  );
}

export function NodeReputationPanel() {
  const polling = usePolling<LnReputation>((signal) => fetchLnReputation(signal), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const data = polling.state === "ready" ? polling.data : null;
  const latest = data?.latest ?? null;

  const uptimeBadge =
    data == null || data.uptime_pct == null ? (
      <Badge tone="muted" dot>
        <Power size={10} /> keine Daten
      </Badge>
    ) : data.uptime_pct >= 99 ? (
      <Badge tone="pos" dot>
        <ShieldCheck size={10} /> {data.uptime_pct}% Uptime
      </Badge>
    ) : (
      <Badge tone="warn" dot>
        <ShieldAlert size={10} /> {data.uptime_pct}% Uptime
      </Badge>
    );

  return (
    <Card padded className="synthwave-pulse-edge overflow-hidden">
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            <Gauge size={14} className="text-ai glow-ai shrink-0" />
            Node-Reputation
          </span>
        }
        subtitle="Uptime · Konnektivität · Routing-Income · read-only"
        right={
          <div className="flex items-center gap-2">
            <LiveDot
              state={polling.state}
              generatedAt={data ? data.generated_at : null}
              staleAfterMs={90_000}
              downAfterMs={240_000}
            />
            {uptimeBadge}
          </div>
        }
      />

      {polling.state === "loading" && (
        <div className="py-4 text-center text-xs text-fg-subtle">Lade Reputation …</div>
      )}

      {polling.state === "error" && (
        <div className="rounded-sm border border-neg/30 bg-neg/5 px-3 py-2 text-2xs font-mono text-neg">
          Endpoint nicht erreichbar ({polling.error.kind}) ·{" "}
          <span className="font-mono">/dashboard/api/ln/reputation</span>
        </div>
      )}

      {data != null && data.count === 0 && (
        <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-3 py-2.5 text-2xs text-fg-muted leading-relaxed">
          Noch <span className="text-fg">keine Telemetrie</span> aufgezeichnet. Der Reputations-Collector
          läuft nur bei aktiviertem Lightning und schreibt periodisch Uptime/Konnektivität in einen
          OTS-verankerbaren Track-Record — die Grundlage für Inbound-Channel- und Oracle-Vertrauen.
        </div>
      )}

      {data != null && data.count > 0 && latest != null && (
        <div className="space-y-2.5">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <Stat
              icon={<Gauge size={10} className="text-ai/70" />}
              label="Uptime (Fenster)"
              value={data.uptime_pct == null ? "n/v" : `${data.uptime_pct}%`}
              tone={data.uptime_pct != null && data.uptime_pct >= 99 ? "text-pos" : "text-warn"}
            />
            <Stat
              icon={<Users size={10} className="text-info/70" />}
              label="Peers (zuletzt)"
              value={String(latest.num_peers)}
            />
            <Stat
              icon={<Network size={10} className="text-info/70" />}
              label="Aktive Channels"
              value={`${latest.num_active_channels}${latest.num_pending_channels ? ` (+${latest.num_pending_channels} pending)` : ""}`}
            />
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone={latest.reachable ? "pos" : "warn"} dot>
              {latest.reachable ? "erreichbar" : "offline"}
            </Badge>
            <Badge tone={latest.synced_to_chain ? "pos" : "muted"}>
              chain {latest.synced_to_chain ? "synced" : "—"}
            </Badge>
            <Badge tone={latest.synced_to_graph ? "pos" : "muted"}>
              graph {latest.synced_to_graph ? "synced" : "—"}
            </Badge>
          </div>

          <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2">
            <div className="flex items-center gap-1 text-2xs text-fg-subtle">
              <Coins size={10} className="text-ai/70" /> Routing-Income (verdient)
            </div>
            <div className="mt-1 grid grid-cols-3 gap-2 font-mono tabular-nums text-2xs text-fg">
              <span title="letzte 24h">24h {fmtFee(latest.routing_fee_day_sat)}</span>
              <span title="letzte 7 Tage">7d {fmtFee(latest.routing_fee_week_sat)}</span>
              <span title="letzte 30 Tage">30d {fmtFee(latest.routing_fee_month_sat)}</span>
            </div>
          </div>

          <div className="flex items-center gap-1.5 pt-0.5 text-2xs text-fg-subtle">
            <Activity size={10} className="text-ai/70" /> {data.count} Datenpunkte im Fenster · „n/v" =
            feereport nicht lesbar (≠ 0 Income)
          </div>
        </div>
      )}
    </Card>
  );
}
