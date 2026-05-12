// 2026-05-12 DALI-arcade-T4: Pacman-Buehne als Cycle-Heartbeat-Panel.
// Spec docs/ui/dali_trades_arcade_spec.md - Operator-Prompt §3-§6.
//
// Position im Trades-Layout: zwischen Schutzschalter-Section und Cycle-Cards-
// Liste. Operator-Entscheidung: eigener Block, kein Overlay ueber Daten.
//
// Datenfluss: NICHT neue Backend-Felder. Rechnet selbst die 4 Geist-Buckets
// + Coin-Counter aus dem cycles-Array - die Status-Granularitaet ist hier
// feiner als in aggregateCyclesHealth() (dort 2 Buckets risk/api, hier
// 4 Geist-Farben rot/violett/gelb/blau).
//
// Status-Mapping Operator-Spec:
//   order_failed, sl_failed                            -> roter Geist (--neg)
//   consensus_rejected, signal_rejected                -> violetter Geist (--ai)
//   risk_rejected, priority_rejected,
//   signal_below_threshold, gate_blocked, blocked      -> gelber Geist (--warn)
//   no_market_data, stale_data, api_error,
//   exchange_timeout, fetch_failed                     -> blauer Geist (--info)
//   completed (mit order)                              -> Coin nach Symbol
//   sonst (no_signal, ...)                             -> ignoriert
//
// Animation: --pacman-march-duration = 30s pro Runde (Operator-Entscheidung
// aus Klaerungsrunde). prefers-reduced-motion: Bahn pausiert (CSS-Rule in
// index.css), Geister/Coins statisch, Score lesbar.

import { useMemo } from "react";
import type { TradingCycle } from "@/lib/api";
import { cn } from "@/lib/utils";

type GhostKind = "order" | "konsens" | "risk" | "api";
type CoinKind = "btc" | "eth" | "sol" | "xrp" | "usdt" | "generic";

type ArcadeAggregate = {
  ghostCounts: Record<GhostKind, number>;
  coinCounts: Record<CoinKind, number>;
  totalGhosts: number;
  totalCoins: number;
};

function statusToGhost(status: string): GhostKind | null {
  if (status === "order_failed" || status === "sl_failed") return "order";
  if (status === "consensus_rejected" || status === "signal_rejected") return "konsens";
  if (
    status === "risk_rejected" ||
    status === "priority_rejected" ||
    status === "signal_below_threshold" ||
    status === "gate_blocked" ||
    status === "blocked"
  ) {
    return "risk";
  }
  if (
    status === "no_market_data" ||
    status === "stale_data" ||
    status === "api_error" ||
    status === "exchange_timeout" ||
    status === "fetch_failed"
  ) {
    return "api";
  }
  return null;
}

function symbolToCoin(symbol: string): CoinKind {
  const head = (symbol.split("/")[0] ?? "").toUpperCase();
  if (head === "BTC") return "btc";
  if (head === "ETH") return "eth";
  if (head === "SOL") return "sol";
  if (head === "XRP") return "xrp";
  if (head === "USDT") return "usdt";
  return "generic";
}

function aggregateArcade(cycles: TradingCycle[]): ArcadeAggregate {
  const ghostCounts: Record<GhostKind, number> = { order: 0, konsens: 0, risk: 0, api: 0 };
  const coinCounts: Record<CoinKind, number> = {
    btc: 0, eth: 0, sol: 0, xrp: 0, usdt: 0, generic: 0,
  };
  for (const c of cycles) {
    if (c.status === "completed" && c.order_created) {
      coinCounts[symbolToCoin(c.symbol)] += 1;
      continue;
    }
    const g = statusToGhost(c.status);
    if (g) ghostCounts[g] += 1;
  }
  const totalGhosts = ghostCounts.order + ghostCounts.konsens + ghostCounts.risk + ghostCounts.api;
  const totalCoins = coinCounts.btc + coinCounts.eth + coinCounts.sol + coinCounts.xrp + coinCounts.usdt + coinCounts.generic;
  return { ghostCounts, coinCounts, totalGhosts, totalCoins };
}

// Pacman-SVG - klassischer ATARI-Look. Mund oeffnet/schliesst via SMIL-Animation
// (calcMode=discrete -> retro-stuck-Effekt). Bahn-Animation kommt von der
// .kai-pacman-march CSS-Klasse - die ist via prefers-reduced-motion abdimmbar.
function PacmanSprite({ size = 28 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      aria-hidden="true"
      style={{ filter: "drop-shadow(0 0 6px rgb(var(--warn) / 0.85))" }}
    >
      <circle cx="16" cy="16" r="14" fill="rgb(var(--warn))" />
      <polygon fill="rgb(var(--bg-0))">
        <animate
          attributeName="points"
          values="16,16 32,4 32,28; 16,16 31,15 31,17; 16,16 32,4 32,28"
          dur="0.42s"
          calcMode="discrete"
          repeatCount="indefinite"
        />
      </polygon>
      <circle cx="14" cy="9" r="1.8" fill="rgb(var(--bg-0))" />
    </svg>
  );
}

// Geist-SVG: klassische Woelbung oben, Zackensaum unten, 2 Augen.
// Farbe via currentColor - der Container setzt color per Tone-Class.
function GhostSprite({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      aria-hidden="true"
      style={{ filter: "drop-shadow(0 0 5px currentColor)" }}
    >
      <path
        d="M2 8 Q 2 1 8 1 Q 14 1 14 8 L 14 14 L 12 13 L 10 15 L 8 13 L 6 15 L 4 13 L 2 14 Z"
        fill="currentColor"
      />
      <circle cx="6" cy="7" r="1.4" fill="rgb(var(--bg-0))" />
      <circle cx="10" cy="7" r="1.4" fill="rgb(var(--bg-0))" />
      <circle cx="6.4" cy="7.2" r="0.65" fill="currentColor" />
      <circle cx="10.4" cy="7.2" r="0.65" fill="currentColor" />
    </svg>
  );
}

// Coin-Glyph + Glow nach Tone. Plain-Glyphs (kein Markenlogo) -
// rechtlich sauber + Stil-Reinheit.
function CoinSprite({ kind, size = 14 }: { kind: CoinKind; size?: number }) {
  const symbol =
    kind === "btc" ? "B"
    : kind === "eth" ? "E"
    : kind === "sol" ? "S"
    : kind === "xrp" ? "X"
    : kind === "usdt" ? "T"
    : "*";
  const tone =
    kind === "btc" ? "rgb(var(--warn))"
    : kind === "eth" ? "rgb(var(--info))"
    : kind === "sol" ? "rgb(var(--ai))"
    : kind === "xrp" ? "rgb(var(--info))"
    : kind === "usdt" ? "rgb(var(--pos))"
    : "rgb(var(--fg-subtle))";
  return (
    <span
      aria-hidden="true"
      className="inline-flex items-center justify-center rounded-full font-mono font-bold"
      style={{
        width: size,
        height: size,
        fontSize: Math.round(size * 0.72),
        color: tone,
        backgroundColor: "rgb(var(--bg-0) / 0.75)",
        border: "1px solid " + tone,
        textShadow: "0 0 4px " + tone,
        boxShadow: "0 0 6px " + tone + ", inset 0 0 3px " + tone,
        lineHeight: 1,
      }}
    >
      {symbol}
    </span>
  );
}

// Score-Zeile: Counter + Sprite-Indikator + Klartext-Label.
function ScoreRow({
  count,
  label,
  sprite,
  toneClass,
}: {
  count: number;
  label: string;
  sprite: React.ReactNode;
  toneClass: string;
}) {
  return (
    <div className={cn(
      "flex items-center gap-2 rounded-xs border border-line-subtle bg-bg-1 px-2 py-1",
      count === 0 && "opacity-50",
    )}>
      <span className={cn("shrink-0", toneClass)}>{sprite}</span>
      <span className="font-mono font-bold text-sm tabular-nums text-fg">
        {count.toString().padStart(2, "0")}
      </span>
      <span className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
        {label}
      </span>
    </div>
  );
}

// Maze-Bahn: dezentes Neon-Gitter-Pattern als Hintergrund-SVG.
// Inline statt CSS-bg-image - so spielt es zuverlaessig mit Tailwind-Tokens.
function MazeBackdrop() {
  return (
    <svg
      width="100%"
      height="100%"
      viewBox="0 0 320 80"
      preserveAspectRatio="none"
      aria-hidden="true"
      className="absolute inset-0"
    >
      <defs>
        <pattern id="arcade-grid" width="32" height="32" patternUnits="userSpaceOnUse">
          <path
            d="M 32 0 L 0 0 0 32"
            fill="none"
            stroke="rgb(var(--info))"
            strokeOpacity="0.15"
            strokeWidth="0.5"
          />
        </pattern>
      </defs>
      <rect width="320" height="80" fill="url(#arcade-grid)" />
      <line
        x1="0" y1="40" x2="320" y2="40"
        stroke="rgb(var(--info))"
        strokeOpacity="0.25"
        strokeWidth="1"
        strokeDasharray="4 4"
      />
    </svg>
  );
}

export function PacmanArcade({ cycles }: { cycles: TradingCycle[] }) {
  const agg = useMemo(() => aggregateArcade(cycles), [cycles]);

  // Sichtbare Geist-Sprite-Anzahl: gedeckelt damit die Bahn nicht ueberquillt.
  // Counter daneben zeigt die wahre Zahl - die Bahn ist Visual-Cue, nicht
  // 1:1-Abbild.
  const visibleSprites = useMemo(() => {
    const out: { kind: "ghost" | "coin"; tint: GhostKind | CoinKind; key: string }[] = [];
    (Object.keys(agg.ghostCounts) as GhostKind[]).forEach((g) => {
      const n = Math.min(agg.ghostCounts[g], 3);
      for (let i = 0; i < n; i++) out.push({ kind: "ghost", tint: g, key: "g-" + g + "-" + i });
    });
    (Object.keys(agg.coinCounts) as CoinKind[]).forEach((c) => {
      const n = Math.min(agg.coinCounts[c], 2);
      for (let i = 0; i < n; i++) out.push({ kind: "coin", tint: c, key: "c-" + c + "-" + i });
    });
    return out;
  }, [agg]);

  const ghostToneClass = (g: GhostKind): string =>
    g === "order" ? "text-neg"
    : g === "konsens" ? "text-ai"
    : g === "risk" ? "text-warn"
    : "text-info";

  const total = agg.totalGhosts + agg.totalCoins;
  const ariaLabel =
    "Live-Arcade Cycle-Heartbeat. " + agg.totalCoins + " Coins gesammelt, " +
    agg.totalGhosts + " Geister im Maze. " +
    "Order " + agg.ghostCounts.order + ", Konsens " + agg.ghostCounts.konsens +
    ", Risk " + agg.ghostCounts.risk + ", API " + agg.ghostCounts.api + ".";

  return (
    <section
      role="region"
      aria-label={ariaLabel}
      className="rounded-md border border-line-subtle bg-bg-1 p-3 sm:p-4"
      style={{ ["--pacman-march-duration" as never]: "30s" }}
    >
      <div className="flex items-baseline justify-between gap-3 flex-wrap mb-3">
        <div>
          <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
            Live-Arcade
          </div>
          <div className="text-sm font-semibold tracking-tight text-fg mt-0.5">
            Cycle-Heartbeat
          </div>
        </div>
        <div className="text-2xs text-fg-subtle font-mono">
          {total === 0 ? "keine Aktivitaet" : total + " Ereignisse - letzte 30 Cycles"}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-3">
        {/* Pacman-Buehne */}
        <div className="relative overflow-hidden rounded-xs border border-line-subtle bg-bg-0 h-[120px] sm:h-[140px]">
          <MazeBackdrop />
          {/* Sammler-Lane: Geister + Coins auf der Bahn rechts -
              Pacman startet links und sammelt sie ein. */}
          <div className="absolute inset-0 flex items-center justify-end pr-4 pl-20 gap-3 sm:gap-4">
            {visibleSprites.length === 0 ? (
              <span className="text-2xs text-fg-subtle italic">
                Maze leer - Pacman wartet auf den naechsten Cycle.
              </span>
            ) : (
              visibleSprites.map((s) =>
                s.kind === "ghost" ? (
                  <span key={s.key} className={ghostToneClass(s.tint as GhostKind)}>
                    <GhostSprite size={22} />
                  </span>
                ) : (
                  <CoinSprite key={s.key} kind={s.tint as CoinKind} size={16} />
                ),
              )
            )}
          </div>
          {/* Pacman - laeuft horizontal via CSS-Animation kai-pacman-march.
              Reduced-Motion: animation-play-state paused via @media-Rule. */}
          <div className="kai-pacman-march absolute top-1/2 -translate-y-1/2">
            <PacmanSprite size={32} />
          </div>
        </div>

        {/* Score-Panel */}
        <div className="grid grid-cols-2 lg:grid-cols-1 gap-1.5 lg:min-w-[200px]">
          <ScoreRow
            count={agg.totalCoins}
            label="Coins"
            sprite={<CoinSprite kind="generic" size={14} />}
            toneClass="text-pos"
          />
          <ScoreRow
            count={agg.ghostCounts.risk}
            label="Risk"
            sprite={<GhostSprite size={16} />}
            toneClass="text-warn"
          />
          <ScoreRow
            count={agg.ghostCounts.api}
            label="API-Fail"
            sprite={<GhostSprite size={16} />}
            toneClass="text-info"
          />
          <ScoreRow
            count={agg.ghostCounts.order}
            label="Order"
            sprite={<GhostSprite size={16} />}
            toneClass="text-neg"
          />
          <ScoreRow
            count={agg.ghostCounts.konsens}
            label="Konsens"
            sprite={<GhostSprite size={16} />}
            toneClass="text-ai"
          />
        </div>
      </div>
    </section>
  );
}

export default PacmanArcade;
