// 2026-06-04 DALI Truth-Visibility-Sprint.
//
// Der P0-Truth-Layer (#147) trennt Backend-seitig sauber zwischen historischer
// Evidenz und aktueller 24h-Lage, markiert Re-Entry als expired, Priority-Lift
// als unbewiesen, Source-Reliability mit 0 trusted, Regime als read-only und
// Low-Priority als insufficient. Im Dashboard war davon kaum etwas sichtbar.
//
// Dieses Modul leitet aus den vorhandenen API-Feldern eine kompakte Liste von
// Truth-Status-Chips ab. Reine Logik, kein React — damit testbar (truthStatus.test.ts)
// und als Single-Source fuer die TruthStatusBar.

import type {
  DashboardQuality,
  DashboardRegime,
  PriorityGateSummary,
} from "@/lib/api";
import { PRIORITY_VERDICT_LABEL } from "@/lib/labels";

export type TruthTone = "critical" | "warn" | "info" | "readonly" | "ok" | "muted";

export type TruthChip = {
  key: string;
  /** Kurzes Label-Praefix, z.B. "Re-Entry". */
  label: string;
  /** Verdichteter Status-Wert, z.B. "abgelaufen" / "0 trusted". */
  value: string;
  tone: TruthTone;
  /** Erklaerung fuer Tooltip / aria. */
  hint: string;
};

// Tone-Rang fuer Sortierung: Kritisches zuerst, Gesundes/Read-only zuletzt.
const TONE_RANK: Record<TruthTone, number> = {
  critical: 0,
  warn: 1,
  info: 2,
  readonly: 3,
  ok: 4,
  muted: 5,
};

function reentryChip(quality: DashboardQuality | null): TruthChip {
  const status = quality?.reentry?.status;
  if (status === "expired") {
    return {
      key: "reentry",
      label: "Re-Entry",
      value: "abgelaufen",
      tone: "warn",
      hint: "Historisches Re-Entry-Ziel ist abgelaufen — Fortschritt bleibt Evidenz, ist aber kein aktueller Freigabezustand. Neue Gate-Definition erforderlich.",
    };
  }
  if (status === "active") {
    return {
      key: "reentry",
      label: "Re-Entry",
      value: "aktiv",
      tone: "info",
      hint: "Re-Entry-Ziel ist aktiv und laeuft auf den Stichtag zu.",
    };
  }
  return {
    key: "reentry",
    label: "Re-Entry",
    value: "unbestätigt",
    tone: "muted",
    hint: "Re-Entry-Status nicht aus den Quality-Daten ableitbar (DATEN-LÜCKE).",
  };
}

function paperEvidenceChip(quality: DashboardQuality | null): TruthChip {
  const ev = quality?.paper_evidence;
  const lifetime = ev?.fills_total ?? quality?.paper_fills_with_pnl ?? 0;
  const recent = ev?.fills_recent_24h ?? 0;
  // 144-vs-0: historische Lifetime-Fills sind KEIN aktueller 24h-Fortschritt.
  if (lifetime > 0 && recent === 0) {
    return {
      key: "paper",
      label: "Paper",
      value: `${lifetime} hist · 0 / 24h`,
      tone: "warn",
      hint: "Historische Paper-Evidence erfuellt, aber 0 Fills in den letzten 24h. Lifetime-Zahlen nicht als aktuellen Fortschritt lesen.",
    };
  }
  if (recent > 0) {
    return {
      key: "paper",
      label: "Paper",
      value: `${recent} / 24h · ${lifetime} hist`,
      tone: "info",
      hint: "Aktuelle 24h-Ausfuehrung vorhanden; Lifetime separat als historische Evidenz.",
    };
  }
  return {
    key: "paper",
    label: "Paper",
    value: `${lifetime} hist · — 24h`,
    tone: "muted",
    hint: "Keine 24h-Evidence verfuegbar; nur Lifetime-Zahlen.",
  };
}

export type PriorityVerdict = {
  /** Grossbuchstaben-Verdict fuer Badge. */
  verdict: string;
  tone: TruthTone;
  detail: string;
};

// Resolver fuer das Priority-Gate: kombiniert Heartbeat (lebt der Loop?) mit
// dem Quality-Verdict (ist High-P belegt?). 0 filled darf nie gruen wirken.
export function resolvePriorityVerdict(
  pg: PriorityGateSummary | null,
): PriorityVerdict {
  if (!pg) {
    return {
      verdict: "UNKNOWN",
      tone: "muted",
      detail: "Priority-Gate-Daten nicht geladen.",
    };
  }
  const hb = pg.heartbeat_status;
  if (hb === "unknown") {
    return {
      verdict: "HEARTBEAT UNKNOWN",
      tone: "critical",
      detail:
        pg.heartbeat_warning ??
        "Loop-Aktivitaet nicht verifiziert — 0 Fills ist KEIN Gesundheitssignal.",
    };
  }
  if (hb === "stale") {
    return {
      verdict: "STALE",
      tone: "warn",
      detail:
        pg.heartbeat_warning ??
        "Trading-Loop-Audit ist veraltet; aktuelle Aktivitaet nicht verifiziert.",
    };
  }
  const v = pg.priority_quality?.current_quality_verdict ?? "unverified";
  const label = PRIORITY_VERDICT_LABEL[v] ?? "UNKNOWN";
  if (v === "priority_validated") {
    // Validiert + Heartbeat aktiv: Gate blockiert bewusst, das ist erklaerbar.
    return {
      verdict: hb === "active_blocking" ? "ACTIVE BLOCKING" : "VALID",
      tone: "info",
      detail: "Gate arbeitet; High-P-Lift im aktuellen Fenster belegt.",
    };
  }
  return {
    verdict: label,
    tone: "warn",
    detail:
      pg.priority_quality?.warning ??
      "Priority-Gate blockiert, aber High-P ist im aktuellen Fenster nicht als Qualitaet belegt.",
  };
}

function priorityChip(pg: PriorityGateSummary | null): TruthChip {
  const r = resolvePriorityVerdict(pg);
  return {
    key: "priority",
    label: "Priority",
    value: r.verdict,
    tone: r.tone,
    hint: r.detail,
  };
}

function sourceReliabilityChip(quality: DashboardQuality | null): TruthChip {
  const rel = quality?.source_reliability;
  if (!rel || rel.status !== "ok") {
    return {
      key: "source",
      label: "Sources",
      value: "k. Daten",
      tone: "muted",
      hint: "Source-Reliability-Report noch nicht verfuegbar (DATEN-LÜCKE).",
    };
  }
  const trusted = rel.trusted_count ?? rel.tier_counts?.trusted ?? 0;
  if (trusted === 0) {
    return {
      key: "source",
      label: "Sources",
      value: "0 trusted",
      tone: "critical",
      hint: "0 vertrauenswuerdige Quellen — Quellenbasis aktuell nicht institutionell belastbar.",
    };
  }
  const status = rel.quality_status;
  if (status === "warning" || status === "stale") {
    return {
      key: "source",
      label: "Sources",
      value: `${trusted} trusted · warn`,
      tone: "warn",
      hint: rel.health_warning ?? "Quellen-Qualitaet eingeschraenkt.",
    };
  }
  return {
    key: "source",
    label: "Sources",
    value: `${trusted} trusted`,
    tone: trusted > 0 ? "ok" : "warn",
    hint: "Vertrauenswuerdige Quellen vorhanden.",
  };
}

function regimeChip(regime: DashboardRegime | null): TruthChip {
  if (!regime) {
    return {
      key: "regime",
      label: "Regime",
      value: "k. Daten",
      tone: "muted",
      hint: "Regime-Snapshot noch nicht verfuegbar.",
    };
  }
  // Read-only ist hier KEINE Warnung, sondern eine bewusste Einordnung:
  // Regime beeinflusst aktuell keine Trades.
  return {
    key: "regime",
    label: "Regime",
    value: "read-only",
    tone: "readonly",
    hint: "Markt-Regime ist read-only Diagnose — beeinflusst aktuell keine Trades (NO TRADE EFFECT).",
  };
}

function signalQualityChip(quality: DashboardQuality | null): TruthChip {
  if (!quality) {
    return {
      key: "signal",
      label: "Signal-Q",
      value: "k. Daten",
      tone: "muted",
      hint: "Quality-Report nicht geladen.",
    };
  }
  const lowP = quality.low_priority_hit_rate_pct;
  const lift = quality.priority_tier_lift_pct;
  if (lowP == null) {
    return {
      key: "signal",
      label: "Signal-Q",
      value: "Low-P insufficient",
      tone: "warn",
      hint: "Keine belastbare Low-P-Stichprobe — Priorisierung nicht als validiert lesen.",
    };
  }
  if (lift == null || lift <= 0) {
    return {
      key: "signal",
      label: "Signal-Q",
      value: "Lift unbewiesen",
      tone: "warn",
      hint: "Priority-Tier-Lift nicht positiv/belegt — High-P darf nicht als Qualitaet gelten.",
    };
  }
  return {
    key: "signal",
    label: "Signal-Q",
    value: "valid",
    tone: "ok",
    hint: "Low-P-Baseline vorhanden und Priority-Lift positiv.",
  };
}

// S6 (#157 Scope-Luecke): entry_mode-Badge inkl. der D-233-Modi. Tone-Logik:
// Kontradiktion = critical (fail-closed, Operator muss handeln); disabled ohne
// offene Routen = warn (alles zu); disabled mit Alias-Routen = info (Lernstroeme
// laufen kontrolliert); limited/learning-Modi = info; paper/probe = ok;
// live_* = critical zur Sichtbarkeit (Echtgeld-Modus nie unauffaellig).
function entryModeChip(quality: DashboardQuality | null): TruthChip {
  const rt = quality?.runtime;
  if (!rt || !rt.entry_mode) {
    return {
      key: "entry-mode",
      label: "Entry-Mode",
      value: "unbekannt",
      tone: "muted",
      hint: "Kein Runtime-Block vom Backend — Badge degradiert ehrlich statt zu raten.",
    };
  }
  const routes = rt.open_routes ?? [];
  const aliasCount = routes.filter((r) => r.alias_used).length;
  if ((rt.contradictions ?? []).length > 0) {
    return {
      key: "entry-mode",
      label: "Entry-Mode",
      value: `${rt.entry_mode} · KONTRADIKTION`,
      tone: "critical",
      hint: `Konfiguration widerspricht dem Modus — alle Routen fail-closed zu: ${(rt.contradictions ?? []).join(", ")}`,
    };
  }
  if (rt.entry_mode.startsWith("live")) {
    return {
      key: "entry-mode",
      label: "Entry-Mode",
      value: rt.entry_mode_label,
      tone: "critical",
      hint: "Live-Entry-Modus aktiv — Echtgeld-Pfad. Muss bewusst und sichtbar sein.",
    };
  }
  if (rt.entry_mode === "disabled") {
    if (routes.length === 0) {
      return {
        key: "entry-mode",
        label: "Entry-Mode",
        value: "disabled · alles zu",
        tone: "warn",
        hint: "Globaler Kill-Switch: keine Route darf neue Positionen oeffnen.",
      };
    }
    return {
      key: "entry-mode",
      label: "Entry-Mode",
      value: `disabled · ${routes.length} Route(n) via Ack`,
      tone: "info",
      hint:
        `Kill-Switch gesetzt; ${routes.map((r) => r.route).join(" + ")} laufen kontrolliert ueber ` +
        `${aliasCount} Three-Arm-Migrations-Alias(e) (D-233). Loop bleibt zu.`,
    };
  }
  return {
    key: "entry-mode",
    label: "Entry-Mode",
    value: rt.entry_mode_label,
    tone:
      rt.entry_mode === "paper_premium_limited" || rt.entry_mode === "paper_learning"
        ? "info"
        : "ok",
    hint: rt.autonomous_loop_open
      ? "Autonomer Loop darf neue Positionen oeffnen (paper)."
      : `Limitierter Paper-Modus (D-233): nur ${routes.map((r) => r.route).join(" + ")} offen, Loop zu.`,
  };
}

// S6: Canary-Attribution (Scope-Luecke aus #157). Verhindert, dass ein
// "gesund" aussehender Shadow-Strom unbemerkt wieder 100% Canary ist
// (Vorfallklasse 2026-06-03).
function shadowAttributionChip(quality: DashboardQuality | null): TruthChip {
  const att = quality?.shadow_attribution;
  if (!att) {
    return {
      key: "shadow-attribution",
      label: "Shadow 24h",
      value: "keine Daten",
      tone: "muted",
      hint: "Kein Attribution-Block vom Backend.",
    };
  }
  const { real_candidates_24h: real, probe_candidates_24h: probe } = att;
  if (real === 0 && probe > 0) {
    return {
      key: "shadow-attribution",
      label: "Shadow 24h",
      value: `0 real · ${probe} probe`,
      tone: "warn",
      hint: "Alle Shadow-Kandidaten der letzten 24h sind Canary/Loop-Proben — der echte Generator-Strom liefert gerade nichts (Feed/Flag pruefen).",
    };
  }
  if (real === 0 && probe === 0) {
    return {
      key: "shadow-attribution",
      label: "Shadow 24h",
      value: "0 Kandidaten",
      tone: "muted",
      hint: "Keine Shadow-Kandidaten in den letzten 24h.",
    };
  }
  return {
    key: "shadow-attribution",
    label: "Shadow 24h",
    value: `${real} real · ${probe} probe`,
    tone: "info",
    hint: "Echte Generator-Kandidaten (source=autonomous_generator) vs Canary-/Loop-Proben der letzten 24h — nur 'real' zaehlt fuer den Edge-Beweis.",
  };
}

/**
 * Leitet die kompakten Truth-Status-Chips fuer die obere Dashboard-Leiste ab.
 * Sortiert nach Dringlichkeit (critical zuerst), aber stabil innerhalb gleicher
 * Tone (Eingabereihenfolge bleibt erhalten).
 */
export function deriveTruthChips(
  quality: DashboardQuality | null,
  regime: DashboardRegime | null,
  priorityGate: PriorityGateSummary | null,
): TruthChip[] {
  const chips = [
    entryModeChip(quality),
    reentryChip(quality),
    paperEvidenceChip(quality),
    priorityChip(priorityGate),
    sourceReliabilityChip(quality),
    regimeChip(regime),
    signalQualityChip(quality),
    shadowAttributionChip(quality),
  ];
  return chips
    .map((chip, idx) => ({ chip, idx }))
    .sort((a, b) => {
      const t = TONE_RANK[a.chip.tone] - TONE_RANK[b.chip.tone];
      return t !== 0 ? t : a.idx - b.idx;
    })
    .map((entry) => entry.chip);
}

/** Hoechste Dringlichkeitsstufe ueber alle Chips — fuer die Leisten-Ueberschrift. */
export function highestTruthTone(chips: TruthChip[]): TruthTone {
  if (chips.length === 0) return "muted";
  return chips.reduce<TruthTone>(
    (worst, c) => (TONE_RANK[c.tone] < TONE_RANK[worst] ? c.tone : worst),
    "muted",
  );
}
