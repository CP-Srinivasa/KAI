import { useCallback } from "react";
import {
  ShieldAlert,
  ShieldCheck,
  Lock,
  Radio,
  AlertTriangle,
  ChevronRight,
} from "lucide-react";
import { fetchPremiumRuntime, type PremiumRuntimeResponse } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { cn } from "@/lib/utils";

/**
 * PremiumRuntimeBanner — die fehlende Wahrheits-Schicht über dem Dashboard.
 *
 * Wurzel 2026-06-04 (DALI Premium-Truth-Sprint): Premium-Signale können geparst,
 * gespeichert und sogar approved werden, ohne je eine Paper-Position zu öffnen —
 * weil ein globaler Safety-Switch (entry_mode / Bridge / Source-Allowlist /
 * Paper-Flag) blockt. Dieser Zustand war im Dashboard NICHT sichtbar. Der Banner
 * macht ihn laut und eindeutig: rot/orange-Glow bei Blockade, ruhiges Cyan wenn
 * Premium-Paper-Execution offen ist. Live bleibt geschützt und ist KEIN Fehler.
 *
 * Datenquelle: GET /api/premium-signals/runtime (read-only). Polling 60s.
 */

type Props = { className?: string };

const RUNTIME_POLL_MS = 60_000;

// Maschinen-Reason → Operator-Klartext + empfohlene Prüf-Aktion.
const REASON_DETAIL: Record<string, { label: string; action: string }> = {
  premium_paper_execution_disabled: {
    label: "Premium Paper-Execution ist abgeschaltet",
    action: "Paper-Flag prüfen (premium.paper_execution_enabled)",
  },
  operator_signal_bridge_disabled: {
    label: "Operator-Signal-Bridge ist deaktiviert",
    action: "Bridge-Status prüfen (operator_signal_bridge_enabled)",
  },
  telegram_premium_channel_not_allowlisted: {
    label: "Premium-Telegram-Quelle ist nicht in der Allowlist",
    action: "Source-Allowlist prüfen",
  },
};

function reasonDetail(reason: string): { label: string; action: string } {
  if (reason in REASON_DETAIL) return REASON_DETAIL[reason];
  if (reason.startsWith("entry_mode=")) {
    const mode = reason.split("=")[1] ?? "?";
    return {
      label: `entry_mode=${mode} verhindert neue Premium-Paper-Entries`,
      action: "Runtime / EXECUTION_ENTRY_MODE prüfen",
    };
  }
  return { label: reason, action: "Runtime prüfen" };
}

function FlagPill({
  label,
  value,
  tone,
  icon,
}: {
  label: string;
  value: string;
  tone: "pos" | "neg" | "warn" | "info" | "muted";
  icon?: JSX.Element;
}): JSX.Element {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-xs border px-1.5 py-0.5 text-2xs font-mono whitespace-nowrap",
        tone === "pos" && "border-pos/30 bg-pos/10 text-pos",
        tone === "neg" && "border-neg/30 bg-neg/10 text-neg",
        tone === "warn" && "border-warn/30 bg-warn/10 text-warn",
        tone === "info" && "border-info/30 bg-info/10 text-info",
        tone === "muted" && "border-line-subtle bg-bg-2 text-fg-muted",
      )}
      title={`${label}: ${value}`}
    >
      {icon}
      <span className="opacity-70">{label}</span>
      <span className="font-semibold">{value}</span>
    </span>
  );
}

function RuntimeFlags({ rt }: { rt: PremiumRuntimeResponse }): JSX.Element {
  const allowlisted = rt.operator_signal_source_allowlist.some((s) =>
    s.toLowerCase().startsWith("telegram"),
  );
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <FlagPill
        label="Entry"
        value={rt.entry_mode}
        tone={rt.entry_mode_blocks_premium_paper ? "neg" : "pos"}
        icon={<Radio size={10} />}
      />
      <FlagPill
        label="Paper"
        value={rt.premium_paper_execution_enabled ? "enabled" : "blocked"}
        tone={rt.premium_paper_execution_enabled ? "pos" : "neg"}
      />
      <FlagPill
        label="Bridge"
        value={rt.operator_signal_bridge_enabled ? "enabled" : "disabled"}
        tone={rt.operator_signal_bridge_enabled ? "pos" : "neg"}
      />
      <FlagPill
        label="Source"
        value={allowlisted ? "allowlisted" : "skipped"}
        tone={allowlisted ? "pos" : "neg"}
      />
      {/* Live ist bewusst geschützt — "off" ist KEIN Fehler, sondern Sicherheit. */}
      <FlagPill
        label="Live"
        value={rt.premium_live_execution_enabled ? "ENABLED" : "protected"}
        tone={rt.premium_live_execution_enabled ? "warn" : "muted"}
        icon={<Lock size={10} />}
      />
    </div>
  );
}

export function PremiumRuntimeBanner({ className }: Props): JSX.Element | null {
  const fetcher = useCallback(
    (signal: AbortSignal) => fetchPremiumRuntime(signal),
    [],
  );
  const polling = usePolling(fetcher, {
    intervalMs: RUNTIME_POLL_MS,
    pauseWhenHidden: true,
  });

  // Lade-/Fehlerzustand: kein lauter Platzhalter, der Banner ist eine Beilage.
  if (polling.state === "loading") return null;
  if (polling.state === "error") {
    return (
      <div
        className={cn(
          "rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-2xs font-mono text-warn flex items-center gap-2",
          className,
        )}
      >
        <AlertTriangle size={12} />
        Premium-Runtime-Status nicht abrufbar ({polling.error.kind}) —{" "}
        <span className="font-mono">/api/premium-signals/runtime</span>
      </div>
    );
  }

  const rt = polling.data;
  const blocked = !rt.can_open_paper_positions;

  if (!blocked) {
    // Ruhiger Cyan-Strip: Premium-Paper-Execution offen.
    return (
      <div
        className={cn(
          "rounded-md border border-info/30 bg-info/[0.06] px-3 py-2",
          "flex flex-wrap items-center justify-between gap-2 glow-info",
          className,
        )}
      >
        <div className="flex items-center gap-2 min-w-0">
          <ShieldCheck size={14} className="text-info shrink-0" />
          <span className="text-xs font-semibold tracking-wide text-info uppercase">
            Premium Runtime aktiv
          </span>
          <span className="text-2xs text-fg-subtle hidden sm:inline">
            Premium-Paper-Entries werden ausgeführt — Live bleibt geschützt.
          </span>
        </div>
        <RuntimeFlags rt={rt} />
      </div>
    );
  }

  // Blockade: lauter rot/orange Cyberpunk-Warnbanner.
  return (
    <div
      className={cn(
        "scanline-overlay overflow-hidden rounded-md border border-neg/50 bg-neg/[0.08]",
        "px-4 py-3 attention-breathe-neg glow-neg",
        className,
      )}
      role="alert"
    >
      <div className="relative flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-2.5 min-w-0">
          <ShieldAlert size={20} className="text-neg shrink-0 mt-0.5" />
          <div className="min-w-0 space-y-1">
            <div className="text-sm font-bold tracking-wide text-neg uppercase">
              Premium Execution blockiert
            </div>
            <div className="text-2xs text-fg-muted leading-relaxed max-w-prose">
              {rt.warning ??
                "Premium-Signale werden geparst und gespeichert, aber es wird keine Paper-Position geöffnet."}
            </div>
          </div>
        </div>
        <RuntimeFlags rt={rt} />
      </div>

      {/* Konkrete Blocker + empfohlene Prüf-Aktionen. */}
      <div className="relative mt-2.5 pt-2.5 border-t border-neg/25 space-y-1">
        {rt.blocking_reasons.map((reason) => {
          const d = reasonDetail(reason);
          return (
            <div
              key={reason}
              className="flex items-start gap-1.5 text-2xs font-mono"
            >
              <ChevronRight size={11} className="text-neg shrink-0 mt-0.5" />
              <span className="text-fg">{d.label}</span>
              <span className="text-fg-subtle">— {d.action}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
