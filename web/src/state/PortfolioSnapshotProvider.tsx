import { createContext, useContext, type ReactNode } from "react";
import { useApi, type AsyncState } from "@/lib/useApi";
import { fetchPortfolioSnapshot, type PortfolioSnapshot } from "@/lib/api";

/**
 * EIN Portfolio-Snapshot fuer die ganze Uebersicht.
 *
 * 2026-09-09 gemessen: die Uebersicht feuerte pro Mount 3x
 * /operator/portfolio-snapshot (PortfolioTile, AllocationTile,
 * ExecutiveSnapshot) und 2x /operator/exposure-summary (RiskMeterTile,
 * ExecutiveSnapshot) — alle 30 s. Serverseitig bauen beide Endpunkte denselben
 * Snapshot, und jeder Bau macht drei Vollpaesse ueber ein 5,2-MB-Audit
 * (Epoch-Scan, Replay, Pydantic-Validierung) plus einen Marktdaten-Fan-out.
 * Macht 15 Vollpaesse und 5 Fan-outs alle 30 Sekunden fuer vier Kacheln, auf
 * einem Raspberry Pi, in einem Single-Worker-Event-Loop.
 *
 * Der volle AsyncState wird durchgereicht, nicht ``data | null``: sonst wuerden
 * bei einem Fehlschlag vier Kacheln still leer statt sichtbar kaputt — der
 * Fehlerzustand ist Teil der Aussage, gerade auf der Risiko-Kachel.
 */
const PortfolioSnapshotContext = createContext<AsyncState<PortfolioSnapshot> | null>(null);

export function PortfolioSnapshotProvider({
  children,
  refreshMs = 30_000,
}: {
  children: ReactNode;
  refreshMs?: number;
}) {
  const snap = useApi(fetchPortfolioSnapshot, refreshMs);
  return (
    <PortfolioSnapshotContext.Provider value={snap}>{children}</PortfolioSnapshotContext.Provider>
  );
}

/**
 * Der geteilte Snapshot, wenn ein Provider darueber liegt.
 *
 * Ausserhalb des Providers ``null`` — die Aufrufer fallen dann auf ihren
 * eigenen ``useApi`` zurueck. So bleiben Portfolio-Seite und Uebersicht
 * unabhaengig, ohne dass ein Panel doppelt laedt, sobald beide unter dem
 * Provider haengen.
 */
export function useSharedPortfolioSnapshot(): AsyncState<PortfolioSnapshot> {
  const ctx = useContext(PortfolioSnapshotContext);
  if (ctx === null) {
    // Bewusst laut: ein stiller Fallback auf einen eigenen useApi wuerde genau
    // die Mehrfachabfrage zurueckbringen, die dieser Provider abschafft.
    throw new Error(
      "useSharedPortfolioSnapshot ohne <PortfolioSnapshotProvider> — " +
        "die Kachel wuerde sonst wieder einzeln laden.",
    );
  }
  return ctx;
}
