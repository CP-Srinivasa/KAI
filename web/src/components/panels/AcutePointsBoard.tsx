// @data-source: /dashboard/api/quality · /dashboard/api/regime · /dashboard/api/priority-gate · /dashboard/api/operator-board
//
// Akute Punkte (UI-Update 2026.06, WP-1.3 / Konzept §7). Handlungsorientierte
// Triage der aktuell blockierenden Gates + akuten Probleme — abgeleitet aus den
// Truth-Chips, je Punkt mit Begründung UND empfohlener Aktion. Distinkt von der
// TruthStatusBar (reine Status-Pills) und dem CommandHeader (nur worst+Count):
// hier steht, WAS zu tun ist. Einklappbar, Default offen (§7).
//
// EHRLICH: nur real ableitbare Kategorien (Gates/Probleme).
//
// 2026-07-30 (Operator-Befund „Stand 2026-07-12 · 18 Tage alt — veraltet"):
// Die offenen Punkte kommen jetzt LIVE aus dem Prä-Reg-Ledger (registriert minus
// aufgelöst) statt aus einer handgepflegten Datei. Die kuratierte Liste bleibt
// als CHRONIK erhalten — erledigte Phasen können nicht veralten, darum feuert
// der Pflege-Hinweis nur noch bei einem wirklich OFFENEN kuratierten Punkt.
import { AlertOctagon, AlertTriangle, CheckCircle2 } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { deriveTruthChips } from "@/lib/truthStatus";
import { acuteChips, recommendedAction } from "@/lib/acutePoints";
import { truthToneToStatusTone } from "@/lib/commandStatus";
import { formatRelative } from "@/lib/time";
import { useApi } from "@/lib/useApi";
import { fetchOperatorBoard } from "@/lib/api";
import type {
  DashboardQuality,
  DashboardRegime,
  OperatorPrereg,
  PriorityGateSummary,
} from "@/lib/api";

// Sprachregel (Lehre kai_news_direction_v2_immature): „fällig" heisst Eval
// fahren, NIE „bestanden"; ohne Zähler wird nichts behauptet.
const PREREG_LABEL: Record<OperatorPrereg["state"], string> = {
  judgeable: "urteilsfähig",
  eval_check: "Evaluator fällig",
  maturing: "reift",
  no_counter: "ungezählt",
};
const PREREG_TONE: Record<OperatorPrereg["state"], "neg" | "warn" | "info" | "muted"> = {
  judgeable: "neg",
  eval_check: "warn",
  maturing: "info",
  no_counter: "muted",
};

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
  const live = b?.live ?? null;
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
          ) : live && live.due_count > 0 ? (
            // Keine akuten Gates, aber ein fälliges Verdikt ist NICHT "ruhig".
            <Badge tone="warn" dot>
              {live.judgeable_count > 0
                ? `${live.judgeable_count} Verdikt fällig`
                : `${live.eval_check_count} Evaluator fällig`}
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
        // „Keine Gates" ist NICHT „nichts zu tun": ein fälliges Verdikt ist eine
        // offene Handlung. Ohne diesen Zweig las das Panel „Lage ruhig", während
        // das Badge darüber „Verdikt fällig" meldete (Widerspruch, 2026-07-30).
        live && live.due_count > 0 ? (
          <div className="flex items-center gap-2 py-2 text-xs text-fg-muted">
            <AlertTriangle size={14} className="shrink-0 text-warn" />
            Keine blockierenden Gates — aber {live.due_count} pre-registrierter Claim braucht
            Handlung (siehe unten: urteilsfähig oder Evaluator-Lauf).
          </div>
        ) : (
          <div className="flex items-center gap-2 py-2 text-xs text-fg-muted">
            <CheckCircle2 size={14} className="shrink-0 text-pos" />
            Keine akuten Gates oder Probleme — Lage ruhig.
          </div>
        )
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

      {live?.has_content ? (
        <div className="mt-3 border-t border-line-subtle pt-3">
          <div className="mb-1.5 flex flex-wrap items-center gap-2">
            <span className="text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
              Offene Prä-Regs
            </span>
            <Badge tone={live.due_count > 0 ? "warn" : "muted"} dot={live.due_count > 0}>
              {live.judgeable_count > 0
                ? `${live.judgeable_count} urteilsfähig / ${live.open_count} offen`
                : live.eval_check_count > 0
                  ? `${live.eval_check_count}× Evaluator fällig / ${live.open_count} offen`
                  : `${live.open_count} offen`}
            </Badge>
            <span className="text-2xs text-pos">live berechnet</span>
          </div>
          <ul className="space-y-1">
            {live.open_preregs.map((p) => (
              <li
                key={p.prereg_id}
                className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded-sm bg-bg-1 px-2 py-1.5"
              >
                <Badge tone={PREREG_TONE[p.state]}>{PREREG_LABEL[p.state]}</Badge>
                <span className="text-2xs font-medium text-fg">{p.name}</span>
                {p.n_proxy !== null && p.n_target ? (
                  <span className="text-2xs tabular-nums text-fg-muted">
                    n≈{p.n_proxy}/{p.n_target}
                    {p.progress_pct !== null ? ` · ${p.progress_pct}%` : ""}
                  </span>
                ) : (
                  <span className="text-2xs text-fg-subtle">ungezählt</span>
                )}
                {p.last_verdict && <Badge tone="muted">{p.last_verdict}</Badge>}
                <code className="text-2xs text-fg-subtle">{p.prereg_id}</code>
                <span className="basis-full text-2xs leading-relaxed text-info">→ {p.action}</span>
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-2xs leading-relaxed text-fg-subtle">
            Stand: {formatRelative(live.generated_at)} · {live.note}
            {live.maturity_state === "unavailable" &&
              " Reife-Zähler nicht erreichbar — n ungezählt, kein Urteil."}
          </p>
        </div>
      ) : null}

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
            <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">Phasen-Chronik</div>
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
            <p className={`text-2xs md:col-span-3 ${b.is_stale ? "text-warn" : "text-fg-subtle"}`}>
              {b.is_stale
                ? `Kuratierte Chronik · offener Punkt seit ${b.stand} unangetastet — bitte pflegen.`
                : `Kuratierte Chronik · letzter Eintrag ${b.stand} (abgeschlossen, veraltet nicht). Der laufende Stand steht oben, live berechnet.`}
            </p>
          )}
        </div>
      ) : live?.has_content ? null : (
        <p className="mt-2 border-t border-line-subtle pt-2 text-2xs text-fg-subtle">
          Keine offenen Prä-Regs und keine kuratierte Chronik — bewusst nicht aus Platzhaltern erfunden.
        </p>
      )}
    </Card>
  );
}
