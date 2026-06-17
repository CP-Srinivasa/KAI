// @data-source: /dashboard/api/quality · /dashboard/api/regime · /dashboard/api/priority-gate
//
// Akute Punkte (UI-Update 2026.06, WP-1.3 / Konzept §7). Handlungsorientierte
// Triage der aktuell blockierenden Gates + akuten Probleme — abgeleitet aus den
// Truth-Chips, je Punkt mit Begründung UND empfohlener Aktion. Distinkt von der
// TruthStatusBar (reine Status-Pills) und dem CommandHeader (nur worst+Count):
// hier steht, WAS zu tun ist. Einklappbar, Default offen (§7).
//
// EHRLICH: nur real ableitbare Kategorien (Gates/Probleme). Todos/Phasen/
// Verbesserungen aus §7 haben keine strukturierte Quelle → nicht gefaket,
// sondern als ausstehend markiert (Operator-Board-Backend).
import { AlertOctagon, AlertTriangle, CheckCircle2 } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { deriveTruthChips } from "@/lib/truthStatus";
import { acuteChips, recommendedAction } from "@/lib/acutePoints";
import { truthToneToStatusTone } from "@/lib/commandStatus";
import { useApi } from "@/lib/useApi";
import { fetchOperatorBoard } from "@/lib/api";
import type {
  DashboardQuality,
  DashboardRegime,
  PriorityGateSummary,
} from "@/lib/api";

export function AcutePointsBoard({
  quality,
  regime,
  priorityGate,
  qualityState,
}: {
  quality: DashboardQuality | null;
  regime: DashboardRegime | null;
  priorityGate: PriorityGateSummary | null;
  qualityState: "loading" | "ready" | "error";
}) {
  const acute = acuteChips(deriveTruthChips(quality, regime, priorityGate));
  const hasCritical = acute.some((c) => c.tone === "critical");
  const board = useApi(fetchOperatorBoard, 300_000);
  const b = board.state === "ready" ? board.data : null;
  const hasBoard = !!b && (b.todos.length > 0 || b.phases.length > 0 || b.improvements.length > 0);

  return (
    <Card padded>
      <CardHeader
        title="Akute Punkte"
        subtitle="Blockierende Gates & akute Probleme — was jetzt Aufmerksamkeit braucht."
        right={
          acute.length > 0 ? (
            <Badge tone={hasCritical ? "neg" : "warn"} dot>
              {acute.length} offen
            </Badge>
          ) : (
            <Badge tone="pos" dot>
              ruhig
            </Badge>
          )
        }
      />

      {qualityState === "error" ? (
        <div className="flex items-center gap-2 rounded-sm border border-neg/30 bg-neg/5 px-3 py-2 text-xs text-neg">
          <AlertOctagon size={14} className="shrink-0" />
          Quality-Endpoint unerreichbar — Lage nicht bestimmbar.
        </div>
      ) : acute.length === 0 ? (
        <div className="flex items-center gap-2 py-2 text-xs text-fg-muted">
          <CheckCircle2 size={14} className="shrink-0 text-pos" />
          Keine akuten Gates oder Probleme — Lage ruhig.
        </div>
      ) : (
        <ul className="space-y-1.5">
          {acute.map((c) => (
            <li
              key={c.key}
              className="flex items-start gap-2 rounded-sm border border-line-subtle bg-bg-1 px-2.5 py-2"
            >
              <AlertTriangle
                size={14}
                className={c.tone === "critical" ? "mt-0.5 shrink-0 text-neg" : "mt-0.5 shrink-0 text-warn"}
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-xs font-semibold text-fg">{c.label}</span>
                  <Badge tone={truthToneToStatusTone(c.tone)}>{c.value}</Badge>
                </div>
                <p className="mt-0.5 text-2xs leading-relaxed text-fg-subtle">{c.hint}</p>
                <p className="mt-0.5 text-2xs leading-relaxed text-info">
                  → {recommendedAction(c.key)}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}

      {hasBoard && b ? (
        <div className="mt-3 grid grid-cols-1 gap-3 border-t border-line-subtle pt-3 md:grid-cols-3">
          <div>
            <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">To-dos</div>
            <ul className="space-y-1 text-2xs text-fg-muted">
              {b.todos.length === 0 ? (
                <li className="text-fg-subtle">—</li>
              ) : (
                b.todos.map((t, i) => (
                  <li key={i} className="flex items-start gap-1.5">
                    {t.priority && <Badge tone="muted">{t.priority}</Badge>}
                    <span className="min-w-0">{t.text}</span>
                  </li>
                ))
              )}
            </ul>
          </div>
          <div>
            <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">Offene Phasen</div>
            <ul className="space-y-1 text-2xs text-fg-muted">
              {b.phases.length === 0 ? (
                <li className="text-fg-subtle">—</li>
              ) : (
                b.phases.map((p, i) => (
                  <li key={i} className="flex items-center gap-1.5">
                    <Badge tone={p.status === "done" ? "pos" : p.status === "active" ? "info" : "muted"}>{p.status}</Badge>
                    <span className="min-w-0 truncate">{p.label}</span>
                  </li>
                ))
              )}
            </ul>
          </div>
          <div>
            <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">Verbesserungen</div>
            <ul className="space-y-1 text-2xs text-fg-muted">
              {b.improvements.length === 0 ? (
                <li className="text-fg-subtle">—</li>
              ) : (
                b.improvements.map((im, i) => <li key={i}>{im.text}</li>)
              )}
            </ul>
          </div>
          {b.stand && (
            <p className="text-2xs text-fg-subtle md:col-span-3">
              Kuratierter Snapshot · Stand {b.stand} · gepflegt in docs/operator_board.json (nicht live-berechnet).
            </p>
          )}
        </div>
      ) : (
        <p className="mt-2 border-t border-line-subtle pt-2 text-2xs text-fg-subtle">
          Todos / offene Phasen / Verbesserungen: kuratierte Quelle (docs/operator_board.json) noch leer/ausstehend — bewusst nicht aus Platzhaltern erfunden.
        </p>
      )}
    </Card>
  );
}
