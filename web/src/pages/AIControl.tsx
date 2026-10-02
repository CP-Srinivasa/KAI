// @data-source: /dashboard/api/ai/control, /dashboard/api/ai/control/history
//
// KI-Kontrollstation, Stufe 1 (nur lesend): Laeuft alles? Wer macht was, und was
// verbraucht es? Wo muss ich eingreifen? Eingriffe folgen in Stufe 2.

import { PanelErrorBoundary } from "@/components/PanelErrorBoundary";
import {
  Accounts,
  AttentionList,
  Connections,
  HeaderStrip,
  History,
  Protocol,
  Workloads,
} from "@/components/aicontrol/Sections";
import { PanelError, PanelLoading } from "@/components/ui/PanelState";
import { PageHeader } from "@/layout/PageHeader";
import { STATE_META, fetchAiControl, fetchAiControlHistory, type AiControlState } from "@/lib/aiControl";
import { useApi } from "@/lib/useApi";

export function AIControlPage() {
  const ctl = useApi(fetchAiControl, 30_000);
  const hist = useApi(fetchAiControlHistory, 300_000);
  const d = ctl.state === "ready" ? ctl.data : null;
  const zaehler = d
    ? (Object.entries(d.summary.state_counts) as [AiControlState, number][])
        .filter(([s]) => s in STATE_META)
        .map(([s, n]) => `${STATE_META[s].symbol} ${n} ${STATE_META[s].label.toLowerCase()}`)
        .join(" · ")
    : "";

  return (
    <div className="mx-auto max-w-[1680px] space-y-4 p-4 xl:p-5">
      <PageHeader
        title="KI-Kontrolle"
        sub="Läuft alles? Wer macht was, und was verbraucht es? Wo muss ich eingreifen?"
        tone="ai"
        right={<span className="font-mono text-xs text-fg-muted">{zaehler}</span>}
      />
      {ctl.state === "loading" ? <PanelLoading /> : null}
      {ctl.state === "error" ? (
        <PanelError
          title="KI-Kontrolle nicht verfügbar"
          error={{
            kind: ctl.error.kind,
            message:
              ctl.error.kind === "not_found"
                ? "Das laufende Backend kennt /dashboard/api/ai/control noch nicht – vermutlich ist sein Release älter als dieses Dashboard."
                : ctl.error.message,
            code: ctl.error.status ? `HTTP ${ctl.error.status}` : null,
          }}
          onRetry={ctl.reload}
        />
      ) : null}
      {d ? (
        <PanelErrorBoundary name="KI-Kontrolle">
          <div className="space-y-4">
            <HeaderStrip s={d.summary} />
            <AttentionList items={d.attention} />
            <Connections c={d.connections} reasons={d.null_reasons} />
            <Workloads items={d.workloads} />
            <Accounts items={d.accounts} writtenAt={d.accounts_written_at} />
            <Protocol items={d.protocol} />
            <History h={hist.state === "ready" ? hist.data : null} />
          </div>
        </PanelErrorBoundary>
      ) : null}
    </div>
  );
}
