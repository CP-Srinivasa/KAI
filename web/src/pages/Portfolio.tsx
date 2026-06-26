import { useState } from "react";
import { AlertCircle, RefreshCw, Play, Target, X, Briefcase, ChevronDown, ChevronUp } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { Badge, Button, Card, CardHeader } from "@/components/ui/Primitives";
import { PageHeader } from "@/layout/PageHeader";
import { useApi } from "@/lib/useApi";
import { LiveDot } from "@/components/ui/LiveDot";
import { liveDotProps } from "@/lib/freshness";
import {
  fetchPortfolioSnapshot,
  fetchExposureSummary,
  fetchRealizedByAsset,
  postReprocess,
  postReconcileCompletion,
  postPositionRepair,
  postManualFill,
  fetchPendingEnvelopes,
  type PaperPosition,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { PremiumTradeCard } from "@/components/panels/PremiumTradeCard";
import { PremiumSignalTrail } from "@/components/panels/PremiumSignalTrail";
import { PremiumRuntimeBanner } from "@/components/panels/PremiumRuntimeBanner";
import { DiversificationPanel } from "@/components/panels/DiversificationPanel";
import { EdgeTruthPanel } from "@/components/panels/EdgeTruthPanel";
import { ChurnPanel } from "@/components/panels/ChurnPanel";
import { MomentumUniversePanel } from "@/components/panels/MomentumUniversePanel";
import { Waterfall } from "@/components/viz/Waterfall";
import { realizedToWaterfall } from "@/lib/pnlWaterfall";
import { useCurrency } from "@/state/CurrencyProvider";
import { formatNumber, type Currency } from "@/lib/money";
import { formatClock, formatShortDate } from "@/lib/time";

/**
 * Adaptive Decimal-Digits für Preis-Anzeige.
 *
 * Wurzel des 2026-05-12 "0.01-Bug": Sub-Cent-Tokens wie Q/USDT (0.01535)
 * wurden bei fixen `digits=2` als "0,01" gerendert — Operator nahm an die
 * Pipeline schreibe 0.01-Defaults, in Wahrheit truncierte das Frontend.
 * Adaptive-Bucketing nach abs(value):
 *   < 0.001   → 8 digits (e.g. SHIB 0.00001234)
 *   < 0.01    → 6 digits (e.g. 1000LUNC 0.103)
 *   < 1       → 4 digits (e.g. Q/USDT 0.0153)
 *   < 100     → 4 digits (e.g. DOGE 0.234, SOL 12.45)
 *   sonst     → 2 digits (e.g. BTC 67451.23)
 */
function priceDigits(v: number | null | undefined): number {
  if (v == null || !Number.isFinite(v)) return 2;
  const abs = Math.abs(v);
  if (abs < 0.001) return 8;
  if (abs < 0.01) return 6;
  if (abs < 1) return 4;
  if (abs < 100) return 4;
  return 2;
}

// 2026-05-25 Forensik-Patch: Realisierte Gewinne nach Asset.
// Liest GET /operator/portfolio/realized-by-asset — keine Live-/Backtest-Abhängigkeit.
function RealizedByAssetPanel() {
  const { fmt } = useCurrency();
  const [sourceFilter, setSourceFilter] = useState<"all" | "premium_telegram" | "autonomous" | "reconciled" | "legacy_unknown">("all");
  // Lange Asset-Liste (oft 25+ Zeilen) standardmäßig auf die Top-5 eingeklappt,
  // damit die Card die Dashboard-Übersicht nicht sprengt (Operator 2026-06-14).
  const [showAllAssets, setShowAllAssets] = useState(false);
  const ASSETS_COLLAPSE_N = 5;
  const data = useApi(
    (signal) =>
      fetchRealizedByAsset(
        signal,
        sourceFilter === "all" ? undefined : sourceFilter,
      ),
    60_000,
    [sourceFilter],
  );
  const filterControls = (
    <div className="inline-flex items-center rounded-sm border border-line-subtle bg-bg-2 p-0.5 flex-wrap gap-0.5">
      {(["all", "premium_telegram", "autonomous", "reconciled", "legacy_unknown"] as const).map((value) => (
        <button
          key={value}
          type="button"
          onClick={() => setSourceFilter(value)}
          className={cn(
            "px-2 py-0.5 text-3xs font-mono rounded-xs transition-colors whitespace-nowrap",
            sourceFilter === value
              ? "bg-info/15 text-info"
              : "text-fg-subtle hover:text-fg",
          )}
        >
          {value === "all"
            ? "Gesamt"
            : value === "premium_telegram"
            ? "Premium TG"
            : value === "autonomous"
            ? "Autonom"
            : value === "reconciled"
            ? "Reconciled"
            : "Legacy/Unbekannt"}
        </button>
      ))}
    </div>
  );

  if (data.state !== "ready") {
    return (
      <Card padded>
        <CardHeader
          title="Realisierte Gewinne nach Asset"
          subtitle="Live aus Paper-Execution-Audit"
          right={filterControls}
        />
        <div className="text-sm text-fg-subtle py-6 text-center">
          {data.state === "error"
            ? `Fehler beim Laden: ${data.error}`
            : "Lädt …"}
        </div>
      </Card>
    );
  }

  const d = data.data;
  if (!d.available) {
    return (
      <Card padded>
        <CardHeader
          title="Realisierte Gewinne nach Asset"
          subtitle="Live aus Paper-Execution-Audit"
          right={filterControls}
        />
        <div className="text-sm text-fg-subtle py-6 text-center">
          Audit nicht verfügbar: {d.error ?? "unbekannt"}
        </div>
      </Card>
    );
  }
  if (d.by_asset.length === 0) {
    return (
      <Card padded>
        <CardHeader
          title="Realisierte Gewinne nach Asset"
          subtitle="Live aus Paper-Execution-Audit"
          right={filterControls}
        />
        <div className="text-sm text-fg-subtle py-6 text-center">
          Noch keine abgeschlossenen Trades für diesen Filter — Paper-Engine hat keine passenden
          position_closed-Events.
        </div>
      </Card>
    );
  }

  const activeFilterLabel =
    sourceFilter === "all"
      ? "volles Paper-Buch"
      : sourceFilter === "premium_telegram"
      ? "nur Premium Telegram"
      : sourceFilter === "autonomous"
      ? "nur Autonome Loop"
      : sourceFilter === "reconciled"
      ? "nur Reconciled Completions"
      : "nur Legacy / Unbekannt";

  const visibleAssets = showAllAssets ? d.by_asset : d.by_asset.slice(0, ASSETS_COLLAPSE_N);

  return (
    <Card padded>
      {/* Filter aus dem Header-right gezogen: dort verdrängten die 5 Filter-
          Buttons + Badge die Subtitle-Zeile und erzwangen den hässlichen
          Umbruch von "168 … über 27 Assets …". Jetzt: Header = Titel + Total,
          Filter eine eigene volle Zeile, Subtitle hat die ganze Breite. */}
      <CardHeader
        title="Realisierte Gewinne nach Asset"
        subtitle={`${d.totals.closed_trades} abgeschlossene Trades · ${d.totals.assets_count} Assets · ${activeFilterLabel}`}
        right={
          <Badge tone={d.totals.realized_pnl_usd >= 0 ? "pos" : "neg"}>
            Gesamt {d.totals.realized_pnl_usd >= 0 ? "+" : ""}
            {fmt(d.totals.realized_pnl_usd, undefined, 2)}
          </Badge>
        }
      />
      <div className="mb-3">{filterControls}</div>
      {(d.totals.quarantined_closes ?? 0) > 0 && (
        <div className="mb-3 text-2xs text-fg-subtle">
          <span className="text-warn">{d.totals.quarantined_closes} korrupte Close(s)</span> (
          {fmt(d.totals.quarantined_pnl_usd ?? 0, undefined, 0)}) forensisch quarantäniert und aus
          den Summen ausgeschlossen — siehe Edge-Wahrheit.
        </div>
      )}
      {d.by_asset.length > 1 && (
        <div className="mb-4">
          <div className="mb-1 text-2xs uppercase tracking-wider text-fg-subtle">
            PnL-Wasserfall — Beitrag je Asset bis zur Netto-Summe
          </div>
          <Waterfall items={realizedToWaterfall(d.by_asset)} format={(v) => fmt(v, undefined, 0)} />
        </div>
      )}
      <div className="grid grid-cols-1 gap-1">
        {visibleAssets.map((row) => {
          const pos = row.realized_pnl_usd >= 0;
          return (
            <div
              key={row.symbol}
              className="grid grid-cols-[1fr_auto_auto_auto] gap-3 items-center px-2 py-1.5 rounded-sm hover:bg-bg-2"
              style={{
                background: pos
                  ? `rgb(var(--pos) / 0.04)`
                  : `rgb(var(--neg) / 0.04)`,
              }}
            >
              <span className="font-semibold">{row.symbol}</span>
              <span className="text-2xs text-fg-subtle">
                {row.closed_trades} Trade(s) · {row.wins}W/{row.losses}L
                {row.partial_closes > 0 ? ` · ${row.partial_closes} partial` : ""}
              </span>
              <span className="text-2xs text-fg-subtle font-mono">
                {row.win_rate_pct != null ? `${row.win_rate_pct.toFixed(0)}%` : "—"}
              </span>
              <span
                className={cn(
                  "font-mono text-right font-semibold",
                  pos ? "text-pos" : "text-neg",
                )}
              >
                {pos ? "+" : ""}
                {fmt(row.realized_pnl_usd, undefined, 2)}
              </span>
            </div>
          );
        })}
      </div>
      {d.by_asset.length > ASSETS_COLLAPSE_N && (
        <ShowMoreToggle
          expanded={showAllAssets}
          totalCount={d.by_asset.length}
          hiddenCount={d.by_asset.length - ASSETS_COLLAPSE_N}
          noun="Assets"
          onToggle={() => setShowAllAssets((s) => !s)}
        />
      )}
      {/* 2026-06-25: Übersicht der letzten Einzel-Trades (Operator-Wunsch).
          Vorzeichen-korrekt; SHORT-Gewinne grün wie LONG. */}
      {(d.recent_trades?.length ?? 0) > 0 && (
        <div className="mt-4 border-t border-line-subtle/40 pt-3">
          <div className="mb-2 text-2xs uppercase tracking-wider text-fg-subtle">
            Letzte Trades ({d.recent_trades!.length})
          </div>
          <div className="grid grid-cols-1 gap-0.5">
            {d.recent_trades!.map((tr, i) => {
              const win = tr.trade_pnl_usd >= 0;
              const side = (tr.position_side ?? "long").toUpperCase();
              return (
                <div
                  key={`${tr.symbol}-${tr.closed_at_utc ?? i}`}
                  className="grid grid-cols-[auto_auto_1fr_auto] items-center gap-2 px-2 py-1 rounded-sm hover:bg-bg-2 text-2xs"
                >
                  <span className="font-mono font-semibold">{tr.symbol}</span>
                  <Badge tone={side === "SHORT" ? "warn" : "info"}>{side}</Badge>
                  <span className="text-fg-subtle truncate">
                    {tr.closed_at_utc ? formatOpenedAt(tr.closed_at_utc) : "—"}
                    {tr.is_partial ? " · partial" : ""}
                    {tr.source ? ` · ${formatSource(tr.source)}` : ""}
                  </span>
                  <span
                    className={cn("font-mono font-semibold text-right", win ? "text-pos" : "text-neg")}
                  >
                    {win ? "+" : ""}
                    {fmt(tr.trade_pnl_usd, undefined, 2)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
      {d.top_performer && d.worst_performer && d.by_asset.length >= 2 && (
        <div className="mt-3 grid grid-cols-2 gap-3 text-2xs text-fg-subtle">
          <div>
            <span className="text-fg">Top-Performer:</span> {d.top_performer.symbol}{" "}
            <span className="text-pos">
              +{fmt(d.top_performer.realized_pnl_usd, undefined, 2)}
            </span>
          </div>
          <div className="text-right">
            <span className="text-fg">Worst-Performer:</span>{" "}
            {d.worst_performer.symbol}{" "}
            <span className={d.worst_performer.realized_pnl_usd >= 0 ? "text-pos" : "text-neg"}>
              {d.worst_performer.realized_pnl_usd >= 0 ? "+" : ""}
              {fmt(d.worst_performer.realized_pnl_usd, undefined, 2)}
            </span>
          </div>
        </div>
      )}
      <div className="mt-2 text-2xs text-fg-subtle">
        Letztes Close: {d.audit_last_event_utc ?? "—"} · Quelle:{" "}
        <span className="font-mono">{d.audit_path}</span>
        {(d.source_prefix || d.source_filter) && (
          <>
            {" "}· Filter: <span className="font-mono">{d.source_prefix || d.source_filter}</span>
          </>
        )}
      </div>
    </Card>
  );
}

export function PortfolioPage() {
  const { t } = useT();
  const { fmt, currency } = useCurrency();
  // Adaptive Decimals als Default — Operator-Auftrag 2026-05-12 Sektion 7
  // verlangt "keine automatische Umwandlung auf 0.01" für Display.
  const fmt$ = (v: number | null | undefined, digits?: number) =>
    v == null ? "—" : fmt(v, undefined, digits ?? priceDigits(v));
  // Retry transient blips fast (capital view shouldn't sit in "error" 30s).
  const snap = useApi(fetchPortfolioSnapshot, 30_000, [], { maxAttempts: 2, baseMs: 1500 });
  const exposure = useApi(fetchExposureSummary, 30_000, [], { maxAttempts: 2, baseMs: 1500 });

  const positions: PaperPosition[] = snap.state === "ready" ? snap.data.positions : [];
  const unrealized = positions.reduce((sum, p) => sum + (p.unrealized_pnl_usd ?? 0), 0);
  // Offene-Positionen-Tabelle einklappbar: standardmäßig nur die 5 neuesten
  // (nach opened_at), Rest hinter einem Toggle — sonst sprengt das Buch bei
  // vielen Positionen die Übersicht (Operator 2026-06-14).
  const [showAllPositions, setShowAllPositions] = useState(false);
  const POSITIONS_COLLAPSE_N = 5;
  const positionsByRecent = [...positions].sort((a, b) => {
    const ta = a.opened_at ? Date.parse(a.opened_at) : 0;
    const tb = b.opened_at ? Date.parse(b.opened_at) : 0;
    return (Number.isFinite(tb) ? tb : 0) - (Number.isFinite(ta) ? ta : 0);
  });
  const visiblePositions = showAllPositions
    ? positionsByRecent
    : positionsByRecent.slice(0, POSITIONS_COLLAPSE_N);
  // Sprint E (2026-05-12): Operator-Actions — Per-Action busy-flag + last-outcome.
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  function runAction(key: string, run: () => Promise<unknown>): void {
    if (actionBusy) return;
    setActionBusy(key);
    setActionMsg(null);
    run()
      .then((r) => {
        setActionMsg({ kind: "ok", text: `${key}: ${shortOutcome(r)}` });
        snap.reload();
      })
      .catch((e: Error) => setActionMsg({ kind: "err", text: `${key}: ${e.message}` }))
      .finally(() => setActionBusy(null));
  }

  // DALI-P1: Bucket-Decomposition
  const totalEquity = snap.state === "ready" ? snap.data.total_equity_usd : 0;
  const cash = snap.state === "ready" ? snap.data.cash_usd : 0;
  const positionsValue = snap.state === "ready" ? snap.data.total_market_value_usd : 0;
  const realized = snap.state === "ready" ? snap.data.realized_pnl_usd : 0;
  // 2026-06-25: short-aware Backend-Wahrheit. unrealizedTotal aus dem Snapshot
  // (vorzeichen-korrekt long+short) statt nur lokaler Summe; feesSpent kumuliert.
  const unrealizedTotal =
    snap.state === "ready" ? snap.data.total_unrealized_pnl_usd ?? unrealized : unrealized;
  const feesSpent = snap.state === "ready" ? snap.data.total_fees_usd ?? 0 : 0;
  // Mai-60-bps-error-path-Artefakt (Tabelle v1.0.0, bis ~01.06.) — separat,
  // damit die reale 10-bps-Fee nicht wie 1/3 des Portfolios aussieht.
  const feesArtifact = snap.state === "ready" ? snap.data.total_fees_artifact_usd ?? 0 : 0;
  // Phantom-Fees: fiktive Fees auf nicht-handelbaren Symbolen (Self-Pair /
  // Stablecoin-Paar) — nie ein echter gebührenpflichtiger Markt, ausgeschlossen.
  const feesPhantom = snap.state === "ready" ? snap.data.total_fees_phantom_usd ?? 0 : 0;
  // Tages-Fee-Last (heute, UTC) + Fill-Zahl — zeigt, wie viel ein Trading-Tag
  // an Fees verschlingt und ob es stark variiert.
  const feesToday = snap.state === "ready" ? snap.data.total_fees_today_usd ?? 0 : 0;
  const fillsToday = snap.state === "ready" ? snap.data.fills_today ?? 0 : 0;
  // Stacked-Bar: nur die positiven Anteile, realized kann negativ sein → Anteil clamp.
  const denomForBar = Math.max(positionsValue + cash + Math.max(realized, 0), 1);
  const pctPositions = (positionsValue / denomForBar) * 100;
  const pctCash = (cash / denomForBar) * 100;
  const pctRealized = (Math.max(realized, 0) / denomForBar) * 100;

  // DALI-P2: PnL-Heatmap-Pills — höchster absoluter PnL für Skalierung.
  const maxAbsPnl = Math.max(...positions.map((p) => Math.abs(p.unrealized_pnl_usd ?? 0)), 1);

  // DALI-P5-Lite: Konzentrations-Tone basierend auf Largest-Position-Weight.
  const largestPct = exposure.state === "ready" ? exposure.data.largest_position_weight_pct ?? 0 : 0;
  const concentrationTone = largestPct > 70 ? "neg" : largestPct > 40 ? "warn" : "pos";

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.portfolio.title")}
        tone="accent"
        icon={<Briefcase size={18} />}
        // DALI-v2 S1: divider=false - Synthwave-Glow lebt in den Cards,
        // nicht freischwebend ueber dem Header (Master-Spec G4).
        divider={false}
        sub={
          snap.state === "ready"
            ? `Paper Portfolio · ${snap.data.position_count} Positionen · Quelle: ${snap.data.source}`
            : "Paper Portfolio Snapshot"
        }
        right={
          <Button
            onClick={() => { snap.reload(); exposure.reload(); }}
            variant="outline"
            size="sm"
          >
            <RefreshCw size={12} /> Aktualisieren
          </Button>
        }
      />

      {/* Premium-Runtime-Wahrheit: erklärt warum Premium-Signale evtl. keine
          Paper-Position öffnen, bevor der Operator den Trail/Portfolio liest. */}
      <PremiumRuntimeBanner />

      {snap.state === "error" && <ErrorCard kind={snap.error.kind} message={snap.error.message} path="/operator/portfolio-snapshot" />}

      {/* DALI-P1: Equity-Decomposition als Bucket-Modell.
          Operator: "Was ist auf der Börse, was im Trade, was Withdraw, was
          auf dem Konto, wo Gewinne." Stacked-Bar visualisiert den
          Geld-Aufenthaltsort, Hero-Number ist Total-Equity.
          2026-05-12 DALI-A12-Konsistenz: synthwave-pulse-edge auch auf
          Portfolio-Cards damit die animierten Regenbogen-Schwünge die
          Seite mit den anderen Pages (Risk/Signals/Trades) verbinden. */}
      {snap.state === "ready" && (
        <Card padded className="synthwave-pulse-edge overflow-hidden">
          <div className="flex items-baseline justify-between mb-3 flex-wrap gap-2">
            <div>
              <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">Gesamt-Equity</div>
              <div
                className={cn(
                  "font-mono text-3xl font-semibold",
                  totalEquity > 0 ? "text-pos" : totalEquity < 0 ? "text-neg" : "text-fg",
                )}
              >
                {fmt$(totalEquity)}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <LiveDot {...liveDotProps(snap)} staleAfterMs={75_000} />
              <Badge tone="info" dot>Paper-Konto · {snap.data.source}</Badge>
            </div>
          </div>
          <div className="flex h-3 w-full overflow-hidden rounded-xs border border-line-subtle bg-bg-2">
            <div
              className="bg-info"
              style={{ width: `${pctPositions}%` }}
              title={`In offenen Positionen: ${fmt$(positionsValue)}`}
            />
            <div
              className="bg-pos"
              style={{ width: `${pctCash}%` }}
              title={`Cash: ${fmt$(cash)}`}
            />
            <div
              className="bg-ai"
              style={{ width: `${pctRealized}%` }}
              title={`Realized PnL (kumuliert positiv): ${fmt$(Math.max(realized, 0))}`}
            />
          </div>
          <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-3 text-xs">
            <BucketLabel
              tone="info"
              label="In Position (Marktwert)"
              value={fmt$(positionsValue)}
              sub={`${snap.data.position_count} offen`}
            />
            <BucketLabel
              tone="pos"
              label="Cash (frei)"
              value={fmt$(cash)}
              sub="liquide"
            />
            <BucketLabel
              tone={realized < 0 ? "neg" : "ai"}
              label="Realisiert (G/V)"
              value={`${realized >= 0 ? "+" : ""}${fmt$(realized)}`}
              sub="kumuliert"
            />
            {/* Im Trade / unrealisiert — short-aware, grün bei Plus, rot bei Minus. */}
            <BucketLabel
              tone={unrealizedTotal > 0 ? "pos" : unrealizedTotal < 0 ? "neg" : "info"}
              label="Im Trade (unrealisiert)"
              value={`${unrealizedTotal >= 0 ? "+" : ""}${fmt$(unrealizedTotal)}`}
              sub={unrealizedTotal >= 0 ? "offener Gewinn" : "offener Verlust"}
            />
            <BucketLabel
              tone="neg"
              label="Fees ausgegeben"
              value={fmt$(feesSpent)}
              sub={
                [
                  "gesamt (Entry+Exit)",
                  feesArtifact > 0 ? `+${fmt$(feesArtifact)} Mai-60bps-Artefakt ausgeschl.` : "",
                  feesPhantom > 0 ? `+${fmt$(feesPhantom)} Phantom (nicht handelbar) ausgeschl.` : "",
                ]
                  .filter(Boolean)
                  .join(" · ")
              }
            />
            <BucketLabel
              tone="neg"
              label="Fees heute"
              value={fmt$(feesToday)}
              sub={`${fillsToday} Fills heute (UTC)`}
            />
          </div>
          <p className="mt-3 pt-3 border-t border-line-subtle/40 text-2xs text-fg-subtle">
            Gesamt-Equity = Cash + Marktwert offener Positionen (Short = Verbindlichkeit),
            inkl. realisierter + unrealisierter G/V; Fees sind bereits abgezogen. Echtzeit aus
            /operator/portfolio-snapshot.
          </p>
        </Card>
      )}

      {/* DALI-P4-Lite: Ehrlicher Hinweis auf fehlende Buckets im Paper-Mode.
          Operator hat explizit nach On-Exchange / Withdrawn / On-Account
          gefragt — diese existieren erst im Live-Mode-Datenmodell. */}
      {/* DALI v2 S4 M2b: Buckets-Panel mit explizitem DevelopmentStatus.
          Operator sieht sofort, dass das auf Live-Mode wartet (planning 10%). */}
      <PreparedPanel
        title="Kapital-Aufteilung: Börse · Konto · Ausgezahlt"
        reason="Aktuell sieht KAI nur den Paper-Konto-Cash. Tatsächliche Börsen-Balances (echte Positionen auf Binance/Bybit), ausgezahlte Beträge und freier Konto-Cash sind im Live-Mode-Datenmodell vorgesehen — im Paper-Mode nicht anwendbar."
        detail={
          <>
            Live-Mode liefert: Börsen-Balance-Reader (Positionen + freie Margin),
            Auszahlungs-Audit aus <span className="font-mono">exchange_relay.py</span> für Withdrawals pro Asset,
            Konto-Cash als freie Margin. Vor Live-Mode keine sinnvolle Visualisierung.
          </>
        }
        status="live_only"
        roadmapNote="Live-only: Börsen-Balance-Reader, freie Margin und Withdrawal-Audit — im Paper-Mode bewusst nicht anwendbar."
      />

      {/* DALI-P2: Per-Asset-Unrealized-PnL als Heatmap-Pills.
          Operator: "Wo sind die Gewinne gemacht worden." */}
      {snap.state === "ready" && positions.length > 0 && (
        <Card padded className="synthwave-pulse-edge overflow-hidden">
          <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold mb-2">
            Unrealized PnL nach Asset (offene Positionen)
          </div>
          <div className="flex flex-wrap gap-1.5">
            {positions.map((p) => {
              const pnl = p.unrealized_pnl_usd ?? 0;
              const tone = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "muted";
              const intensity = Math.min(Math.abs(pnl) / maxAbsPnl, 1);
              return (
                <span
                  key={p.symbol}
                  title={`Entry ${fmt$(p.avg_entry_price)} · Markt ${fmt$(p.market_price)} · Wert ${fmt$(p.market_value_usd)}`}
                  className={cn(
                    "inline-flex items-baseline gap-1.5 rounded-sm border px-2 py-1 text-xs font-mono",
                    tone === "pos" && "border-pos/30 text-pos",
                    tone === "neg" && "border-neg/30 text-neg",
                    tone === "muted" && "border-line-subtle text-fg-muted",
                  )}
                  style={{
                    backgroundColor:
                      tone === "pos"
                        ? `rgb(var(--pos) / ${0.04 + intensity * 0.12})`
                        : tone === "neg"
                          ? `rgb(var(--neg) / ${0.04 + intensity * 0.12})`
                          : undefined,
                  }}
                >
                  <span className="font-semibold">{p.symbol}</span>
                  <span>
                    {pnl >= 0 ? "+" : ""}
                    {fmt$(pnl, 0)}
                  </span>
                </span>
              );
            })}
          </div>
          <div className="mt-2 text-2xs text-fg-subtle leading-relaxed">
            Stärke der Hintergrund-Färbung = Größe des PnL relativ zur größten Bewegung. Hover für Entry/Markt-Preis.
          </div>
        </Card>
      )}

      {/* 2026-06-23: Edge-Wahrheit sicht- + nachvollziehbar im Dashboard
          (canonical vs voller Stream, Quarantäne-Transparenz) — schließt die
          Lücke aus der Edge-Epochen-Forensik (canonical-Edge lag nur in der CLI). */}
      <EdgeTruthPanel />

      {/* /goal 2026-06-25 Churn-Frage: Fee-Effizienz sicht-/nachvollziehbar —
          Brutto-vor-Fees vs Netto-nach-Fees, Fee-Drag, Fees/Handelstag-Trend.
          Reine Messung (kein Min-Hold/Frequenz-Gate; Daten widerlegten beides). */}
      <ChurnPanel />

      {/* G0 (/goal Momentum-Universe-Rotation): das aus EIGENEN Börsendaten
          gebildete Universe (most-traded × best-performer) sicht-/nachvollziehbar.
          READ-ONLY Sicht; Rotation + Edge-Messung folgen (G1/G2). */}
      <MomentumUniversePanel />

      {/* 2026-05-25 Forensik-Patch: PreparedPanel ersetzt durch echte Visualisierung.
          Endpoint GET /operator/portfolio/realized-by-asset existiert jetzt,
          liest Paper-Audit ohne Live-Mode / Backtest-Endpoint-Abhängigkeit. */}
      <RealizedByAssetPanel />

      {/* /goal 2026-05-28: Diversifikation & Klumpenrisiko — Asset-Verteilung,
          Short-Term vs Reserve, Cluster-Warnungen, Signalherkunft und
          diversifizierte Alternativen statt BTC/ETH-Schleife. */}
      <DiversificationPanel />

      {/* DALI-P-Klartext: Exposure-Card komplett umstrukturiert.
          Operator: "Was soll ich unter OHNE PREIS 2 BTC/USDT (100%) verstehen?
          Konzentrationsrisiko 100%? BRUTTO-... 1.776,00€ NETTO-E... abgeschnitten?"
          Lösung: Klartext-Reihen mit voller Label-Breite, kein truncate. Erklärung
          in jeder Zeile statt Snake-Case-Keys. Konzentrations-Visualisierung mit
          Klartext-Aussage. */}
      {exposure.state === "ready" && (
        <Card padded className="synthwave-pulse-edge overflow-hidden">
          <CardHeader
            title="Exposure & Risiko-Übersicht"
            subtitle="Wie ist das Portfolio gerade aufgestellt — und wo sind Stolperfallen?"
            right={
              <span title={`Mark-to-Market-Status: ${exposure.data.mark_to_market_status}`}>
                <Badge tone={exposure.data.mark_to_market_status === "ok" ? "pos" : "warn"} dot>
                  Bewertung: {exposure.data.mark_to_market_status === "ok" ? "frisch" : exposure.data.mark_to_market_status}
                </Badge>
              </span>
            }
          />

          {/* Zwei Hero-Werte: Brutto + Netto in voller Breite, Klartext-Hinweis darunter. */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
            <div className="rounded-md border border-line-subtle bg-bg-2 p-3">
              <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">Brutto-Exposure</div>
              <div className="mt-1 font-mono text-xl font-semibold text-fg">{fmt$(exposure.data.gross_exposure_usd)}</div>
              <div className="mt-1 text-2xs text-fg-subtle leading-relaxed">
                Summe aller absoluten Positionswerte — unabhängig von Long/Short.
              </div>
            </div>
            <div className="rounded-md border border-line-subtle bg-bg-2 p-3">
              <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">Netto-Exposure</div>
              <div className="mt-1 font-mono text-xl font-semibold text-fg">{fmt$(exposure.data.net_exposure_usd)}</div>
              <div className="mt-1 text-2xs text-fg-subtle leading-relaxed">
                Long-Positionen minus Short — der Richtungs-Bias deines Portfolios.
              </div>
            </div>
          </div>

          {/* Positions-Health: Preis-Status pro Position */}
          <div className="space-y-2 mb-3">
            <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">Preis-Status der Positionen</div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-xs">
              <PriceStatusRow
                count={exposure.data.priced_position_count}
                label="mit frischem Marktpreis"
                hint="Bewertung verlässlich"
                tone="pos"
              />
              <PriceStatusRow
                count={exposure.data.stale_position_count}
                label="mit altem Preis"
                hint="Bewertung evtl. ungenau"
                tone={exposure.data.stale_position_count > 0 ? "warn" : "muted"}
              />
              <PriceStatusRow
                count={exposure.data.unavailable_price_count}
                label="ohne Preis"
                hint={exposure.data.unavailable_price_count > 0 ? "Provider lieferte keinen Kurs — diese Positionen können nicht bewertet werden" : "alle Positionen haben einen Kurs"}
                tone={exposure.data.unavailable_price_count > 0 ? "neg" : "muted"}
              />
            </div>
          </div>

          {/* Konzentrations-Indikator mit Klartext-Aussage */}
          {exposure.data.largest_position_symbol && (
            <div className="pt-3 border-t border-line-subtle/40">
              <div className="flex items-baseline justify-between mb-1.5 flex-wrap gap-2">
                <span className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">Konzentrationsrisiko</span>
                <span
                  className={cn(
                    "font-mono text-xs font-semibold",
                    concentrationTone === "neg" && "text-neg",
                    concentrationTone === "warn" && "text-warn",
                    concentrationTone === "pos" && "text-pos",
                  )}
                >
                  {largestPct.toFixed(1)}% in {exposure.data.largest_position_symbol}
                </span>
              </div>
              <div className="h-1.5 w-full rounded-xs bg-bg-2 overflow-hidden">
                <div
                  className={cn(
                    "h-full transition-all",
                    concentrationTone === "neg" && "bg-neg",
                    concentrationTone === "warn" && "bg-warn",
                    concentrationTone === "pos" && "bg-pos",
                  )}
                  style={{ width: `${Math.min(largestPct, 100)}%` }}
                />
              </div>
              <div className="mt-1.5 text-2xs text-fg-muted leading-relaxed">
                {largestPct >= 99 && (snap.state === "ready" && snap.data.position_count <= 1)
                  ? <>Du hältst aktuell nur eine Position — entsprechend liegen 100% deines Markteinsatzes in <span className="font-mono">{exposure.data.largest_position_symbol}</span>. Mehr Positionen reduzieren das Klumpenrisiko.</>
                  : concentrationTone === "neg"
                    ? <>Hoch konzentriert ({'>'}70% in einer Position) — Klumpenrisiko. Wenn dieses Asset stark fällt, fällt das ganze Portfolio mit.</>
                    : concentrationTone === "warn"
                      ? <>Erhöhte Konzentration (40–70% in einer Position). Im Auge behalten.</>
                      : <>Gut diversifiziert ({'<'}40% in einer Position).</>}
              </div>
            </div>
          )}
        </Card>
      )}
      {exposure.state === "error" && <ErrorCard kind={exposure.error.kind} message={exposure.error.message} path="/operator/exposure-summary" />}

      {/* 2026-05-16 V3: Premium-Trade-Karten (Hero-Komponente pro aktivem
          Telegram-Premium-Signal). Zeigt Plan (Entry, SL, 4 TP-Tiers) + Live
          (Markt, Bewegung, PnL) + nächsten Close-Trigger in einer kompakten
          Karte — der Tabelle-Zeilen-Pfad bleibt zusätzlich erhalten. Operator
          sieht in 1 Sekunde wo der Trade steht und was als nächstes passiert.
          Filter: source startet mit "telegram_premium" (deckt
          _channel + _channel_approved). */}
      {snap.state === "ready" && positions.filter((p) => (p.source ?? "").startsWith("telegram_premium")).length > 0 && (
        <section className="space-y-3">
          <div className="flex items-baseline justify-between gap-2 flex-wrap px-1">
            <div>
              <div className="text-sm font-semibold tracking-tight text-fg">Aktive Premium-Trades</div>
              <div className="text-2xs text-fg-subtle">
                Hero-Ansicht pro offenem Telegram-Premium-Signal — Plan, Live-Kurs und nächster Trigger.
              </div>
            </div>
            <div className="text-2xs text-fg-subtle font-mono">
              {positions.filter((p) => (p.source ?? "").startsWith("telegram_premium")).length} aktiv
            </div>
          </div>
          {positions
            .filter((p) => (p.source ?? "").startsWith("telegram_premium"))
            .map((p) => (
              <PremiumTradeCard
                key={`premium-${p.symbol}-${p.correlation_id ?? "no-cid"}`}
                position={p}
                fmt$={fmt$}
                busy={actionBusy !== null}
                onClose={(symbol, idempotencyKey, mktUnavailable) => {
                  // Hard-Confirm-Logik gespiegelt von der Tabellen-Zeile in #46:
                  // bei fehlender Markt-Bewertung ausführlich erklären was passiert.
                  const tierCount = (p.take_profit_tiers ?? []).length;
                  const confirmMsg = mktUnavailable
                    ? `⚠️ ${symbol}\n\n` +
                      `Diese Position LEBT — Stop-Loss (${fmt$(p.stop_loss)}) und ` +
                      `Take-Profit-Tier(s) (${fmt$(p.take_profit)}${tierCount > 1 ? ` +${tierCount - 1}` : ""}) ` +
                      `sind weiter aktiv im Hintergrund.\n\n` +
                      `Es fehlt nur der aktuelle Markt-Preis (Provider listet diesen Token nicht). ` +
                      `Die Position wird automatisch geschlossen sobald SL oder ein TP-Tier erreicht ist.\n\n` +
                      `Wenn du JETZT manuell schließt:\n` +
                      `• Close-Preis = Einstieg (${fmt$(p.avg_entry_price)}) ohne Markt-Daten\n` +
                      `• kein realer Profit, nur Slippage + Fees\n` +
                      `• die geplante Strategie wird abgebrochen\n\n` +
                      `Wirklich JETZT manuell schließen?`
                    : `Premium-Trade ${symbol} schließen?\n\n` +
                      `Stop-Loss steht bei ${fmt$(p.stop_loss)}, nächster TP-Tier bei ` +
                      `${fmt$((p.take_profit_tiers ?? [])[0]?.price ?? p.take_profit)}.\n\n` +
                      `Manuelles Schließen bricht den Plan ab.`;
                  if (!window.confirm(confirmMsg)) return;
                  runAction(`Close ${symbol}`, () =>
                    postPositionRepair(symbol, "close", { idempotency_key: idempotencyKey }),
                  );
                }}
              />
            ))}
        </section>
      )}

      {/* /goal 2026-05-20: Premium-Signal Trail — End-to-End-Sicht aller
          Pipeline-Stages pro Envelope (raw → envelope → approved → bridge
          → paper → closed). Ersetzt die alte "External grün, Portfolio leer"-
          Wahrnehmung durch differenzierte Status (CLOSED / BRIDGE_REJECTED /
          PAPER_REJECTED / SOURCE_SKIPPED / PENDING_ENTRY / OPEN). */}
      <PremiumSignalTrail limit={20} />

      {/* Sprint E (2026-05-12): Operator-Actions für Premium-Signal-Pipeline */}
      <Card padded className="synthwave-pulse-edge overflow-hidden">
        <CardHeader title="Operator-Aktionen" />
        <div className="flex flex-wrap gap-2 mt-2">
          <Button
            size="sm"
            variant="outline"
            disabled={actionBusy !== null}
            onClick={() => {
              const key = `reprocess-${Date.now()}`;
              runAction("Reprocess Bridge", () => postReprocess(undefined, key));
            }}
          >
            <Play size={12} /> Reprocess Bridge
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={actionBusy !== null}
            onClick={() => {
              const sym = window.prompt("Symbol (z.B. TRUTH/USDT) für Target-Completion-Reconcile?");
              if (!sym) return;
              const priceStr = window.prompt("Touch-Price (leer = aktueller Markt)?") ?? "";
              const price = priceStr.trim() ? Number(priceStr.replace(",", ".")) : undefined;
              const key = `reconcile-${sym}-${Date.now()}`;
              runAction(`Reconcile ${sym}`, () =>
                postReconcileCompletion(sym, Number.isFinite(price) ? price : undefined, key),
              );
            }}
          >
            <Target size={12} /> Target-Completion erzwingen
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={actionBusy !== null}
            onClick={() => {
              const env = window.prompt("Envelope-ID (aus pending-envelopes oder Telegram-Log)?");
              if (!env) return;
              const key = `fill-${env}`;
              runAction(`Manual Fill ${env.slice(0, 12)}…`, () => postManualFill(env, key));
            }}
          >
            <Play size={12} /> Manueller Signal-Fill
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={actionBusy !== null}
            onClick={async () => {
              setActionBusy("Pending-Liste");
              setActionMsg(null);
              try {
                const r = await fetchPendingEnvelopes(50);
                const summary = r.envelopes
                  .map((e) => `${e.symbol ?? "?"} · ${e.current_bridge_stage ?? "—"} · ${e.envelope_id}`)
                  .join("\n");
                setActionMsg({
                  kind: "ok",
                  text: `${r.count} pending envelopes:\n${summary || "(keine)"}`,
                });
              } catch (e) {
                setActionMsg({ kind: "err", text: `Pending-Liste: ${(e as Error).message}` });
              } finally {
                setActionBusy(null);
              }
            }}
          >
            <RefreshCw size={12} /> Pending-Envelopes
          </Button>
        </div>
        {actionBusy && (
          <div className="text-2xs text-fg-subtle mt-2 font-mono">
            … {actionBusy} läuft
          </div>
        )}
        {actionMsg && (
          <div
            className={cn(
              "text-2xs mt-2 font-mono whitespace-pre-wrap break-all",
              actionMsg.kind === "ok" ? "text-pos" : "text-neg",
            )}
          >
            {actionMsg.text}
          </div>
        )}
        <div className="text-2xs text-fg-subtle mt-3">
          Idempotent via interne Action-Keys. Aktionen erscheinen in
          <span className="font-mono"> artifacts/premium_signal_actions.jsonl</span>.
        </div>
      </Card>

      {/* 2026-05-16 V2: Banner wenn ≥1 Position keinen Markt-Preis hat — verhindert
          False-Close-Reflex ("rein und tot"). Die Position LEBT, nur die Bewertung
          steht aus, weil der Preis-Provider den Token nicht listet. Operator-Auftrag
          nach BAS/ASTER-Forensik 2026-05-14. */}
      {snap.state === "ready" && positions.some((p) => p.market_data_available === false) && (
        <Card padded className="border-info/40 bg-info/5">
          <div className="flex items-start gap-3">
            <AlertCircle size={16} className="text-info shrink-0 mt-0.5" />
            <div className="text-xs leading-relaxed">
              <div className="font-semibold text-fg mb-1">
                {positions.filter((p) => p.market_data_available === false).length} Position(en) ohne aktuellen Markt-Preis
              </div>
              <div className="text-fg-muted">
                Diese Positionen <strong>leben</strong> — Stop-Loss und Take-Profit-Tiers wirken weiter im Hintergrund.
                Nur die Live-Bewertung (Markt-Preis, Wert, offener G/V) ist aktuell nicht verfügbar, weil der Preis-Provider
                <span className="font-mono"> ({snap.data.source}) </span>
                den Token nicht listet.
                <span className="block mt-1 text-fg-subtle">
                  „Schließen" nur klicken wenn du die Position wirklich auflösen willst — der Trade läuft sonst normal seinen geplanten Verlauf.
                </span>
              </div>
            </div>
          </div>
        </Card>
      )}

      <Card padded={false}>
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-line-subtle">
          <div className="text-sm font-semibold tracking-tight text-fg">Offene Positionen</div>
          <div className="text-2xs text-fg-subtle font-mono">
            {snap.state === "ready"
              ? showAllPositions || positions.length <= POSITIONS_COLLAPSE_N
                ? `${positions.length} Positionen`
                : `${POSITIONS_COLLAPSE_N} von ${positions.length} · neueste zuerst`
              : ""}
          </div>
        </div>
        <div className="relative">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
            {/* DALI v2 S4 M2c: Klartext-Spalten + title-Tooltipps fuer Bedeutung
                (Master-Spec G1 + G2). Trading-Standard-Kuerzel (Long/Short, SL/TP)
                bleiben - sie sind Operator-Vokabular. Raw-Begriffe in title. */}
            <thead>
              <tr className="text-fg-subtle text-2xs uppercase tracking-wider">
                <th className="text-left font-semibold px-3 py-2" title="Trading-Symbol">Symbol</th>
                <th className="text-left font-semibold px-3 py-2" title="Richtung: Long = auf steigende Kurse, Short = auf fallende">Richtung</th>
                <th className="text-right font-semibold px-3 py-2" title="Hebel (Leverage) — Multiplikator des Einsatzes">Hebel</th>
                <th className="text-right font-semibold px-3 py-2" title="Stueckzahl der Position">Menge</th>
                <th className="text-right font-semibold px-3 py-2" title="Durchschnittlicher Einstiegspreis">Einstieg</th>
                <th className="text-right font-semibold px-3 py-2" title="Aktueller Marktpreis">Markt</th>
                <th className="text-right font-semibold px-3 py-2" title="Aktueller Marktwert der Position">Wert</th>
                <th className="text-right font-semibold px-3 py-2" title="Nicht realisierter Gewinn/Verlust — noch offen">Offen G/V</th>
                <th className="text-right font-semibold px-3 py-2" title="Realisierter Gewinn/Verlust — bereits gebucht">Geb. G/V</th>
                <th className="text-right font-semibold px-3 py-2" title="Stop-Loss-Limit: Position schliesst bei diesem Preis automatisch (Verlustbegrenzung)">Stop</th>
                <th className="text-right font-semibold px-3 py-2" title="Take-Profit-Limit: Position schliesst bei diesem Preis automatisch (Gewinnmitnahme)">Ziel</th>
                <th className="text-left font-semibold px-3 py-2" title="Wo das Signal urspruenglich herkam">Quelle</th>
                <th className="text-right font-semibold px-3 py-2">Aktion</th>
              </tr>
            </thead>
            <tbody>
              {snap.state === "loading" && (
                <tr><td colSpan={13} className="px-4 py-6 text-center text-fg-subtle">{t("common.loading")}</td></tr>
              )}
              {snap.state === "ready" && positions.length === 0 && (
                <tr><td colSpan={13} className="px-4 py-6 text-center text-fg-subtle">{t("common.no_data")}</td></tr>
              )}
              {visiblePositions.map((p) => {
                const side = (p.position_side ?? "long").toUpperCase();
                const sideTone = side === "SHORT" ? "neg" : "pos";
                const lev = p.leverage;
                const tierCount = (p.take_profit_tiers ?? []).length;
                const sourceLabel = formatSource(p.source);
                const isStale = p.market_data_is_stale === true;
                const mktUnavailable = p.market_data_available === false;
                const unreal = p.unrealized_pnl_usd ?? 0;
                const realiz = p.realized_pnl_usd ?? 0;
                return (
                  <tr key={`${p.symbol}-${p.correlation_id ?? "no-cid"}`} className="border-t border-line-subtle hover:bg-bg-2">
                    <td className="px-3 py-2 font-mono font-semibold whitespace-nowrap">
                      <div className="flex items-center gap-1.5">
                        <span>{p.symbol}</span>
                        {/* 2026-05-16 V2: Inline-Marker wenn Provider keinen Preis liefert.
                            Position lebt, Bewertung steht aus — stoppt False-Close-Klick. */}
                        {mktUnavailable && (
                          <Badge
                            tone="info"
                            title="Preis-Provider liefert keinen Kurs für diesen Token — die Position lebt, SL/TP sind aktiv, nur die Live-Bewertung fehlt."
                          >
                            Preis steht aus
                          </Badge>
                        )}
                      </div>
                      {p.opened_at && (
                        <div className="text-2xs text-fg-subtle font-normal mt-0.5">
                          {formatOpenedAt(p.opened_at)}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <Badge tone={sideTone}>{side}</Badge>
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {lev != null && lev > 0 ? `${lev}x` : "—"}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">{formatQty(p.quantity, currency)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmt$(p.avg_entry_price)}</td>
                    <td className={cn(
                      "px-3 py-2 text-right font-mono",
                      isStale && "text-warn",
                      mktUnavailable && "text-info",
                    )}>
                      {mktUnavailable ? (
                        <span
                          className="text-2xs"
                          title="Preis-Provider liefert für diesen Token keinen Kurs. Position lebt unverändert weiter."
                        >
                          kein Kurs
                        </span>
                      ) : (
                        <>
                          {fmt$(p.market_price)}
                          {isStale && (
                            <span
                              className="ml-1 text-2xs"
                              title="Marktpreis ist veraltet (stale) — Bewertung evtl. ungenau"
                            >
                              ·alt
                            </span>
                          )}
                        </>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {/* C-Fix 2026-06-13: kein Live-Kurs → Einstandswert (≈)
                          statt leer. mark_basis kennzeichnet die Quelle. */}
                      {p.market_value_usd == null && p.mark_basis === "entry_fallback" && p.display_value_usd != null ? (
                        <span
                          className="text-info"
                          title="Kein Live-Kurs vom Provider — angezeigt ist der Einstandswert (Menge × Einstieg), NICHT Live-bewertet."
                        >
                          ≈ {fmt$(p.display_value_usd)}
                        </span>
                      ) : (
                        fmt$(p.market_value_usd)
                      )}
                    </td>
                    <td className={cn(
                      "px-3 py-2 text-right font-mono",
                      unreal > 0 && "text-pos",
                      unreal < 0 && "text-neg",
                    )}>
                      {unreal >= 0 && p.unrealized_pnl_usd != null ? "+" : ""}
                      {fmt$(p.unrealized_pnl_usd)}
                    </td>
                    <td className={cn(
                      "px-3 py-2 text-right font-mono",
                      realiz > 0 && "text-pos",
                      realiz < 0 && "text-neg",
                    )}>
                      {fmt$(p.realized_pnl_usd)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-fg-subtle">{fmt$(p.stop_loss)}</td>
                    <td className="px-3 py-2 text-right font-mono text-fg-subtle">
                      {fmt$(p.take_profit)}
                      {tierCount > 1 && (
                        <span className="ml-1 text-2xs text-info">+{tierCount - 1}</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-left text-2xs text-fg-subtle whitespace-nowrap">
                      {sourceLabel}
                    </td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">
                      <button
                        type="button"
                        disabled={actionBusy !== null}
                        className={cn(
                          "inline-flex items-center gap-1 text-2xs font-mono px-2 py-1 rounded border border-line-subtle",
                          "hover:border-neg hover:text-neg disabled:opacity-50",
                        )}
                        onClick={() => {
                          // 2026-05-16 V2: Hard-Confirm bei fehlender Markt-Bewertung —
                          // explizit erklären dass die Position LEBT und SL/TP aktiv sind.
                          // Verhindert False-Close-Klick aus Unsicherheit ("rein und tot").
                          const confirmMsg = mktUnavailable
                            ? `⚠️ ${p.symbol}\n\n` +
                              `Diese Position LEBT — Stop-Loss (${fmt$(p.stop_loss)}) und ` +
                              `Take-Profit-Tier(s) (${fmt$(p.take_profit)}${tierCount > 1 ? ` +${tierCount - 1}` : ""}) ` +
                              `sind weiter aktiv im Hintergrund.\n\n` +
                              `Es fehlt nur der aktuelle Markt-Preis (Provider listet diesen Token nicht). ` +
                              `Die Position wird automatisch geschlossen sobald SL oder ein TP-Tier erreicht ist.\n\n` +
                              `Wenn du JETZT manuell schließt:\n` +
                              `• Close-Preis = Einstieg (${fmt$(p.avg_entry_price)}) ohne Markt-Daten\n` +
                              `• kein realer Profit, nur Slippage + Fees\n` +
                              `• die geplante Strategie wird abgebrochen\n\n` +
                              `Wirklich JETZT manuell schließen?`
                            : `Position ${p.symbol} schließen (Notfall-Close zum avg_entry)?`;
                          if (!window.confirm(confirmMsg)) return;
                          const key = `close-${p.symbol}-${Date.now()}`;
                          runAction(`Close ${p.symbol}`, () =>
                            postPositionRepair(p.symbol, "close", { idempotency_key: key }),
                          );
                        }}
                      >
                        <X size={10} /> Schließen
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
          <div
            className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-bg-1 to-transparent md:hidden"
            aria-hidden
          />
        </div>
        {snap.state === "ready" && positions.length > POSITIONS_COLLAPSE_N && (
          <div className="px-4 py-2.5 border-t border-line-subtle">
            <ShowMoreToggle
              expanded={showAllPositions}
              totalCount={positions.length}
              hiddenCount={positions.length - POSITIONS_COLLAPSE_N}
              noun="Positionen"
              onToggle={() => setShowAllPositions((s) => !s)}
            />
          </div>
        )}
      </Card>

      {/* DALI v2 S4 M2b: Equity-Kurve mit DevelopmentStatus.
          Operator-Brief: "wie sich das Kapital entwickelt, Drawdowns,
          historische Performance, kritische Entwicklungen". Wartet auf
          Aggregations-Endpoint (planning 20%). */}
      <PreparedPanel
        title="Kapital-Verlauf & historische Gewinne"
        reason="Wie hat sich das Kapital über Zeit entwickelt? Wo waren die größten Rückschläge (Drawdowns)? Welche Phasen waren kritisch? Visualisierung als Linienchart mit Drawdown-Spiegel."
        detail={
          <>
            Rohdaten in <span className="font-mono">artifacts/paper_execution_audit.jsonl</span>.
            Geplant: Equity-Linie + Drawdown-Banner + Max-Drawdown-Marker.
            Zielanzeige: kritische Phasen werden mit warn/neg-Tönen markiert.
          </>
        }
        status="roadmap"
        roadmapNote="Roadmap: Equity/Drawdown-Aggregation aus paper_execution_audit.jsonl."
      />
    </div>
  );
}

// 2026-05-10 DALI-P-Klartext: Preis-Status-Reihe im Exposure-Card.
// Statt "unavailable_price 2 BTC/USDT (100%)" jetzt "2 Positionen ohne Preis
// — Provider lieferte keinen Kurs". Klartext + Tone-Akzent links.
function PriceStatusRow({
  count,
  label,
  hint,
  tone,
}: {
  count: number;
  label: string;
  hint: string;
  tone: "pos" | "warn" | "neg" | "muted";
}) {
  const accentBar =
    tone === "pos" ? "bg-pos"
    : tone === "warn" ? "bg-warn"
    : tone === "neg" ? "bg-neg"
    : "bg-fg-subtle/40";
  const valueColor =
    tone === "pos" ? "text-pos"
    : tone === "warn" ? "text-warn"
    : tone === "neg" ? "text-neg"
    : "text-fg-muted";
  return (
    <div className="flex items-start gap-2">
      <span className={cn("mt-1 h-3 w-1 rounded-full shrink-0", accentBar)} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-1.5">
          <span className={cn("font-mono font-semibold text-base", valueColor)}>{count}</span>
          <span className="text-xs text-fg break-words">{label}</span>
        </div>
        <div className="text-2xs text-fg-subtle leading-relaxed mt-0.5">{hint}</div>
      </div>
    </div>
  );
}

/**
 * Einheitlicher Einklapp-Toggle für lange Listen/Tabellen (DALI 2026-06-14).
 * Eingeklappt sind nur die ersten N Einträge sichtbar; der Toggle nennt die
 * Gesamtzahl und wie viele verborgen sind, damit der Operator nichts übersieht.
 */
function ShowMoreToggle({
  expanded,
  totalCount,
  hiddenCount,
  noun,
  onToggle,
}: {
  expanded: boolean;
  totalCount: number;
  hiddenCount: number;
  noun: string;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      className={cn(
        "mt-2 inline-flex items-center gap-1.5 rounded-sm px-2 py-1 text-2xs font-medium",
        "text-fg-subtle hover:text-fg hover:bg-bg-2 transition-colors",
        "focus:outline-none focus-visible:ring-1 focus-visible:ring-accent/40",
      )}
    >
      {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      {expanded
        ? "Weniger anzeigen"
        : `Alle ${totalCount} ${noun} anzeigen · +${hiddenCount}`}
    </button>
  );
}

function BucketLabel({
  tone,
  label,
  value,
  sub,
}: {
  tone: "info" | "pos" | "ai" | "neg" | "muted";
  label: string;
  value: string;
  sub: string;
}) {
  const accentBar =
    tone === "info" ? "bg-info"
    : tone === "pos" ? "bg-pos"
    : tone === "ai" ? "bg-ai"
    : tone === "neg" ? "bg-neg"
    : "bg-fg-subtle/40";
  const accentText =
    tone === "info" ? "text-info"
    : tone === "pos" ? "text-pos"
    : tone === "ai" ? "text-ai"
    : tone === "neg" ? "text-neg"
    : "text-fg-muted";
  return (
    <div className="flex items-start gap-2">
      <span className={cn("mt-1 h-3 w-1 rounded-full shrink-0", accentBar)} aria-hidden />
      <div className="min-w-0">
        <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">{label}</div>
        <div className={cn("font-mono font-semibold text-base", accentText)}>{value}</div>
        <div className="text-2xs text-fg-subtle font-mono">{sub}</div>
      </div>
    </div>
  );
}

/**
 * Adaptive Quantity-Formatter. Sub-cent-Tokens haben oft 6-stellige Qty
 * (e.g. Q/USDT 85488.547329). High-price-Tokens (BTC) haben 0.01-stellige
 * Qty. fixed `toFixed(6)` macht beides unleserlich.
 */
function formatQty(v: number | null | undefined, currency: Currency): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  const maxDigits = abs >= 1000 ? 2 : abs >= 1 ? 4 : 6;
  return formatNumber(v, { currency, maxDigits });
}

function formatOpenedAt(iso: string): string {
  try {
    const d = new Date(iso);
    if (!Number.isFinite(d.getTime())) return iso.slice(0, 16);
    const today = new Date();
    const sameDay = d.toDateString() === today.toDateString();
    if (sameDay) {
      return formatClock(iso);
    }
    return formatShortDate(iso) + " " + formatClock(iso);
  } catch {
    return iso.slice(0, 16);
  }
}

/**
 * Short, human-readable Source-Label. Maps the long internal source-tags
 * the bridge writes (e.g. `telegram_premium_channel_approved`) to chip-friendly
 * compact labels. Unknown tags fall through with truncated original.
 */
/**
 * Concise toast-message for an action outcome. Avoids dumping the full JSON
 * payload into the operator-visible Action-Banner.
 */
function shortOutcome(r: unknown): string {
  if (r == null || typeof r !== "object") return "ok";
  const obj = r as Record<string, unknown>;
  if (typeof obj.status === "string") return String(obj.status);
  if (obj.tick && typeof obj.tick === "object") {
    const t = obj.tick as Record<string, unknown>;
    return `filled=${t.filled ?? 0} pending=${t.newly_pending ?? 0}+${t.re_pending ?? 0}`;
  }
  return "ok";
}

function formatSource(src: string | null | undefined): string {
  if (!src) return "—";
  const s = src.toLowerCase();
  if (s.startsWith("telegram_premium_channel")) {
    return s.endsWith("_approved") ? "TG·premium·✓" : "TG·premium";
  }
  if (s === "dashboard") return "dashboard";
  if (s.startsWith("tradingview")) return "TV";
  if (s.length > 18) return s.slice(0, 16) + "…";
  return s;
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
