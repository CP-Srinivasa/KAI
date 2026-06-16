// @data-source: /dashboard/api/lightning
//
// Node-/Chain-Status als KPI-Karte für die §8-Redaktion (UI-Update 2026.06, WP-1.4).
// Ehrlich gegen das BESTEHENDE Lightning-Endpoint: disabled (default-off) /
// unavailable (Grund) / ok (Sync + Blockhöhe). KEIN Fake — solange L1 nicht live
// ist, zeigt die Karte ehrlich "deaktiviert/unavailable". Audit-/Replay-/Truth-
// Layer-KPIs bleiben ausgespart (chain-integrity #273 ungemerged, kein Endpoint).
import { Card } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { Badge } from "@/components/ui/Primitives";
import { useApi } from "@/lib/useApi";
import { useCurrency } from "@/state/CurrencyProvider";
import { fetchLightningStatus } from "@/lib/api";

export function NodeStatusKpi() {
  const { fmtNum } = useCurrency();
  const q = useApi(fetchLightningStatus, 60_000);
  const d = q.state === "ready" ? q.data : null;

  return (
    <Card padded>
      <div className="text-2xs uppercase tracking-wider text-fg-muted">Node / Chain</div>
      <div className="mt-1.5 flex items-center gap-2">
        {q.state === "error" ? (
          <StatusPill kind="critical" label="Endpoint-Fehler" />
        ) : d == null ? (
          <StatusPill kind="pending" label="lädt" />
        ) : d.state === "ok" ? (
          <StatusPill kind="operational" label="Node ok" />
        ) : d.state === "unavailable" ? (
          <StatusPill kind="degraded" label="unavailable" />
        ) : (
          <Badge tone="muted" dot title="Read-only Lightning/Chain-Integration ist default-off.">
            deaktiviert
          </Badge>
        )}
      </div>
      <div className="mt-1 text-2xs text-fg-subtle">
        {d?.state === "ok" ? (
          <span className="font-mono">
            {d.synced_to_chain ? "synced" : "syncing"}
            {d.block_height > 0 ? ` · Block ${fmtNum(d.block_height)}` : ""}
            {` · ${d.num_active_channels} ch`}
          </span>
        ) : d?.state === "unavailable" ? (
          <span className="break-words">{d.reason || "Node nicht erreichbar."}</span>
        ) : d?.state === "disabled" ? (
          <span>read-only Integration aus (default-off)</span>
        ) : null}
      </div>
    </Card>
  );
}
