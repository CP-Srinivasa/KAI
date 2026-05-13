import { useState } from "react";
import { Send, RefreshCw, AlertCircle, Inbox, Bell, Info, CheckCircle2, XCircle } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { Badge, Button, Card } from "@/components/ui/Primitives";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/layout/PageHeader";
import { useApi } from "@/lib/useApi";
import { fetchAlertAudit, postAlertTest, ApiError } from "@/lib/api";
import { formatAbsolute, formatRelative } from "@/lib/time";
import { cn } from "@/lib/utils";
import { OUTCOME_LABEL_DE } from "@/lib/labels";
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

// 2026-05-10 DALI-A1: Send-Status aus message_id ableiten.
// dry_run → kein realer Versand · null → kein Beleg · sonst → versendet.
type SendStatus = "sent" | "dry_run" | "unknown";
function deriveSendStatus(messageId: string | null): SendStatus {
  if (messageId === "dry_run") return "dry_run";
  if (messageId === null || messageId === "") return "unknown";
  return "sent";
}

// 2026-05-10 DALI-A4: strukturiertes Test-Result.
type TestResultState = {
  kind: "ok" | "error";
  headline: string;
  detail?: string;
  channels?: Array<{ channel: string; success: boolean; message_id?: string | null; error?: string }>;
};

const SENTIMENT_TONE: Record<string, "pos" | "neg" | "muted"> = {
  bullish: "pos",
  bearish: "neg",
  neutral: "muted",
  mixed: "muted",
};

export function AlertsPage() {
  const { t } = useT();
  const audit = useApi(fetchAlertAudit, 30_000);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResultState | null>(null);

  async function runTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await postAlertTest();
      const ok = res.results.filter((r) => r.success).length;
      setTestResult({
        kind: ok === res.dispatched ? "ok" : "error",
        headline: ok === res.dispatched ? "Test-Alert erfolgreich" : "Test-Alert teilweise fehlgeschlagen",
        detail: `${ok}/${res.dispatched} Kanäle erfolgreich.`,
        channels: res.results.map((r) => ({
          channel: r.channel,
          success: r.success,
          message_id: r.message_id ?? null,
          error: r.error ?? undefined,
        })),
      });
      audit.reload();
    } catch (e) {
      if (e instanceof ApiError) {
        setTestResult({ kind: "error", headline: "Test-Alert fehlgeschlagen", detail: `${e.kind}: ${e.message}` });
      } else {
        setTestResult({ kind: "error", headline: "Test-Alert fehlgeschlagen", detail: (e as Error).message });
      }
    } finally {
      setTesting(false);
    }
  }

  const entries = audit.state === "ready" ? audit.data.alerts : [];
  const rows = entries.slice(-50).reverse();
  const total = audit.state === "ready" ? audit.data.total_alerts : 0;
  const totalResolved = audit.state === "ready" ? audit.data.total_resolved ?? 0 : 0;

  // DALI-A5-Lite: Versand-Health der angezeigten Zeilen aggregieren.
  const sendHealth = rows.reduce(
    (acc, a) => {
      const s = deriveSendStatus(a.message_id);
      acc[s]++;
      return acc;
    },
    { sent: 0, dry_run: 0, unknown: 0 },
  );

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.alerts.title")}
        tone="accent"
        icon={<Bell size={18} />}
        // DALI-v2 S1: divider=false - Synthwave-Linie wandert auf die
        // Versand-Diagnose-Card direkt darunter (Master-Spec G4).
        divider={false}
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

      {/* DALI-A3 + DALI-v2 S1: Versand-Diagnose-Hinweis mit Synthwave-Top-Edge
          (Master-Spec G4 - Lichtkante integriert statt freischwebend). */}
      <Card padded className="synthwave-pulse-edge border-l-4 border-l-info">
        <div className="flex items-start gap-3 text-xs">
          <Info size={14} className="mt-0.5 text-info shrink-0" aria-hidden />
          <div className="min-w-0 space-y-1">
            <div className="font-semibold text-fg">Versand-Diagnose — was diese Seite weiß</div>
            <div className="text-fg-muted leading-relaxed">
              <strong className="text-pos">Versendet</strong> (Message-ID vorhanden) ·{" "}
              <strong className="text-warn">Dry-Run</strong> (kein realer Versand) ·{" "}
              <strong className="text-neg">Unbekannt</strong> (kein Versand-Beleg).
            </div>
            <div className="text-2xs text-fg-subtle leading-relaxed">
              Geplant in Phase 2: HTTP-Status pro Channel, Fehler-Grund (z.B. „Telegram 429
              rate-limit"), Retry-Versuche. Voraussetzung: <span className="font-mono">AlertAuditRecord</span> in{" "}
              <span className="font-mono">app/alerts/audit.py</span> um Felder{" "}
              <span className="font-mono">delivery_status / http_status / error_reason</span> erweitern.
            </div>
          </div>
        </div>
      </Card>

      {/* DALI-A5-Lite: 3-KPI-Quick-Health der angezeigten Zeilen */}
      {audit.state === "ready" && rows.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Card padded>
            <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">Alerts (sichtbar)</div>
            <div className="mt-1 font-mono text-lg font-semibold text-info">{rows.length}</div>
            <div className="mt-1 text-2xs text-fg-subtle font-mono">jüngste 50 von {total}</div>
          </Card>
          <Card padded>
            <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">Sauber versendet</div>
            <div
              className={cn(
                "mt-1 font-mono text-lg font-semibold",
                sendHealth.sent === rows.length ? "text-pos" : "text-warn",
              )}
            >
              {sendHealth.sent}/{rows.length}
            </div>
            <div className="mt-1 text-2xs text-fg-subtle font-mono">Message-ID vorhanden</div>
          </Card>
          <Card padded>
            <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">Dry-Run / Unbekannt</div>
            <div
              className={cn(
                "mt-1 font-mono text-lg font-semibold",
                sendHealth.dry_run + sendHealth.unknown > 0 ? "text-warn" : "text-fg-muted",
              )}
            >
              {sendHealth.dry_run + sendHealth.unknown}
            </div>
            <div className="mt-1 text-2xs text-fg-subtle font-mono">
              {sendHealth.dry_run} Dry-Run · {sendHealth.unknown} unbekannt
            </div>
          </Card>
        </div>
      )}

      {/* DALI-A4: Test-Result als persistente Card mit klarem Erfolgs-Indikator,
          Per-Channel-Pills, Schließen-Button. */}
      {testResult && (
        <Card
          padded
          className={cn("border-l-4", testResult.kind === "ok" ? "border-l-pos" : "border-l-neg")}
        >
          <div className="flex items-start gap-3">
            {testResult.kind === "ok" ? (
              <CheckCircle2 size={20} className="text-pos mt-0.5 shrink-0" />
            ) : (
              <XCircle size={20} className="text-neg mt-0.5 shrink-0" />
            )}
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-fg">{testResult.headline}</div>
              {testResult.detail && (
                <div className="text-xs text-fg-muted mt-0.5">{testResult.detail}</div>
              )}
              {testResult.channels && testResult.channels.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {testResult.channels.map((c, i) => (
                    <Badge
                      key={i}
                      tone={c.success ? "pos" : "neg"}
                      dot
                      className="font-mono"
                    >
                      <span title={c.error ?? c.message_id ?? undefined}>
                        {c.channel} · {c.success ? "OK" : "FAIL"}
                      </span>
                    </Badge>
                  ))}
                </div>
              )}
            </div>
            <button
              onClick={() => setTestResult(null)}
              className="text-fg-subtle hover:text-fg shrink-0"
              aria-label="Schließen"
            >
              ×
            </button>
          </div>
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
                  <th className="text-left font-semibold px-4 py-2">Was</th>
                  <th className="text-left font-semibold px-4 py-2">{t("common.channel")}</th>
                  <th className="text-left font-semibold px-4 py-2">Versand</th>
                  <th className="text-left font-semibold px-4 py-2">Trade-Vorhersage</th>
                  <th className="text-left font-semibold px-4 py-2">Digest</th>
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
                  const sendStatus = deriveSendStatus(a.message_id);
                  const sentimentTone = a.sentiment_label
                    ? SENTIMENT_TONE[a.sentiment_label.toLowerCase()] ?? "muted"
                    : "muted";
                  return (
                    <tr key={`${a.document_id}-${i}`} className="border-t border-line-subtle hover:bg-bg-2">
                      <td className="px-4 py-2 text-xs text-fg-muted" title={formatAbsolute(a.dispatched_at)}>
                        {formatRelative(a.dispatched_at)}
                      </td>
                      {/* DALI-A2: "Was"-Spalte mit Sentiment+Assets+Priority+Source.
                          Document-Hash nur noch im title-Tooltip — kein sichtbares Murmeln. */}
                      <td className="px-4 py-2 max-w-[280px]" title={`Document-ID: ${a.document_id}`}>
                        <div className="flex items-center gap-1.5 min-w-0 flex-wrap">
                          {a.sentiment_label && (
                            <Badge tone={sentimentTone}>{a.sentiment_label}</Badge>
                          )}
                          <span className="font-mono text-xs text-fg truncate">
                            {a.affected_assets && a.affected_assets.length > 0
                              ? a.affected_assets.join(", ")
                              : "—"}
                          </span>
                          {a.priority != null && (
                            <span className="text-2xs text-fg-subtle font-mono">P{a.priority}</span>
                          )}
                        </div>
                        {a.source_name && (
                          <div className="text-2xs text-fg-subtle truncate mt-0.5">aus {a.source_name}</div>
                        )}
                      </td>
                      <td className="px-4 py-2">
                        <Badge tone={a.channel === "telegram" ? "info" : "muted"}>{a.channel}</Badge>
                      </td>
                      {/* DALI-A1: Versand-Spalte (separater Status, kein outcome). */}
                      <td className="px-4 py-2">
                        {sendStatus === "sent" && (
                          <span title={`Message-ID ${a.message_id}`}>
                            <Badge tone="pos" dot>versendet</Badge>
                          </span>
                        )}
                        {sendStatus === "dry_run" && (
                          <span title="Dry-Run-Mode: Alert wurde NICHT real versendet.">
                            <Badge tone="warn" dot>Dry-Run</Badge>
                          </span>
                        )}
                        {sendStatus === "unknown" && (
                          <span title="Kein Message-ID-Beleg — Versand-Status unbekannt.">
                            <Badge tone="neg" dot>unbekannt</Badge>
                          </span>
                        )}
                      </td>
                      {/* DALI-A1: Trade-Vorhersage-Spalte (outcome) — sprachlich getrennt von Versand. */}
                      <td className="px-4 py-2">
                        {a.outcome ? (
                          <span
                            className="inline-flex items-center gap-1.5"
                            title={a.resolved_at ? `aufgelöst am ${formatAbsolute(a.resolved_at)}` : undefined}
                          >
                            <Badge tone={OUTCOME_TONE[a.outcome]}>{OUTCOME_LABEL_DE[a.outcome]}</Badge>
                            {durationLabel && (
                              <span className="font-mono text-2xs text-fg-subtle">{durationLabel}</span>
                            )}
                          </span>
                        ) : (
                          <span className="text-fg-subtle text-2xs italic">offen — wird beobachtet</span>
                        )}
                      </td>
                      <td className="px-4 py-2">
                        {a.is_digest ? (
                          <Badge tone="warn">digest</Badge>
                        ) : (
                          <span className="text-fg-subtle text-2xs">—</span>
                        )}
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
