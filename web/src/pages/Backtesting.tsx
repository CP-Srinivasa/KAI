import { useT } from "@/i18n/I18nProvider";
import { PageHeader } from "@/layout/PageHeader";
import { PreparedPanel } from "@/components/panels/PreparedPanel";

export function BacktestPage() {
  const { t } = useT();
  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.backtest.title")}
        sub="Historical Replay & Strategy-Validation — vorbereitet"
      />

      <PreparedPanel
        title="Historical Replay (Strategie-Backtesting)"
        reason="Echte Backtests erfordern deterministische Replay-Engine über resolved alerts + Paper-Execution-Logs. Simulierte Ergebnisse wurden entfernt (no fake UI)."
        detail={
          <>
            Rohdaten vorhanden: <span className="font-mono">alert_outcomes.jsonl</span>, <span className="font-mono">paper_execution_audit.jsonl</span>,
            <span className="font-mono"> ph5_hold_metrics_report.json</span>. Geplant: <span className="font-mono">POST /operator/backtest/replay</span> mit
            Zeitfenster, Strategie-Config und deterministischem Output.
          </>
        }
      />

      <PreparedPanel
        title="Strategy-Library"
        reason="Strategien (forward_precision_60, priority_9_only, bullish_only …) werden später als versionierte Konfig-Objekte geführt und über die UI ausgewählt."
        detail="Phase 2 · nach Backtest-Endpoint."
      />
    </div>
  );
}
