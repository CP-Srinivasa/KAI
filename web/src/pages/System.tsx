// @data-source: /health · /health/timers · /operator/status
//
// System & Health (UI-Update 2026.06, WP-2.4 / Konzept §20). Eigene IA-Seite für
// den Betriebszustand: Backend-Health, Timer-Gesundheit, Execution-/Write-Back-
// Readiness — read-only, nur echte Daten. Operator-WRITE-Aktionen (run-once,
// Agent-Commands) bleiben bewusst in ihren bestehenden Surfaces; diese Seite ist
// die Lese-/Status-Ebene.
import type { ReactNode } from "react";
import { PageHeader } from "@/layout/PageHeader";
import { Card, Badge } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { PanelErrorBoundary } from "@/components/PanelErrorBoundary";
import { TimerHealthCard } from "@/components/panels/TimerHealthCard";
import { useApi } from "@/lib/useApi";
import { useBackendHealth } from "@/lib/useBackendHealth";
import { fetchTimerHealth, fetchOperatorStatus } from "@/lib/api";
import { backendHealthToStatus } from "@/lib/commandStatus";
import { timerStateToStatus } from "@/lib/systemHealth";

function StatCard({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <Card padded>
      <div className="text-2xs uppercase tracking-wider text-fg-muted">{label}</div>
      <div className="mt-1.5">{children}</div>
    </Card>
  );
}

export function SystemPage() {
  const health = useBackendHealth();
  const timers = useApi(fetchTimerHealth, 60_000);
  const op = useApi(fetchOperatorStatus, 30_000);
  const t = timers.state === "ready" ? timers.data : null;
  const o = op.state === "ready" ? op.data : null;

  return (
    <div className="p-4 xl:p-5 space-y-4 max-w-[1680px] mx-auto">
      <PageHeader title="System & Health" sub="Betriebszustand auf einen Blick — Backend, Timer, Ausführungs-Readiness." />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <StatCard label="Backend">
          <div className="flex items-center gap-2">
            <StatusPill
              kind={backendHealthToStatus(health.state)}
              label={health.state === "connected" ? `v${health.version}` : health.state}
            />
          </div>
        </StatCard>

        <StatCard label="Timer-Gesundheit">
          {t == null ? (
            <StatusPill kind={timers.state === "error" ? "critical" : "pending"} label={timers.state === "error" ? "Endpoint-Fehler" : "lädt"} />
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill kind={timerStateToStatus(t.state)} label={t.state} />
              <span className="font-mono text-2xs text-fg-subtle">
                {t.active}/{t.total} aktiv
                {t.critical_count ? ` · ${t.critical_count} kritisch` : ""}
              </span>
            </div>
          )}
        </StatCard>

        <StatCard label="Execution-Readiness">
          {o == null ? (
            <StatusPill kind={op.state === "error" ? "critical" : "pending"} label={op.state === "error" ? "Endpoint-Fehler" : "lädt"} />
          ) : (
            <div className="flex flex-wrap items-center gap-1.5">
              <StatusPill
                kind={o.execution_enabled ? "live" : "execution-off"}
                label={o.execution_enabled ? "Execution an" : "Execution aus"}
              />
              <StatusPill
                kind={o.write_back_allowed ? "operational" : "write-back-locked"}
                label={o.write_back_allowed ? "Write-Back frei" : "Write-Back gesperrt"}
              />
              {o.status && <Badge tone="muted">{o.status}</Badge>}
            </div>
          )}
        </StatCard>
      </div>

      <PanelErrorBoundary name="Timer-Health">
        <TimerHealthCard />
      </PanelErrorBoundary>
    </div>
  );
}
