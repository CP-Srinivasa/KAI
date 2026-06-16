// @data-source: /dashboard/api/chain
import { Bitcoin, ShieldCheck, ShieldAlert, Power } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { LiveDot } from "@/components/ui/LiveDot";
import { fetchChainStatus, type ChainStatus } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";

// L1 — souveräne On-Chain-Wahrheit aus KAIs EIGENER bitcoind (read-only, default-off).
// Ehrlich: zeigt disabled / unavailable / ok ohne Fake. Tip-Höhe + Sync + Fee +
// Mempool kommen aus der eigenen Node statt aus einer Dritt-API — und bleiben
// truthful, selbst wenn lnd gesperrt ist. Kein schreibender Pfad.
// 80er-Neon: info/cyan (Wahrheit), synthwave-pulse-edge, Mono-Zahlen.

const POLL_MS = 60_000;

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="text-2xs uppercase tracking-wider text-fg-subtle">{label}</span>
      <span className="font-mono tabular-nums text-fg">{value}</span>
    </div>
  );
}

export function ChainPanel() {
  const polling = usePolling<ChainStatus>((signal) => fetchChainStatus(signal), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const data = polling.state === "ready" ? polling.data : null;

  const stateBadge =
    data == null ? null : data.state === "ok" ? (
      <Badge tone="pos" dot>
        <ShieldCheck size={10} /> erreichbar
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
            <Bitcoin size={14} className="text-info glow-info shrink-0" />
            Bitcoin Node (L1)
          </span>
        }
        subtitle="eigene bitcoind · On-Chain-Wahrheit · read-only"
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
        <div className="py-4 text-center text-xs text-fg-subtle">Lade Chain-Status …</div>
      )}

      {polling.state === "error" && (
        <div className="rounded-sm border border-neg/30 bg-neg/5 px-3 py-2 text-2xs font-mono text-neg">
          Chain-Endpoint nicht erreichbar ({polling.error.kind}) ·{" "}
          <span className="font-mono">/dashboard/api/chain</span>
        </div>
      )}

      {data?.state === "disabled" && (
        <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-3 py-2.5 text-2xs text-fg-muted leading-relaxed">
          Chain-Truth ist <span className="text-fg">deaktiviert</span> (default-off). Der L1-Adapter
          (<span className="font-mono">app/chain</span>, bitcoind read-only) ist verdrahtet — sobald
          der Operator <span className="font-mono">APP_CHAIN_*</span> wirt + das Feature aktiviert,
          erscheint hier Höhe/Sync/Fee/Mempool aus der eigenen Node.
        </div>
      )}

      {data?.state === "unavailable" && (
        <div className="rounded-sm border border-warn/30 bg-warn/10 px-3 py-2.5 attention-breathe-warn">
          <div className="text-sm font-semibold text-warn">bitcoind aktiviert, aber nicht erreichbar</div>
          <p className="mt-1 break-words text-2xs font-mono text-fg-muted">{data.reason || "—"}</p>
        </div>
      )}

      {data?.state === "ok" && (
        <div className="space-y-1">
          <div className="flex items-baseline justify-between gap-3 py-0.5">
            <span className="text-2xs uppercase tracking-wider text-fg-subtle">Netz</span>
            <Badge tone="info">{data.chain || "—"}</Badge>
          </div>
          <div className="flex items-baseline justify-between gap-3 py-0.5">
            <span className="text-2xs uppercase tracking-wider text-fg-subtle">Sync</span>
            <Badge tone={data.synced ? "pos" : "warn"}>
              {data.synced ? "synced" : "syncing"}
            </Badge>
          </div>
          <Stat label="Tip-Höhe" value={data.blocks ? data.blocks.toLocaleString("de-DE") : "—"} />
          {data.headers !== data.blocks && (
            <Stat label="Headers" value={data.headers ? data.headers.toLocaleString("de-DE") : "—"} />
          )}
          <Stat
            label="Fee (≈6 Blk)"
            value={data.fee_sat_vb != null ? `${data.fee_sat_vb.toFixed(1)} sat/vB` : "—"}
          />
          <Stat label="Mempool" value={`${data.mempool_tx.toLocaleString("de-DE")} tx`} />
        </div>
      )}
    </Card>
  );
}
