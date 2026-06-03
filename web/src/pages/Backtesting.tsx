import { History } from "lucide-react";
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
        tone="ai"
        icon={<History size={18} />}
        // DALI-v2 S1: divider=false - Historical-Replay-Card traegt den
        // Glow (PreparedPanel default, Master-Spec G4).
        divider={false}
      />

      {/* DALI v2 S7 M8b: Historical Replay mit DevelopmentStatus.
          Operator-Brief: "welche Strategie getestet wird, welche Ergebnisse
          existieren, welche Performance, welche Risiken, welche Strategien
          aktiv/nutzbar". */}
      <PreparedPanel
        title="Historischer Replay — Strategie-Backtesting"
        reason="Wie hätte sich eine Strategie in den letzten Tagen/Wochen geschlagen? Was war die Trefferquote, was der maximale Drawdown? Wo gab es Stress-Phasen? Deterministischer Replay über resolved alerts + Paper-Execution-Logs."
        detail={
          <>
            Rohdaten vorhanden: <span className="font-mono">alert_outcomes.jsonl</span>,{" "}
            <span className="font-mono">paper_execution_audit.jsonl</span>,{" "}
            <span className="font-mono">ph5_hold_metrics_report.json</span>.
            Geplant: <span className="font-mono">POST /operator/backtest/replay</span> mit Zeitfenster,
            Strategie-Config und deterministischem Output. Zielanzeige: Performance-Chart, Trefferquote,
            Max-Drawdown, Risiko-Verteilung.
          </>
        }
        phase="planning"
        progress={25}
        timeline="Dashboard-Roadmap — Replay-Engine als eigener Sprint"
      />

      {/* DALI v2 S7 M8b: Strategie-Bibliothek (planning 15%). */}
      <PreparedPanel
        title="Strategie-Bibliothek"
        reason="Welche Strategien existieren? Welche sind aktiv, welche im Test, welche zurückgestellt? Versionierte Konfig-Objekte mit Operator-Auswahl, Klartext-Beschreibung pro Strategie und letzter Backtest-Performance."
        detail={
          <>
            Strategien-Beispiele aktuell intern: <span className="font-mono">forward_precision_60</span>,{" "}
            <span className="font-mono">priority_9_only</span>, <span className="font-mono">bullish_only</span>,{" "}
            <span className="font-mono">high_confluence</span>. Geplant: UI-Liste mit Performance-Stats und Aktiv-Toggle.
          </>
        }
        phase="planning"
        progress={15}
        timeline="Dashboard-Roadmap — nach Backtest-Endpoint"
      />
    </div>
  );
}
