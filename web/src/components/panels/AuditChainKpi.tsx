// @data-source: /dashboard/api/audit-chain
//
// Audit-Chain Tamper-Evidence KPI (#314, Truth-Layer). Rechnet den Attestation-
// Ledger (artifacts/truth/attestation_ledger.jsonl) nach: ok (tamper-frei) / leer
// (noch nichts attestiert) / broken (Manipulation erkannt) / unavailable (Datei
// unlesbar). Dritte Truth-Layer-KPI neben Replay-Status (Portfolio-Rekonstruier-
// barkeit) und OTS-Integrity (On-Chain-Anchoring). Kein Fake, ehrlich gegen das
// /dashboard/api/audit-chain-Endpoint.
//
// Quelle bis 2026-09-04 war die Decision-Journal-Hash-Kette. Die hatte keinen
// Schreiber: das Panel versprach Tamper-Evidence über eine Kette, die nie befüllt
// wurde, und konnte nur "leer" zeigen. Jetzt zeigt es die Kette, die läuft.
import { Card, Badge } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { useApi } from "@/lib/useApi";
import { fetchAuditChain } from "@/lib/api";
import type { StatusKind } from "@/lib/status";

/** Chain-State → kanonischer StatusKind. "empty" wird separat als ruhiger
 *  muted-Badge gezeigt (nicht hierüber). Pure/testbar. */
export function auditChainStateToStatus(state: string): StatusKind {
  switch (state) {
    case "ok":
      return "verified";
    case "broken":
      return "critical";
    case "unavailable":
      return "degraded";
    default:
      return "unverified";
  }
}

export function AuditChainKpi() {
  const q = useApi(fetchAuditChain, 60_000);
  const d = q.state === "ready" ? q.data : null;

  return (
    <Card padded>
      <div className="text-2xs uppercase tracking-wider text-fg-muted">
        Truth-Ledger (Tamper)
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        {q.state === "error" ? (
          <StatusPill kind="critical" label="Endpoint-Fehler" />
        ) : d == null ? (
          <StatusPill kind="pending" label="lädt" />
        ) : d.state === "empty" ? (
          <Badge tone="muted" dot title="Noch keine Attestierung im Truth-Ledger.">
            leer
          </Badge>
        ) : (
          <StatusPill
            kind={auditChainStateToStatus(d.state)}
            label={d.state === "ok" ? "tamper-frei" : d.state === "broken" ? "Tamper!" : d.state}
          />
        )}
      </div>
      <div className="mt-1 text-2xs text-fg-subtle">
        {d?.state === "ok" ? (
          <span className="font-mono break-all">
            {d.entries} Attestierung{d.entries === 1 ? "" : "en"} verkettet
          </span>
        ) : d?.state === "broken" ? (
          <span className="break-words">
            {d.errors} Tamper-Fehler{d.first_error ? ` · ${d.first_error}` : ""}
          </span>
        ) : d?.state === "empty" ? (
          <span>noch keine Attestierung</span>
        ) : d?.reason ? (
          <span className="break-words">{d.reason}</span>
        ) : null}
      </div>
    </Card>
  );
}
