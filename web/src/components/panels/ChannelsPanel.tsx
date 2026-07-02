// @data-source: /dashboard/api/ln/channels
import { Waypoints, ShieldCheck, ShieldAlert, Power, Zap, ArrowDownLeft, ArrowUpRight, Hourglass } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { LiveDot } from "@/components/ui/LiveDot";
import { fetchLnChannels, type LnChannels, type LnChannel, type LnPendingChannel } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";

// Per-Channel-Aufschlüsselung (Phase 1, read-only, default-off). Zeigt EHRLICH
// disabled / unavailable / leer / ok — und pro Channel Outbound (senden) vs
// Inbound (empfangen) + Aktiv-Status. Kein schreibender/kapitalrelevanter Pfad
// (lnd listchannels). Ergänzt das aggregierte LightningPanel um die Detailsicht.

const POLL_MS = 60_000;

const fmtSats = (sat: number) => `${sat.toLocaleString("de-DE")} sats`;
const shortKey = (k: string) => (k.length > 16 ? `${k.slice(0, 8)}…${k.slice(-6)}` : k);

function LiquidityBar({ local, remote }: { local: number; remote: number }) {
  const total = local + remote;
  const localPct = total > 0 ? (local / total) * 100 : 0;
  const remotePct = total > 0 ? (remote / total) * 100 : 0;
  return (
    <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-bg-2">
      <div className="bg-ai/70" style={{ width: `${localPct}%` }} title={`outbound ${fmtSats(local)}`} />
      <div className="bg-info/70" style={{ width: `${remotePct}%` }} title={`inbound ${fmtSats(remote)}`} />
    </div>
  );
}

function ChannelRow({ ch }: { ch: LnChannel }) {
  return (
    <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <span
            className={ch.active ? "h-1.5 w-1.5 rounded-full bg-pos" : "h-1.5 w-1.5 rounded-full bg-warn"}
            title={ch.active ? "aktiv" : "inaktiv"}
            aria-hidden
          />
          <span className="truncate font-mono text-2xs text-fg-subtle" title={ch.remote_pubkey}>
            {shortKey(ch.remote_pubkey || ch.channel_id || "—")}
          </span>
        </div>
        <span className="font-mono tabular-nums text-2xs text-fg-subtle">{fmtSats(ch.capacity_sat)}</span>
      </div>
      <LiquidityBar local={ch.local_sat} remote={ch.remote_sat} />
      <div className="flex items-center justify-between gap-3 text-2xs">
        <span className="flex items-center gap-1 text-ai">
          <ArrowUpRight size={10} /> {fmtSats(ch.local_sat)}
        </span>
        <span className="flex items-center gap-1 text-info">
          <ArrowDownLeft size={10} /> {fmtSats(ch.remote_sat)}
        </span>
      </div>
    </div>
  );
}

function PendingRow({ ch }: { ch: LnPendingChannel }) {
  return (
    <div className="rounded-sm border border-warn/30 bg-warn/5 px-2.5 py-2 space-y-1">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 min-w-0">
          <Hourglass size={10} className="text-warn shrink-0" />
          <span className="truncate font-mono text-2xs text-fg-subtle" title={ch.remote_pubkey}>
            {shortKey(ch.remote_pubkey || "—")}
          </span>
        </span>
        <span className="font-mono tabular-nums text-2xs text-warn">{fmtSats(ch.capacity_sat)}</span>
      </div>
      <div className="flex items-center justify-between gap-2 text-2xs text-fg-subtle">
        <span>wartet auf Funding-Bestätigungen</span>
        <span className="truncate font-mono" title={ch.channel_point}>
          {ch.channel_point ? `${ch.channel_point.slice(0, 10)}…` : ""}
        </span>
      </div>
    </div>
  );
}

export function ChannelsPanel() {
  const polling = usePolling<LnChannels>(
    (signal) => fetchLnChannels(signal),
    { intervalMs: POLL_MS, pauseWhenHidden: true, retry: { maxAttempts: 3, baseMs: 2_000 } },
  );
  const data = polling.state === "ready" ? polling.data : null;

  const stateBadge =
    data == null ? null : data.state === "ok" ? (
      <Badge tone={data.num_pending > 0 ? "warn" : "pos"} dot>
        <ShieldCheck size={10} /> {data.num_channels} Channels
        {data.num_pending > 0 ? ` · ${data.num_pending} pending` : ""}
      </Badge>
    ) : data.state === "disabled" ? (
      <Badge tone="muted" dot>
        <Power size={10} /> deaktiviert
      </Badge>
    ) : (
      <Badge tone="warn" dot>
        <ShieldAlert size={10} /> nicht erreichbar
      </Badge>
    );

  return (
    <Card padded className="synthwave-pulse-edge overflow-hidden">
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            <Waypoints size={14} className="text-ai glow-ai shrink-0" />
            Channels & Liquidität
          </span>
        }
        subtitle="lnd listchannels · read-only (Phase 1)"
        right={
          <div className="flex items-center gap-2">
            <LiveDot
              state={polling.state}
              generatedAt={data ? data.generated_at : null}
              staleAfterMs={90_000}
              downAfterMs={240_000}
            />
            {stateBadge}
          </div>
        }
      />

      {polling.state === "loading" && (
        <div className="py-4 text-center text-xs text-fg-subtle">Lade Channels …</div>
      )}

      {polling.state === "error" && (
        <div className="rounded-sm border border-neg/30 bg-neg/5 px-3 py-2 text-2xs font-mono text-neg">
          Endpoint nicht erreichbar ({polling.error.kind}) ·{" "}
          <span className="font-mono">/dashboard/api/ln/channels</span>
        </div>
      )}

      {data?.state === "disabled" && (
        <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-3 py-2.5 text-2xs text-fg-muted leading-relaxed">
          Lightning ist <span className="text-fg">deaktiviert</span> (default-off). Sobald der Operator
          das Feature aktiviert, erscheint hier die Per-Channel-Liste mit Inbound/Outbound je Channel —
          read-only, kein schreibender Pfad.
        </div>
      )}

      {data?.state === "unavailable" && (
        <div className="rounded-sm border border-warn/30 bg-warn/10 px-3 py-2.5 attention-breathe-warn">
          <div className="text-sm font-semibold text-warn">Node aktiviert, aber nicht erreichbar</div>
          <p className="mt-1 break-words text-2xs font-mono text-fg-muted">{data.reason || "—"}</p>
        </div>
      )}

      {data?.state === "ok" && data.num_channels === 0 && data.num_pending === 0 && (
        <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-3 py-2.5 text-2xs text-fg-muted leading-relaxed">
          Node erreichbar, aber <span className="text-fg">keine offenen Channels</span>. Inbound/Outbound
          entstehen erst, wenn der Operator (gated) Liquidität aufbaut.
        </div>
      )}

      {data?.state === "ok" && data.num_pending > 0 && (
        <div className="space-y-1.5 pb-2">
          {data.pending.map((ch) => (
            <PendingRow key={ch.channel_point || ch.remote_pubkey} ch={ch} />
          ))}
        </div>
      )}

      {data?.state === "ok" && data.num_channels > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3 rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-1.5">
            <span className="flex items-center gap-1 text-2xs text-ai">
              <ArrowUpRight size={11} /> Outbound {fmtSats(data.total_local_sat)}
            </span>
            <span className="flex items-center gap-1 text-2xs text-info">
              <ArrowDownLeft size={11} /> Inbound {fmtSats(data.total_remote_sat)}
            </span>
          </div>
          <div className="space-y-1.5">
            {data.channels.map((ch) => (
              <ChannelRow key={ch.channel_id || ch.remote_pubkey} ch={ch} />
            ))}
          </div>
          <div className="flex items-center gap-1.5 pt-0.5 text-2xs text-fg-subtle">
            <Zap size={10} className="text-ai/70" /> Outbound = senden · Inbound = empfangen
          </div>
        </div>
      )}
    </Card>
  );
}
