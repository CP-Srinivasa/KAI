import { useState } from "react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { BackendStatusBanner } from "./BackendStatusBanner";
import { useRouter } from "@/state/Router";
import { Dashboard } from "@/pages/Dashboard";
import { SignalsPage } from "@/pages/Signals";
import { MarketsPage } from "@/pages/Markets";
import { TradesPage } from "@/pages/Trades";
import { PortfolioPage } from "@/pages/Portfolio";
import { RiskPage } from "@/pages/Risk";
import { AIInsightsPage } from "@/pages/AIInsightsPage";
import { AlertsPage } from "@/pages/Alerts";
import { NewsPage } from "@/pages/News";
import { BacktestPage } from "@/pages/Backtesting";
import { ExternalSignalsPage } from "@/pages/ExternalSignals";
import { AgentsPage } from "@/pages/Agents";
import { SettingsPage } from "@/pages/Settings";
import { useAppState } from "@/state/AppState";

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
          {renderRoute(route)}
        </main>
      </div>
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
