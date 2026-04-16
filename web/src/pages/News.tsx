import { useT } from "@/i18n/I18nProvider";
import { PageHeader } from "@/layout/PageHeader";
import { PreparedPanel } from "@/components/panels/PreparedPanel";

export function NewsPage() {
  const { t } = useT();
  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.news.title")}
        sub="News-Stream mit Sentiment, Impact & Signal-Verknüpfung — vorbereitet"
      />

      <PreparedPanel
        title="Live News-Stream"
        reason="Das Ingestion-Backend verarbeitet News bereits (RSS, Feeds, klassifizierte Quellen) — ein Operator-Read-Endpoint für die UI-Konsole fehlt noch."
        detail={
          <>
            Geplante Route: <span className="font-mono">GET /operator/recent-news</span> — lesen aus <span className="font-mono">artifacts/analysis_results/*.json</span>,
            ergänzt um Sentiment/Impact-Score und verlinkte Signal-IDs.
          </>
        }
      />

      <PreparedPanel
        title="News-Detail mit Signal-Kontext"
        reason="Drawer mit Zusammenfassung, Quelle, verknüpften Signalen und Outcome-Tracking ist UI-seitig vorgesehen."
        detail="Phase 2 · nach Operator-News-Endpoint."
      />
    </div>
  );
}
