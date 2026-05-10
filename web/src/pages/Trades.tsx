import { Fragment } from "react";
import { AlertCircle, RefreshCw, ArrowLeftRight, Activity } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { Badge, Button, Card, CardHeader, Kpi } from "@/components/ui/Primitives";
import { PageHeader } from "@/layout/PageHeader";
import { useApi } from "@/lib/useApi";
import { fetchTradingLoopStatus, fetchRecentCycles, type TradingCycle } from "@/lib/api";
import { cn } from "@/lib/utils";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { LABEL_DE, CYCLE_STATUS_EXPLAIN, humanizeLabel } from "@/lib/labels";

// 2026-05-10 DALI-T1: Klartext-Synopsis aus Cycle-Buckets ableiten.
// Operator-Frage "was sagt mir die Seite?" wird in 1 Satz beantwortet.
function summarizeCycles(cyclesList: TradingCycle[]): string {
  if (cyclesList.length === 0) return "Noch keine Cycles in der jüngsten Historie.";
  const counts: Record<string, number> = {};
  for (const c of cyclesList) counts[c.status] = (counts[c.status] ?? 0) + 1;
  const parts: string[] = [];
  const completed = counts.completed ?? 0;
  if (completed > 0) parts.push(`${completed}× ausgeführt`);
  if (counts.no_signal) parts.push(`${counts.no_signal}× kein Signal`);
  if (counts.no_market_data) parts.push(`${counts.no_market_data}× keine Markt-Daten`);
  if (counts.consensus_rejected) parts.push(`${counts.consensus_rejected}× Konsens abgelehnt`);
  if (counts.order_failed) parts.push(`${counts.order_failed}× Order fehlgeschlagen`);
  if (counts.stale_data) parts.push(`${counts.stale_data}× veraltete Daten`);
  // letzten ausgeführten Trade finden
  const lastCompleted = [...cyclesList].reverse().find((c) => c.status === "completed");
  const lastSegment = lastCompleted
    ? ` — letzter Trade ${lastCompleted.symbol} ${formatTimeShort(lastCompleted.started_at)}`
    : "";
  return `Letzte ${cyclesList.length} Cycles: ${parts.join(", ") || "—"}${lastSegment}.`;
}

function formatTimeShort(iso: string): string {
  try {
    const dt = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - dt.getTime();
    const diffH = Math.round(diffMs / 3600000);
    if (diffH < 1) return "vor weniger als 1h";
    if (diffH < 24) return `vor ${diffH}h`;
    return `vor ${Math.round(diffH / 24)}d`;
  } catch {
    return iso.substring(11, 16);
  }
}

// 2026-05-10 DALI-BTC-Pacman: Bitcoin-Pacman frisst Currency-Symbole.
// Operator-Bild 2026-05-10: Bitcoin-Logo als Pacman, Mund auf/zu, davor
// Currency-Pellets ($/€/¥/£) die nacheinander "gefressen" werden.
type PelletKind = "dollar" | "euro" | "yen" | "pound" | "btc" | "ghost" | "empty";

// Cycle-Status → Pellet-Mapping. Verschiedene Currency-Symbole spiegeln den
// Status: completed = $ (Hauptgewinn), no_signal = · (kleiner Punkt), failed = Geist.
function pelletForStatus(status: string, idx: number): PelletKind {
  if (status === "completed") {
    // Power-Pellet: rotierende Currency-Symbole pro completed-Cycle
    const symbols: PelletKind[] = ["dollar", "euro", "yen", "pound"];
    return symbols[idx % symbols.length];
  }
  if (status === "order_failed" || status === "consensus_rejected" || status === "priority_rejected") {
    return "ghost";
  }
  if (status === "blocked") return "empty";
  return "empty"; // no_signal, no_market_data etc → kleiner Punkt
}

// Bitcoin-Pacman: orange Kreis mit B-Logo links, Mund-Klappen rechts
// rotieren auf/zu via CSS-Animation (transform-rotate, ease-in-out).
// SVG damit Mund-Klappen-Animation deklarativ sauber ist.
function BitcoinPacman({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      className="btc-pacman"
      aria-hidden="true"
    >
      {/* Bitcoin-Body: oranger Kreis */}
      <circle cx="16" cy="16" r="14" fill="rgb(var(--warn))" />
      {/* B-Logo (Bitcoin) — bold, weiss/hell, links sodass Mund rechts frei ist */}
      <text
        x="10"
        y="22"
        fontSize="18"
        fontWeight="900"
        fill="rgb(var(--bg-0))"
        fontFamily="ui-serif, Georgia, serif"
      >
        ₿
      </text>
      {/* Mund-Klappe oben — rotiert auf-zu */}
      <polygon
        className="btc-jaw-top"
        points="16,16 32,16 32,2 16,2"
        fill="rgb(var(--bg-0))"
      />
      {/* Mund-Klappe unten — rotiert auf-zu */}
      <polygon
        className="btc-jaw-bottom"
        points="16,16 32,16 32,30 16,30"
        fill="rgb(var(--bg-0))"
      />
      {/* Auge */}
      <circle cx="14" cy="9" r="1.4" fill="rgb(var(--bg-0))" />
    </svg>
  );
}

// Currency-Pellet: $-, €-, ¥-, £- oder ₿-Symbol als Neon-Pellet.
// Pellet-Größe + Glow nach Status. Hover-scale für Detail.
function CurrencyPellet({ kind, title }: { kind: PelletKind; title: string }) {
  if (kind === "ghost") {
    // Pacman-Geist: rotes pixel-Quadrat mit roundes Top + 2 weiße Augen.
    return (
      <span title={title} className="inline-flex shrink-0 transition-transform hover:scale-150" aria-hidden="true">
        <svg width={11} height={11} viewBox="0 0 16 16" style={{ filter: "drop-shadow(0 0 4px rgb(var(--neg) / 0.85))" }}>
          {/* Body */}
          <path d="M2 8 Q 2 1 8 1 Q 14 1 14 8 L 14 14 L 12 13 L 10 15 L 8 13 L 6 15 L 4 13 L 2 14 Z" fill="rgb(var(--neg))" />
          {/* Augen */}
          <circle cx="6" cy="7" r="1.3" fill="rgb(var(--bg-0))" />
          <circle cx="10" cy="7" r="1.3" fill="rgb(var(--bg-0))" />
          <circle cx="6.4" cy="7.2" r="0.6" fill="rgb(var(--neg))" />
          <circle cx="10.4" cy="7.2" r="0.6" fill="rgb(var(--neg))" />
        </svg>
      </span>
    );
  }
  if (kind === "empty") {
    return (
      <span
        title={title}
        aria-hidden="true"
        className="inline-block shrink-0 rounded-full"
        style={{ width: 3, height: 3, backgroundColor: "rgb(var(--fg-subtle) / 0.25)" }}
      />
    );
  }
  // Currency-Pellet — Symbol mit Glow
  const symbol = kind === "dollar" ? "$" : kind === "euro" ? "€" : kind === "yen" ? "¥" : kind === "pound" ? "£" : "₿";
  // Verschiedene Tone für verschiedene Currencies — pos/info/ai/warn-Mischung
  const tone =
    kind === "dollar" ? "rgb(var(--pos))"
    : kind === "euro" ? "rgb(var(--info))"
    : kind === "yen"  ? "rgb(var(--ai))"
    : kind === "pound" ? "rgb(var(--accent))"
    : "rgb(var(--warn))";
  return (
    <span
      title={title}
      aria-hidden="true"
      className="currency-pellet shrink-0 transition-transform hover:scale-150"
      style={{
        width: 11,
        height: 11,
        fontSize: 11,
        color: tone,
        textShadow: `0 0 6px ${tone}, 0 0 10px ${tone}`,
        animation: "pacman-power-pulse 0.9s steps(1) infinite",
      }}
    >
      {symbol}
    </span>
  );
}

// Notes-humanize: kurz lesbar statt snake_case-Roh-String.
function humanizeNote(note: string): string {
  // Beispiel: "signal_block:reason=consensus" → "signal block (reason=consensus)"
  const [head, tail] = note.split(":");
  const headHum = LABEL_DE[head ?? ""] ?? head?.replace(/_/g, " ") ?? note;
  if (tail) return `${headHum} (${tail})`;
  return headHum;
}

export function TradesPage() {
  const { t } = useT();
  const status = useApi(fetchTradingLoopStatus, 20_000);
  const cycles = useApi((s) => fetchRecentCycles(30, s), 15_000);

  const cyclesList = cycles.state === "ready" ? cycles.data.recent_cycles : [];
  const completed24h = cyclesList.filter((c) => c.status === "completed").length;

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.trades.title")}
        tone="pos"
        icon={<ArrowLeftRight size={18} />}
        sub={
          status.state === "ready"
            ? `Mode: ${status.data.mode} · Letzter Status: ${status.data.last_cycle_status ?? "—"}`
            : "Was wurde zuletzt ausgeführt und mit welchem Ergebnis."
        }
        right={
          <Button onClick={() => { status.reload(); cycles.reload(); }} variant="outline" size="sm">
            <RefreshCw size={12} /> Aktualisieren
          </Button>
        }
      />

      {status.state === "error" && <ErrorCard kind={status.error.kind} message={status.error.message} path="/operator/trading-loop/status" />}

      {/* DALI-T1: Hero-Banner mit Klartext-Synopsis + Mini-Sparkline.
          Beantwortet "was sagt mir die Seite" in einem Satz und einer
          Pillen-Reihe — Operator scannt Muster (Wand aus grauen Pillen
          = Signal-Drought, rote Pille = Order-Failed) ohne Tabelle. */}
      {cycles.state === "ready" && cyclesList.length > 0 && (
        <Card padded className="border-l-4 border-l-info">
          <div className="flex items-start gap-3">
            <Activity size={18} className="text-info mt-0.5 shrink-0" aria-hidden />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-fg leading-relaxed">
                {summarizeCycles(cyclesList)}
              </div>
              <div className="mt-2.5 flex items-center gap-2.5 flex-wrap">
                <BitcoinPacman size={22} />
                <div className="flex items-center gap-1.5 flex-wrap">
                  {cyclesList.slice(-30).map((c, i) => (
                    <CurrencyPellet
                      key={i}
                      kind={pelletForStatus(c.status, i)}
                      title={`${LABEL_DE[c.status] ?? c.status} · ${c.symbol}`}
                    />
                  ))}
                </div>
              </div>
              <div className="mt-1.5 text-2xs text-fg-subtle font-mono flex flex-wrap items-center gap-x-3 gap-y-1">
                <span>letzte {Math.min(cyclesList.length, 30)} Cycles</span>
                <span className="inline-flex items-center gap-1">
                  <span style={{ color: "rgb(var(--pos))", textShadow: "0 0 4px rgb(var(--pos))", fontWeight: 700 }}>$ € ¥ £</span>
                  <span>= ausgeführter Trade</span>
                </span>
                <span className="inline-flex items-center gap-1">
                  <CurrencyPellet kind="ghost" title="" />
                  <span>= Order-/Konsens-/Priority-Fehler</span>
                </span>
                <span className="inline-flex items-center gap-1">
                  <span style={{ color: "rgb(var(--fg-subtle))" }}>·</span>
                  <span>= kein Trade-Anlass</span>
                </span>
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* DALI-T2-v2: Status-Werte sind kategorisch (nicht numerisch) — gehoeren
          in Badges, nicht in Hero-Schrift (overflow bei "Konsens abgelehnt").
          Hero-Number "Ausgefuehrte Trades" links (col-span-2), rechts daneben
          eine kombinierte Status-Card mit Letzter Status + Auto-Loop als Badges. */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Kpi
          label="Ausgeführte Trades"
          value={String(completed24h)}
          sub={`von ${cyclesList.length} Cycles in der jüngsten Historie`}
          tone={completed24h > 0 ? "pos" : "muted"}
          size="hero"
        />
        <Card padded>
          <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">Letzter Cycle</div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {status.state === "ready" && status.data.last_cycle_status ? (
              <span title={status.data.last_cycle_status}>
                <Badge
                  tone={
                    status.data.last_cycle_status === "completed"
                      ? "pos"
                      : status.data.last_cycle_status === "order_failed" ||
                        status.data.last_cycle_status === "consensus_rejected"
                        ? "neg"
                        : "warn"
                  }
                  dot
                >
                  {LABEL_DE[status.data.last_cycle_status] ?? status.data.last_cycle_status}
                </Badge>
              </span>
            ) : (
              <Badge tone="muted">—</Badge>
            )}
          </div>
          <div className="mt-3 text-2xs uppercase tracking-wider text-fg-subtle font-semibold">Auto-Loop</div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {status.state === "ready" ? (
              <Badge tone={status.data.auto_loop_enabled ? "pos" : "muted"} dot>
                {status.data.auto_loop_enabled ? "aktiv" : "aus"}
              </Badge>
            ) : (
              <Badge tone="muted">—</Badge>
            )}
            {status.state === "ready" && (
              <span className="text-2xs text-fg-subtle font-mono">
                Mode: {status.data.mode}
              </span>
            )}
          </div>
        </Card>
      </div>

      {status.state === "ready" && (
        <Card padded>
          <CardHeader
            title="Execution-Guardrails"
            right={
              <Badge tone={status.data.execution_enabled ? "pos" : "muted"} dot>
                {status.data.execution_enabled ? "execution aktiv" : "paper / shadow"}
              </Badge>
            }
          />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
            <RowKV k="write_back_allowed" v={status.data.write_back_allowed ? "erlaubt" : "gesperrt"} tone={status.data.write_back_allowed ? "pos" : "muted"} />
            <RowKV k="run_once_allowed" v={status.data.run_once_allowed ? "bereit" : "blockiert"} tone={status.data.run_once_allowed ? "pos" : "warn"} />
            <RowKV k="run_once_block_reason" v={status.data.run_once_block_reason ?? "—"} />
            <RowKV k="last_cycle_id" v={status.data.last_cycle_id?.slice(-14) ?? "—"} />
            <RowKV k="last_cycle_symbol" v={status.data.last_cycle_symbol ?? "—"} />
            <RowKV
              k="last_cycle_completed_at"
              v={status.data.last_cycle_completed_at?.substring(0, 19).replace("T", " ") ?? "—"}
            />
          </div>
        </Card>
      )}

      <Card padded={false} className="synthwave-pulse-edge overflow-hidden">
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-line-subtle">
          <div className="text-sm font-semibold tracking-tight text-fg">Letzte Trading-Cycles</div>
          <div className="text-2xs text-fg-subtle font-mono">
            {cycles.state === "ready" ? `${cycles.data.recent_cycles.length} Einträge` : ""}
          </div>
        </div>
        {cycles.state === "error" ? (
          <div className="p-4">
            <ErrorCard kind={cycles.error.kind} message={cycles.error.message} path="/operator/trading-loop/recent-cycles" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-fg-subtle text-2xs uppercase tracking-wider">
                  <th className="text-left font-semibold px-4 py-2">Zeit</th>
                  <th className="text-left font-semibold px-4 py-2">Symbol</th>
                  <th className="text-left font-semibold px-4 py-2">Status</th>
                  <th className="text-left font-semibold px-4 py-2 whitespace-nowrap">
                    Pipeline
                    <span className="ml-1 normal-case text-fg-subtle/70 font-normal">(Daten → Signal → Risk → Order → Fill)</span>
                  </th>
                  <th className="text-left font-semibold px-4 py-2">Notes</th>
                </tr>
              </thead>
              <tbody>
                {cycles.state === "loading" && (
                  <tr><td colSpan={5} className="px-4 py-6 text-center text-fg-subtle">{t("common.loading")}</td></tr>
                )}
                {cycles.state === "ready" && cycles.data.recent_cycles.length === 0 && (
                  <tr><td colSpan={5} className="px-4 py-6 text-center text-fg-subtle">{t("common.no_data")}</td></tr>
                )}
                {cycles.state === "ready" && cycles.data.recent_cycles.slice().reverse().map((c) => <CycleRow key={c.cycle_id} c={c} />)}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <PreparedPanel
        title="Guarded run-once trigger"
        reason="POST /operator/trading-loop/run-once (Idempotency-Key nötig) erlaubt gezielten Cycle im Paper/Shadow-Modus."
        detail="UI-Trigger erfordert confirm-flow + Idempotency-Key-Generierung — in Phase 2 geplant."
      />
    </div>
  );
}

function CycleRow({ c }: { c: TradingCycle }) {
  const toneFor = (s: string) => {
    if (s === "completed") return "pos";
    if (s === "no_signal") return "muted";
    if (s === "no_market_data" || s === "stale_data") return "warn";
    if (s === "order_failed" || s === "consensus_rejected") return "neg";
    return "neutral";
  };
  // 2026-05-10 DALI-T-Pipeline: 5x BoolDot-Spalten → 1 Pipeline-Visualisierung
  // mit Neon-Lichtpunkten (cyan-glow für erreicht, gedimmt sonst). Verbindungs-
  // linien zwischen Steps zeigen Fluss; abgebrochen wo der Cycle stoppte.
  // completed-Rows bekommen subtilen pos-Glow als Zeilen-Hintergrund.
  return (
    <tr
      className={cn(
        "border-t border-line-subtle hover:bg-bg-2",
        c.status === "completed" && "bg-pos/[0.03]",
      )}
    >
      <td className="px-4 py-2 font-mono text-2xs text-fg-subtle whitespace-nowrap">
        {c.started_at.substring(11, 19)}
      </td>
      <td className="px-4 py-2 font-mono font-semibold whitespace-nowrap">{c.symbol}</td>
      <td className="px-4 py-2">
        <span title={CYCLE_STATUS_EXPLAIN[c.status] ?? c.status}>
          <Badge tone={toneFor(c.status)}>{LABEL_DE[c.status] ?? c.status}</Badge>
        </span>
      </td>
      <td className="px-4 py-2">
        <CyclePipeline c={c} />
      </td>
      <td className="px-4 py-2 max-w-[420px]">
        <div className="flex flex-wrap gap-1">
          {c.notes.slice(0, 3).map((n, i) => (
            <span
              key={i}
              title={n}
              className="inline-flex items-center rounded-xs border border-line-subtle bg-bg-2 px-1.5 py-0.5 text-[10px] font-mono text-fg-subtle"
            >
              {humanizeNote(n)}
            </span>
          ))}
          {c.notes.length > 3 && (
            <span className="text-2xs text-fg-subtle">+{c.notes.length - 3}</span>
          )}
          {c.notes.length === 0 && (
            <span className="text-2xs text-fg-subtle italic">—</span>
          )}
        </div>
      </td>
    </tr>
  );
}

// Neon-Pipeline: 5 Steps als runde Lichtpunkte mit Glow + Verbindungslinien.
// Reached-Steps: cyan/info mit glow-info. Nicht erreicht: gedimmtes fg-subtle/20.
// Fail-Step (z.B. order_failed bei letztem reached): wird in neg/glow-neg gerendert.
function CyclePipeline({ c }: { c: TradingCycle }) {
  const steps = [
    { reached: c.market_data_fetched, label: "Markt-Daten", key: "data" },
    { reached: c.signal_generated, label: "Signal erzeugt", key: "signal" },
    { reached: c.risk_approved, label: "Risk-Gate bestanden", key: "risk" },
    { reached: c.order_created, label: "Order erstellt", key: "order" },
    { reached: c.fill_simulated, label: "Fill simuliert", key: "fill" },
  ];
  // Den letzten erreichten Step finden — wenn der Cycle dort gestoppt ist und
  // Status ein Fehler-Status ist, soll dieser Punkt rot leuchten.
  // (findLastIndex ist ES2023 — target ist niedriger, also manueller Loop.)
  let lastReachedIdx = -1;
  for (let j = steps.length - 1; j >= 0; j--) {
    if (steps[j].reached) {
      lastReachedIdx = j;
      break;
    }
  }
  const isFail = c.status === "order_failed" || c.status === "consensus_rejected";
  const isWarn = c.status === "no_market_data" || c.status === "stale_data";
  const stoppedAt = lastReachedIdx >= 0 && lastReachedIdx < steps.length - 1 ? lastReachedIdx : -1;
  return (
    <div className="flex items-center gap-0">
      {steps.map((s, i) => {
        const isStopAt = i === stoppedAt && (isFail || isWarn);
        const dotTone = !s.reached
          ? "bg-fg-subtle/20"
          : isStopAt && isFail
            ? "bg-neg glow-neg"
            : isStopAt && isWarn
              ? "bg-warn glow-warn"
              : "bg-info glow-info";
        const lineTone =
          i < steps.length - 1
            ? steps[i + 1].reached
              ? "bg-info/45"
              : s.reached && isStopAt && isFail
                ? "bg-neg/30"
                : s.reached && isStopAt && isWarn
                  ? "bg-warn/30"
                  : "bg-fg-subtle/15"
            : "";
        return (
          <Fragment key={s.key}>
            <span
              title={`${s.label}: ${s.reached ? "✓ erreicht" : "× nicht erreicht"}`}
              className={cn("h-2.5 w-2.5 rounded-full shrink-0 transition-transform hover:scale-150", dotTone)}
            />
            {i < steps.length - 1 && (
              <span className={cn("h-px w-4 shrink-0", lineTone)} />
            )}
          </Fragment>
        );
      })}
    </div>
  );
}

function RowKV({ k, v, tone }: { k: string; v: string; tone?: "pos" | "neg" | "warn" | "muted" }) {
  return (
    <div className="flex items-center justify-between gap-2 overflow-hidden border-b border-line-subtle/50 py-1">
      <span className="min-w-0 truncate font-mono text-2xs text-fg-subtle" title={k}>{humanizeLabel(k)}</span>
      <span className={cn(
        "shrink-0 font-mono text-right",
        tone === "pos" && "text-pos",
        tone === "neg" && "text-neg",
        tone === "warn" && "text-warn",
        tone === "muted" && "text-fg-muted",
      )}>{v}</span>
    </div>
  );
}

function ErrorCard({ kind, message, path }: { kind: string; message: string; path: string }) {
  return (
    <Card padded className="border-neg/30 bg-neg/5">
      <div className="flex items-start gap-3 text-xs text-neg">
        <AlertCircle size={16} className="mt-0.5 shrink-0" />
        <div className="min-w-0">
          <div className="font-semibold">Endpoint nicht erreichbar</div>
          <div className="text-fg-muted mt-1 break-words">{kind} · {message}</div>
          <div className="text-2xs text-fg-subtle mt-1 font-mono break-all">{path}</div>
        </div>
      </div>
    </Card>
  );
}
