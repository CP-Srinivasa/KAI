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

export const ROADMAP_SNAPSHOT_DATE = "2026-07-04";

export const ROADMAPS: Roadmap[] = [
  {
    id: "truth-platform",
    title: "Research-/Truth-Plattform (ADR-0012)",
    subtitle: "Prä-registrieren → messen → mechanisch urteilen → versiegeln",
    phases: [
      {
        id: "t0",
        label: "T0 · Pivot: Alpha-These selbst widerlegt",
        status: "done",
        note: "canonical-edge NO_GO terminal (episoden-dedupliziert n=104; Detail via canonical-edge CLI); Momentum n=178 + Execution-Alpha + news_direction (terminal_dead) falsifiziert",
      },
      {
        id: "t1",
        label: "T1 · Wahrheitskette live",
        status: "done",
        note: "hash-verkettetes Attestations-Ledger (30 Records) + Prä-Reg-Ledger (8 Hyp., Gate-JSON in ID) + Familien-Stop-Rule als Code + tägliche OTS-Bitcoin-Anker",
      },
      {
        id: "t2",
        label: "T2 · Extern nachrechenbar",
        status: "done",
        note: "Zweitmaschinen-Repro byte-exakt (Linux/ARM/Py3.12 → Win/x64/Py3.13); Input-Pinning + --verify <seq> Ein-Kommando-Verifikation + dependency-freies Repro-Bundle",
      },
      {
        id: "t3",
        label: "T3 · Publikation öffentlich",
        status: "done",
        note: "kai-trader.org/paper live (Methodik + K1-Pilot-Audit, EN, KAI-Lightning) + /oracle/verdicts & /oracle/fee-series öffentlich zahlbar (L402, 10 sat) — CF-Bypass 07-04",
      },
      {
        id: "t4",
        label: "T4 · M3: erstes externes Validierungssignal",
        status: "active",
        note: "Kriterium vorab versiegelt (erstes-von-drei: Extern-Repro ODER Fach-Feedback ODER zahlender Fremder; prereg c489079289070a8c, Revisit 2026-09-29); C1-Demand-Fenster 30 Tage ab Listing-Post",
      },
      {
        id: "t5",
        label: "T5 · Monetarisierung",
        status: "gated",
        note: "bewusst später-gegated (ADR-0012): erst externes Validierungssignal, dann Zuschnitt",
      },
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
      {
        id: "l45",
        label: "L4/L5 · Agentische Wert-Schicht",
        status: "done",
        note: "LIVE seit 07-01/04: Node „KAI“ + 400k-ACINQ-Channel · Lightning-Address kai@pay.kai-trader.org (erster externer Receive 25k sat) · L402-Paywall öffentlich · Spend nur via HOTP+Policy (Floor 1,84M), Dauerbetrieb auf invoice-only-Macaroon (A4-Split)",
      },
    ],
  },
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
];
