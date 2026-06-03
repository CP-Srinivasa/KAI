import { Newspaper } from "lucide-react";
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
        tone="info"
        icon={<Newspaper size={18} />}
        // DALI-v2 S1: divider=false - Live-News-Stream-Card haelt die
        // Lichtkante (PreparedPanel default, Master-Spec G4).
        divider={false}
      />

      {/* DALI v2 S6 M7b: Live News-Stream Panel mit DevelopmentStatus.
          Backend ingestiert bereits News (skeleton 50%), Read-Endpoint fehlt. */}
      <PreparedPanel
        title="Live-News-Stream"
        reason="Welche News kommen gerade rein? Wie ist die Stimmung pro Headline? Welche Märkte sind betroffen? Stream aus klassifizierten RSS-Quellen, Feeds und kuratierter Source-Liste."
        detail={
          <>
            Backend-Ingestion läuft (RSS, Feeds, klassifizierte Quellen werden bereits verarbeitet).
            Geplanter Read-Endpoint: <span className="font-mono">GET /operator/recent-news</span> — liefert{" "}
            <span className="font-mono">artifacts/analysis_results/*.json</span> mit Sentiment/Impact-Score
            und verlinkten Signal-IDs.
          </>
        }
        phase="skeleton"
        progress={50}
        timeline="Dashboard-Roadmap — nach Operator-News-Endpoint"
      />

      {/* DALI v2 S6 M7b: News-Detail mit Signal-Kontext (planning 20% - UI-Konzept). */}
      <PreparedPanel
        title="News-Detail mit Signal-Verknüpfung"
        reason="Tiefere Sicht pro News-Item: Was steht drin (Zusammenfassung)? Wer hat es geschrieben (Quelle)? Welche Signale haben darauf reagiert? Was war der Outcome? Drill-down von News → Signal → Trade-Ergebnis."
        detail={
          <>
            Zielanzeige: Drawer mit Klartext-Zusammenfassung, Quellen-Reputation, verknüpften
            Signal-IDs und Outcome-Tracking. Erfordert das Operator-News-Endpoint plus
            Signal-Linking-Tabelle.
          </>
        }
        phase="planning"
        progress={20}
        timeline="Dashboard-Roadmap — nach Live-News-Stream"
      />
    </div>
  );
}
