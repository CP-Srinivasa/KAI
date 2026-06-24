// @data-source: /dashboard/api/source-lifecycle
import { ListOrdered } from "lucide-react";
import { Card, CardHeader, Badge, InfoHint, SectionLabel, StatusDot } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { LiveDot } from "@/components/ui/LiveDot";
import { fetchSourceLifecycle, type SourceLifecycle, type SourceRankEntry } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { sourceLabel } from "@/lib/sourceLabels";
import { gradeOf, topFlop, type Grade } from "@/lib/sourceGrade";
import { cn } from "@/lib/utils";

// Quellen-Güte (Phase 0 / DALI-P-201): die EINE kanonische Rangliste aus
// monitor/source_ranking.json. Beantwortet „wer trifft die Richtung — beste
// zuerst". Echte Trefferquote (point_estimate) als Headline, Wilson-Untergrenze
// als ehrliche Vertrauenszahl daneben, Stichprobe ohne verwirrenden Nenner.
// Gate (n≥50) ist ein BADGE („belastbar"/„dünn"), KEIN Filter — alle Quellen
// sichtbar, dünne Stichproben markiert, nie als belastbar getarnt. Top/Flop als
// Hero-Streifen aus derselben Liste, jüngste Statuswechsel als Audit-Spur.
// Reiner Read, fail-closed.

const POLL_MS = 60_000;

const GRADE_TEXT: Record<Grade, string> = {
  pos: "text-pos",
  warn: "text-warn",
  neg: "text-neg",
  muted: "text-fg-muted",
};

function ago(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "0m";
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

// Headline: echte Trefferquote mit einer Nachkommastelle. Wilson: gerundet.
function pct1(v: number | null): string {
  return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}
function pct0(v: number | null): string {
  return v == null ? "—" : `${(v * 100).toFixed(0)}%`;
}

function RankRow({ e, showRank = true }: { e: SourceRankEntry; showRank?: boolean }) {
  const grade = gradeOf(e.point_estimate);
  return (
    <div className="flex items-center gap-2 py-1.5">
      <StatusDot tone={grade} className="shrink-0" />
      {showRank && (
        <span className="w-6 shrink-0 text-right font-mono text-2xs text-fg-subtle">{e.rank}</span>
      )}
      <span className="min-w-0 flex-1 truncate text-sm text-fg">
        {sourceLabel(e.source_name).label}
      </span>
      {/* Belastbarkeit: Gate als Badge, nicht als Filter. */}
      {e.provisional ? (
        <StatusPill kind="unverified" label="dünn" dot={false} showIcon={false} />
      ) : (
        <StatusPill kind="verified" label="belastbar" dot={false} showIcon={false} />
      )}
      {e.pinned && (
        <StatusPill kind="verified" label="fix gesetzt" dot={false} showIcon={false} />
      )}
      {e.silent && (
        <StatusPill kind="stale" label="verstummt" dot={false} showIcon={false} />
      )}
      {e.rotation_flagged && (
        <Badge tone="warn" dot>
          Rotation
        </Badge>
      )}
      <span className="w-16 shrink-0 text-right font-mono tabular-nums text-2xs text-fg-subtle">
        {e.n} Sig.
      </span>
      <span
        className="w-24 shrink-0 text-right font-mono tabular-nums text-2xs text-fg-subtle"
        title="Wilson-Untergrenze 95% — selbst pessimistisch gerechnet liegt die Quelle über diesem Wert."
      >
        ≥{pct0(e.wilson_lower_95)} sicher
      </span>
      <span
        className={cn(
          "w-16 shrink-0 text-right font-mono tabular-nums text-sm font-semibold",
          GRADE_TEXT[grade],
        )}
      >
        {pct1(e.point_estimate)}
      </span>
    </div>
  );
}

export function SourceLifecyclePanel() {
  const polling = usePolling<SourceLifecycle>((signal) => fetchSourceLifecycle(signal), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const data = polling.state === "ready" ? polling.data : null;
  const counts = data?.counts ?? {};
  const validated = counts.validated ?? 0;
  const provisional = counts.provisional ?? 0;
  const rotation = counts.rotation_flagged ?? 0;
  const pinned = counts.pinned ?? 0;
  const ranked = data?.ranked ?? [];
  const { strong, weak } = topFlop(ranked);

  return (
    <Card padded className="overflow-hidden">
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            <ListOrdered size={14} className="text-info shrink-0" />
            Quellen-Güte
          </span>
        }
        subtitle="Wer trifft die Richtung — beste zuerst. Alle Quellen sichtbar, dünne Stichproben ehrlich markiert."
        right={
          <div className="flex items-center gap-2">
            <LiveDot
              state={polling.state}
              generatedAt={data ? data.generated_at : null}
              staleAfterMs={26 * 60 * 60 * 1000}
              downAfterMs={50 * 60 * 60 * 1000}
            />
            {validated > 0 && (
              <Badge tone="pos" dot>
                {validated} belastbar
              </Badge>
            )}
            {provisional > 0 && (
              <Badge tone="muted" dot>
                {provisional} dünn
              </Badge>
            )}
            {pinned > 0 && (
              <Badge tone="pos" dot>
                {pinned} fix
              </Badge>
            )}
            {rotation > 0 && (
              <Badge tone="warn" dot>
                {rotation} Rotation
              </Badge>
            )}
          </div>
        }
      />

      {polling.state === "loading" && (
        <div className="py-4 text-center text-xs text-fg-subtle">Lade Quellen-Güte …</div>
      )}

      {polling.state === "error" && (
        <div className="rounded-sm border border-neg/30 bg-neg/5 px-3 py-2 text-2xs font-mono text-neg">
          Endpoint nicht erreichbar ({polling.error.kind}) ·{" "}
          <span className="font-mono">/dashboard/api/source-lifecycle</span>
        </div>
      )}

      {data && !data.available && (
        <div className="py-3 text-center text-xs text-fg-subtle">
          Noch kein Ranking — der recalc-Job hat{" "}
          <span className="font-mono">source_ranking.json</span> noch nicht geschrieben.
        </div>
      )}

      {data && data.available && ranked.length === 0 && (
        <div className="py-3 text-center text-xs text-fg-subtle">
          Noch keine Quelle mit aufgelöstem Signal im Fenster.
        </div>
      )}

      {data && data.available && ranked.length > 0 && (
        <>
          {/* Hero: Top/Flop aus derselben Liste, damit der Blick in 3s sitzt. */}
          {strong.length > 0 ? (
            <div className="mb-3 space-y-3">
              <div>
                <SectionLabel className="text-pos mb-0.5">Stärkste Quellen</SectionLabel>
                <div className="divide-y divide-line-subtle/40">
                  {strong.map((e) => (
                    <RankRow key={`s-${e.source_name}`} e={e} showRank={false} />
                  ))}
                </div>
              </div>
              {weak.length > 0 && (
                <div>
                  <SectionLabel className="text-neg mb-0.5">
                    Schwächste &amp; Stilllegen-Kandidaten
                  </SectionLabel>
                  <div className="divide-y divide-line-subtle/40">
                    {weak.map((e) => (
                      <RankRow key={`w-${e.source_name}`} e={e} showRank={false} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="mb-3 py-2 text-center text-2xs text-fg-subtle">
              Noch keine bewertbaren Trefferquoten — sobald Signale auflösen, erscheint hier die
              Rangliste.
            </div>
          )}

          {/* Volltabelle: alle Quellen, mit Spalten-Erklärungen. */}
          <div className="border-t border-line-subtle/60 pt-2">
            <div className="flex items-center gap-2 pb-1">
              <SectionLabel className="flex-1">Alle Quellen</SectionLabel>
              <span className="flex w-16 shrink-0 items-center justify-end gap-1 text-2xs text-fg-subtle">
                n
                <InfoHint
                  label="Aufgelöste Signale (n)"
                  side="left"
                  hint="So viele Signale dieser Quelle haben ein Ergebnis (Treffer oder Fehlschlag). Wenige Signale = die Trefferquote ist Zufall ausgesetzt. Ab 50 gilt sie als belastbar."
                />
              </span>
              <span className="flex w-24 shrink-0 items-center justify-end gap-1 text-2xs text-fg-subtle">
                Sicher-Min
                <InfoHint
                  label="Sicher-Mindestwert"
                  side="left"
                  hint="Die statistisch abgesicherte Untergrenze der Trefferquote (Wilson, 95%). „≥63% sicher“ heißt: selbst pessimistisch gerechnet liegt die Quelle über 63%. Schützt vor Glückstreffern bei kleiner Stichprobe — deshalb sortiert die Rangliste hiernach."
                />
              </span>
              <span className="flex w-16 shrink-0 items-center justify-end gap-1 text-2xs text-fg-subtle">
                Treffer
                <InfoHint
                  label="Trefferquote"
                  side="left"
                  hint="Anteil der News-Alerts dieser Quelle, deren Richtung über den Mess-Horizont aufging (Treffer ÷ aufgelöste Signale). 50% = Münzwurf. Höher = die Quelle zeigt öfter richtig. Noch ohne Kosten/Edge — die Netto-bps-Spalte folgt später."
                />
              </span>
            </div>
            <div className="divide-y divide-line-subtle/60">
              {ranked.map((e) => (
                <RankRow key={e.source_name} e={e} />
              ))}
            </div>
          </div>

          <div className="mt-2 text-2xs text-fg-subtle">
            Sortierung nach Sicher-Mindestwert (schützt vor Glückstreffern). Netto-Edge in bps folgt
            in einer späteren Phase.
          </div>
        </>
      )}

      {data && data.recent_events.length > 0 && (
        <div className="mt-3 border-t border-line-subtle/60 pt-2">
          <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
            Jüngste Statuswechsel
          </div>
          <ul className="space-y-0.5">
            {data.recent_events.map((ev, i) => (
              <li
                key={`${ev.source}-${ev.recorded_at_utc}-${i}`}
                className="flex items-center gap-2 text-2xs"
              >
                <span className="min-w-0 flex-1 truncate text-fg-muted">
                  {sourceLabel(ev.source).label}
                </span>
                <span className="shrink-0 font-mono text-fg-subtle">
                  {ev.from_status} → {ev.to_status}
                </span>
                <span
                  className="w-8 shrink-0 text-right font-mono text-fg-subtle"
                  title={ev.recorded_at_utc}
                >
                  {ago(ev.recorded_at_utc)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
