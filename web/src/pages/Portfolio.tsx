import { useState } from "react";
import { AlertCircle, RefreshCw, Play, Target, X, Briefcase } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { Badge, Button, Card, CardHeader } from "@/components/ui/Primitives";
import { PageHeader } from "@/layout/PageHeader";
import { useApi } from "@/lib/useApi";
import {
  fetchPortfolioSnapshot,
  fetchExposureSummary,
  postReprocess,
  postReconcileCompletion,
  postPositionRepair,
  postManualFill,
  fetchPendingEnvelopes,
  type PaperPosition,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { useCurrency } from "@/state/CurrencyProvider";

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

export function PortfolioPage() {
  const { t } = useT();
  const { fmt } = useCurrency();
  // Adaptive Decimals als Default — Operator-Auftrag 2026-05-12 Sektion 7
  // verlangt "keine automatische Umwandlung auf 0.01" für Display.
  const fmt$ = (v: number | null | undefined, digits?: number) =>
    v == null ? "—" : fmt(v, undefined, digits ?? priceDigits(v));
  const snap = useApi(fetchPortfolioSnapshot, 30_000);
  const exposure = useApi(fetchExposureSummary, 30_000);

  const positions: PaperPosition[] = snap.state === "ready" ? snap.data.positions : [];
  const unrealized = positions.reduce((sum, p) => sum + (p.unrealized_pnl_usd ?? 0), 0);
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
            <Badge tone="info" dot>Paper-Konto · {snap.data.source}</Badge>
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
          <div className="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
            <BucketLabel
              tone="info"
              label="In Positionen"
              value={fmt$(positionsValue)}
              sub={`${snap.data.position_count} offen`}
            />
            <BucketLabel
              tone="pos"
              label="Cash (Paper)"
              value={fmt$(cash)}
              sub="liquide"
            />
            <BucketLabel
              tone={realized < 0 ? "neg" : "ai"}
              label="Realized PnL"
              value={fmt$(realized)}
              sub="kumuliert"
            />
          </div>
          <div className="mt-3 pt-3 border-t border-line-subtle/40 flex items-baseline justify-between text-xs">
            <span className="text-fg-subtle">Unrealized PnL (offene Positionen)</span>
            <span
              className={cn(
                "font-mono font-semibold",
                unrealized > 0 ? "text-pos" : unrealized < 0 ? "text-neg" : "text-fg-muted",
              )}
            >
              {unrealized >= 0 ? "+" : ""}
              {fmt$(unrealized)}
            </span>
          </div>
        </Card>
      )}

      {/* DALI-P4-Lite: Ehrlicher Hinweis auf fehlende Buckets im Paper-Mode.
          Operator hat explizit nach On-Exchange / Withdrawn / On-Account
          gefragt — diese existieren erst im Live-Mode-Datenmodell. */}
      <PreparedPanel
        title="Buckets: On-Exchange · On-Account · Withdrawn"
        reason="KAI führt aktuell nur den Paper-Account-Cash separat. Tatsächliche On-Exchange-Balances (echte Positionen auf Binance/Bybit), Withdraw-Historie und freier Account-Cash sind im Live-Mode-Datenmodell vorgesehen — aber im Paper-Mode nicht relevant."
        detail={
          <>
            Phase Live (Sprint 39+): Exchange-Balance-Reader joint Positions + freie Margins. Withdraw-Audit aus{" "}
            <span className="font-mono">exchange_relay.py</span> ergibt Withdrawals pro Asset. Vor Live-Mode keine sinnvolle Visualisierung.
          </>
        }
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

      {/* PreparedPanel für Realized-PnL-pro-Asset (Backend-Lücke). */}
      <PreparedPanel
        title="Realized PnL nach Asset"
        reason="Per-Asset-Aufschlüsselung der realisierten Gewinne braucht Aggregation aus paper_execution_audit.jsonl — Endpoint folgt in Phase 2."
        detail={
          <>
            Quelle: <span className="font-mono">artifacts/paper_execution_audit.jsonl</span>. Geplant:{" "}
            <span className="font-mono">GET /operator/portfolio/realized-by-asset</span>.
          </>
        }
      />

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

      <Card padded={false}>
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-line-subtle">
          <div className="text-sm font-semibold tracking-tight text-fg">Offene Positionen</div>
          <div className="text-2xs text-fg-subtle font-mono">
            {snap.state === "ready" ? `${snap.data.position_count} Positionen` : ""}
          </div>
        </div>
        <div className="relative">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
            <thead>
              <tr className="text-fg-subtle text-2xs uppercase tracking-wider">
                <th className="text-left font-semibold px-3 py-2">Symbol</th>
                <th className="text-left font-semibold px-3 py-2">Side</th>
                <th className="text-right font-semibold px-3 py-2">Lev</th>
                <th className="text-right font-semibold px-3 py-2">Qty</th>
                <th className="text-right font-semibold px-3 py-2">Entry</th>
                <th className="text-right font-semibold px-3 py-2">Markt</th>
                <th className="text-right font-semibold px-3 py-2">Wert</th>
                <th className="text-right font-semibold px-3 py-2">Unrealized</th>
                <th className="text-right font-semibold px-3 py-2">Realized</th>
                <th className="text-right font-semibold px-3 py-2">SL</th>
                <th className="text-right font-semibold px-3 py-2">TP</th>
                <th className="text-left font-semibold px-3 py-2">Source</th>
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
              {positions.map((p) => {
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
                      <div>{p.symbol}</div>
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
                    <td className="px-3 py-2 text-right font-mono">{formatQty(p.quantity)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmt$(p.avg_entry_price)}</td>
                    <td className={cn(
                      "px-3 py-2 text-right font-mono",
                      (isStale || mktUnavailable) && "text-warn",
                    )}>
                      {fmt$(p.market_price)}
                      {isStale && <span className="ml-1 text-2xs">·stale</span>}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">{fmt$(p.market_value_usd)}</td>
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
                          if (!window.confirm(`Position ${p.symbol} schließen (Notfall-Close zum avg_entry)?`)) return;
                          const key = `close-${p.symbol}-${Date.now()}`;
                          runAction(`Close ${p.symbol}`, () =>
                            postPositionRepair(p.symbol, "close", { idempotency_key: key }),
                          );
                        }}
                      >
                        <X size={10} /> Close
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
      </Card>

      <PreparedPanel
        title="Equity-Kurve & historische PnL"
        reason="Equity-Verlauf, Drawdown-Kurve und Realized-PnL-Historie."
        detail="Quelle: artifacts/paper_execution_audit.jsonl — Aggregations-Endpoint folgt in Phase 2."
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
function formatQty(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1000) return v.toLocaleString("de-DE", { maximumFractionDigits: 2 });
  if (abs >= 1) return v.toLocaleString("de-DE", { maximumFractionDigits: 4 });
  return v.toLocaleString("de-DE", { maximumFractionDigits: 6 });
}

function formatOpenedAt(iso: string): string {
  try {
    const d = new Date(iso);
    if (!Number.isFinite(d.getTime())) return iso.slice(0, 16);
    const today = new Date();
    const sameDay = d.toDateString() === today.toDateString();
    if (sameDay) {
      return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    }
    return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" }) +
      " " + d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
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
