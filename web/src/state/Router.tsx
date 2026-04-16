import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export const ROUTES = [
  "dashboard",
  "signals",
  "markets",
  "trades",
  "portfolio",
  "risk",
  "ai",
  "alerts",
  "news",
  "backtest",
  "external",
  "agents",
  "settings",
] as const;

export type Route = (typeof ROUTES)[number];

type Ctx = { route: Route; navigate: (r: Route) => void };

const RouteCtx = createContext<Ctx | null>(null);

function parseHash(): Route {
  const h = (typeof window !== "undefined" ? window.location.hash.replace(/^#/, "") : "") as Route;
  return (ROUTES as readonly string[]).includes(h) ? (h as Route) : "dashboard";
}

export function RouterProvider({ children }: { children: ReactNode }) {
  const [route, setRoute] = useState<Route>(parseHash);

  useEffect(() => {
    const h = () => setRoute(parseHash());
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);

  const navigate = useCallback((r: Route) => {
    if (window.location.hash.replace(/^#/, "") === r) return;
    window.location.hash = r;
  }, []);

  const value = useMemo<Ctx>(() => ({ route, navigate }), [route, navigate]);
  return <RouteCtx.Provider value={value}>{children}</RouteCtx.Provider>;
}

export function useRouter() {
  const c = useContext(RouteCtx);
  if (!c) throw new Error("useRouter outside provider");
  return c;
}
