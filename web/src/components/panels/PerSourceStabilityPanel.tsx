import { Card, CardHeader, Badge, ProgressBar, InfoHint } from "@/components/ui/Primitives";
import type { DashboardQuality } from "@/lib/api";
import { cn } from "@/lib/utils";
import { sourceLabel } from "@/lib/sourceLabels";

// V-DB4e 2026-05-08: Per-source 30-day rolling stability tile.
// 2026-05-11 DALI Mobile-Klarheit (Tranche A, Folge zu Precision-Patch):
//   - Stacked-Mobile-Layout: Source-Name immer voll (kein truncate).
//   - InfoHint-Primitive (DALI-P-026) fuer Begriffe stabil/Drift + Wilson-CI.
//   - Pro Source: Header (Name+Status-Badge) / Hero-Satz (juengstes Fenster) /
//     ProgressBar + Wilson-CI-Strip + Threshold-Marker / CI-Klarschrift /
//     Drift-Mosaik (alt -> neu).
//   - Deutsche Microcopy spiegelt PerSourcePrecisionPanel-Vorlage.
//   - 80s-Retro-Neon-Style erhalten (border-info/40, text-pos, glow-info).
//
// Stability-Semantik: Backend liefert pro Source drei rollende 30-Tage-Fenster.
// Eine Source ist stabil, wenn JEDES Fenster mit Daten n>=min_resolved_per_window
// UND Wilson-Untergrenze>=min_wilson_low_pct erfuellt. Bar zeigt das juengste
// Fenster, Mosaik zeigt den Verlauf (alt -> neu).

type StabilityRoot = NonNullable<DashboardQuality["per_source_stability"]>;
type StabilityWindow = StabilityRoot["by_source"][string]["windows"][number];

type Tone = "pos" | "warn" | "neg" | "muted";

function windowTone(w: StabilityWindow): Tone {
  if (!w.n_threshold_met) return "muted";
  if (w.passes_window) return "pos";
  if (w.wilson_low_threshold_met) return "warn";
  return "neg";
}

function windowTitle(w: StabilityWindow): string {
  const start = w.window_start.substring(0, 10);
  const end = w.window_end.substring(0, 10);
  return (
    start +
    " -> " +
    end +
    " - n=" +
    w.resolved +
    " - " +
    (w.hit_rate_pct != null ? w.hit_rate_pct.toFixed(1) + "%" : "-") +
    " - CI-Untergrenze " +
    (w.ci_low_pct != null ? w.ci_low_pct.toFixed(1) + "%" : "-") +
    (w.fail_reason ? " - " + w.fail_reason : "")
  );
}

const TONE_DOT_CLASS: Record<Tone, string> = {
  pos: "bg-pos",
  warn: "bg-warn",
  neg: "bg-neg",
  muted: "bg-fg-subtle/50",
};

const TONE_TEXT_CLASS: Record<Tone, string> = {
  pos: "text-pos",
  warn: "text-warn",
  neg: "text-neg",
  muted: "text-fg-subtle",
};

const TONE_STRIP_CLASS: Record<Tone, string> = {
  pos: "bg-pos/60",
  warn: "bg-warn/60",
  neg: "bg-neg/60",
  muted: "bg-fg-subtle/40",
};

// Wilson-Lower-Strip: zeigt ab der statistischen Untergrenze (Wilson 95%) bis
// 100% den plausiblen Bereich, in dem die wahre Hit-Rate mindestens liegt.
// Auf Stability-Ebene gibt das Backend nur ci_low_pct (keinen oberen Rand) -
// wir visualisieren daher die Untergrenze als "ab hier ist die Quote belastbar".
function WilsonLowerStrip({
  ciLo,
  tone,
}: {
  ciLo: number | null | undefined;
  tone: Tone;
}) {
  if (ciLo == null) return null;
  const lo = Math.max(0, Math.min(100, ciLo));
  const width = Math.max(2, 100 - lo);
  return (
    <div className="relative h-0.5 w-full mt-1 rounded-full bg-line/40" aria-hidden="true">
      <div
        className={cn("absolute h-full rounded-full", TONE_STRIP_CLASS[tone])}
        style={{ left: lo + "%", width: width + "%" }}
      />
    </div>
  );
}

export function PerSourceStabilityPanel({ data }: { data: DashboardQuality | null }) {
  const pss = data?.per_source_stability;

  if (!pss || Object.keys(pss.by_source).length === 0) {
    return (
      <Card padded>
        <CardHeader
          title={
            <span className="inline-flex items-center gap-1.5">
              Quellen-Stabilitaet (gleitend)
              <InfoHint
                label="Quellen-Stabilitaet"
                hint="Wie verlaesslich war jede Quelle zuletzt - gleitender Hit-Rate-Score ueber drei 30-Tage-Fenster. Eine Quelle ist stabil, wenn sie in jedem Fenster genug Signale UND eine ausreichend hohe statistische Untergrenze hatte."
              />
            </span>
          }
          subtitle="Liefert eine Quelle ueber drei 30-Tage-Fenster konstant Treffer - oder driftet sie weg?"
        />
        <div className="rounded-md border border-line-subtle bg-bg-2 px-3 py-4 text-xs text-fg-muted leading-relaxed">
          <div className="font-medium text-fg mb-1">
            Stability-Daten kommen mit dem naechsten Hold-Report.
          </div>
          <p>
            Sobald drei vollstaendige 30-Tage-Fenster aufgeloest sind, erscheint hier pro Quelle
            die juengste Trefferquote als Bar plus drei Drift-Kacheln (alt -&gt; neu) als Verlauf.
          </p>
        </div>
      </Card>
    );
  }

  const sources = Object.entries(pss.by_source).sort((a, b) => {
    if (a[1].stable !== b[1].stable) return a[1].stable ? -1 : 1;
    return a[0].localeCompare(b[0]);
  });

  const stableCount = sources.filter(([, m]) => m.stable).length;
  const total = sources.length;
  const minResolved = pss.min_resolved_per_window;
  const minWilson = pss.min_wilson_low_pct;
  const windowDays = pss.window_days;

  return (
    <Card padded>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-1.5">
            Quellen-Stabilitaet (gleitend)
            <InfoHint
              label="Quellen-Stabilitaet"
              hint="Wie verlaesslich war jede Quelle zuletzt - gleitender Hit-Rate-Score ueber drei 30-Tage-Fenster. Eine Quelle ist stabil, wenn sie in jedem Fenster genug Signale UND eine ausreichend hohe statistische Untergrenze hatte."
            />
          </span>
        }
        subtitle={
          "Drei rollende " +
          windowDays +
          "-Tage-Fenster, je mindestens " +
          minResolved +
          " aufgeloeste Signale, statistische Untergrenze >= " +
          minWilson.toFixed(0) +
          "% (Wilson). " +
          stableCount +
          " von " +
          total +
          " Quellen sind stabil."
        }
        right={
          <Badge
            tone={stableCount === total ? "pos" : stableCount > 0 ? "warn" : "muted"}
            dot
          >
            {stableCount}/{total} stabil
          </Badge>
        }
      />
      <div className="space-y-3">
        {sources.map(([source, m]) => {
          const latest = m.windows[0];
          const latestTone: Tone = latest ? windowTone(latest) : "muted";
          const display = sourceLabel(source);
          const titleText = display.hint
            ? display.hint + " - Backend-Key: " + source
            : "Backend-Key: " + source;
          const resolved = latest?.resolved ?? 0;
          const hits = latest?.hits ?? 0;
          const hitRate = latest?.hit_rate_pct;
          const ciLo = latest?.ci_low_pct;

          const statusLabel = m.stable ? "stabil" : "Drift";
          const statusTone: Tone = m.stable ? "pos" : "warn";
          const statusHint = m.stable
            ? "Alle drei 30-Tage-Fenster mit Daten erfuellen n>=" +
              minResolved +
              " UND Wilson-Untergrenze>=" +
              minWilson.toFixed(0) +
              "%. Quelle liefert konsistent verlaessliche Signale."
            : "Mindestens ein Fenster reisst die Stabilitaets-Schwellen. Hit-Rate kippt zwischen den Fenstern - Quelle aktuell nicht durchgaengig verlaesslich.";

          return (
            <div
              key={source}
              className={cn(
                "rounded-md border border-line-subtle bg-bg-2/30 p-3",
                !m.stable && "attention-breathe-warn",
              )}
            >
              <div className="flex items-start justify-between gap-2 mb-1.5">
                <div className="flex items-start gap-1.5 min-w-0 flex-1">
                  <span
                    className={cn(
                      "inline-block h-2 w-2 rounded-full shrink-0 mt-1",
                      TONE_DOT_CLASS[latestTone],
                    )}
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
                  tone={statusTone}
                  className="shrink-0 inline-flex items-center gap-1"
                >
                  {statusLabel}
                  <InfoHint label={statusLabel} hint={statusHint} side="left" />
                </Badge>
              </div>

              <div className="flex items-baseline justify-between gap-2 text-2xs">
                <span className="text-fg-muted">
                  <span className="font-mono tabular-nums text-fg">{hits}</span>
                  {" von "}
                  <span className="font-mono tabular-nums text-fg">{resolved}</span>
                  {" trafen "}
                  <span className="text-fg-subtle">(letztes Fenster)</span>
                </span>
                <span
                  className={cn(
                    "font-mono font-semibold text-xs tabular-nums",
                    TONE_TEXT_CLASS[latestTone],
                  )}
                >
                  {hitRate != null ? hitRate.toFixed(1) + "%" : "-"}
                </span>
              </div>

              <div className="mt-1.5 relative">
                <ProgressBar
                  value={hitRate ?? null}
                  target={minWilson}
                  tone={
                    latestTone === "pos"
                      ? "pos"
                      : latestTone === "neg"
                        ? "neg"
                        : "auto"
                  }
                  sufficientSample={latest?.n_threshold_met ?? false}
                  label={display.label + " juengste Trefferquote"}
                  size="md"
                />
                <WilsonLowerStrip ciLo={ciLo} tone={latestTone} />
                <span
                  className="absolute top-0 h-2 w-px bg-fg-subtle/35 pointer-events-none"
                  style={{ left: minWilson + "%" }}
                  title={
                    "Stabilitaets-Schwelle: " +
                    minWilson.toFixed(0) +
                    "% Wilson-Untergrenze"
                  }
                  aria-hidden="true"
                />
              </div>

              {ciLo != null && (
                <div className="mt-1.5 inline-flex items-center gap-1 text-2xs text-fg-subtle">
                  <span>
                    Statistische Untergrenze: <span className="font-mono tabular-nums">{ciLo.toFixed(0)}%</span>
                  </span>
                  <InfoHint
                    label="Wilson-Untergrenze (95%)"
                    hint="Mit 95% Wahrscheinlichkeit liegt die wahre Trefferquote im juengsten Fenster mindestens hier. Je hoeher, desto belastbarer die Quote - kleine Stichproben druecken die Untergrenze."
                  />
                </div>
              )}

              <div className="mt-2 flex items-center justify-between gap-2">
                <span className="text-2xs text-fg-subtle inline-flex items-center gap-1">
                  Verlauf
                  <InfoHint
                    label="Drift-Verlauf"
                    hint="Drei rollende 30-Tage-Fenster, links = aelteste, rechts = juengste. Gruen erfuellt die Schwellen, gelb knapp, rot reisst die Quote, grau zu wenig Daten. Wenn die Kacheln von links nach rechts kippen, driftet die Quelle."
                    side="right"
                  />
                </span>
                <div
                  className="flex gap-1 shrink-0"
                  aria-label={display.label + " Drift-Verlauf von alt nach neu"}
                >
                  {[...m.windows].reverse().map((w, idx) => {
                    const t = windowTone(w);
                    return (
                      <div
                        key={idx}
                        title={windowTitle(w)}
                        className={cn(
                          "h-5 w-10 rounded-xs border text-[10px] flex items-center justify-center font-mono tabular-nums",
                          t === "pos" && "bg-pos/15 text-pos border-pos/30",
                          t === "warn" && "bg-warn/15 text-warn border-warn/30",
                          t === "neg" && "bg-neg/15 text-neg border-neg/30",
                          t === "muted" && "bg-bg-3 text-fg-subtle border-line-subtle",
                        )}
                      >
                        {w.resolved > 0 ? "n" + w.resolved : "-"}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-4 pt-3 border-t border-line-subtle text-2xs text-fg-muted leading-relaxed space-y-1">
        <div>
          <span className="text-pos font-semibold">stabil</span>: jedes Fenster mit Daten erfuellt n&gt;={minResolved} UND Wilson-Untergrenze&gt;={minWilson.toFixed(0)}%.
        </div>
        <div>
          <span className="text-warn font-semibold">Drift</span>: mindestens ein Fenster reisst die Schwellen - Quote kippt zwischen den Fenstern.
        </div>
        <div>
          Mosaik = drei Buckets von links (alt) nach rechts (neu, ~aktuell). Hover ueber eine Kachel fuer Fenster-Details.
        </div>
      </div>
    </Card>
  );
}
