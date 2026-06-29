import { CalendarClock, Info, AlertTriangle } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Primitives";
import { useApi } from "@/lib/useApi";
import { fetchUnlockCalendar, type UnlockCalendarEntry } from "@/lib/api";
import { cn } from "@/lib/utils";

// @data-source: /dashboard/api/unlock-calendar

/**
 * Unlock-Kalender-Panel (ADR 0012 truth-pivot, Phase 2).
 *
 * KONTEXT, KEIN SIGNAL. Zeigt das nächste geplante Token-Unlock je Coin (Tage
 * entfernt + Anteil max_supply) aus dem öffentlichen DefiLlama-Artefakt — als
 * Risiko-/Volatilitäts-Marker rund um eine Cliff, damit der Operator eine
 * anstehende Freischaltung sieht. Token-Unlocks ALS RICHTUNG sind terminal
 * widerlegt (#487 beta-neutral pooled −111 bps; #482 0 Survivors); das Panel sagt
 * das explizit und verwendet bewusst KEINE long/short-Sprache. READ-ONLY.
 */

function fmtDays(d: number): string {
  if (d < 1) return "< 1 Tag";
  const v = d.toLocaleString("de-DE", { maximumFractionDigits: d < 10 ? 1 : 0 });
  return `${v} Tage`;
}

function fmtFrac(f: number | null): string {
  if (f == null) return "—";
  return `${(f * 100).toLocaleString("de-DE", { maximumFractionDigits: 2 })} %`;
}

function fmtDate(iso: string): string {
  const dt = new Date(iso);
  return Number.isNaN(dt.getTime())
    ? "?"
    : dt.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "2-digit" });
}

// Näher = mehr Operator-Aufmerksamkeit (Volatilitäts-Marker), NICHT Richtung.
function urgencyColor(days: number): string {
  if (days <= 3) return "text-warn";
  if (days <= 14) return "text-info";
  return "text-fg-subtle";
}

export function UnlockCalendarPanel() {
  const data = useApi((signal) => fetchUnlockCalendar(signal), 60_000, [], {
    maxAttempts: 2,
    baseMs: 1500,
  });

  const header = (
    <CardHeader
      title="Unlock-Kalender"
      subtitle="Nächste Token-Freischaltungen je Coin — Kontext, kein Signal"
    />
  );

  if (data.state !== "ready") {
    return (
      <Card padded>
        {header}
        <div className="py-6 text-center text-sm text-fg-subtle">
          {data.state === "error" ? `Fehler beim Laden: ${data.error.message}` : "Lädt …"}
        </div>
      </Card>
    );
  }

  const d = data.data;
  // Honest staleness: the backend flags stale=true when the weekly refresh has
  // not run in >14 days (or wrote no timestamp) — so a dead feed cannot look fresh.
  const ageLabel =
    d.age_days == null
      ? "Stand unbekannt"
      : d.age_days < 1
        ? "Stand: heute"
        : `Stand: vor ${d.age_days.toLocaleString("de-DE", { maximumFractionDigits: 0 })} Tagen`;
  const staleBanner = d.stale ? (
    <div className="mb-3 flex items-start gap-2 rounded-sm border border-warn/30 bg-warn/10 px-2.5 py-1.5">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" />
      <p className="text-3xs leading-snug text-warn">
        Kalender möglicherweise veraltet ({ageLabel.toLowerCase()}) — der wöchentliche
        Refresh ist seit über 14 Tagen nicht gelaufen. Werte mit Vorsicht lesen.
      </p>
    </div>
  ) : null;
  const freshnessFooter = (
    <div className="mt-2 text-3xs text-fg-subtle">
      {ageLabel}
      {d.generated_at ? ` · Quelle: DefiLlama (${fmtDate(d.generated_at)})` : ""}
    </div>
  );
  const disclaimer = (
    <div className="mb-3 flex items-start gap-2 rounded-sm border border-line-subtle bg-bg-2 px-2.5 py-1.5">
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fg-subtle" />
      <p className="text-3xs leading-snug text-fg-subtle">
        {d.note ??
          "Kontext, kein Signal — Unlocks als Richtung sind widerlegt (#487/#482)."}{" "}
        Ein anstehendes Unlock ist ein Risiko-/Volatilitäts-Hinweis, keine Kauf-
        oder Verkaufs-Empfehlung.
      </p>
    </div>
  );

  if (!d.available || d.tokens.length === 0) {
    return (
      <Card padded>
        {header}
        {disclaimer}
        {staleBanner}
        <div className="py-6 text-center text-sm text-fg-subtle">
          {d.error
            ? `Unlock-Kalender nicht verfügbar: ${d.error}`
            : "Keine anstehenden Unlocks im Datenbestand."}
        </div>
      </Card>
    );
  }

  return (
    <Card padded>
      {header}
      {disclaimer}
      {staleBanner}
      <ul className="divide-y divide-line-subtle">
        {d.tokens.map((t: UnlockCalendarEntry) => (
          <li key={t.symbol} className="flex items-center justify-between gap-3 py-2">
            <div className="flex items-center gap-2 min-w-0">
              <CalendarClock className={cn("h-4 w-4 shrink-0", urgencyColor(t.days_until))} />
              <span className="font-mono text-sm text-fg truncate">{t.symbol}</span>
            </div>
            <div className="flex items-center gap-4 shrink-0 text-right">
              <div className="tabular-nums">
                <div className={cn("text-sm font-medium", urgencyColor(t.days_until))}>
                  {fmtDays(t.days_until)}
                </div>
                <div className="text-3xs text-fg-subtle">{fmtDate(t.event_iso)}</div>
              </div>
              <div className="w-16 tabular-nums">
                <div className="text-sm text-fg">{fmtFrac(t.frac_of_max_supply)}</div>
                <div className="text-3xs text-fg-subtle">v. Supply</div>
              </div>
            </div>
          </li>
        ))}
      </ul>
      {freshnessFooter}
    </Card>
  );
}
