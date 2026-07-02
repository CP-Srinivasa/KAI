// @data-source: /dashboard/api/integrity
//
// Audit-Integritäts-KPI (#314, Audit-Layer-Slice / Konzept §8/§9). Ehrlich gegen
// das BESTEHENDE L3-Endpoint: disabled (default-off) / no_anchor (an, noch nichts
// verankert) / ok (Anchor gefunden; proof_available = OTS-Proof on-chain). Kein
// Fake, kein neuer Backend-Pfad. Eine von drei Truth-Layer-KPIs (#314): hier
// OTS-Anchoring; daneben ReplayStatusKpi (Portfolio-Rekonstruierbarkeit) und
// AuditChainKpi (Decision-Journal Tamper-Evidence).
import { Card, Badge } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { useApi } from "@/lib/useApi";
import { fetchIntegrity } from "@/lib/api";
import type { StatusKind } from "@/lib/status";

/** Integritäts-State (+ Proof-State) → kanonischer StatusKind. "disabled" wird
 *  separat als ruhiger muted-Badge gezeigt (nicht hierüber). Ehrlich: nur ein
 *  Bitcoin-confirmed Proof gilt als "verified" — ein bloß eingereichter (pending)
 *  OTS-Proof bleibt "pending", bis die Bitcoin-Attestation gemined ist.
 *  Pure/testbar. */
export function integrityStateToStatus(state: string, proofState: string): StatusKind {
  switch (state) {
    case "ok":
      return proofState === "confirmed" ? "verified" : "pending";
    case "no_anchor":
      return "pending";
    case "unavailable":
      return "degraded";
    default:
      return "unverified";
  }
}

export function AuditIntegrityKpi() {
  const q = useApi(fetchIntegrity, 60_000);
  const d = q.state === "ready" ? q.data : null;

  return (
    <Card padded>
      <div className="text-2xs uppercase tracking-wider text-fg-muted">Audit-Integrität (L3)</div>
      <div className="mt-1.5 flex items-center gap-2">
        {q.state === "error" ? (
          <StatusPill kind="critical" label="Endpoint-Fehler" />
        ) : d == null ? (
          <StatusPill kind="pending" label="lädt" />
        ) : d.state === "disabled" ? (
          <Badge tone="muted" dot title="OpenTimestamps-Anchoring ist default-off.">
            deaktiviert
          </Badge>
        ) : (
          <StatusPill
            kind={integrityStateToStatus(d.state, d.proof_state)}
            label={
              d.state === "ok"
                ? d.proof_state === "confirmed"
                  ? "Bitcoin-verankert"
                  : d.proof_state === "pending"
                    ? "OTS pending"
                    : d.proof_available
                      ? "OTS-Proof"
                      : "aufgezeichnet"
                : d.state === "no_anchor"
                  ? "kein Anchor"
                  : d.state
            }
          />
        )}
      </div>
      <div className="mt-1 text-2xs text-fg-subtle">
        {d?.state === "ok" ? (
          <span className="font-mono break-all">
            {d.anchor_count} Anchor{d.anchor_count === 1 ? "" : "s"}
            {d.last_anchored_at ? ` · ${d.last_anchored_at.substring(0, 16).replace("T", " ")}` : ""}
            {d.proof_state === "confirmed"
              ? ` · Bitcoin #${d.bitcoin_height ?? "?"}`
              : d.proof_state === "pending"
                ? " · wartet auf Bitcoin-Bestätigung"
                : d.proof_available
                  ? " · OTS-Proof"
                  : " · noch kein Proof"}
          </span>
        ) : d?.state === "no_anchor" ? (
          <span>aktiviert, noch nichts verankert</span>
        ) : d?.state === "disabled" ? (
          <span>L3-Anchoring aus (default-off)</span>
        ) : d?.reason ? (
          <span className="break-words">{d.reason}</span>
        ) : null}
      </div>
    </Card>
  );
}
