import {
  LayoutDashboard,
  Radio,
  ArrowLeftRight,
  Briefcase,
  ShieldAlert,
  Sparkles,
  Bell,
  Settings,
  Upload,
  ChevronLeft,
  ChevronRight,
  FlaskConical,
  Shield,
  Zap,
  Bot,
  Bitcoin,
  QrCode,
  Database,
  Activity,
  Map,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { StatusDot } from "@/components/ui/Primitives";
import { useState, type ReactNode } from "react";
import { useT } from "@/i18n/I18nProvider";
import { useRouter, type Route } from "@/state/Router";
import { useAppState } from "@/state/AppState";
import { KaiMark } from "@/components/brand/KaiMark";

type Item = { id: Route; labelKey: string; icon: ReactNode; count?: number; prepared?: boolean };

// Live-verdrahtet (Phase 1)
const LIVE: Item[] = [
  { id: "dashboard", labelKey: "nav.dashboard", icon: <LayoutDashboard size={16} /> },
  { id: "signals", labelKey: "nav.signals", icon: <Radio size={16} /> },
  { id: "external", labelKey: "nav.external", icon: <Upload size={16} /> },
  { id: "sources", labelKey: "nav.sources", icon: <Database size={16} /> },
  { id: "trades", labelKey: "nav.trades", icon: <ArrowLeftRight size={16} /> },
  { id: "portfolio", labelKey: "nav.portfolio", icon: <Briefcase size={16} /> },
  { id: "alerts", labelKey: "nav.alerts", icon: <Bell size={16} /> },
  { id: "risk", labelKey: "nav.risk", icon: <ShieldAlert size={16} /> },
  { id: "ai", labelKey: "nav.ai", icon: <Sparkles size={16} /> },
];

// Souveränität (eigene Bitcoin/Lightning-Node, Truth-Layer)
const SOVEREIGN: Item[] = [
  { id: "node", labelKey: "nav.node", icon: <Bitcoin size={16} /> },
  { id: "pay", labelKey: "nav.pay", icon: <QrCode size={16} /> },
];

// Kontroll-Ebene (Claude-Code-only Agenten)
const CONTROL: Item[] = [
  { id: "agents", labelKey: "nav.agents", icon: <Bot size={16} /> },
  { id: "roadmaps", labelKey: "nav.roadmaps", icon: <Map size={16} /> },
];

const SYSTEM: Item[] = [
  { id: "system", labelKey: "nav.system", icon: <Activity size={16} /> },
  { id: "settings", labelKey: "nav.settings", icon: <Settings size={16} /> },
];

type SidebarProps = {
  mobileOpen?: boolean;
  onMobileClose?: () => void;
};

export function Sidebar({ mobileOpen = false, onMobileClose }: SidebarProps) {
  const [collapsed, setCollapsed] = useState(false);
  const { t } = useT();
  const { route, navigate } = useRouter();
  const { mode } = useAppState();

  // 2026-09-15 DALI v2.1: Diese Pille zeigte "LIVE" in Signalgruen mit
  // pulsierendem Punkt — auf Basis von `mode` aus localStorage. Sie las sich als
  // Betriebszustand "Live-Handel laeuft", war aber eine Browser-Vorwahl ohne
  // jede Backend-Bindung. Die Handelsfreigabe steht in `execution_enabled` und
  // wird im Lage-Streifen der Uebersicht angezeigt.
  //
  // Die Pille bleibt (die Vorwahl existiert und wird in Einstellungen
  // persistiert), aber sie behauptet nichts mehr: eine Tonalitaet fuer alle
  // Modi, kein Puls, und der Text sagt, was sie ist.
  const modeLabel = mode === "live" ? t("topbar.mode_live") : mode === "sim" ? t("topbar.mode_sim") : t("topbar.mode_paper");
  const ModeIcon = mode === "live" ? Zap : mode === "sim" ? FlaskConical : Shield;

  const handleNavigate = (r: Route) => {
    navigate(r);
    if (onMobileClose) onMobileClose();
  };

  return (
    <>
      {/* Mobile overlay backdrop */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 md:hidden"
          onClick={onMobileClose}
          aria-hidden
        />
      )}
      <aside
        className={cn(
          "flex flex-col border-r border-line-subtle bg-bg-1 transition-[width,transform] duration-200 shrink-0",
          // Desktop: in flow, width-driven
          "md:relative md:translate-x-0",
          collapsed ? "md:w-[68px]" : "md:w-[232px]",
          // Mobile: fixed drawer slide-in
          "fixed top-0 left-0 z-40 h-screen w-[260px]",
          mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0",
        )}
      >
      <div className="flex items-center gap-2.5 h-14 px-4 border-b border-line-subtle">
        <KaiMark className="h-7 w-7 shrink-0 text-fg" />
        {!collapsed && (
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-semibold tracking-tight text-fg">KAI</div>
            <div className="text-2xs text-fg-subtle leading-tight">Control Center</div>
          </div>
        )}
        {/* Mobile close button */}
        {onMobileClose && (
          <button
            onClick={onMobileClose}
            className="md:hidden ml-auto h-11 w-11 grid place-items-center rounded-sm text-fg-muted hover:bg-bg-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/70"
            aria-label="Menü schließen"
          >
            <X size={16} />
          </button>
        )}
      </div>

      {!collapsed && (
        <div className="px-3 pt-3">
          <div
            className="flex items-center justify-between px-2 py-1.5 rounded-sm border border-line-subtle bg-bg-2 text-xs text-fg-muted"
            title="Lokale Ansichtsvorwahl im Browser. Sie steuert KEINE Ausführung — die echte Handelsfreigabe steht im Lage-Streifen der Übersicht (execution_enabled)."
          >
            <div className="flex items-center gap-2 min-w-0">
              <StatusDot tone="muted" />
              <ModeIcon size={12} aria-hidden />
              <span className="truncate">
                <span className="text-fg-subtle">Ansicht: </span>
                <span className="font-semibold text-fg">{modeLabel}</span>
              </span>
            </div>
            <span className="text-2xs opacity-70 font-mono shrink-0">{t("topbar.env_phase")}</span>
          </div>
        </div>
      )}

      <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-0.5">
        <NavGroup label="Live" items={LIVE} collapsed={collapsed} route={route} navigate={handleNavigate} />
        <div className="h-2" />
        <NavGroup label="Souveränität" items={SOVEREIGN} collapsed={collapsed} route={route} navigate={handleNavigate} />
        <div className="h-2" />
        <NavGroup label="Kontrolle" items={CONTROL} collapsed={collapsed} route={route} navigate={handleNavigate} />
      </nav>

      <div className="border-t border-line-subtle px-2 py-2 space-y-0.5">
        {SYSTEM.map((it) => (
          <NavItem key={it.id} item={it} active={route === it.id} collapsed={collapsed} onClick={() => handleNavigate(it.id)} />
        ))}
        <button
          onClick={() => setCollapsed((v) => !v)}
          className={cn(
            "hidden md:flex w-full items-center gap-2 h-11 lg:h-8 px-2.5 rounded-sm text-fg-muted hover:bg-bg-2 hover:text-fg transition-colors text-xs",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/70",
            collapsed && "justify-center",
          )}
          aria-label={collapsed ? "Seitenleiste ausklappen" : "Seitenleiste einklappen"}
        >
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          {!collapsed && <span>{t("nav.collapse")}</span>}
        </button>
      </div>
      </aside>
    </>
  );
}

function NavGroup({
  label,
  items,
  collapsed,
  route,
  navigate,
}: {
  label: string;
  items: Item[];
  collapsed: boolean;
  route: Route;
  navigate: (r: Route) => void;
}) {
  return (
    <div className="space-y-0.5">
      {!collapsed && (
        <div className="px-2 pt-1 pb-1.5 text-2xs font-semibold uppercase tracking-[0.1em] text-fg-subtle">
          {label}
        </div>
      )}
      {items.map((it) => (
        <NavItem
          key={it.id}
          item={it}
          active={route === it.id}
          collapsed={collapsed}
          onClick={() => navigate(it.id)}
        />
      ))}
    </div>
  );
}

function NavItem({
  item,
  active,
  collapsed,
  onClick,
}: {
  item: Item;
  active: boolean;
  collapsed: boolean;
  onClick: () => void;
}) {
  const { t } = useT();
  const label = t(item.labelKey);
  return (
    <button
      onClick={onClick}
      className={cn(
        // 2026-09-15 DALI v2.1: h-8 (32px) ist im Drawer die primaere
        // Touchflaeche und lag unter jedem Mindestmass. Mobil h-11 = 44px,
        // ab md wieder h-8 (dort zeigt eine Maus, kein Finger).
        "group relative w-full flex items-center gap-2.5 h-11 lg:h-8 px-2.5 rounded-sm text-xs transition-colors",
        // Fokus war unsichtbar: Tastatur-Navigation durch die Sidebar zeigte
        // keinen Ring, nur den Browser-Default, den `rounded-sm` verdeckt.
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/70",
        active ? "bg-bg-3 text-fg" : "text-fg-muted hover:bg-bg-2 hover:text-fg",
        collapsed && "justify-center",
      )}
      aria-current={active ? "page" : undefined}
      title={collapsed ? label : undefined}
    >
      {active && <span className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-full bg-accent" />}
      <span className={cn("shrink-0", active ? "text-accent" : item.prepared ? "text-fg-subtle/60" : "text-fg-subtle group-hover:text-fg-muted")}>
        {item.icon}
      </span>
      {!collapsed && (
        <>
          <span className={cn("flex-1 text-left truncate font-medium", item.prepared && !active && "text-fg-muted/70")}>{label}</span>
          {item.prepared && (
            <span className="ml-auto rounded-xs border border-line-subtle bg-bg-2 px-1.5 py-0.5 text-[9px] font-mono text-fg-subtle uppercase tracking-wider">
              geplant
            </span>
          )}
          {item.count !== undefined && !item.prepared && (
            <span
              className={cn(
                "ml-auto rounded-xs border px-1.5 py-0.5 text-2xs font-mono",
                active
                  ? "border-accent/30 bg-accent-soft text-accent"
                  : "border-line-subtle bg-bg-2 text-fg-subtle",
              )}
            >
              {item.count}
            </span>
          )}
        </>
      )}
    </button>
  );
}
