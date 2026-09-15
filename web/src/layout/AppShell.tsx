import { useState, lazy, Suspense } from "react";
import { Bell, Briefcase, LayoutDashboard, MoreHorizontal, Radio } from "lucide-react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { BackendStatusBanner } from "./BackendStatusBanner";
import { CommandPalette } from "@/components/CommandPalette";
import { PanelErrorBoundary } from "@/components/PanelErrorBoundary";
import { useRouter, type Route } from "@/state/Router";
import { Dashboard } from "@/pages/Dashboard";
import { useAppState } from "@/state/AppState";
import { cn } from "@/lib/utils";

// Dashboard bleibt eager (Default-Route, First-Paint).
// Alle anderen Routen werden on-demand geladen → kleinerer Initial-Bundle.
const SignalsPage = lazy(() => import("@/pages/Signals").then((m) => ({ default: m.SignalsPage })));
const TradesPage = lazy(() => import("@/pages/Trades").then((m) => ({ default: m.TradesPage })));
const PortfolioPage = lazy(() =>
  import("@/pages/Portfolio").then((m) => ({ default: m.PortfolioPage })),
);
const RiskPage = lazy(() => import("@/pages/Risk").then((m) => ({ default: m.RiskPage })));
const AIInsightsPage = lazy(() =>
  import("@/pages/AIInsightsPage").then((m) => ({ default: m.AIInsightsPage })),
);
const AlertsPage = lazy(() => import("@/pages/Alerts").then((m) => ({ default: m.AlertsPage })));
const ExternalSignalsPage = lazy(() =>
  import("@/pages/ExternalSignals").then((m) => ({ default: m.ExternalSignalsPage })),
);
const AgentsPage = lazy(() => import("@/pages/Agents").then((m) => ({ default: m.AgentsPage })));
const SourcesPage = lazy(() => import("@/pages/Sources").then((m) => ({ default: m.SourcesPage })));
const NodePage = lazy(() => import("@/pages/Node").then((m) => ({ default: m.NodePage })));
const PayPage = lazy(() => import("@/pages/Pay").then((m) => ({ default: m.PayPage })));
const SystemPage = lazy(() => import("@/pages/System").then((m) => ({ default: m.SystemPage })));
const RoadmapsPage = lazy(() => import("@/pages/Roadmaps").then((m) => ({ default: m.RoadmapsPage })));
const SettingsPage = lazy(() =>
  import("@/pages/Settings").then((m) => ({ default: m.SettingsPage })),
);

function RouteFallback() {
  return (
    <div className="p-6 text-sm text-fg-muted" role="status" aria-live="polite">
      Lade Modul …
    </div>
  );
}

export function AppShell() {
  const { route } = useRouter();
  const { density } = useAppState();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  return (
    <div className="min-h-screen flex bg-bg-0 text-fg" data-density={density}>
      <Sidebar mobileOpen={mobileNavOpen} onMobileClose={() => setMobileNavOpen(false)} />
      <div className="flex-1 min-w-0 flex flex-col">
        {/* 2026-09-15 DALI v2.1: die gruene "Live-Trading aktiv"-Leuchtlinie ist
            entfernt. Sie haing am localStorage-Schalter `mode`, nicht an der
            Backend-Wahrheit (`execution_enabled`) — ein Klick im Browser liess
            das Dashboard Live-Handel behaupten, den es nicht gibt. Die echte
            Freigabe steht im Lage-Streifen der Uebersicht. */}
        <BackendStatusBanner />
        <Topbar onMobileMenuToggle={() => setMobileNavOpen((v) => !v)} />
        {/* overflow-x-clip statt -hidden: `hidden` macht aus <main> einen
            Scroll-Container und damit jeden `sticky top-0`-Nachfahren wirkungslos
            (gemessen bei scrollY=800: CommandHeader auf top=-700px, die
            "nie wegscrollende Lage-Leiste" war weggescrollt). `clip` beschneidet
            genauso, erzeugt aber KEINEN Scroll-Container — sticky funktioniert.
            Das untere Padding haelt Inhalt frei von der mobilen Bottom-Nav. */}
        <main
          className="flex-1 min-w-0 overflow-x-clip pb-[calc(env(safe-area-inset-bottom)+4.25rem)] md:pb-0"
          key={route}
        >
          {/* Boundary OUTSIDE Suspense so it catches both lazy-load and render
              errors; keyed by route via the parent <main key={route}> so it
              resets on navigation. A crash in one page degrades to a reset card
              instead of blanking the whole shell. */}
          <PanelErrorBoundary name={route}>
            <Suspense fallback={<RouteFallback />}>{renderRoute(route)}</Suspense>
          </PanelErrorBoundary>
        </main>
      </div>
      <MobileBottomNav onMore={() => setMobileNavOpen(true)} />
      <CommandPalette />
    </div>
  );
}

/**
 * Mobile Haupt-Navigation (DALI v2.1).
 *
 * Bis hierher war der Hamburger-Drawer der EINZIGE Weg zu jedem Ziel: jede
 * Navigation auf dem Telefon kostete zwei Taps und verdeckte dabei den ganzen
 * Bildschirm. Vier direkte Ziele deckenden Alltag ab, alles andere bleibt
 * hinter "Mehr" im Drawer erreichbar — bewusst nicht mehr als vier, sonst ist
 * wieder nichts wichtig. Touchflaeche 44x44 (h-14 = 56px inkl. Label),
 * safe-area-Inset fuer die Home-Indicator-Zone, aria-current fuer die
 * Screenreader-Position.
 */
const MOBILE_NAV: Array<{ id: Route; label: string; icon: typeof Bell }> = [
  { id: "dashboard", label: "Übersicht", icon: LayoutDashboard },
  { id: "signals", label: "Signale", icon: Radio },
  { id: "portfolio", label: "Portfolio", icon: Briefcase },
  { id: "alerts", label: "Alerts", icon: Bell },
];

function MobileBottomNav({ onMore }: { onMore: () => void }) {
  const { route, navigate } = useRouter();
  return (
    <nav
      aria-label="Haupt-Navigation"
      className="md:hidden fixed inset-x-0 bottom-0 z-30 flex items-stretch border-t border-line-subtle bg-bg-1/95 backdrop-blur pb-[env(safe-area-inset-bottom)]"
    >
      {MOBILE_NAV.map((it) => {
        const active = route === it.id;
        const Icon = it.icon;
        return (
          <button
            key={it.id}
            type="button"
            onClick={() => navigate(it.id)}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex-1 min-w-[44px] min-h-[56px] flex flex-col items-center justify-center gap-0.5 text-2xs font-medium",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/70",
              active ? "text-accent" : "text-fg-muted",
            )}
          >
            {/* Aktiv ist nicht nur Farbe: die Oberkante markiert die Position
                zusaetzlich (Status nie nur ueber Farbe). */}
            <span
              className={cn("h-[2px] w-6 rounded-full", active ? "bg-accent" : "bg-transparent")}
              aria-hidden
            />
            <Icon size={18} aria-hidden />
            <span>{it.label}</span>
          </button>
        );
      })}
      <button
        type="button"
        onClick={onMore}
        aria-label="Mehr — alle Bereiche öffnen"
        className="flex-1 min-w-[44px] min-h-[56px] flex flex-col items-center justify-center gap-0.5 text-2xs font-medium text-fg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/70"
      >
        <span className="h-[2px] w-6 rounded-full bg-transparent" aria-hidden />
        <MoreHorizontal size={18} aria-hidden />
        <span>Mehr</span>
      </button>
    </nav>
  );
}

function renderRoute(r: string) {
  switch (r) {
    case "dashboard":
      return <Dashboard />;
    case "signals":
      return <SignalsPage />;
    case "trades":
      return <TradesPage />;
    case "portfolio":
      return <PortfolioPage />;
    case "risk":
      return <RiskPage />;
    case "ai":
      return <AIInsightsPage />;
    case "alerts":
      return <AlertsPage />;
    case "external":
      return <ExternalSignalsPage />;
    case "sources":
      return <SourcesPage />;
    case "node":
      return <NodePage />;
    case "pay":
      return <PayPage />;
    case "agents":
      return <AgentsPage />;
    case "roadmaps":
      return <RoadmapsPage />;
    case "system":
      return <SystemPage />;
    case "settings":
      return <SettingsPage />;
    default:
      return <Dashboard />;
  }
}
