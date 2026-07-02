// @data-source: /dashboard/api/replay-status
//
// Replay-SSOT-Status-KPI (#314, Replay-Layer-Slice / Konzept §8/§9). Ehrlich
// gegen das Replay des Paper-Execution-Audits (artifacts/paper_execution_audit.jsonl):
// ok (sauberer Replay) / degraded (resiliente Skips oder Lifecycle-Fehler) /
// unavailable (Replay fehlgeschlagen/Datei fehlt) / warming (Cache kalt). Misst
// Replay-INTEGRITÄT, nicht Performance (PnL lebt im Portfolio-Block). Kein Fake.
import { Card } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { useApi } from "@/lib/useApi";
import { fetchReplayStatus } from "@/lib/api";
import type { StatusKind } from "@/lib/status";

/** Replay-State → kanonischer StatusKind. Pure/testbar. */
export function replayStateToStatus(state: string): StatusKind {
  switch (state) {
    case "ok":
      return "operational";
    case "degraded":
      return "degraded";
    case "unavailable":
      return "degraded";
    default:
      return "pending"; // warming / unbekannt
  }
}

export function ReplayStatusKpi() {
  const q = useApi(fetchReplayStatus, 60_000);
  const d = q.state === "ready" ? q.data : null;

  return (
    <Card padded>
      <div className="text-2xs uppercase tracking-wider text-fg-muted">Replay-SSOT</div>
      <div className="mt-1.5 flex items-center gap-2">
        {q.state === "error" ? (
          <StatusPill kind="critical" label="Endpoint-Fehler" />
        ) : d == null || d.warming || d.state === "warming" ? (
          <StatusPill kind="pending" label="lädt" />
        ) : (
          <StatusPill
            kind={replayStateToStatus(d.state)}
            label={
              d.state === "ok" ? "konsistent" : d.state === "degraded" ? "degradiert" : "unavailable"
            }
          />
        )}
      </div>
      <div className="mt-1 text-2xs text-fg-subtle">
        {d == null || d.warming ? null : d.state === "unavailable" ? (
          <span className="break-words">{d.reason || "Replay nicht verfügbar."}</span>
        ) : (
          <span className="font-mono">
            {d.positions} Pos · {d.fills_replayed} Fills · Skips {d.skipped_events} · LC-Err{" "}
            {d.lifecycle_errors}
          </span>
        )}
      </div>
    </Card>
  );
}
