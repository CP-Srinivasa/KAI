import { useState } from "react";
import { Send, RefreshCw, AlertCircle, Inbox } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { Badge, Button, Card } from "@/components/ui/Primitives";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/layout/PageHeader";
import { useApi } from "@/lib/useApi";
import { fetchAlertAudit, postAlertTest, ApiError } from "@/lib/api";
import { formatAbsolute, formatRelative } from "@/lib/time";
import { cn } from "@/lib/utils";
import type { AlertOutcome } from "@/lib/api";

function formatResolvedAfter(seconds: number | undefined): string | null {
  if (seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 60) return `+${Math.round(seconds)}s`;
  if (seconds < 3600) return `+${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `+${Math.round(seconds / 3600)}h`;
  return `+${Math.round(seconds / 86400)}d`;
}

const OUTCOME_TONE: Record<AlertOutcome, "pos" | "neg" | "muted"> = {
  hit: "pos",
  miss: "neg",
  inconclusive: "muted",
};

export function AlertsPage() {
  const { t } = useT();
  const audit = useApi(fetchAlertAudit, 30_000);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  async function runTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await postAlertTest();
      const ok = res.results.filter((r) => r.success).length;
      setTestResult(`${ok}/${res.dispatched} Kanäle erfolgreich.`);
      audit.reload();
    } catch (e) {
      if (e instanceof ApiError) setTestResult(`Fehler (${e.kind}): ${e.message}`);
      else setTestResult(`Fehler: ${(e as Error).message}`);
    } finally {
      setTesting(false);
    }
  }

  const entries = audit.state === "ready" ? audit.data.alerts : [];
  const rows = entries.slice(-50).reverse();
  const total = audit.state === "ready" ? audit.data.total_alerts : 0;
  const totalResolved = audit.state === "ready" ? audit.data.total_resolved ?? 0 : 0;

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.alerts.title")}
        sub={
          audit.state === "ready"
            ? `${total} Einträge · ${totalResolved} aufgelöst · zeige jüngste ${rows.length}`
            : t("pages.alerts.sub")
        }
        right={
          <div className="flex items-center gap-2">
            <Button onClick={() => audit.reload()} variant="outline" size="sm">
              <RefreshCw size={12} /> Aktualisieren
            </Button>
            <Button onClick={runTest} disabled={testing} variant="primary" size="sm">
              <Send size={12} /> {testing ? "Sende …" : "Test-Alert senden"}
            </Button>
          </div>
        }
      />

      {testResult && (
        <Card padded className="border-accent/30 bg-accent/5">
          <div className="text-xs text-fg">{testResult}</div>
        </Card>
      )}

      {audit.state === "error" && (
        <Card padded className="border-neg/30 bg-neg/5">
          <div className="flex items-start gap-3 text-xs text-neg">
            <AlertCircle size={16} className="mt-0.5 shrink-0" />
            <div className="min-w-0">
              <div className="font-semibold">Alert-Audit nicht erreichbar</div>
              <div className="text-fg-muted mt-1 break-words">
                {audit.error.kind} · {audit.error.message}
              </div>
              <div className="text-2xs text-fg-subtle mt-1 font-mono break-all">GET /operator/alert-audit</div>
            </div>
          </div>
        </Card>
      )}

      {audit.state === "ready" && rows.length === 0 ? (
        <EmptyState
          icon={<Inbox size={18} />}
          title="Noch keine Alerts"
          hint="Sobald directional Alerts dispatched werden, erscheinen sie hier. Test-Alert oben sendet einen Ping durch alle konfigurierten Kanäle."
          action={
            <Button onClick={runTest} disabled={testing} variant="outline" size="sm">
              <Send size={12} /> {testing ? "Sende …" : "Test-Alert senden"}
            </Button>
          }
        />
      ) : (
        <Card padded={false}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-fg-subtle text-2xs uppercase tracking-wider">
                  <th className="text-left font-semibold px-4 py-2">{t("common.time")}</th>
                  <th className="text-left font-semibold px-4 py-2">Document</th>
                  <th className="text-left font-semibold px-4 py-2">{t("common.channel")}</th>
                  <th className="text-left font-semibold px-4 py-2">Digest</th>
                  <th className="text-left font-semibold px-4 py-2">Aufgelöst</th>
                  <th className="text-left font-semibold px-4 py-2">Message-ID</th>
                </tr>
              </thead>
              <tbody>
                {audit.state === "loading" && (
                  <tr>
                    <td colSpan={6} className="px-4 py-6 text-center text-fg-subtle">
                      {t("common.loading")}
                    </td>
                  </tr>
                )}
                {rows.map((a, i) => {
                  const durationLabel = formatResolvedAfter(a.resolved_after_seconds);
                  return (
                    <tr key={`${a.document_id}-${i}`} className="border-t border-line-subtle hover:bg-bg-2">
                      <td className="px-4 py-2 text-xs text-fg-muted" title={formatAbsolute(a.dispatched_at)}>
                        {formatRelative(a.dispatched_at)}
                      </td>
                      <td className="px-4 py-2 font-mono text-2xs text-fg-muted">
                        {a.document_id.substring(0, 18)}
                      </td>
                      <td className="px-4 py-2">
                        <Badge tone={a.channel === "telegram" ? "info" : "muted"}>{a.channel}</Badge>
                      </td>
                      <td className="px-4 py-2">
                        {a.is_digest ? (
                          <Badge tone="warn">digest</Badge>
                        ) : (
                          <span className="text-fg-subtle text-2xs">—</span>
                        )}
                      </td>
                      <td className="px-4 py-2">
                        {a.outcome ? (
                          <span
                            className="inline-flex items-center gap-1.5"
                            title={a.resolved_at ? `resolved at ${formatAbsolute(a.resolved_at)}` : undefined}
                          >
                            <Badge tone={OUTCOME_TONE[a.outcome]}>{a.outcome}</Badge>
                            {durationLabel && (
                              <span className="font-mono text-2xs text-fg-subtle">{durationLabel}</span>
                            )}
                          </span>
                        ) : (
                          <span className="text-fg-subtle text-2xs">offen</span>
                        )}
                      </td>
                      <td className="px-4 py-2 font-mono text-2xs text-fg-subtle">
                        <span className={cn(a.message_id === "dry_run" && "text-warn")}>
                          {a.message_id ?? "—"}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
