// Deklarative Roadmap-Daten (WP-2.2 / Konzept §19). WICHTIG/EHRLICH: es gibt KEIN
// strukturiertes Roadmap-Backend — diese Daten sind ein DEKLARATIVER Snapshot
// (dokumentierte Realität, gleiche Ehrlichkeitsstufe wie die L1-L5-Build-Badges
// der Node-Seite), KEINE live-berechneten Fortschritts-Metriken. Stand sichtbar
// gemacht über ROADMAP_SNAPSHOT_DATE; bei Änderungen hier pflegen.
import type { StatusKind } from "@/lib/status";
import type { Tone } from "@/lib/tone";

export type PhaseStatus = "done" | "active" | "planned" | "gated";

export type RoadmapPhase = {
  id: string;
  label: string;
  status: PhaseStatus;
  note?: string;
};

export type Roadmap = {
  id: string;
  title: string;
  subtitle: string;
  phases: RoadmapPhase[];
};

/** Phasen-Status → kanonischer StatusKind (für StatusPill). */
export function phaseStatusKind(s: PhaseStatus): StatusKind {
  switch (s) {
    case "done":
      return "completed";
    case "active":
      return "active";
    case "gated":
      return "blocked";
    case "planned":
    default:
      return "pending";
  }
}

/** Phasen-Status → Tone (für die TimelineRail-Segmente). */
export function phaseStatusTone(s: PhaseStatus): Tone {
  switch (s) {
    case "done":
      return "pos";
    case "active":
      return "info";
    case "gated":
      return "warn";
    case "planned":
    default:
      return "neutral";
  }
}

export const ROADMAP_SNAPSHOT_DATE = "2026-06-25";

export const ROADMAPS: Roadmap[] = [
  {
    id: "ui-2026-06",
    title: "Dashboard UI-Update 2026.06",
    subtitle: "Konzept-Sprint in 5 Phasen",
    phases: [
      { id: "p0", label: "Phase 0 · Design-System", status: "done", note: "Status-SSOT, Viz-Primitives, Explainer" },
      { id: "p1", label: "Phase 1 · Command Center", status: "done", note: "Command Header, Executive Snapshot, Akute Punkte, Node-KPI — live" },
      { id: "p2", label: "Phase 2 · Neue Seiten", status: "done", note: "Quellen ✓ · System ✓ · Node ✓ · Roadmaps ✓ — live" },
      { id: "p3", label: "Phase 3 · Per-Seite-Overhaul", status: "done", note: "Märkte/Signale/Trades/Portfolio/Alerts/Risiko/KI/Agenten — gemerged+deployed" },
      { id: "p4", label: "Phase 4 · Interaktion & Politur", status: "done", note: "Dichte/Fokus/Vollbild — live" },
    ],
  },
  {
    id: "sovereignty",
    title: "Souveränität — Bitcoin / Lightning / Truth",
    subtitle: "Vier Säulen der Wahrheits-Verankerung",
    phases: [
      { id: "l1", label: "L1 · Souveräne On-Chain-Wahrheit", status: "done", note: "live: eigener bitcoind-Provider, Fee-Shadow akkumuliert" },
      { id: "l3", label: "L3 · Audit-Integrität (OpenTimestamps)", status: "done", note: "live: OTS-Stamper + tägliche Anchor-/Upgrade-Timer aktiv" },
      { id: "l2", label: "L2 · On-Chain als 5. Bayes-Evidence", status: "active", note: "gebaut + läuft shadow-only (akkumuliert), inert/edge-gated" },
      { id: "l45", label: "L4/L5 · Agentische Wert-Schicht", status: "gated", note: "gebaut (U1–U5) + inert: Empfang kapitalfrei, Senden hinter Kapital-Gate" },
    ],
  },
];
