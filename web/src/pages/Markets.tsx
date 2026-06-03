import { LineChart } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { PageHeader } from "@/layout/PageHeader";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { TradingViewChart } from "@/components/trading/tradingview";

export function MarketsPage() {
  const { t } = useT();
  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.markets.title")}
        sub="Multi-Asset-Marktübersicht — vorbereitet für Integration"
        tone="info"
        icon={<LineChart size={18} />}
        // DALI-v2 S1: divider=false - PreparedPanel haelt synthwave-pulse-edge
        // als Default-Pattern (Master-Spec G4 - Lichtkante in der Card).
        divider={false}
      />

      <TradingViewChart title="TradingView-Chart" />

      {/* DALI v2 S7 M6b: Marktübersicht-Panel mit DevelopmentStatus.
          Operator-Brief: "Marktstatus, aktive Assets, verknüpfte Signale,
          relevante Bewegungen, Risiko, Trendrichtung". */}
      <PreparedPanel
        title="Marktübersicht — Krypto · ETF · Aktien · Anleihen · Rohstoffe"
        reason="Wie steht der Markt gerade? Welche Assets sind aktiv, welche fallen? Wo zeigt sich Trendrichtung, wo Risiko? Klassen-gefilterte Übersicht über alle Anlageklassen mit Kurs-, Volumen- und Sentiment-Daten."
        detail={
          <>
            Geplanter Endpoint: <span className="font-mono">GET /markets/overview</span> — klassen-gefilterte
            Kurs- und Signalzuordnung. Quellen: CoinGecko (Krypto), integrierte Equity/ETF-Provider.
            Zielanzeige: Risiko-Badge pro Asset, Trend-Pfeile, Top-Movers, verknüpfte Signal-IDs.
          </>
        }
        phase="planning"
        progress={15}
        timeline="Dashboard-Roadmap — Dashboard-Konsolidierung"
      />

      {/* DALI v2 S7 M6b: Asset-Detail mit Signal-Verknuepfung (skeleton 40%). */}
      <PreparedPanel
        title="Asset-Detail mit Signal-Verknüpfung"
        reason="Drill-down pro Asset: welche Signale waren aktiv? Welche News sind verlinkt? Wie ist das Risikoprofil? Wo steht der Asset relativ zur Vergleichsgruppe? Tiefere Sicht für Operator-Entscheidungen."
        detail={
          <>
            UI-Konzept steht. Benötigt strukturiertes Markets-Mapping im Backend
            (Asset-Klassifizierung, Korrelations-Cluster) und das Operator-Signals-Endpoint.
            Zielanzeige: Trendrichtung-Banner, Signal-History, News-Liste, Risiko-Badge.
          </>
        }
        phase="planning"
        progress={20}
        timeline="Dashboard-Roadmap — nach Operator-Signals-Endpoint"
      />
    </div>
  );
}
