// Fixed Command Header (UI-Update 2026.06, WP-1.1 / Konzept §4A).
//
// Eine immer sichtbare, sticky Lage-Leiste mit dem KRITISCHSTEN at-a-glance:
// KAI-Live-Zustand (kompakter Glyph statt Dauerschleifen-Text), Backend-Health,
// der dringlichste Wahrheits-Status und die Zahl offener Warnungen — plus
// Report-Frische. Die ausführlichen Panels (TruthStatusBar, KaiLiveWidget,
// PremiumRuntimeBanner) bleiben darunter; dieser Header ist die verdichtete,
// nie wegscrollende Ebene. "Wenn alles wichtig ist, ist nichts wichtig" (§3):
// bewusst nur wenige, dafür sofort lesbare Signale.
import { Badge } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { useBackendHealth } from "@/lib/useBackendHealth";
import { useSharedPortfolioSnapshot } from "@/state/PortfolioSnapshotProvider";
import { exposureFromSnapshot } from "@/lib/exposureFromSnapshot";
import { formatAbsolute, formatRelative, parseIso } from "@/lib/time";
import {
  deriveTruthChips,
  highestTruthTone,
  type TruthTone,
} from "@/lib/truthStatus";
import {
  backendHealthToStatus,
  kaiStateToStatus,
  truthToneToStatusTone,
} from "@/lib/commandStatus";
import type {
  DashboardQuality,
  DashboardRegime,
  PriorityGateSummary,
} from "@/lib/api";
import type { KaiRuntimeState } from "@/kai/types";
import { cn } from "@/lib/utils";

const ATTENTION_TONES: ReadonlySet<TruthTone> = new Set<TruthTone>(["critical", "warn"]);

/** Ab hier ist der Report nicht mehr "eben", sondern alt. Der Poller laeuft
 *  alle 30 s; 5 Minuten sind zehn ausgefallene Runden — das ist keine
 *  Schwankung mehr, sondern ein Befund. */
const STALE_AFTER_MS = 5 * 60_000;

export function CommandHeader({
  kai,
  kaiError = false,
  quality,
  regime,
  priorityGate,
  qualityState,
}: {
  kai: KaiRuntimeState | null;
  /** true when /api/kai/state failed with an auth/permission error — render an
   *  honest "unavailable" pill instead of a permanent "lädt" (which would imply
   *  the request is still in flight). */
  kaiError?: boolean;
  quality: DashboardQuality | null;
  regime: DashboardRegime | null;
  priorityGate: PriorityGateSummary | null;
  qualityState: "loading" | "ready" | "error";
}) {
  const health = useBackendHealth();
  // Kein zusaetzlicher Abruf: der Snapshot liegt schon unter dem Provider der
  // Uebersicht. Er ist die EINZIGE belastbare Quelle fuer die Handelsfreigabe —
  // der Paper/Sim/Live-Schalter in der Topbar ist localStorage, kein Systemzustand.
  const snap = useSharedPortfolioSnapshot();
  const exposure = snap.state === "ready" ? exposureFromSnapshot(snap.data) : null;
  const chips = deriveTruthChips(quality, regime, priorityGate);
  const topChip = chips[0] ?? null;
  const attentionCount = chips.filter((c) => ATTENTION_TONES.has(c.tone)).length;
  const worst = highestTruthTone(chips);

  const generatedAt = quality?.generated_at ?? null;
  const parsed = parseIso(generatedAt);
  const ageMs = parsed ? Date.now() - parsed.getTime() : null;
  const stale = ageMs != null && ageMs > STALE_AFTER_MS;

  return (
    <div
      className={cn(
        // top-14 = Hoehe der sticky Topbar; z-10 bleibt unter deren z-20.
        "sticky top-14 z-10 -mx-4 mb-1 flex flex-wrap items-center gap-2 border-b px-4 py-2 backdrop-blur xl:-mx-5 xl:px-5",
        "bg-bg-0/85",
        worst === "critical" ? "border-neg/40" : worst === "warn" ? "border-warn/30" : "border-line-subtle",
      )}
    >
      <span className="text-2xs font-bold uppercase tracking-widest text-fg-subtle">KAI</span>

      {/* KAI-Live-Zustand — kompakter Status statt Dauerschleifen-Text.
          Phase-1-Stub wird ehrlich als Platzhalter gekennzeichnet, nicht als
          „Live ·"-Status getarnt (sonst stünde dort dauerhaft „Live · IDLE",
          obwohl der Zustand gar nicht aus echten System-Inputs abgeleitet ist). */}
      {kai ? (
        kai.is_stub ? (
          <span title="Phase-1-Platzhalter: KAI-Laufzeit-Zustand ist noch nicht an echte System-Inputs (Loop/Alerts/Exposure) verdrahtet und steht konstant auf IDLE — kein live abgeleiteter Status.">
            <StatusPill kind="pending" label={`KAI · Stub (P${kai.phase ?? 1})`} />
          </span>
        ) : (
          <StatusPill kind={kaiStateToStatus(kai.state)} label={`Live · ${kai.state}`} />
        )
      ) : kaiError ? (
        <span title="KAI-Status-Endpoint nicht erreichbar (Auth, Netzwerk oder Serverfehler). Kein live abgeleiteter Zustand — bewusst kein Handeln, und ausdrücklich kein erfundenes OFFLINE.">
          <StatusPill kind="fail-closed" label="KAI · n/v" />
        </span>
      ) : (
        <StatusPill kind="pending" label="Live · lädt" />
      )}

      {/* 2026-09-15 DALI v2.1: Drei GETRENNTE Aussagen, die vorher vermischt
          waren. "Backend v0.42" plus ein gruener Punkt neben einem Zeitstempel
          las sich als eine einzige Zusage "Live-Daten" — und der Handelsmodus
          stand anderswo (Topbar, Sidebar-Pille) und kam aus localStorage.
          Verbindung, Datenalter und Handelsfreigabe sind drei unabhaengige
          Tatsachen und werden jetzt auch so gelesen. Version gehoert in den
          Footer, nicht in die Lage-Leiste. */}

      {/* (1) Verbindung zum Backend. */}
      <StatusPill
        kind={backendHealthToStatus(health.state)}
        label={health.state === "connected" ? "Backend verbunden" : `Backend ${health.state}`}
      />

      {/* (2) Datenalter — relativ, mit ehrlicher Stale-Schwelle. Eine stehende
          Uhr ist gefaehrlicher als eine fehlende. */}
      <Badge
        tone={qualityState === "error" ? "neg" : stale ? "warn" : qualityState === "ready" ? "muted" : "neutral"}
        dot
        title={
          qualityState === "ready"
            ? `Report erzeugt: ${formatAbsolute(generatedAt)}` +
              (stale ? ` · aelter als ${Math.round(STALE_AFTER_MS / 60000)} min — Pipeline pruefen` : "")
            : "Datenalter unbekannt — der Quality-Report ist nicht abrufbar."
        }
      >
        {qualityState === "ready"
          ? `Daten ${formatRelative(generatedAt)}${stale ? " · veraltet" : ""}`
          : qualityState === "error"
            ? "Daten n/v"
            : "Daten laden …"}
      </Badge>

      {/* (3) Handelsfreigabe aus dem Backend — NICHT der lokale Ansichtsmodus. */}
      <Badge
        tone={exposure == null ? "neutral" : exposure.execution_enabled ? "warn" : "muted"}
        dot
        title={
          exposure == null
            ? "Handelsfreigabe unbekannt: der Portfolio-Snapshot antwortet nicht. Bewusst kein 'aus' behaupten."
            : exposure.execution_enabled
              ? "Backend fuehrt Orders aus (execution_enabled=true)." +
                (exposure.write_back_allowed ? " Write-Back frei." : " Write-Back gesperrt.")
              : "Backend fuehrt KEINE Orders aus (execution_enabled=false). Der Paper/Sim/Live-Schalter in der Topbar aendert daran nichts."
        }
      >
        {exposure == null
          ? "Handel n/v"
          : exposure.execution_enabled
            ? "Handel: Ausfuehrung AN"
            : "Handel: Ausfuehrung AUS"}
      </Badge>

      {/* Dringlichster Wahrheits-Status (entry-mode/Gates/…). */}
      {topChip && (
        <Badge tone={truthToneToStatusTone(topChip.tone)} dot title={topChip.hint}>
          {topChip.label}: {topChip.value}
        </Badge>
      )}

      {/* Offene Warnungen verdichtet. */}
      {attentionCount > 0 && (
        <Badge
          tone={worst === "critical" ? "neg" : "warn"}
          title="Anzahl Wahrheits-Status mit Warn-/Kritisch-Ton — Details in der Truth-Leiste unten."
        >
          {attentionCount} {attentionCount === 1 ? "Warnung" : "Warnungen"}
        </Badge>
      )}

      {/* Der frueher hier rechts stehende Report-Zeitstempel ist in die
          Daten-Pille gewandert (er stand fuenfmal auf derselben Seite). */}
    </div>
  );
}
