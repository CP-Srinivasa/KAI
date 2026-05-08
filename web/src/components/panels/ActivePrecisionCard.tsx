import { memo, useMemo, useState } from "react";
import { Card, CardHeader, Badge, ProgressBar } from "@/components/ui/Primitives";
import { useT } from "@/i18n/I18nProvider";
import { cn } from "@/lib/utils";
import type { DashboardProvenance, ProvenanceMetrics } from "@/lib/api";

// Active-Precision split per Signal-Source mit Wilson 95%-CI.
// Quelle: /dashboard/api/provenance (app/alerts/provenance_metrics.py).
// Zweck: Re-Entry-Verdict am 2026-05-16 — nur Sources mit sample_sufficient
// (>=30 resolved) sind judgment-ready. Legacy-unknown-Bucket bleibt sichtbar
// damit Operator erkennt, wie viel Altlast die Baseline drueckt.
//
// DALI-P-066 (2026-05-08): Karte konzeptuell lesbar gemacht.
//   - Subtitle beantwortet die Karten-Frage in einem Satz.
//   - Inline-Help-Disclosure mit Glossar.
//   - Source-Keys humanisiert (Mapping-Tabelle, Backend-Key bleibt sichtbar).
//   - Spalten-Header ueber der per-Source-Liste.
//   - Verdict-Text in Klartext (nicht Insider-Slang).
//   - Empty-State mit Erklaerung statt nur "laedt...".
// ProgressBar/Trust-Badge/Tone-Logik aus DALI-F-033 + DALI-P-064 unveraendert.

const SOURCE_DISPLAY: Record<string, { label: string; hint?: string }> = {
  unknown: {
    label: "Legacy (vor Provenance V1)",
    hint: "Alerts aus der Zeit, bevor die Source-Tagging-Pipeline live war. Druecken die Baseline nach unten — verschwinden ueber Zeit.",
  },
  rss: {
    label: "RSS-Feeds (gemischt)",
    hint: "Klassische News-Feeds: Coindesk, Cointelegraph-RSS etc. Sammelbucket fuer alles per RSS.",
  },
  tradingview_webhook: {
    label: "TradingView Webhook",
    hint: "Direkt von TradingView-Alerts an unseren Webhook. Hier landet die TV-Pivot-Pipeline.",
  },
  cointelegraph: {
    label: "Cointelegraph",
    hint: "Cointelegraph als eigene Source (jenseits der gemischten RSS-Bucket).",
  },
  decrypt: {
    label: "Decrypt News",
    hint: "decrypt.co — eigener Feed.",
  },
  tradingview: {
    label: "TradingView (legacy)",
    hint: "Alte TV-Eintraege vor Webhook-Cutover. Wird mit der Zeit von tradingview_webhook abgeloest.",
  },
};

function formatSourceLabel(key: string): { label: string; hint?: string } {
  return SOURCE_DISPLAY[key] ?? { label: key };
}

function fmtPct(n: number | null): string {
  return n == null ? "—" : `${n.toFixed(1)}%`;
}

function ciLabel(m: ProvenanceMetrics): string {
  if (m.ci_low_pct == null || m.ci_high_pct == null) return "—";
  return `${m.ci_low_pct.toFixed(1)}–${m.ci_high_pct.toFixed(1)}`;
}

function verdictTone(v: string): "pos" | "warn" | "neg" | "neutral" {
  if (v === "tv_significantly_better_than_rss") return "pos";
  if (v === "rss_significantly_better_than_tv") return "neg";
  if (v === "overlapping_confidence_intervals_no_significant_difference") return "warn";
  return "neutral";
}

function verdictText(v: string): string {
  const map: Record<string, string> = {
    tv_significantly_better_than_rss:
      "TradingView liefert messbar bessere Treffer als RSS.",
    rss_significantly_better_than_tv:
      "RSS liefert messbar bessere Treffer als TradingView.",
    overlapping_confidence_intervals_no_significant_difference:
      "Stichprobe noch zu klein, um TV von RSS sauber zu trennen.",
    insufficient_sample_for_split_comparison:
      "Noch nicht genug aufgeloeste Outcomes fuer einen Vergleich.",
  };
  return map[v] ?? v;
}

function HelpDisclosure() {
  const [open, setOpen] = useState(false);
  return (
    <div className="mb-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 text-2xs font-medium text-fg-muted hover:text-fg transition-colors"
        aria-expanded={open}
        aria-controls="active-precision-help"
      >
        <span
          className={cn(
            "inline-flex h-3.5 w-3.5 items-center justify-center rounded-full border border-line text-2xs font-semibold leading-none",
            open ? "bg-bg-3 text-fg" : "text-fg-muted",
          )}
          aria-hidden="true"
        >
          ?
        </span>
        <span>Was zeigt diese Karte?</span>
        <span className="text-fg-subtle text-2xs">{open ? "schliessen" : "oeffnen"}</span>
      </button>
      {open && (
        <div
          id="active-precision-help"
          className="mt-2 rounded-md border border-line-subtle bg-bg-2 px-3 py-2.5 text-2xs leading-relaxed text-fg-muted space-y-1.5"
        >
          <p>
            <span className="font-semibold text-fg">Quelle:</span> Woher der Alert kam
            (RSS, TradingView-Webhook, Cointelegraph, …). Der kleine Mono-Suffix daneben
            ist der Backend-Key.
          </p>
          <p>
            <span className="font-semibold text-fg">Treffer-Quote:</span> Anteil der
            aufgeloesten Alerts dieser Quelle, deren Outcome <em>hit</em> war
            (resolved = hits + misses; inconclusive zaehlt nicht mit).
          </p>
          <p>
            <span className="font-semibold text-fg">Konfidenz (CI):</span> Wilson-95%-Intervall.
            Kurz: in welchem Band die wahre Treffer-Quote vermutlich liegt. Schmales Band =
            verlaesslich. Breites Band = noch zu wenig Daten.
          </p>
          <p>
            <span className="font-semibold text-fg">Baseline vs. Aktiv:</span> Baseline rechnet
            alles, auch alte Alerts ohne Source-Tag (Legacy). Aktiv rechnet nur die getaggten.
            Baseline schwaecher als Aktiv heisst: Legacy-Alt zieht runter.
          </p>
          <p>
            <span className="font-semibold text-fg">Verdict:</span> Vergleich RSS vs.
            TradingView-Webhook. Erst sinnvoll, wenn beide ein ausreichendes Sample haben.
          </p>
          <p>
            <span className="font-semibold text-fg">Vorlaeufig-Badge:</span> Quelle liegt noch
            unter dem Mindestsample (heute n=30). Wert wird gezeigt, ist aber nicht
            judgment-ready.
          </p>
        </div>
      )}
    </div>
  );
}

function ActivePrecisionCardImpl({
  data,
}: {
  data: DashboardProvenance | null;
}) {
  const { t } = useT();
  const sourcesByResolvedDesc = useMemo(
    () => (data ? [...data.by_source].sort((a, b) => b.resolved - a.resolved) : []),
    [data],
  );

  if (!data) {
    return (
      <Card padded>
        <CardHeader
          title="Treffer-Quote pro Signal-Quelle"
          subtitle="Welche Quelle liefert wie zuverlaessig Treffer?"
        />
        <div className="rounded-md border border-line-subtle bg-bg-2 px-3 py-4 text-xs text-fg-muted">
          <div className="font-medium text-fg mb-1">Daten werden geladen …</div>
          <p className="leading-relaxed">
            Sobald Outcomes pro Source aufgeloest sind, erscheint hier die Treffer-Quote
            mit Wilson-95%-Konfidenzintervall pro Quelle.
          </p>
        </div>
      </Card>
    );
  }

  const tvPipe = data.tradingview_pipeline;
  const active = data.overall_active;
  const activeDelta =
    active && active.hit_rate_pct != null && data.overall.hit_rate_pct != null
      ? active.hit_rate_pct - data.overall.hit_rate_pct
      : null;

  return (
    <Card padded>
      <CardHeader
        title="Treffer-Quote pro Signal-Quelle"
        subtitle="Welche unserer Signal-Quellen liefert wie zuverlaessig Treffer? (Wilson 95%-CI, ab n=30 belastbar)"
        right={
          <Badge tone={verdictTone(data.verdict)} dot>
            {data.overall.resolved} resolved
          </Badge>
        }
      />

      <HelpDisclosure />

      <div className="mb-3 grid grid-cols-1 sm:grid-cols-2 gap-2">
        <div
          className="rounded-md border border-line-subtle px-3 py-2.5 bg-bg-2"
          title="Baseline rechnet ALLE Alerts mit, inklusive Legacy ohne Source-Tag."
        >
          <div className="text-2xs text-fg-muted uppercase tracking-wide">
            Baseline (alle Alerts, inkl. Legacy)
          </div>
          <div className="mt-1 flex items-baseline gap-2 font-mono">
            <span className="text-base font-semibold text-fg">
              {fmtPct(data.overall.hit_rate_pct)}
            </span>
            <span className="text-2xs text-fg-subtle">
              CI {ciLabel(data.overall)}
            </span>
            <span className="text-2xs text-fg-muted">
              n={data.overall.resolved}
            </span>
          </div>
        </div>
        {active != null && (
          <div
            className="rounded-md border border-line-strong px-3 py-2.5 bg-bg-2"
            title="Aktiv rechnet nur Alerts mit gesetztem Source-Tag (Provenance V1+)."
          >
            <div className="text-2xs text-fg-muted uppercase tracking-wide flex items-center gap-1">
              Aktiv (nur getaggte Quellen)
              {activeDelta != null && Math.abs(activeDelta) >= 1 && (
                <Badge tone={activeDelta > 0 ? "pos" : "neg"} dot={false}>
                  {activeDelta > 0 ? "+" : ""}
                  {activeDelta.toFixed(1)}pp
                </Badge>
              )}
            </div>
            <div className="mt-1 flex items-baseline gap-2 font-mono">
              <span
                className={cn(
                  "text-base font-semibold",
                  active.hit_rate_pct != null && active.hit_rate_pct >= 60
                    ? "text-pos"
                    : active.hit_rate_pct != null && active.hit_rate_pct >= 40
                      ? "text-warn"
                      : "text-fg",
                )}
              >
                {fmtPct(active.hit_rate_pct)}
              </span>
              <span className="text-2xs text-fg-subtle">
                CI {ciLabel(active)}
              </span>
              <span className="text-2xs text-fg-muted">
                n={active.resolved}
              </span>
            </div>
          </div>
        )}
      </div>

      <div className="space-y-2">
        {sourcesByResolvedDesc.length === 0 ? (
          <div className="rounded-md border border-line-subtle bg-bg-2 px-3 py-3 text-xs text-fg-muted leading-relaxed">
            <div className="font-medium text-fg mb-0.5">
              Noch keine aufloesbaren Outcomes pro Quelle.
            </div>
            <p>
              Sobald die ersten Alerts ein hit/miss-Outcome bekommen, erscheinen sie hier
              gruppiert nach Quelle.
            </p>
          </div>
        ) : (
          <>
            <div
              className="grid items-baseline gap-3 px-1 pb-1 text-2xs font-medium uppercase tracking-wide text-fg-subtle border-b border-line-subtle"
              style={{ gridTemplateColumns: "minmax(0, 1fr) auto auto auto" }}
              aria-hidden="true"
            >
              <span>Quelle</span>
              <span className="text-right">Treffer</span>
              <span className="text-right">Konfidenz (95%)</span>
              <span className="text-right">Hits / Miss</span>
            </div>

            {sourcesByResolvedDesc.map((m) => {
              const hasValue = m.hit_rate_pct != null;
              const green = hasValue && m.sample_sufficient && m.hit_rate_pct! >= 60;
              const orange =
                hasValue && m.sample_sufficient && m.hit_rate_pct! >= 40 && !green;
              const sourceTone: "pos" | "warn" | "neg" | "muted" = !hasValue
                ? "muted"
                : green
                  ? "pos"
                  : orange
                    ? "warn"
                    : "neg";
              const display = formatSourceLabel(m.source);
              const dotClass =
                sourceTone === "pos"
                  ? "bg-pos"
                  : sourceTone === "warn"
                    ? "bg-warn"
                    : sourceTone === "neg"
                      ? "bg-neg"
                      : "bg-fg-subtle/50";
              return (
                <div key={m.source}>
                  <div
                    className="grid items-baseline gap-3 text-xs"
                    style={{ gridTemplateColumns: "minmax(0, 1fr) auto auto auto" }}
                  >
                    <div className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 min-w-0">
                      {/* DALI-Folge 2026-05-08: Tone-Dot vor dem Label,
                          damit Operator sofort visuell zuordnen kann
                          (gut/mittel/schlecht/inaktiv) — zusaetzlich zur Bar. */}
                      <span
                        className={cn("inline-block h-2 w-2 rounded-full shrink-0 self-center", dotClass)}
                        aria-hidden="true"
                      />
                      <span
                        className="text-fg font-medium truncate"
                        title={display.hint}
                      >
                        {display.label}
                      </span>
                      <span
                        className="text-2xs font-mono text-fg-subtle truncate"
                        title={`Backend-Source-Key: ${m.source}`}
                      >
                        {m.source}
                      </span>
                      {!m.sample_sufficient && (
                        <span
                          className="inline-flex items-center gap-1 rounded-xs border border-line-subtle bg-bg-2 px-1.5 py-0.5 text-2xs font-mono text-fg-subtle shrink-0"
                          title={`Stichprobe zu klein fuer Urteil (${m.resolved}/${data.min_sample_for_judgment} resolved). Wert sichtbar, aber nicht judgment-ready.`}
                        >
                          vorlaeufig · n={m.resolved}/{data.min_sample_for_judgment}
                        </span>
                      )}
                    </div>
                    <span
                      className={cn(
                        "text-sm font-semibold font-mono text-right",
                        green ? "text-pos" : orange ? "text-warn" : "text-fg",
                      )}
                    >
                      {fmtPct(m.hit_rate_pct)}
                    </span>
                    <span className="text-2xs text-fg-subtle font-mono text-right">
                      {ciLabel(m)}
                    </span>
                    <span className="text-2xs text-fg-muted font-mono text-right">
                      {m.hits}h / {m.misses}m
                    </span>
                  </div>
                  {/* Bar-Wrapper mit 60%-Threshold-Marker (Re-Entry-Gate).
                      ProgressBar size="md" macht den Balken visuell deutlich
                      praesenter als das duenne size="sm". */}
                  <div className="relative mt-1.5">
                    <ProgressBar
                      value={m.hit_rate_pct}
                      target={100}
                      tone={sourceTone}
                      size="md"
                      label={`Treffer-Quote ${display.label}: ${fmtPct(m.hit_rate_pct)}`}
                      sufficientSample={m.sample_sufficient}
                    />
                    <span
                      className="absolute top-0 bottom-0 w-px bg-fg-subtle/35 pointer-events-none"
                      style={{ left: "60%" }}
                      title="Re-Entry-Gate liegt bei 60% Treffer-Quote"
                      aria-hidden="true"
                    />
                  </div>
                </div>
              );
            })}
          </>
        )}
      </div>

      <div className="mt-4 pt-3 border-t border-line-subtle text-2xs text-fg-muted leading-relaxed space-y-1">
        <div>
          <span className="text-fg-subtle">Verdict:</span>{" "}
          <span
            className={cn(
              "font-semibold",
              verdictTone(data.verdict) === "pos" && "text-pos",
              verdictTone(data.verdict) === "warn" && "text-warn",
              verdictTone(data.verdict) === "neg" && "text-neg",
            )}
            title={`Backend-Verdict-Key: ${data.verdict}`}
          >
            {verdictText(data.verdict)}
          </span>
        </div>
        <div title="TV-Webhook-Pipeline-Volumen. Pending = Events im Buffer, Smoke = Test-Events, Live = produktive Events.">
          {t("primitives.tv_pipeline_label")}: {t("primitives.tv_pipeline_pending")}=
          <span className="font-mono">{tvPipe.pending_events}</span>, {t("primitives.tv_pipeline_smoke")}=
          <span className="font-mono">{tvPipe.smoke_test_events}</span>, {t("primitives.tv_pipeline_real")}=
          <span className="font-mono">{tvPipe.real_events}</span>
        </div>
        {data.notes.length > 0 && (
          <ul className="list-disc pl-4 space-y-0.5">
            {data.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        )}
        {data.generated_at && (
          <div className="font-mono text-fg-subtle">
            {data.generated_at.substring(0, 19).replace("T", " ")}
          </div>
        )}
      </div>
    </Card>
  );
}

export const ActivePrecisionCard = memo(ActivePrecisionCardImpl);
