// @data-source: /dashboard/api/lightning + /dashboard/api/chain (L1) + /dashboard/api/integrity (L3)
import type { ReactNode } from "react";
import {
  Bitcoin,
  Anchor,
  Waypoints,
  Coins,
  ShieldCheck,
  Lock,
  Network,
  FileSignature,
  Clock,
  Database,
} from "lucide-react";
import { PageHeader } from "@/layout/PageHeader";
import { Card, Badge, SectionLabel, InfoHint } from "@/components/ui/Primitives";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { LightningPanel } from "@/components/panels/LightningPanel";
import { ChannelsPanel } from "@/components/panels/ChannelsPanel";
import { NodeReputationPanel } from "@/components/panels/NodeReputationPanel";
import { LnOpsAuditPanel } from "@/components/panels/LnOpsAuditPanel";
import { LnControlPanel } from "@/components/panels/LnControlPanel";
import { BlitzInfoPanel } from "@/components/panels/BlitzInfoPanel";
import { ChainPanel } from "@/components/panels/ChainPanel";
import { AuditIntegrityKpi } from "@/components/panels/AuditIntegrityKpi";
import { cn } from "@/lib/utils";

// Node & Chain — KAIs souveräne Bitcoin/Lightning-Schicht, ehrlich phasiert.
//
// Designprinzip (KAI-Doktrin): KEINE Fake-Live-Zahlen. Jede Kachel rendert
// ihren WAHREN Stand:
//   - Lightning  → echter /dashboard/api/lightning-Adapter, LIVE (Node erreichbar).
//   - L1 (Chain) → LIVE: APP_CHAIN_ENABLED, eigener bitcoind, Fee-Shadow akkumuliert.
//   - L3 (OTS)   → LIVE: APP_INTEGRITY_ENABLED, OTS-Stamper, tägliche Anchor-/Upgrade-Timer.
//   - L2 (5. Bayes-Evidence) → gebaut, shadow-only, akkumuliert (edge-gated).
//   - L4/L5 (Wert-Schicht) → LIVE hinter Policy-Envelope + HOTP-Confirm; der wahre
//     Gate-Zustand (pay_enabled) kommt aus der API, nie aus statischem Text.
// Rechtfertigung dieser Schicht = Souveränität/Integrität/Resilienz, NICHT ein
// romantisches Trading-Edge-Versprechen (Edge nur wenn shadow-bewiesen).

type PillarTone = "info" | "ai" | "warn";

const PILLAR_BAR: Record<PillarTone, string> = {
  info: "before:bg-info",
  ai: "before:bg-ai",
  warn: "before:bg-warn",
};

function Pillar({
  layer,
  title,
  icon,
  tone,
  badge,
  badgeTone,
  body,
  hint,
}: {
  layer: string;
  title: string;
  icon: ReactNode;
  tone: PillarTone;
  badge: string;
  badgeTone: "info" | "ai" | "warn" | "neg" | "pos";
  body: ReactNode;
  hint: ReactNode;
}) {
  return (
    <Card
      padded
      className={cn(
        "relative overflow-hidden pl-4",
        "before:absolute before:left-0 before:top-0 before:bottom-0 before:w-[3px] before:rounded-full",
        PILLAR_BAR[tone],
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={cn("shrink-0", tone === "info" ? "text-info" : tone === "ai" ? "text-ai" : "text-warn")}>
            {icon}
          </span>
          <div className="min-w-0">
            <div className="text-2xs font-mono uppercase tracking-wider text-fg-subtle">{layer}</div>
            <h3 className="text-sm font-semibold text-fg leading-tight flex items-center gap-1.5">
              {title}
              <InfoHint label={layer} hint={hint} />
            </h3>
          </div>
        </div>
        <Badge tone={badgeTone}>{badge}</Badge>
      </div>
      <p className="mt-2 text-xs text-fg-muted leading-relaxed">{body}</p>
    </Card>
  );
}

function UseCase({
  id,
  title,
  icon,
  body,
  chip,
  chipTone,
}: {
  id: string;
  title: string;
  icon: ReactNode;
  body: string;
  chip: string;
  chipTone: "pos" | "warn" | "info";
}) {
  return (
    <Card padded className="h-full">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-info shrink-0">{icon}</span>
          <div className="min-w-0">
            <div className="text-2xs font-mono uppercase tracking-wider text-fg-subtle">{id}</div>
            <h3 className="text-sm font-semibold text-fg leading-tight">{title}</h3>
          </div>
        </div>
        <Badge tone={chipTone}>{chip}</Badge>
      </div>
      <p className="mt-2 text-xs text-fg-muted leading-relaxed">{body}</p>
    </Card>
  );
}

export function NodePage() {
  return (
    <div className="p-5 xl:p-6 space-y-6 max-w-[1680px] mx-auto">
      <PageHeader
        title="Node & Chain"
        sub="KAIs souveräne Bitcoin- & Lightning-Schicht — Wahrheit, Integrität, Resilienz"
        tone="info"
        icon={<Bitcoin size={18} />}
        right={
          <Badge tone="info" dot>
            <ShieldCheck size={10} /> Truth Node
          </Badge>
        }
      />

      {/* Truth-Node-Framing — ehrlicher Rahmen, kein Edge-Versprechen */}
      <Card padded className="glow-border-accent bg-bg-1">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div className="min-w-0">
            <SectionLabel>Positionierung</SectionLabel>
            <p className="mt-1 text-sm text-fg leading-relaxed">
              KAI entwickelt sich vom API-abhängigen Signal-Tool zur{" "}
              <span className="text-info font-semibold">selbst-souveränen, wahrheits-verankerten</span>{" "}
              Krypto-Intelligenz. Eigene volle <span className="font-mono text-fg">bitcoind</span> +
              funded <span className="font-mono text-fg">lnd</span> sind die physische Verkörperung der
              DNA: Fail-Closed, keine blinde Vorhersage, Replay-SSOT, keine Drittanbieter-Abhängigkeit.
            </p>
          </div>
        </div>
        <div className="mt-3 rounded-sm border border-warn/25 bg-warn/5 px-3 py-2 text-2xs text-fg-muted leading-relaxed">
          <span className="font-semibold text-warn">Ehrlich:</span> Die kurzfristige Rechtfertigung
          ist <span className="text-fg">Souveränität, Integrität, Resilienz</span> — nicht ein
          romantisches Trading-Edge-Versprechen. On-Chain-Edge zählt nur, wenn er shadow-bewiesen ist.
        </div>
      </Card>

      {/* Vier Säulen — je echter Build-/Gate-Status, kein Fake */}
      <section className="space-y-3">
        <SectionLabel>Vier Säulen der Souveränität</SectionLabel>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Pillar
            layer="L1"
            title="Souveräne On-Chain-Wahrheit"
            icon={<Database size={18} />}
            tone="info"
            badge="live"
            badgeTone="info"
            body={
              <>
                Eigener Chain-Provider über <span className="font-mono text-fg">bitcoind</span>, Mempool
                und Fees. CostModel und Truth-Layer aus eigener Node statt Schätzung/API.
              </>
            }
            hint="LIVE: APP_CHAIN_ENABLED=true gegen den eigenen bitcoind (10.27.0.51), read-only JSON-RPC (PR #273/#313). /dashboard/api/chain + Live-Panel zeigen echte Höhe/Sync/Fee/Mempool; Fee-Shadow-Stream akkumuliert."
          />
          <Pillar
            layer="L3"
            title="Audit-Integrität (OpenTimestamps)"
            icon={<Anchor size={18} />}
            tone="info"
            badge="live"
            badgeTone="info"
            body={
              <>
                Täglicher On-Chain-Anchor des Audit-Digests. KAI beweist kryptographisch, dass seine
                Aufzeichnungen (Replay-SSOT) unverändert sind.
              </>
            }
            hint="LIVE: APP_INTEGRITY_ENABLED=true, OTS-Stamper (echte .ots seit #415), tägliche Anchor- + ots-upgrade-Timer aktiv. Verankert artifacts/paper_execution_audit.jsonl."
          />
          <Pillar
            layer="L2"
            title="On-Chain als 5. Bayes-Evidence"
            icon={<Waypoints size={18} />}
            tone="ai"
            badge="gebaut · shadow-only"
            badgeTone="ai"
            body={
              <>
                Mempool- und Fee-Perzentile aus der eigenen Node als 5. Evidence (neben Funding, OI,
                LS, Hype) — richtungs-agnostisch, edge-gated. Keine Kapitalfreigabe ohne Beweis.
              </>
            }
            hint="GEBAUT + läuft: APP_L2_EVIDENCE_ENABLED=true, shadow-only/inert (direction_aligned=0 → LLR=0, null Sizing-Impact, mathematisch erzwungen), akkumuliert l2_evidence_shadow.jsonl. Evaluator (evaluate_l2_evidence.py, moving-block-bootstrap autokorrelations-robust) ready; Trust-Promote erst nach Edge-Beweis (operator-gated)."
          />
          <Pillar
            layer="L4/L5"
            title="Agentische Wert-Schicht"
            icon={<Lock size={18} />}
            tone="warn"
            badge="LIVE · policy-gebremst"
            badgeTone="warn"
            body={
              <>
                L402 + Empfang + Channel-Open sind <span className="font-mono text-fg">live</span> —
                jede kapital-wirksame Aktion läuft durch Policy-Envelope (Caps + Reserve-Floor) und
                HOTP-Confirm. Den echten Kill-Switch-Zustand zeigt die LN-Steuerung unten.
              </>
            }
            hint="Seit 2026-07-01 operator-armiert: pay/receive/l402_enabled=true, Cockpit-Macaroon + HOTP-Seed provisioniert, enge Policy (allowed_actions, per-action-/daily-Cap, reserve_floor, confirm_threshold=1 ⇒ HOTP auf JEDEM Spend). Erster Channel (400k, ACINQ) über genau diesen Pfad eröffnet. Rollback: APP_LN_PAY_ENABLED=false."
          />
        </div>
      </section>

      {/* Live + Phase-2 — asymmetrisch: echtes Live-Panel links, ehrliche Roadmap rechts */}
      <section className="space-y-3">
        <SectionLabel>Node-Status</SectionLabel>
        <div className="grid gap-3 lg:grid-cols-2">
          <div className="space-y-3">
            <ChainPanel />
            <LightningPanel />
            <div className="rounded-sm border border-info/25 bg-info/5 px-3 py-2.5 text-2xs text-fg-muted leading-relaxed">
              <span className="flex items-center gap-1.5 font-semibold text-info">
                <Network size={11} /> Wiring-Stand (verifiziert · 2026-07-02)
                <InfoHint
                  label="Read- UND Value-Wiring live"
                  hint="Read-Pfad LIVE (Macaroon + base_url + TLS in der Prod-.env auf .23). Wert-Schicht seit 2026-07-01 operator-armiert: Cockpit-Macaroon (invoices+channel-write, NIE admin), HOTP-Seed, Policy-Envelope. Der wahre Kill-Switch-Zustand (pay_enabled) kommt live aus der API und steht im LN-Steuerung-Panel — dieser Text behauptet keinen Gate-Zustand."
                />
              </span>
              <p className="mt-1">
                Node-seitig laufen <span className="font-mono text-fg">bitcoind</span> +{" "}
                <span className="font-mono text-fg">lnd</span> (Hybrid: Clearnet + Tor). Read-Adapter{" "}
                <span className="font-mono text-pos">live</span>; Wert-Schicht{" "}
                <span className="font-mono text-pos">armiert</span> hinter Policy-Envelope + HOTP —
                erster Channel-Open (400k, ACINQ) lief 07-01 über genau diesen Pfad. Live-Zustand von{" "}
                <span className="font-mono text-fg">pay_enabled</span> zeigt die LN-Steuerung rechts.
              </p>
            </div>
            <BlitzInfoPanel />
            <AuditIntegrityKpi />
          </div>

          <div className="space-y-3">
            <ChannelsPanel />
            <PreparedPanel
              title="B2-Funds-Recovery"
              reason="On-Chain-Balance (confirmed/unconfirmed) und Channel-Inbound/Outbound sind jetzt LIVE (read-only) im Lightning- und Channels-Panel. Offen bleibt der Recovery-Status der letzten Channel-Mittel."
              detail={
                <>
                  B2 (<span className="font-mono">sweeptimelock</span>) der letzten ~306k sats —
                  erscheint künftig als ehrlicher <span className="font-mono">recovering/syncing</span>-
                  Zustand, getrennt von bereits bestätigter Balance. Keine Fake-Zahl.
                </>
              }
              status="roadmap"
              roadmapNote="Phase-2 (Resilienz-Sprint): SCB-Monitoring + B2-Recovery-Status (operator-exekutiert)."
            />
            <NodeReputationPanel />
            <LnControlPanel />
            <LnOpsAuditPanel />
          </div>
        </div>
      </section>

      {/* Use-Cases — warum jemand einen Channel zu KAI aufbaut */}
      <section className="space-y-3">
        <div className="flex items-center gap-1.5">
          <SectionLabel>Use-Cases — KAI als Truth Node</SectionLabel>
          <InfoHint
            label="Demand-Side"
            hint="Inbound-Channels folgen einem begehrten zahlbaren PRODUKT. KAIs Asset ist verifizierbare Wahrheit/Provenance/Reputation — NICHT Vorhersage. UC-3/4 fallen fast geschenkt aus L1/L3 ab."
          />
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <UseCase
            id="UC-1"
            title="Souveräner DLC-/Daten-Oracle"
            icon={<FileSignature size={18} />}
            body="Schnorr-Attestation verifizierbarer Fakten (BTC/USD@Block, Event-Outcomes) → Bitcoin-DLC-Settlement. Attestiert statt prognostiziert = identitäts-safe."
            chip="pre-edge-safe"
            chipTone="pos"
          />
          <UseCase
            id="UC-2"
            title="Truth/Intelligence-Oracle via L402"
            icon={<Coins size={18} />}
            body="Kalibrierte Wahrscheinlichkeit + On-Chain-Track-Record, für Mensch und Agent — sats-per-call. Der Intelligence-Teil bleibt gated."
            chip="Intelligence gated"
            chipTone="warn"
          />
          <UseCase
            id="UC-3"
            title="Timestamping-as-a-Service"
            icon={<Clock size={18} />}
            body="Notarization/Timestamping fremder Daten via OpenTimestamps — dieselbe L3-Maschinerie, die KAIs eigene Integrität verankert."
            chip="pre-edge-safe"
            chipTone="pos"
          />
          <UseCase
            id="UC-4"
            title="On-Chain-Daten-Oracle"
            icon={<Database size={18} />}
            body="Verifizierte Fakten zu Fees, Mempool und Tx-Finalität aus eigener Node — dieselbe L1-Maschinerie wie CostModel/Truth-Layer."
            chip="pre-edge-safe"
            chipTone="pos"
          />
        </div>
      </section>
    </div>
  );
}
