// @data-source: /dashboard/api/node/blitz
import { MonitorCheck, Power, ShieldAlert } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { fetchNodeBlitz, type NodeBlitz, type NodeBlitzData } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";

// RaspiBlitz-Info-Spiegel: dieselbe Sicht wie der SSH-Info-Screen des Nodes
// (Version, CPU/Temp/RAM/SSD, bitcoind, lnd, Fee-Report, URI) — read-only über
// einen forced-command-SSH-Key, der NUR das Info-Skript ausführen kann. Fail-soft:
// disabled/unreachable wird ehrlich angezeigt statt mit alten Zahlen zu lügen.

const POLL_MS = 60_000;

const nv = "n/v";
const fmtNum = (n: number | null | undefined) => (n == null ? nv : n.toLocaleString("de-DE"));

function fmtUptime(s: number | null): string {
  if (s == null) return nv;
  const d = Math.floor(s / 86_400);
  const h = Math.floor((s % 86_400) / 3_600);
  return d > 0 ? `up ${d}d ${h}h` : `up ${h}h ${Math.floor((s % 3_600) / 60)}m`;
}

function Row({ label, value, tone }: { label: string; value: string; tone?: "pos" | "warn" }) {
  const toneCls = tone === "pos" ? "text-pos" : tone === "warn" ? "text-warn" : "text-fg";
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="shrink-0 text-2xs text-fg-subtle">{label}</span>
      <span className={`truncate text-right font-mono text-2xs ${toneCls}`}>{value}</span>
    </div>
  );
}

function BlitzBody({ d }: { d: NodeBlitzData }) {
  const b = d.bitcoind;
  const l = d.lnd;
  const syncOk = b.sync_pct != null && b.sync_pct >= 99.99;
  const memPct =
    d.mem_total_mb && d.mem_available_mb != null
      ? Math.round(((d.mem_total_mb - d.mem_available_mb) / d.mem_total_mb) * 100)
      : null;
  const uri = l.uris.length > 0 ? l.uris[0] : l.pubkey ? `${l.pubkey}@…` : nv;
  return (
    <div className="space-y-2.5">
      <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2 space-y-1">
        <Row
          label={`RaspiBlitz ${d.raspiblitz_version || ""}`.trim()}
          value={`${d.hostname || nv} · ${fmtUptime(d.uptime_seconds)}`}
        />
        <Row
          label="CPU / Temp"
          value={`load ${d.load ? d.load.map((x) => x.toFixed(2)).join(", ") : nv} · ${d.cpu_temp_c != null ? `${d.cpu_temp_c}°C` : nv}`}
          tone={d.cpu_temp_c != null && d.cpu_temp_c >= 75 ? "warn" : undefined}
        />
        <Row
          label="RAM / SSD"
          value={`${memPct != null ? `${memPct}% von ${fmtNum(d.mem_total_mb)}M` : nv} · ${d.disk_total_gb != null ? `${d.disk_total_gb}TB`.replace(/^(\d{4,})TB$/, "$1GB") : nv}${d.disk_used_pct != null ? ` ${d.disk_used_pct}%` : ""}`}
        />
      </div>

      <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2 space-y-1">
        <Row
          label={`bitcoind ${b.version || ""}`.trim()}
          value={`${b.chain || nv} · ${fmtNum(b.peers)} peers`}
        />
        <Row
          label="Blocks"
          value={`${fmtNum(b.blocks)}/${fmtNum(b.headers)} · Sync ${b.sync_pct != null ? `${b.sync_pct.toFixed(2)}%` : nv}`}
          tone={syncOk ? "pos" : "warn"}
        />
      </div>

      <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2 space-y-1">
        <Row
          label={`LND ${l.version ? l.version.split(" ")[0] : nv}`}
          value={`Wallet ${fmtNum(l.wallet_confirmed_sat)} sat${l.wallet_unconfirmed_sat ? ` (+${fmtNum(l.wallet_unconfirmed_sat)} unbest.)` : ""}`}
        />
        <Row
          label="Channels"
          value={`${fmtNum(l.active_channels)} aktiv · ${fmtNum(l.pending_channels)} pending · ${fmtNum(l.peers)} peers`}
          tone={(l.pending_channels ?? 0) > 0 ? "warn" : undefined}
        />
        <Row
          label="Liquidität"
          value={`out ${fmtNum(l.channel_local_sat)} · in ${fmtNum(l.channel_remote_sat)} sat`}
        />
        <Row
          label="Fee-Report (D-W-M)"
          value={`${fmtNum(l.fee_report.day_sat)}-${fmtNum(l.fee_report.week_sat)}-${fmtNum(l.fee_report.month_sat)} sat`}
        />
      </div>

      <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-1.5">
        <div className="text-2xs text-fg-subtle">Node-URI</div>
        <div className="break-all font-mono text-2xs text-fg-muted" title={uri}>
          {uri}
        </div>
      </div>
    </div>
  );
}

export function BlitzInfoPanel() {
  const polling = usePolling<NodeBlitz>((signal) => fetchNodeBlitz(signal), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const data = polling.state === "ready" ? polling.data : null;

  const badge =
    data == null ? null : data.available ? (
      <Badge tone="pos" dot>
        <MonitorCheck size={10} /> live
      </Badge>
    ) : data.reason === "disabled" ? (
      <Badge tone="muted" dot>
        <Power size={10} /> deaktiviert
      </Badge>
    ) : (
      <Badge tone="warn" dot>
        <ShieldAlert size={10} /> nicht erreichbar
      </Badge>
    );

  return (
    <Card padded className="overflow-hidden">
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            <MonitorCheck size={14} className="text-info shrink-0" />
            RaspiBlitz-Spiegel
          </span>
        }
        subtitle="System + Node 1:1 wie der SSH-Info-Screen · read-only forced-command"
        right={badge}
      />

      {polling.state === "loading" && (
        <div className="py-4 text-center text-xs text-fg-subtle">Lade Node-Snapshot …</div>
      )}

      {polling.state === "error" && (
        <div className="rounded-sm border border-neg/30 bg-neg/5 px-3 py-2 text-2xs font-mono text-neg">
          Endpoint nicht erreichbar ({polling.error.kind}) ·{" "}
          <span className="font-mono">/dashboard/api/node/blitz</span>
        </div>
      )}

      {data != null && !data.available && (
        <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-3 py-2.5 text-2xs text-fg-muted leading-relaxed">
          {data.reason === "disabled" ? (
            <>
              Spiegel ist <span className="text-fg">deaktiviert</span> (default-off). Aktivierung über{" "}
              <span className="font-mono">APP_LN_BLITZ_INFO_ENABLED=true</span> + forced-command-SSH-Key.
            </>
          ) : (
            <>
              Node-Snapshot nicht abrufbar: <span className="font-mono text-warn">{data.reason}</span>
            </>
          )}
        </div>
      )}

      {data?.available && data.data != null && <BlitzBody d={data.data} />}
    </Card>
  );
}
