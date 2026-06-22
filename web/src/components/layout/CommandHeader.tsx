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

function freshness(iso: string | null | undefined): string {
  if (!iso) return "—";
  return iso.substring(0, 19).replace("T", " ");
}

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
  const chips = deriveTruthChips(quality, regime, priorityGate);
  const topChip = chips[0] ?? null;
  const attentionCount = chips.filter((c) => ATTENTION_TONES.has(c.tone)).length;
  const worst = highestTruthTone(chips);

  return (
    <div
      className={cn(
        "sticky top-0 z-20 -mx-4 mb-1 flex flex-wrap items-center gap-2 border-b px-4 py-2 backdrop-blur xl:-mx-5 xl:px-5",
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
        <span title="KAI-Status-Endpoint nicht verfügbar (Auth/Zugriff). Kein live abgeleiteter Zustand — bewusst kein Handeln.">
          <StatusPill kind="fail-closed" label="KAI · n/v" />
        </span>
      ) : (
        <StatusPill kind="pending" label="Live · lädt" />
      )}

      {/* Backend-Gesundheit. */}
      <StatusPill
        kind={backendHealthToStatus(health.state)}
        label={health.state === "connected" ? `Backend v${health.version}` : `Backend ${health.state}`}
      />

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

      {/* Report-Frische rechtsbündig. */}
      <span className="ml-auto inline-flex items-center gap-1.5 font-mono text-2xs text-fg-subtle">
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            qualityState === "ready" ? "bg-pos" : qualityState === "error" ? "bg-neg" : "bg-fg-subtle",
          )}
        />
        {qualityState === "ready"
          ? `Report ${freshness(quality?.generated_at)}`
          : qualityState === "error"
            ? "Report-Fehler"
            : "lädt …"}
      </span>
    </div>
  );
}
