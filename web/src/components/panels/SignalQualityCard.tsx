import { memo, useMemo } from "react";
import { Card, CardHeader, InfoHint } from "@/components/ui/Primitives";
import { LiveDot } from "@/components/ui/LiveDot";
import type { DashboardQuality } from "@/lib/api";
import { cn } from "@/lib/utils";

// 2026-05-11 DALI Operator-Klarheit:
//   - Deutscher Untertitel pro Kennzahl ("Anteil verwertbarer Signale").
//   - InfoHint mit deutscher Definition pro Metrik.
//   - Visueller Trennstrich zwischen High-P und Low-P fuer klarere Hierarchie.

type Props = {
  data: DashboardQuality | null;
  state: "loading" | "ready" | "error";
  generatedAt: string | null;
};

type QualityTone = "pos" | "neg" | "neutral";

type Row = {
  key: string;
  label: string;
  sub: string;
  value: string;
  tone: QualityTone;
  hint: string;
};

function fmtPct(v: number | null | undefined): string {
  return v != null ? v.toFixed(2) + "%" : "-";
}

function fmtOptionalPct(v: number | null | undefined, missing: string): string {
  return v != null ? v.toFixed(2) + "%" : missing;
}

function buildRows(data: DashboardQuality): Row[] {
  return [
    {
      key: "actionable",
      label: "Actionable Rate",
      sub: "Anteil verwertbarer Signale",
      value: fmtPct(data.actionable_rate_pct),
      tone: "neutral",
      hint: "Anteil aller eingehenden Signale, die nach den Filtern als handelbar eingestuft wurden. Hohe Quote = viele Signale schaffen es durch die Quality-Gates. Sehr hohe Quote kann auch heissen: Gates zu locker.",
    },
    {
      key: "fp",
      label: "False Positive",
      sub: "Anteil aufgeloester Fehlsignale",
      value: fmtPct(data.false_positive_pct),
      tone: "neg",
      hint: "Anteil der aufgeloesten Signale, die sich rueckblickend als falsch entpuppt haben (SL getroffen statt TP). Niedrig ist gut. Wird aus dem Hold-Report berechnet.",
    },
    {
      key: "hi-hit",
      label: "High-Priority Hit Rate",
      sub: "Treffer der wichtigsten Signale",
      value: fmtPct(data.high_priority_hit_rate_pct),
      tone: "pos",
      hint: "Trefferquote der hoechsten Prioritaetsstufe (groesste Confluence, staerkste Source-Mix). Sollte deutlich ueber der Low-P-Quote liegen, sonst ist die Priorisierung kaputt.",
    },
    {
      key: "lo-hit",
      label: "Low-Priority Hit Rate",
      sub: "Treffer der schwaecheren Signale",
      value: fmtOptionalPct(data.low_priority_hit_rate_pct, "insufficient"),
      tone: "neutral",
      hint: "Trefferquote der niedrigeren Prioritaetsstufen. Wenn hier insufficient steht, gibt es keine belastbare Low-P-Stichprobe; die Priorisierung darf dann nicht als validiert gelten.",
    },
    {
      key: "dir-docs",
      label: "Direktionale Dokumente",
      sub: "Long/Short-tagged Quellen heute",
      value: String(data.directional_count),
      tone: "neutral",
      hint: "Wieviele Quelldokumente heute eine Richtung (Long/Short) bekommen haben. Indikator fuer das Tagging-Volumen, nicht fuer Qualitaet.",
    },
  ];
}

function SignalQualityCardImpl({ data, state, generatedAt }: Props) {
  const rows = useMemo(() => (data ? buildRows(data) : []), [data]);

  return (
    <Card padded>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-1.5">
            Signal-Qualitaet
            <InfoHint
              label="Signal-Qualitaet"
              hint="Fuenf rollende Kennzahlen aus dem letzten Hold-Report. Zeigen, wie gut die Pipeline aktuell zwischen guten und schlechten Signalen trennt."
            />
          </span>
        }
        right={<LiveDot state={state} generatedAt={generatedAt} />}
      />
      {data ? (
        <div className="divide-y divide-line-subtle/60">
          {rows.map((r) => (
            <div key={r.key} className="flex items-start justify-between gap-3 py-2 first:pt-0 last:pb-0">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 text-xs text-fg">
                  <span className="font-medium">{r.label}</span>
                  <InfoHint label={r.label} hint={r.hint} />
                </div>
                <div className="text-2xs text-fg-subtle leading-snug mt-0.5">{r.sub}</div>
              </div>
              <span
                className={cn(
                  "font-mono font-semibold text-sm shrink-0 tabular-nums",
                  r.tone === "pos" && "text-pos",
                  r.tone === "neg" && "text-neg",
                  r.tone === "neutral" && "text-fg",
                )}
              >
                {r.value}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <div className="py-4 text-center text-xs text-fg-subtle">
          Quality-Report laedt...
        </div>
      )}
    </Card>
  );
}

export const SignalQualityCard = memo(SignalQualityCardImpl);
