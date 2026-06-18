// @data-source: /dashboard/api/lightning
import { Zap, ShieldCheck, ShieldAlert, Power } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { LiveDot } from "@/components/ui/LiveDot";
import { fetchLightningStatus, type LightningStatus } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";

// Lightning-Node-Status (Phase 1, read-only, default-off). Macht die Integration
// greifbar OHNE Fake: zeigt ehrlich disabled / unavailable / ok und — wenn der
// Node erreichbar ist — die getinfo-Detailfelder. Kein schreibender Pfad.
// 80er-Neon: synthwave-pulse-edge, glow-Badges, Mono-Zahlen, Zap-Glyph.

const POLL_MS = 60_000;

function Stat({ label, value, mono = true }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="text-2xs uppercase tracking-wider text-fg-subtle">{label}</span>
      <span className={mono ? "font-mono tabular-nums text-fg" : "text-fg"}>{value}</span>
    </div>
  );
}

const fmtSats = (sat: number) => `${sat.toLocaleString("de-DE")} sats`;
const fmtBtc = (sat: number) =>
  `${(sat / 1e8).toLocaleString("de-DE", { minimumFractionDigits: 8, maximumFractionDigits: 8 })} BTC`;

export function LightningPanel() {
  const polling = usePolling<LightningStatus>(
    (signal) => fetchLightningStatus(signal),
    { intervalMs: POLL_MS, pauseWhenHidden: true, retry: { maxAttempts: 3, baseMs: 2_000 } },
  );
  const data = polling.state === "ready" ? polling.data : null;

  const stateBadge =
    data == null ? null : data.state === "ok" ? (
      <Badge tone="pos" dot>
        <ShieldCheck size={10} /> erreichbar
      </Badge>
    ) : data.state === "pending" ? (
      <Badge tone="info" dot>
        <Zap size={10} /> lädt
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
            <Zap size={14} className="text-ai glow-ai shrink-0" />
            Lightning Node
          </span>
        }
        subtitle="RaspiBlitz/lnd · read-only (Phase 1)"
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
        <div className="py-4 text-center text-xs text-fg-subtle">Lade Node-Status …</div>
      )}

      {polling.state === "error" && (
        <div className="rounded-sm border border-neg/30 bg-neg/5 px-3 py-2 text-2xs font-mono text-neg">
          Status-Endpoint nicht erreichbar ({polling.error.kind}) ·{" "}
          <span className="font-mono">/dashboard/api/lightning</span>
        </div>
      )}

      {data?.state === "pending" && (
        <div className="py-4 text-center text-xs text-fg-subtle">
          Node-Status wird geladen … <span className="text-2xs">(Hintergrund-Cache wärmt auf)</span>
        </div>
      )}

      {data?.state === "disabled" && (
        <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-3 py-2.5 text-2xs text-fg-muted leading-relaxed">
          Lightning ist <span className="text-fg">deaktiviert</span> (default-off). Phase-1-Adapter
          ist verdrahtet und read-only — sobald der Operator das Feature aktiviert, erscheint hier
          der Live-Node-Status (kein schreibender/kapitalrelevanter Pfad).
        </div>
      )}

      {data?.state === "unavailable" && (
        <div className="rounded-sm border border-warn/30 bg-warn/10 px-3 py-2.5 attention-breathe-warn">
          <div className="text-sm font-semibold text-warn">Node aktiviert, aber nicht erreichbar</div>
          <p className="mt-1 break-words text-2xs font-mono text-fg-muted">{data.reason || "—"}</p>
        </div>
      )}

      {data?.state === "ok" && (
        <div className="space-y-1">
          {!data.info_available && (
            <div className="mb-2 rounded-sm border border-info/30 bg-info/10 px-2.5 py-1.5 text-2xs text-info">
              Node erreichbar (server_state {data.server_state || "—"}) · getinfo-Details noch
              ausstehend{data.reason ? ` — ${data.reason}` : ""}
            </div>
          )}
          {data.alias && <Stat label="Alias" value={data.alias} mono={false} />}
          <div className="flex items-baseline justify-between gap-3 py-0.5">
            <span className="text-2xs uppercase tracking-wider text-fg-subtle">Sync</span>
            <div className="flex items-center gap-1.5">
              <Badge tone={data.synced_to_chain ? "pos" : "warn"}>
                {data.synced_to_chain ? "chain" : "chain…"}
              </Badge>
              <Badge tone={data.synced_to_graph ? "pos" : "warn"}>
                {data.synced_to_graph ? "graph" : "graph…"}
              </Badge>
            </div>
          </div>
          <Stat label="Block" value={data.block_height ? data.block_height.toLocaleString("de-DE") : "—"} />
          <Stat label="Peers" value={String(data.num_peers)} />
          <Stat label="Channels" value={String(data.num_active_channels)} />
          {data.num_pending_channels > 0 && (
            <Stat label="Channels pending" value={String(data.num_pending_channels)} />
          )}

          {data.balances_available ? (
            <div className="mt-2 rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2 space-y-0.5">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-2xs uppercase tracking-wider text-fg-subtle">Funds · read-only</span>
                <span className="font-mono tabular-nums text-2xs text-fg-subtle">
                  {fmtBtc(data.wallet_confirmed_sat)}
                </span>
              </div>
              <Stat label="On-Chain" value={fmtSats(data.wallet_confirmed_sat)} />
              {data.wallet_total_sat !== data.wallet_confirmed_sat && (
                <Stat
                  label="On-Chain unbest."
                  value={fmtSats(data.wallet_total_sat - data.wallet_confirmed_sat)}
                />
              )}
              <Stat label="Channels outbound" value={fmtSats(data.channel_local_sat)} />
              <Stat label="Channels inbound" value={fmtSats(data.channel_remote_sat)} />
            </div>
          ) : data.info_available ? (
            <div className="mt-1 text-2xs text-fg-subtle">Balances ausstehend (read-only)</div>
          ) : null}

          {data.version && <Stat label="Version" value={data.version} mono={false} />}
          {data.identity_pubkey && (
            <div className="pt-1">
              <div className="text-2xs uppercase tracking-wider text-fg-subtle">Pubkey</div>
              <div className="break-all font-mono text-2xs text-fg-subtle">{data.identity_pubkey}</div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
