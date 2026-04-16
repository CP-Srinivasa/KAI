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
      />

      <TradingViewChart title="TradingView-Chart" />

      <PreparedPanel
        title="Marktübersicht (Crypto · ETF · Equities · Bonds · Commodities)"
        reason="Kurs-, Volumen- und Sentiment-Live-Daten über ein einheitliches Markets-API werden noch nicht vom Backend bereitgestellt."
        detail={
          <>
            Geplanter Endpoint: <span className="font-mono">GET /markets/overview</span> mit klassen-gefilterter Kurs- und Signalzuordnung.
            Datenquellen: CoinGecko (crypto), integrierte Equity/ETF-Provider — in Phase 2 der Dashboard-Konsolidierung.
          </>
        }
      />

      <PreparedPanel
        title="Asset-Detail & verknüpfte Signale"
        reason="Drill-down von Asset → aktive Signale → zugehörige News ist UI-seitig vorgesehen, benötigt aber strukturiertes Markets-Mapping im Backend."
        detail="Phase 2 · nach Operator-Signals-Endpoint."
      />
    </div>
  );
}
