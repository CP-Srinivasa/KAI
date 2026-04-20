import { useState, lazy, Suspense } from "react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { BackendStatusBanner } from "./BackendStatusBanner";
import { CommandPalette } from "@/components/CommandPalette";
import { useRouter } from "@/state/Router";
import { Dashboard } from "@/pages/Dashboard";
import { useAppState } from "@/state/AppState";

// Dashboard bleibt eager (Default-Route, First-Paint).
// Alle anderen Routen werden on-demand geladen → kleinerer Initial-Bundle.
const SignalsPage = lazy(() => import("@/pages/Signals").then((m) => ({ default: m.SignalsPage })));
const MarketsPage = lazy(() => import("@/pages/Markets").then((m) => ({ default: m.MarketsPage })));
const TradesPage = lazy(() => import("@/pages/Trades").then((m) => ({ default: m.TradesPage })));
const PortfolioPage = lazy(() =>
  import("@/pages/Portfolio").then((m) => ({ default: m.PortfolioPage })),
);
const RiskPage = lazy(() => import("@/pages/Risk").then((m) => ({ default: m.RiskPage })));
const AIInsightsPage = lazy(() =>
  import("@/pages/AIInsightsPage").then((m) => ({ default: m.AIInsightsPage })),
);
const AlertsPage = lazy(() => import("@/pages/Alerts").then((m) => ({ default: m.AlertsPage })));
const NewsPage = lazy(() => import("@/pages/News").then((m) => ({ default: m.NewsPage })));
const BacktestPage = lazy(() =>
  import("@/pages/Backtesting").then((m) => ({ default: m.BacktestPage })),
);
const ExternalSignalsPage = lazy(() =>
  import("@/pages/ExternalSignals").then((m) => ({ default: m.ExternalSignalsPage })),
);
const AgentsPage = lazy(() => import("@/pages/Agents").then((m) => ({ default: m.AgentsPage })));
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
  const { mode } = useAppState();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  return (
    <div className="min-h-screen flex bg-bg-0 text-fg">
      <Sidebar mobileOpen={mobileNavOpen} onMobileClose={() => setMobileNavOpen(false)} />
      <div className="flex-1 min-w-0 flex flex-col">
        {mode === "live" && (
          <div className="h-1 bg-gradient-to-r from-neg via-neg/60 to-neg" aria-hidden />
        )}
        <BackendStatusBanner />
        <Topbar onMobileMenuToggle={() => setMobileNavOpen((v) => !v)} />
        <main className="flex-1 min-w-0 overflow-x-hidden" key={route}>
          <Suspense fallback={<RouteFallback />}>{renderRoute(route)}</Suspense>
        </main>
      </div>
      <CommandPalette />
    </div>
  );
}

function renderRoute(r: string) {
  switch (r) {
    case "dashboard":
      return <Dashboard />;
    case "signals":
      return <SignalsPage />;
    case "markets":
      return <MarketsPage />;
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
    case "news":
      return <NewsPage />;
    case "backtest":
      return <BacktestPage />;
    case "external":
      return <ExternalSignalsPage />;
    case "agents":
      return <AgentsPage />;
    case "settings":
      return <SettingsPage />;
    default:
      return <Dashboard />;
  }
}
