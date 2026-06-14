// @data-source: props (parent-provided)
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

type RowTag = { text: string; tone: "warn" | "neg" | "muted" | "info" };

type Row = {
  key: string;
  label: string;
  sub: string;
  value: string;
  tone: QualityTone;
  hint: string;
  /** Kleiner Status-Tag rechts neben dem Label (z.B. INSUFFICIENT, TAGGING VOLUME). */
  tag?: RowTag;
};

function fmtPct(v: number | null | undefined): string {
  return v != null ? v.toFixed(2) + "%" : "-";
}

function fmtOptionalPct(v: number | null | undefined, missing: string): string {
  return v != null ? v.toFixed(2) + "%" : missing;
}

function buildRows(data: DashboardQuality): Row[] {
  // DALI Truth-Sprint 2026-06-04: High-P ist nur dann "gut", wenn der
  // Priority-Tier-Lift positiv UND belegt ist — sonst ist die Priorisierung
  // nicht validiert. Low-P ohne Stichprobe = insufficient, nicht nacktes "-".
  const lift = data.priority_tier_lift_pct;
  const liftProven = lift != null && lift > 0;
  const lowPInsufficient = data.low_priority_hit_rate_pct == null;

  const highTag: RowTag | undefined = liftProven
    ? undefined
    : lift != null && lift <= 0
      ? { text: "Lift ≤0", tone: "neg" }
      : { text: "unbewiesen", tone: "warn" };

  return [
    {
      key: "actionable",
      label: "Actionable Rate",
      sub: "Anteil verwertbarer Signale",
      value: fmtPct(data.actionable_rate_pct),
      tone: "neutral",
      tag: { text: "kontext", tone: "muted" },
      hint: "Anteil aller eingehenden Signale, die nach den Filtern als handelbar eingestuft wurden. Hohe Quote = viele Signale schaffen es durch die Quality-Gates. ACHTUNG: Eine sehr hohe Rate kann auch lockere Gates bedeuten, nicht bessere Qualitaet.",
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
      // Nur pos, wenn Lift belegt — sonst neutral mit Warn-Tag.
      tone: liftProven ? "pos" : "neutral",
      tag: highTag,
      hint: "Trefferquote der hoechsten Prioritaetsstufe (groesste Confluence, staerkste Source-Mix). Nur positiv zu lesen, wenn der Priority-Tier-Lift positiv und belegt ist — sonst ist die Priorisierung nicht validiert.",
    },
    {
      key: "lo-hit",
      label: "Low-Priority Hit Rate",
      sub: "Treffer der schwaecheren Signale",
      value: fmtOptionalPct(data.low_priority_hit_rate_pct, "insufficient"),
      tone: "neutral",
      tag: lowPInsufficient ? { text: "insufficient", tone: "warn" } : undefined,
      hint: "Trefferquote der niedrigeren Prioritaetsstufen. Wenn hier insufficient steht, gibt es keine belastbare Low-P-Stichprobe; die Priorisierung darf dann nicht als validiert gelten.",
    },
    {
      key: "dir-docs",
      label: "Direktionale Dokumente",
      sub: "Long/Short-tagged Quellen heute",
      value: String(data.directional_count),
      tone: "neutral",
      tag: { text: "tagging volume", tone: "muted" },
      hint: "Wieviele Quelldokumente heute eine Richtung (Long/Short) bekommen haben. Dies ist ein Tagging-VOLUMEN, kein Qualitaets-Indikator.",
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
                <div className="flex items-center gap-1.5 text-xs text-fg flex-wrap">
                  <span className="font-medium">{r.label}</span>
                  <InfoHint label={r.label} hint={r.hint} />
                  {r.tag && (
                    <span
                      className={cn(
                        "rounded-xs border px-1 py-0 text-[9px] font-mono uppercase tracking-wider",
                        r.tag.tone === "neg" && "border-neg/40 bg-neg/10 text-neg",
                        r.tag.tone === "warn" && "border-warn/40 bg-warn/10 text-warn",
                        r.tag.tone === "info" && "border-info/40 bg-info/10 text-info",
                        r.tag.tone === "muted" && "border-line bg-bg-2 text-fg-subtle",
                      )}
                    >
                      {r.tag.text}
                    </span>
                  )}
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
