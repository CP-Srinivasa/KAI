import { Info, ShieldAlert, TrendingUp, TrendingDown, Eye } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { PageHeader } from "@/layout/PageHeader";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { Badge, Card, CardHeader } from "@/components/ui/Primitives";
import { Gauge } from "@/components/viz/Gauge";
import { useApi } from "@/lib/useApi";
import { LiveDot } from "@/components/ui/LiveDot";
import { liveDotProps } from "@/lib/freshness";
import { fetchExposureSummary, fetchOperatorReadiness } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useCurrency } from "@/state/CurrencyProvider";
import { humanizeLabel } from "@/lib/labels";

const METRIC_HINT: Record<string, string> = {
  gross_exposure: "Summe aller absoluten Positionswerte in USD. Höher = mehr Kapital im Markt.",
  net_exposure: "Long minus Short in USD. Richtungsbias des Portfolios.",
  priced_positions: "Positionen mit frischem Marktpreis (Freshness-Gate bestanden).",
  stale_positions: "Positionen mit altem Preis — Mark-to-Market ist ungenau.",
  unavailable_price: "Positionen ohne verfügbaren Preis (Provider-Ausfall oder Symbol-Mismatch).",
  mark_to_market: "Qualität der aktuellen Portfolio-Bewertung (fresh / degraded / unavailable).",
  largest_position: "Größte Einzelposition als Konzentrations-Indikator.",
  status: "Readiness-Gesamtstatus aus Trading-Loop, Risk-Gate und Approval-Mode.",
  execution_enabled: "Aktiviert echte Order-Execution. In KAI fail-closed: immer off bis explizit freigegeben.",
  write_back_allowed: "Erlaubt Write-Operationen auf Trading-Journal. Nur im Paper-Mode true.",
  report_type: "Report-Typ-Kennung des Endpoints (intern).",
};

export function RiskPage() {
  const { t } = useT();
  const { fmt } = useCurrency();
  const fmt$ = (v: number | null | undefined, d = 2) =>
    v == null ? "—" : fmt(v, undefined, d);
  const exposure = useApi(fetchExposureSummary, 30_000);
  const readiness = useApi(fetchOperatorReadiness, 60_000);

  // 2026-05-10 DALI-R-Heroes: 3 Risiko-KPIs aus Exposure-Data abgeleitet.
  const exp = exposure.state === "ready" ? exposure.data : null;
  const concentrationPct = exp?.largest_position_weight_pct ?? 0;
  const totalPositions = exp ? (exp.priced_position_count + exp.stale_position_count + exp.unavailable_price_count) : 0;
  const valuationHealthPct = totalPositions > 0
    ? (exp ? (exp.priced_position_count / totalPositions) * 100 : 0)
    : 100;
  // Long/Short-Balance: |net|/gross zeigt wie einseitig das Portfolio ist.
  const directionalBiasPct = exp && exp.gross_exposure_usd > 0
    ? Math.abs(exp.net_exposure_usd / exp.gross_exposure_usd) * 100
    : 0;
  const isLongBiased = exp ? exp.net_exposure_usd >= 0 : true;

  // Tone-Logik je KPI
  const concTone = concentrationPct > 70 ? "neg" : concentrationPct > 40 ? "warn" : "pos";
  const healthTone = valuationHealthPct < 50 ? "neg" : valuationHealthPct < 90 ? "warn" : "pos";
  const biasTone = directionalBiasPct > 80 ? "warn" : "pos";

  // Gesamtbewertung
  const riskScores = [concTone, healthTone, biasTone];
  const negCount = riskScores.filter((s) => s === "neg").length;
  const warnCount = riskScores.filter((s) => s === "warn").length;
  const overallTone = negCount > 0 ? "neg" : warnCount >= 2 ? "warn" : warnCount === 1 ? "info" : "pos";
  const overallHeadline =
    overallTone === "neg" ? "Erhöhtes Risiko"
    : overallTone === "warn" ? "Aufmerksamkeit nötig"
    : overallTone === "info" ? "Auffälligkeit"
    : "Risiko-Lage stabil";

  // Klartext-Hinweise für den "Was ist gerade gefährlich?"-Banner
  const dangerNotes: string[] = [];
  if (concentrationPct > 70) dangerNotes.push(`Klumpenrisiko: ${concentrationPct.toFixed(0)}% in ${exp?.largest_position_symbol ?? "einer Position"}.`);
  if (concentrationPct > 40 && concentrationPct <= 70) dangerNotes.push(`Erhöhte Konzentration: ${concentrationPct.toFixed(0)}% in ${exp?.largest_position_symbol ?? "einer Position"}.`);
  if (exp && exp.unavailable_price_count > 0) dangerNotes.push(`${exp.unavailable_price_count} Position(en) ohne Marktpreis — Bewertung unsicher.`);
  if (exp && exp.stale_position_count > 0) dangerNotes.push(`${exp.stale_position_count} Position(en) mit veralteten Preisen.`);
  if (directionalBiasPct > 80 && exp && exp.gross_exposure_usd > 0) {
    dangerNotes.push(`Stark einseitig: ${directionalBiasPct.toFixed(0)}% des Markteinsatzes ${isLongBiased ? "long" : "short"}.`);
  }

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.risk.title")}
        sub="Wo steht das Portfolio gerade — was ist sauber, was ist heikel?"
        tone="warn"
        icon={<ShieldAlert size={18} />}
        // DALI-v2 S1: divider=false - Hero-Bewertungs-Banner traegt den Glow
        // (synthwave-pulse-edge ist da bereits gesetzt, Master-Spec G4).
        divider={false}
      />

      {/* DALI-R-Hero-v2: Gesamtbewertung als Banner mit Klartext-Risikoliste.
          Operator: "Risk genau das selbe nicht aussagekraeftig und tot."
          Lösung: oben drei klare Risk-KPIs + sammelnder Bewertungs-Banner. */}
      {exp && (
        <Card
          padded
          className={cn(
            "synthwave-pulse-edge overflow-hidden border-l-4",
            overallTone === "neg" && "border-l-neg",
            overallTone === "warn" && "border-l-warn",
            overallTone === "info" && "border-l-info",
            overallTone === "pos" && "border-l-pos",
          )}
        >
          <div className="flex items-start gap-3 flex-wrap">
            <ShieldAlert
              size={22}
              className={cn(
                "mt-0.5 shrink-0",
                overallTone === "neg" && "text-neg",
                overallTone === "warn" && "text-warn",
                overallTone === "info" && "text-info",
                overallTone === "pos" && "text-pos",
              )}
              aria-hidden
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">Gesamt-Risikolage</span>
                <span
                  className={cn(
                    "font-mono font-semibold text-base",
                    overallTone === "neg" && "text-neg",
                    overallTone === "warn" && "text-warn",
                    overallTone === "info" && "text-info",
                    overallTone === "pos" && "text-pos",
                  )}
                >
                  {overallHeadline}
                </span>
                <LiveDot {...liveDotProps(exposure)} staleAfterMs={75_000} className="ml-auto self-center" />
              </div>
              {dangerNotes.length > 0 ? (
                <ul className="mt-2 space-y-1 text-xs text-fg-muted">
                  {dangerNotes.map((n, i) => (
                    <li key={i} className="flex items-start gap-2">
                      <span className="mt-1.5 inline-block h-1.5 w-1.5 rounded-full bg-warn shrink-0 glow-warn" aria-hidden />
                      <span>{n}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="mt-1 text-xs text-fg-muted">
                  Keine akuten Auffälligkeiten — Konzentration, Bewertungs-Frische und Richtungs-Bias im grünen Bereich.
                </div>
              )}
            </div>
          </div>
        </Card>
      )}

      {/* Risk-Meter: Klumpenrisiko + Richtungs-Bias als Pegel (Konzept §16). */}
      {exp && (
        <Card padded>
          <CardHeader
            title="Risk-Meter"
            subtitle="Klumpenrisiko und Richtungs-Bias als Pegel — je weiter ausgeschlagen, desto heikler."
          />
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col items-center">
              <Gauge
                value={concentrationPct}
                min={0}
                max={100}
                tone={concTone}
                label={`${concentrationPct.toFixed(0)}%`}
                className="h-16 w-32"
              />
              <div className="mt-1 text-2xs text-fg-subtle">
                Konzentration{exp.largest_position_symbol ? ` · ${exp.largest_position_symbol}` : ""}
              </div>
            </div>
            <div className="flex flex-col items-center">
              <Gauge
                value={directionalBiasPct}
                min={0}
                max={100}
                tone={biasTone}
                label={`${directionalBiasPct.toFixed(0)}%`}
                className="h-16 w-32"
              />
              <div className="mt-1 text-2xs text-fg-subtle">
                Richtungs-Bias · {exp.gross_exposure_usd > 0 ? (isLongBiased ? "long" : "short") : "ohne Einsatz"}
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* Drei Risiko-Hero-KPIs nebeneinander */}
      {exp && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <RiskKpi
            icon={<ShieldAlert size={16} />}
            label="Konzentration"
            valueText={`${concentrationPct.toFixed(0)}%`}
            sub={
              exp.largest_position_symbol
                ? `in ${exp.largest_position_symbol}`
                : "keine Positionen"
            }
            headline={
              concentrationPct > 70 ? "Klumpenrisiko"
              : concentrationPct > 40 ? "erhöht"
              : "diversifiziert"
            }
            tone={concTone}
            barPct={concentrationPct}
          />
          <RiskKpi
            icon={<Eye size={16} />}
            label="Bewertungs-Frische"
            valueText={`${valuationHealthPct.toFixed(0)}%`}
            sub={`${exp.priced_position_count}/${totalPositions || 0} mit frischem Preis`}
            headline={
              valuationHealthPct < 50 ? "kritisch"
              : valuationHealthPct < 90 ? "teilweise stale"
              : "frisch"
            }
            tone={healthTone}
            barPct={valuationHealthPct}
          />
          <RiskKpi
            icon={isLongBiased ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
            label="Richtungs-Bias"
            valueText={`${directionalBiasPct.toFixed(0)}%`}
            sub={exp.gross_exposure_usd > 0 ? (isLongBiased ? "long-lastig" : "short-lastig") : "ohne Einsatz"}
            headline={
              directionalBiasPct > 80 ? "stark einseitig"
              : directionalBiasPct > 50 ? "tendenziell"
              : "ausgewogen"
            }
            tone={biasTone}
            barPct={directionalBiasPct}
          />
        </div>
      )}

      {/* Sekundäre Detail-Metriken */}
      {exposure.state === "ready" && (
        <Card padded>
          <CardHeader
            title="Exposure-Details"
            subtitle="Live aus /operator/exposure-summary"
            right={
              <Badge tone={exposure.data.mark_to_market_status === "ok" ? "pos" : "warn"} dot>
                Bewertung: {exposure.data.mark_to_market_status === "ok" ? "frisch" : exposure.data.mark_to_market_status}
              </Badge>
            }
          />
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-xs">
            <KV k="gross_exposure" v={fmt$(exposure.data.gross_exposure_usd)} />
            <KV k="net_exposure" v={fmt$(exposure.data.net_exposure_usd)} />
            <KV k="priced_positions" v={String(exposure.data.priced_position_count)} />
            <KV
              k="stale_positions"
              v={String(exposure.data.stale_position_count)}
              tone={exposure.data.stale_position_count > 0 ? "warn" : undefined}
            />
            <KV
              k="unavailable_price"
              v={String(exposure.data.unavailable_price_count)}
              tone={exposure.data.unavailable_price_count > 0 ? "warn" : undefined}
            />
            <KV
              k="largest_position"
              v={
                exposure.data.largest_position_symbol
                  ? `${exposure.data.largest_position_symbol} (${exposure.data.largest_position_weight_pct?.toFixed(1)}%)`
                  : "—"
              }
            />
          </div>
        </Card>
      )}

      {readiness.state === "ready" && (
        <Card padded>
          <CardHeader title="System-Bereitschaft" subtitle="Wer darf gerade was machen?" />
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <KV k="status" v={readiness.data.status} tone={readiness.data.status === "ready" ? "pos" : "warn"} />
            <KV
              k="execution_enabled"
              v={readiness.data.execution_enabled ? "aktiv" : "aus"}
              tone={readiness.data.execution_enabled ? "warn" : "muted"}
            />
            <KV
              k="write_back_allowed"
              v={readiness.data.write_back_allowed ? "erlaubt" : "gesperrt"}
              tone={readiness.data.write_back_allowed ? "pos" : "muted"}
            />
            <KV k="report_type" v={readiness.data.report_type} />
          </div>
        </Card>
      )}

      {/* DALI v2 S5 M4b: Risk-Score & Volatilitaet mit Phase-Anzeige.
          Operator-Brief: "aktuelles Marktrisiko, Stabilitaet, Schwankungen,
          Risikoentwicklung + Handlungsempfehlungen + Risiko-Level + Statusfarben". */}
      <PreparedPanel
        title="Marktrisiko & Schwankungen (7 / 30 Tage)"
        reason="Wie risikoreich ist der Markt gerade? Wie stark schwanken die Preise? Wo zeigt sich Stress, wo ist es ruhig? Aggregierter Portfolio-Risk-Score plus Volatilitats-Fenster und Max-Drawdown als Marktstabilitäts-Signal."
        detail={
          <>
            Geplant: <span className="font-mono">GET /operator/risk-summary</span> — leitet aus
            <span className="font-mono"> paper_execution_audit.jsonl</span> + Exposure-Summary ab.
            Zielanzeige: Risk-Level-Badge (niedrig / mittel / hoch) + Handlungsempfehlung
            (z.B. "Position-Size reduzieren bei Vol &gt; 30d-Median").
          </>
        }
        status="roadmap"
        roadmapNote="Roadmap: GET /operator/risk-summary (Exposure + Paper-Audit, Vol 7/30d, Max-DD)."
      />

      {/* DALI v2 S5 M4c: "Missed-Signal-Analyse" -> "Verpasste Trading-Chancen"
          (Operator-Brief: Umbenennen + verstaendliche Erklaerung pro Block). */}
      <PreparedPanel
        title="Verpasste Trading-Chancen"
        reason="Welche Signale hat der Risk- oder Priority-Gate blockiert? Welche dieser Trades wären potenziell profitabel gewesen? Und welche Schutzmechanismen haben sinnvoll gegriffen? Diese Analyse zeigt sowohl Lernen (echte Chancen verpasst) als auch Bestätigung (Verluste verhindert)."
        detail={
          <>
            Erfordert Outcome-Join: <span className="font-mono">blocked_alerts.jsonl</span> ×{" "}
            <span className="font-mono">alert_outcomes.jsonl</span> mit hypothetischem
            Forward-PnL. Zielanzeige: Top-10 verpasste Chancen mit Grund (Risk-Cap / Priority-Gate /
            Konzentration / Cooldown) + tatsaechlicher 24h-Performance.
          </>
        }
        status="roadmap"
        roadmapNote="Roadmap: blocked_alerts.jsonl × alert_outcomes.jsonl (Top-10 mit Blockgrund + 24h-Forward-PnL)."
      />
    </div>
  );
}

function RiskKpi({
  icon,
  label,
  valueText,
  sub,
  headline,
  tone,
  barPct,
}: {
  icon: React.ReactNode;
  label: string;
  valueText: string;
  sub: string;
  headline: string;
  tone: "pos" | "warn" | "neg";
  barPct: number;
}) {
  return (
    <Card padded>
      <div className="flex items-baseline justify-between gap-2 mb-2">
        <div className="flex items-center gap-1.5">
          <span
            className={cn(
              "shrink-0",
              tone === "neg" && "text-neg",
              tone === "warn" && "text-warn",
              tone === "pos" && "text-pos",
            )}
            aria-hidden
          >
            {icon}
          </span>
          <span className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">{label}</span>
        </div>
        <Badge tone={tone}>{headline}</Badge>
      </div>
      <div
        className={cn(
          "font-mono text-3xl font-semibold",
          tone === "neg" && "text-neg",
          tone === "warn" && "text-warn",
          tone === "pos" && "text-pos",
        )}
      >
        {valueText}
      </div>
      <div className="mt-1 text-2xs text-fg-subtle font-mono">{sub}</div>
      <div className="mt-3 h-1.5 w-full rounded-xs bg-bg-2 overflow-hidden">
        <div
          className={cn(
            "h-full transition-all",
            tone === "neg" && "bg-neg glow-neg",
            tone === "warn" && "bg-warn glow-warn",
            tone === "pos" && "bg-pos glow-pos",
          )}
          style={{ width: `${Math.min(Math.max(barPct, 0), 100)}%` }}
        />
      </div>
    </Card>
  );
}

function KV({ k, v, tone }: { k: string; v: string; tone?: "pos" | "neg" | "warn" | "muted" }) {
  const hint = METRIC_HINT[k];
  return (
    <div className="flex items-center justify-between gap-2 overflow-hidden border-b border-line-subtle/50 py-1">
      <span className="min-w-0 inline-flex items-center gap-1">
        <span className="truncate text-2xs text-fg-subtle" title={k}>{humanizeLabel(k)}</span>
        {hint && (
          <span
            tabIndex={0}
            title={hint}
            className="inline-flex text-fg-subtle/70 hover:text-fg-subtle focus:text-fg-subtle cursor-help"
            aria-label={`${k}: ${hint}`}
          >
            <Info size={11} />
          </span>
        )}
      </span>
      <span
        className={cn(
          "shrink-0 font-mono text-right",
          tone === "pos" && "text-pos",
          tone === "neg" && "text-neg",
          tone === "warn" && "text-warn",
          tone === "muted" && "text-fg-muted",
        )}
      >
        {v}
      </span>
    </div>
  );
}
