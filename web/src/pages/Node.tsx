// @data-source: /dashboard/api/lightning (live) · L1/L3 status = build-state (kein Live-Endpoint)
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
import { cn } from "@/lib/utils";

// Node & Chain — KAIs souveräne Bitcoin/Lightning-Schicht, ehrlich phasiert.
//
// Designprinzip (KAI-Doktrin): KEINE Fake-Live-Zahlen. Jede Kachel rendert
// ihren WAHREN Stand:
//   - Lightning-Liveness  → echter /dashboard/api/lightning-Adapter (Phase 1,
//     heute disabled/unavailable bis Macaroon in Prod-.env gewired ist).
//   - L1/L3               → Code gemerged (PR #273), default-off, NICHT
//     live-aktiviert, KEIN Dashboard-Endpoint → als Build-Status, nicht Live.
//   - Channels/Balance/Reputation → NICHT im Phase-1-Adapter → Phase-2-Roadmap.
//   - L2/L4/L5            → geplant bzw. hinter Kapital-Gate + Human-in-the-Loop.
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
            badge="gebaut · default-off"
            badgeTone="info"
            body={
              <>
                Eigener Chain-Provider über <span className="font-mono text-fg">bitcoind</span>, Mempool
                und Fees. CostModel und Truth-Layer aus eigener Node statt Schätzung/API.
              </>
            }
            hint="Code gemerged (PR #273, app/chain/, read-only JSON-RPC), default-off. Noch kein Wiring in market-data/CostModel und kein Dashboard-Endpoint → hier Build-Status, keine Live-Zahlen."
          />
          <Pillar
            layer="L3"
            title="Audit-Integrität (OpenTimestamps)"
            icon={<Anchor size={18} />}
            tone="info"
            badge="gebaut · default-off"
            badgeTone="info"
            body={
              <>
                Täglicher On-Chain-Anchor des Audit-Digests. KAI beweist kryptographisch, dass seine
                Aufzeichnungen (Replay-SSOT) unverändert sind.
              </>
            }
            hint="Stamper/Anchor gemerged (PR #273, app/integrity/), default-off. OTS-Lib + Live-Anchor-Aktivierung ausstehend."
          />
          <Pillar
            layer="L2"
            title="On-Chain als 5. Bayes-Evidence"
            icon={<Waypoints size={18} />}
            tone="ai"
            badge="geplant · Phase-2"
            badgeTone="ai"
            body={
              <>
                Mempool-, Fee- und Flow-Signale wie Funding, OI oder Hype als zusätzliche Evidence —
                shadow-first, edge-gated. Keine Kapitalfreigabe ohne Beweis.
              </>
            }
            hint="Analog HYPE-S1: default-off, shadow-only, trust erst nach Shadow-Auswertung. Noch nicht gebaut."
          />
          <Pillar
            layer="L4/L5"
            title="Agentische Wert-Schicht"
            icon={<Lock size={18} />}
            tone="warn"
            badge="gated · Kapital-Gate"
            badgeTone="neg"
            body={
              <>
                BOLT12-Empfang, L402 (sats-per-API-call), KAI-Intelligenz hinter L402. Strikt hinter
                zwei Kapital-Gates + Human-in-the-Loop.
              </>
            }
            hint="Kapital-Bewegung nur bei (a) Edge bewiesen n≳100/EV>0 UND (b) Kapital live. Echte Coins nie autonom — Operator führt aus."
          />
        </div>
      </section>

      {/* Live + Phase-2 — asymmetrisch: echtes Live-Panel links, ehrliche Roadmap rechts */}
      <section className="space-y-3">
        <SectionLabel>Node-Status</SectionLabel>
        <div className="grid gap-3 lg:grid-cols-2">
          <div className="space-y-3">
            <LightningPanel />
            <div className="rounded-sm border border-info/25 bg-info/5 px-3 py-2.5 text-2xs text-fg-muted leading-relaxed">
              <span className="flex items-center gap-1.5 font-semibold text-info">
                <Network size={11} /> Wiring-Stand (gemessen 2026-06-16)
                <InfoHint
                  label="Zwei Schritte bis Live"
                  hint="Live-Daten brauchen (1) Macaroon/base_url/TLS in der Prod-.env auf .23 UND (2) settings.lightning.enabled=true. Flag-Flip allein liefert nur 'unavailable: no macaroon'."
                />
              </span>
              <p className="mt-1">
                Node-seitig laufen <span className="font-mono text-fg">bitcoind</span> +{" "}
                <span className="font-mono text-fg">lnd</span> (WireGuard verbunden). Der Prod-Adapter
                liefert aktuell ehrlich <span className="font-mono text-warn">disabled</span> bzw.{" "}
                <span className="font-mono text-warn">unavailable: no macaroon</span> — das Credential-
                Wiring in die Prod-<span className="font-mono">.env</span> steht noch aus.
              </p>
            </div>
          </div>

          <div className="space-y-3">
            <PreparedPanel
              title="Channels & Liquidität (Inbound/Outbound)"
              reason="Wie viel kann KAI empfangen, wie viel senden? Welche Channels stehen, wie ist die Balance verteilt? Grundlage für BOLT12-Empfang und LSP-Strategie."
              detail={
                <>
                  Nicht im Phase-1-Adapter (heute <span className="font-mono">getinfo</span>-only:
                  state/sync/block/peers/active-channels/alias). Inbound/Outbound, Channel-Detail und
                  LSP/Amboss-Magma/Loop-Status brauchen eine Adapter-Erweiterung.
                </>
              }
              status="roadmap"
              roadmapNote="Phase-2: app/lightning/adapter um channelbalance + listchannels erweitern."
            />
            <PreparedPanel
              title="On-Chain-Balance & B2-Funds-Recovery"
              reason="Wie viel liegt on-chain in eigener bitcoind-Wallet? Wo steht die laufende Funds-Recovery der letzten Channel-Mittel?"
              detail={
                <>
                  On-Chain-Balance ist heute kein Adapter-Feld. B2 (
                  <span className="font-mono">sweeptimelock</span>) der letzten ~306k sats reift ~06-17 —
                  erscheint als ehrlicher <span className="font-mono">recovering/syncing</span>-Zustand,
                  getrennt von bereits bestätigter Balance. Keine Fake-Zahl.
                </>
              }
              status="roadmap"
              roadmapNote="Phase-2: Wallet-Balance-Feld + B2-Recovery-Status (operator-exekutiert)."
            />
            <PreparedPanel
              title="Node-Reputation"
              reason="Wie vertrauenswürdig ist die Node im Netz? On-Chain-Track-Record als Trust-Signal für Inbound-Channels und Oracle-Use-Cases."
              detail="Reputation/Track-Record-Aggregation ist kein Phase-1-Feld. Folgt aus L3-Anchor-Historie + Channel-Uptime."
              status="roadmap"
              roadmapNote="Phase-2: nach L3-Anchor-Live + Channel-Telemetrie."
            />
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
