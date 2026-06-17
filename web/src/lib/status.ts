// Status-Sprache SSOT (UI-Update 2026.06, WP-0.1 / Konzept §22).
//
// EINE kanonische Quelle für die ~21 System-/Trading-/Daten-Zustände, die das
// Dashboard visuell unterscheidbar machen muss. Jeder Zustand bekommt genau:
//   - tone     : semantische Farbe (deckungsgleich mit Badge-Tönen)
//   - icon     : lucide-Glyph (Bedeutung auch ohne Farbe erkennbar — A11y)
//   - label    : kurzer Anzeigetext
//   - tooltip  : "Was bedeutet dieser Zustand?"
//   - action   : "empfohlene Aktion" (leer = keine Aktion nötig)
//   - severity : Dringlichkeitsrang (0 = höchste Aufmerksamkeit) für Sortierung
//
// Reine Logik, kein React — damit testbar (status.test.ts) und als Single-Source
// für <StatusPill> (components/ui/StatusPill.tsx) und alle künftigen WP-Karten.
// Baut bewusst auf den bestehenden Badge-Tönen auf; ersetzt Ad-hoc-Tönung NICHT
// rückwirkend, sondern bietet den gemeinsamen Nenner für neue Komponenten.

import {
  Activity,
  AlertCircle,
  AlertOctagon,
  AlertTriangle,
  Ban,
  Banknote,
  BadgeCheck,
  CheckCircle2,
  Copy,
  EyeOff,
  FlaskConical,
  HelpCircle,
  History,
  Hourglass,
  Lock,
  PauseCircle,
  PowerOff,
  Radio,
  ShieldCheck,
  XCircle,
  type LucideIcon,
} from "lucide-react";

/** Renderbare Töne — deckungsgleich mit BadgeTone in Primitives.tsx. */
export type StatusTone = "neutral" | "pos" | "neg" | "warn" | "info" | "ai" | "muted";

/** Kanonische Zustände aus Konzept §22. */
export type StatusKind =
  | "operational"
  | "idle"
  | "active"
  | "degraded"
  | "blocked"
  | "fail-closed"
  | "execution-off"
  | "write-back-locked"
  | "dry-run"
  | "paper"
  | "real"
  | "shadow"
  | "live"
  | "verified"
  | "unverified"
  | "stale"
  | "duplicate"
  | "rejected"
  | "pending"
  | "completed"
  | "urgent"
  | "critical";

export type StatusDescriptor = {
  kind: StatusKind;
  label: string;
  tone: StatusTone;
  icon: LucideIcon;
  tooltip: string;
  /** Empfohlene Operator-Aktion; "" wenn keine nötig. */
  action: string;
  /** 0 = höchste Dringlichkeit (kritisch zuerst), 21 = ruhigster Zustand. */
  severity: number;
};

// Reihenfolge der Tabelle = Dringlichkeit (severity = Index). Kritisches oben,
// gesunde/ruhige Zustände unten. Töne semantisch: neg = Gefahr/Echtgeld,
// warn = Achtung/kontrollierter Stopp, info = Aktivität/Lernmodus,
// pos = gesund/bestätigt, muted = bewusst-aus/neutral.
const ORDER: ReadonlyArray<Omit<StatusDescriptor, "severity">> = [
  {
    kind: "critical",
    label: "kritisch",
    tone: "neg",
    icon: AlertOctagon,
    tooltip: "Kritischer Zustand — unmittelbare Aufmerksamkeit nötig.",
    action: "Sofort prüfen und Ursache beheben.",
  },
  {
    kind: "urgent",
    label: "dringend",
    tone: "warn",
    icon: AlertCircle,
    tooltip: "Dringend — zeitnahes Handeln erforderlich, noch nicht kritisch.",
    action: "Zeitnah bearbeiten, bevor es eskaliert.",
  },
  {
    kind: "blocked",
    label: "blockiert",
    tone: "warn",
    icon: Ban,
    tooltip: "Ein Gate blockiert die Aktion bewusst (konservativ).",
    action: "Blockierendes Gate prüfen — ist die Blockade gewollt?",
  },
  {
    kind: "degraded",
    label: "degradiert",
    tone: "warn",
    icon: AlertTriangle,
    tooltip: "Teilweise funktionsfähig — eingeschränkter Betrieb.",
    action: "Eingeschränkte Komponente prüfen.",
  },
  {
    kind: "fail-closed",
    label: "fail-closed",
    tone: "warn",
    icon: Lock,
    tooltip: "Sicherer Stopp: bei Unsicherheit wird NICHT gehandelt (Default-Posture).",
    action: "Keine Aktion nötig; bewusstes Entsperren nur über Operator.",
  },
  {
    kind: "write-back-locked",
    label: "Write-Back gesperrt",
    tone: "warn",
    icon: Lock,
    tooltip: "Schreibender Pfad gesperrt — keine persistierenden Zustandsänderungen.",
    action: "Sperre nur bewusst aufheben (Schutzschalter).",
  },
  {
    kind: "execution-off",
    label: "Execution aus",
    tone: "muted",
    icon: PowerOff,
    tooltip: "Ausführung global abgeschaltet — keine neuen Orders/Positionen.",
    action: "Bewusst; Aktivierung nur über Operator.",
  },
  {
    kind: "unverified",
    label: "unverifiziert",
    tone: "warn",
    icon: HelpCircle,
    tooltip: "Nicht belegt/verifiziert — nicht als bestätigt lesen.",
    action: "Belegen oder als Datenlücke kennzeichnen.",
  },
  {
    kind: "stale",
    label: "veraltet",
    tone: "warn",
    icon: History,
    tooltip: "Daten sind veraltet — kein aktueller Stand.",
    action: "Quelle/Refresh prüfen.",
  },
  {
    kind: "rejected",
    label: "abgelehnt",
    tone: "warn",
    icon: XCircle,
    tooltip: "Abgelehnt (Gate/Filter/Validierung) — nicht weiterverarbeitet.",
    action: "Ablehnungsgrund prüfen, falls unerwartet.",
  },
  {
    kind: "duplicate",
    label: "Duplikat",
    tone: "muted",
    icon: Copy,
    tooltip: "Doppelter Eintrag — als Duplikat erkannt und übersprungen.",
    action: "Bei hoher Duplikatrate Quelle prüfen.",
  },
  {
    kind: "pending",
    label: "ausstehend",
    tone: "info",
    icon: Hourglass,
    tooltip: "In Bearbeitung / wartet auf Abschluss.",
    action: "",
  },
  {
    kind: "real",
    label: "Real",
    tone: "neg",
    icon: Banknote,
    tooltip: "Echtgeld-Pfad — muss bewusst und sichtbar sein.",
    action: "Nur mit Human-in-the-Loop und gesetzten Kapital-Gates.",
  },
  {
    kind: "live",
    label: "Live",
    tone: "neg",
    icon: Radio,
    tooltip: "Live-Modus — wirkt auf den echten Ausführungs-/Datenpfad.",
    action: "Bewusst betreiben; Live-Zustände nie unauffällig.",
  },
  {
    kind: "dry-run",
    label: "Dry-Run",
    tone: "info",
    icon: FlaskConical,
    tooltip: "Simulation — Aktionen werden nur protokolliert, nicht ausgeführt.",
    action: "",
  },
  {
    kind: "shadow",
    label: "Shadow",
    tone: "muted",
    icon: EyeOff,
    tooltip: "Schattenbetrieb — Messung ohne Wirkung (edge-gated, kein Kapital).",
    action: "",
  },
  {
    kind: "paper",
    label: "Paper",
    tone: "info",
    icon: FlaskConical,
    tooltip: "Paper-Trading — buchhalterisch, ohne Echtgeld.",
    action: "",
  },
  {
    kind: "active",
    label: "aktiv",
    tone: "info",
    icon: Activity,
    tooltip: "Verarbeitet gerade aktiv.",
    action: "",
  },
  {
    kind: "idle",
    label: "idle",
    tone: "muted",
    icon: PauseCircle,
    tooltip: "Bereit, wartet — kein aktueller Verarbeitungsanlass.",
    action: "",
  },
  {
    kind: "verified",
    label: "verifiziert",
    tone: "pos",
    icon: BadgeCheck,
    tooltip: "Belegt/verifiziert — bestätigter Zustand.",
    action: "",
  },
  {
    kind: "operational",
    label: "operational",
    tone: "pos",
    icon: ShieldCheck,
    tooltip: "Betriebsbereit und gesund.",
    action: "",
  },
  {
    kind: "completed",
    label: "abgeschlossen",
    tone: "pos",
    icon: CheckCircle2,
    tooltip: "Erfolgreich abgeschlossen.",
    action: "",
  },
];

/** Kanonische Registry: jeder StatusKind → vollständiger Deskriptor (mit severity). */
export const STATUS_REGISTRY: Record<StatusKind, StatusDescriptor> = ORDER.reduce(
  (acc, entry, idx) => {
    acc[entry.kind] = { ...entry, severity: idx };
    return acc;
  },
  {} as Record<StatusKind, StatusDescriptor>,
);

/** Deskriptor zu einem Zustand. */
export function getStatus(kind: StatusKind): StatusDescriptor {
  return STATUS_REGISTRY[kind];
}

/** Ton eines Zustands (für Nicht-Pill-Verwendung, z.B. Kartenrahmen). */
export function statusTone(kind: StatusKind): StatusTone {
  return STATUS_REGISTRY[kind].tone;
}

/** Dringlichkeitsrang (0 = höchste). */
export function statusSeverity(kind: StatusKind): number {
  return STATUS_REGISTRY[kind].severity;
}

/** Vergleicht zwei Zustände nach Dringlichkeit — kritischste zuerst. */
export function compareStatus(a: StatusKind, b: StatusKind): number {
  return statusSeverity(a) - statusSeverity(b);
}

/** Dringlichster Zustand aus einer Liste (oder null bei leer). */
export function mostUrgent(kinds: readonly StatusKind[]): StatusKind | null {
  if (kinds.length === 0) return null;
  return kinds.reduce((worst, k) => (statusSeverity(k) < statusSeverity(worst) ? k : worst));
}
