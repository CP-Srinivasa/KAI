import { Card, CardHeader, Badge, ProgressBar, InfoHint } from "@/components/ui/Primitives";
import type { DashboardQuality } from "@/lib/api";
import { cn } from "@/lib/utils";
import { sourceLabel } from "@/lib/sourceLabels";

// V-DB4a 2026-05-08: Per-source active precision panel.
// V-DB5 2026-05-09: Tone-Dot, gateLabel(min_resolved), deutsche Microcopy.
// 2026-05-11 DALI Mobile-Klarheit:
//   - Stacked-Mobile-Layout: Source-Name immer voll (kein truncate auf <sm).
//   - Wilson-CI als duenner Neon-Strip unter der ProgressBar (visuelle Spannweite).
//   - "n<X"-Badge + Quality-Badges mit deutscher InfoHint.
//   - Treffer/Stichprobe als deutsche Klarschrift ("18 von 19 trafen") statt nur n=19.
//   - Subtitle deutsch, Card-Footer-Microcopy entkrampft.

type SourceMetrics = NonNullable<DashboardQuality["per_source_active_precision"]>["by_source"][string];
type GateTone = "pos" | "warn" | "neg" | "muted";

function gateBadgeTone(m: SourceMetrics): GateTone {
  if (m.passes_gate) return "pos";
  if (m.n_threshold_met && !m.wilson_low_threshold_met) return "neg";
  if (!m.n_threshold_met && m.wilson_low_threshold_met) return "warn";
  return "muted";
}

function gateLabel(m: SourceMetrics, minResolved: number): string {
  if (m.passes_gate) return "OK";
  if (m.n_threshold_met && !m.wilson_low_threshold_met) return "Quote zu niedrig";
  if (!m.n_threshold_met) return "n<" + String(minResolved);
  return "-";
}

function gateHint(m: SourceMetrics, minResolved: number, minWilson: number): string {
  if (m.passes_gate) {
    return "Diese Source besteht das Quality-Gate: genuegend aufgeloeste Signale (n>=" + String(minResolved) + ") UND Wilson-Untergrenze >=" + minWilson.toFixed(0) + "%.";
  }
  if (m.n_threshold_met && !m.wilson_low_threshold_met) {
    return "Stichprobe gross genug, aber die statistische Untergrenze liegt unter der Hit-Rate-Schwelle. Source aktuell zu unzuverlaessig.";
  }
  if (!m.n_threshold_met) {
    return "Noch zu wenig aufgeloeste Signale (n<" + String(minResolved) + ") fuer eine belastbare Aussage. Hit-Rate kann zufaellig sein.";
  }
  return "Kein Urteil moeglich.";
}

const TONE_DOT_CLASS: Record<GateTone, string> = {
  pos: "bg-pos",
  warn: "bg-warn",
  neg: "bg-neg",
  muted: "bg-fg-subtle/50",
};

const TONE_TEXT_CLASS: Record<GateTone, string> = {
  pos: "text-pos",
  warn: "text-warn",
  neg: "text-neg",
  muted: "text-fg-subtle",
};

const TONE_STRIP_CLASS: Record<GateTone, string> = {
  pos: "bg-pos/60",
  warn: "bg-warn/60",
  neg: "bg-neg/60",
  muted: "bg-fg-subtle/40",
};

// Wilson-CI-Strip: duenne Neon-Linie unter der ProgressBar, deren linke Kante
// = ci_low_pct und rechte Kante = ci_high_pct (in % der Bar-Breite). Zeigt
// Operator visuell die Range der statistisch plausiblen Hit-Rates.
function WilsonStrip({
  ciLo,
  ciHi,
  tone,
}: {
  ciLo: number | null | undefined;
  ciHi: number | null | undefined;
  tone: GateTone;
}) {
  if (ciLo == null || ciHi == null) return null;
  const lo = Math.max(0, Math.min(100, ciLo));
  const hi = Math.max(0, Math.min(100, ciHi));
  const width = Math.max(2, hi - lo);
  return (
    <div
      className="relative h-0.5 w-full mt-1 rounded-full bg-line/40"
      aria-hidden="true"
    >
      <div
        className={cn("absolute h-full rounded-full", TONE_STRIP_CLASS[tone])}
        style={{ left: lo + "%", width: width + "%" }}
      />
    </div>
  );
}

export function PerSourcePrecisionPanel({ data }: { data: DashboardQuality | null }) {
  const psp = data?.per_source_active_precision;
  if (!psp) {
    return (
      <Card padded>
        <CardHeader
          title="Source-Precision"
          subtitle="Per-Source-Precision wird im naechsten Hold-Report berechnet."
        />
        <div className="text-2xs text-fg-subtle">noch keine Daten</div>
      </Card>
    );
  }

  const sources = Object.entries(psp.by_source).sort(
    (a, b) => (b[1].resolved ?? 0) - (a[1].resolved ?? 0),
  );

  const passing = sources.filter(([, m]) => m.passes_gate).length;
  const total = sources.length;
  const minResolved = psp.min_resolved;
  const minWilson = psp.min_wilson_low_pct;

  return (
    <Card padded>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-1.5">
            Quellen-Treffsicherheit
            <InfoHint
              label="Quellen-Treffsicherheit"
              hint="Wie oft jede Datenquelle aktuell richtig liegt. Eine Quelle besteht das Gate, wenn sie genuegend Signale hatte UND ihre statistische Untergrenze ueber der Mindestquote liegt."
            />
          </span>
        }
        subtitle={
          "Schwelle: mindestens " + minResolved + " aufgeloeste Signale, statistische Untergrenze >= " + minWilson.toFixed(0) + "% (Wilson). " + passing + " von " + total + " Quellen bestehen."
        }
        right={
          <Badge tone={passing > 0 ? "pos" : passing === 0 && total > 0 ? "warn" : "muted"} dot>
            {passing}/{total}
          </Badge>
        }
      />
      <div className="space-y-3">
        {sources.map(([source, m]) => {
          const tone = gateBadgeTone(m);
          const ciLo = m.ci_low_pct;
          const ciHi = m.ci_high_pct;
          const display = sourceLabel(source);
          const titleText = display.hint
            ? display.hint + " - Backend-Key: " + source
            : "Backend-Key: " + source;
          const hits = m.hits ?? 0;
          const resolved = m.resolved ?? 0;
          return (
            <div
              key={source}
              className="rounded-md border border-line-subtle bg-bg-2/30 p-3"
            >
              {/* Zeile 1: Source-Name (immer voll, bricht auf Mobile um) + Gate-Badge */}
              <div className="flex items-start justify-between gap-2 mb-1.5">
                <div className="flex items-start gap-1.5 min-w-0 flex-1">
                  <span
                    className={cn("inline-block h-2 w-2 rounded-full shrink-0 mt-1", TONE_DOT_CLASS[tone])}
                    aria-hidden="true"
                  />
                  <span
                    className="text-xs font-semibold text-fg break-words"
                    title={titleText}
                  >
                    {display.label}
                  </span>
                </div>
                <Badge
                  tone={tone === "muted" ? "muted" : tone}
                  className={cn(
                    "shrink-0 inline-flex items-center gap-1",
                    tone === "neg" && "attention-breathe-neg",
                  )}
                >
                  {gateLabel(m, minResolved)}
                  <InfoHint
                    label={gateLabel(m, minResolved)}
                    hint={gateHint(m, minResolved, minWilson)}
                    side="left"
                  />
                </Badge>
              </div>

              {/* Zeile 2: Klarschrift-Treffer + Hit-Rate */}
              <div className="flex items-baseline justify-between gap-2 text-2xs">
                <span className="text-fg-muted">
                  <span className="font-mono text-fg">{hits}</span>
                  {" von "}
                  <span className="font-mono text-fg">{resolved}</span>
                  {" Signalen trafen"}
                </span>
                <span className={cn("font-mono font-semibold text-xs tabular-nums", TONE_TEXT_CLASS[tone])}>
                  {m.hit_rate_pct != null ? m.hit_rate_pct.toFixed(1) + "%" : "-"}
                </span>
              </div>

              {/* Zeile 3: ProgressBar + WilsonStrip darunter */}
              <div className="mt-1.5">
                <ProgressBar
                  value={m.hit_rate_pct}
                  target={minWilson}
                  tone={tone === "pos" ? "pos" : tone === "neg" ? "neg" : "auto"}
                  sufficientSample={m.n_threshold_met}
                  label={display.label + " hit rate"}
                  size="md"
                />
                <WilsonStrip ciLo={ciLo} ciHi={ciHi} tone={tone} />
              </div>

              {/* Zeile 4: CI-Klarschrift + InfoHint */}
              {ciLo != null && ciHi != null && (
                <div className="mt-1.5 inline-flex items-center gap-1 text-2xs text-fg-subtle">
                  <span>
                    Plausibler Bereich: {ciLo.toFixed(0)}-{ciHi.toFixed(0)}%
                  </span>
                  <InfoHint
                    label="Konfidenzintervall (Wilson 95%)"
                    hint="Statistisches Sicherheitsfenster: mit 95% Wahrscheinlichkeit liegt die wahre Trefferquote in diesem Bereich. Schmaler Bereich = belastbare Aussage. Breiter Bereich = Stichprobe zu klein."
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
      {sources.length === 0 && (
        <div className="text-2xs text-fg-subtle">keine Quelle mit aufgeloesten Signalen</div>
      )}
      <div className="mt-4 pt-3 border-t border-line-subtle text-2xs text-fg-muted leading-relaxed space-y-1">
        <div>
          <span className="text-pos font-semibold">OK</span>: Stichprobe ausreichend UND statistische Untergrenze &gt;= {minWilson.toFixed(0)}%.
        </div>
        <div>
          <span className="text-neg font-semibold">Quote zu niedrig</span>: genug Signale, aber Trefferquote unter Schwelle.
        </div>
        <div>
          <span className="text-warn font-semibold">n&lt;{minResolved}</span>: noch zu wenig aufgeloeste Signale, Aussage unsicher.
        </div>
      </div>
    </Card>
  );
}
