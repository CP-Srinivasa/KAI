// @data-source: /dashboard/api/integrity
//
// Audit-Integritäts-KPI (#314, Audit-Layer-Slice / Konzept §8/§9). Ehrlich gegen
// das BESTEHENDE L3-Endpoint: disabled (default-off) / no_anchor (an, noch nichts
// verankert) / ok (Anchor gefunden; proof_available = OTS-Proof on-chain). Kein
// Fake, kein neuer Backend-Pfad. Eine von drei Truth-Layer-KPIs (#314): hier
// OTS-Anchoring; daneben ReplayStatusKpi (Portfolio-Rekonstruierbarkeit) und
// AuditChainKpi (Attestation-Ledger Tamper-Evidence).
import { Card, Badge } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { useApi } from "@/lib/useApi";
import { fetchIntegrity } from "@/lib/api";
import type { StatusKind } from "@/lib/status";

/** Integritäts-State (+ Proof-State + Bitcoin-Prüfung) → kanonischer StatusKind.
 *  "disabled" wird separat als ruhiger muted-Badge gezeigt (nicht hierüber).
 *  Ehrlich (Audit A3): "verified" NUR, wenn der Proof eine Bitcoin-Attestation trägt
 *  (proof_state "confirmed") UND die Prüfung gegen den echten Blockheader für GENAU
 *  diesen Proof-Inhalt "verified" ergab. Eine Attestation allein ist nur behauptet.
 *  Pure/testbar. */
export function integrityStateToStatus(
  state: string,
  proofState: string,
  bitcoinVerification = "",
): StatusKind {
  switch (state) {
    case "ok":
      if (proofState !== "confirmed") return "pending";
      if (bitcoinVerification === "verified") return "verified";
      if (bitcoinVerification === "mismatch") return "critical";
      if (bitcoinVerification === "unverifiable") return "degraded";
      return "pending";
    case "no_anchor":
      return "pending";
    case "unavailable":
      return "degraded";
    default:
      return "unverified";
  }
}

/** Kurzes deutsches Label zum Status oben. Pure/testbar. */
export function integrityLabel(
  state: string,
  proofState: string,
  bitcoinVerification: string,
  proofAvailable: boolean,
): string {
  if (state === "no_anchor") return "kein Anchor";
  if (state !== "ok") return state;
  if (proofState === "confirmed") {
    if (bitcoinVerification === "verified") return "Bitcoin-geprüft";
    if (bitcoinVerification === "mismatch") return "Beweis UNGÜLTIG";
    if (bitcoinVerification === "unverifiable") return "nicht prüfbar";
    return "bestätigt, Bitcoin-Prüfung ausstehend";
  }
  if (proofState === "pending") return "OTS pending";
  return proofAvailable ? "OTS-Proof" : "aufgezeichnet";
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
            kind={integrityStateToStatus(d.state, d.proof_state, d.bitcoin_verification)}
            label={integrityLabel(
              d.state,
              d.proof_state,
              d.bitcoin_verification,
              d.proof_available,
            )}
          />
        )}
      </div>
      <div className="mt-1 text-2xs text-fg-subtle">
        {d?.state === "ok" ? (
          <span className="font-mono break-all">
            {d.anchor_count} Anchor{d.anchor_count === 1 ? "" : "s"}
            {d.last_anchored_at ? ` · ${d.last_anchored_at.substring(0, 16).replace("T", " ")}` : ""}
            {d.proof_state === "confirmed"
              ? ` · Bitcoin #${d.bitcoin_height ?? "?"}${
                  d.bitcoin_verification === "verified" ? " (Header geprüft)" : " (ungeprüft)"
                }`
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
