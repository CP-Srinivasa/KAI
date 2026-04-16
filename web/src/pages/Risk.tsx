import { useT } from "@/i18n/I18nProvider";
import { PageHeader } from "@/layout/PageHeader";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { Card, CardHeader } from "@/components/ui/Primitives";
import { useApi } from "@/lib/useApi";
import { fetchExposureSummary, fetchOperatorReadiness } from "@/lib/api";
import { cn } from "@/lib/utils";

const fmt$ = (v: number | null | undefined, d = 2) =>
  v == null ? "—" : `$${v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`;

export function RiskPage() {
  const { t } = useT();
  const exposure = useApi(fetchExposureSummary, 30_000);
  const readiness = useApi(fetchOperatorReadiness, 60_000);

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.risk.title")}
        sub="Live-Exposure (real) + Risiko-Analysen (vorbereitet)"
      />

      {exposure.state === "ready" && (
        <Card padded>
          <CardHeader title="Paper-Portfolio Exposure" subtitle="Live aus /operator/exposure-summary" />
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
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
            <KV k="mark_to_market" v={exposure.data.mark_to_market_status} />
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
          <CardHeader title="Readiness-Guardrails" subtitle="Live aus /operator/readiness" />
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <KV k="status" v={readiness.data.status} tone={readiness.data.status === "ready" ? "pos" : "warn"} />
            <KV
              k="execution_enabled"
              v={String(readiness.data.execution_enabled)}
              tone={readiness.data.execution_enabled ? "warn" : "muted"}
            />
            <KV
              k="write_back_allowed"
              v={String(readiness.data.write_back_allowed)}
              tone={readiness.data.write_back_allowed ? "pos" : "muted"}
            />
            <KV k="report_type" v={readiness.data.report_type} />
          </div>
        </Card>
      )}

      <PreparedPanel
        title="Risk-Score & Volatilität (7d / 30d)"
        reason="Aggregierter Portfolio-Risk-Score, Vola-Windows und Max-Drawdown werden noch nicht als Metrik exponiert."
        detail="Geplant: GET /operator/risk-summary — leitet aus paper_execution_audit.jsonl + Exposure ab. Phase 2."
      />

      <PreparedPanel
        title="Missed-Signal-Analyse"
        reason="Welche Signale wurden vom Gate/Risk blockiert, und was wäre der hypothetische PnL? Erfordert Outcome-Join über alert_outcomes.jsonl."
        detail="Phase 2 · nach Signal-Detail-Endpoint."
      />
    </div>
  );
}

function KV({ k, v, tone }: { k: string; v: string; tone?: "pos" | "neg" | "warn" | "muted" }) {
  return (
    <div className="flex items-center justify-between border-b border-line-subtle/50 py-1">
      <span className="font-mono text-2xs text-fg-subtle">{k}</span>
      <span
        className={cn(
          "font-mono",
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
