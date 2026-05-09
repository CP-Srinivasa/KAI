import { Card, CardHeader } from "@/components/ui/Primitives";
import { useCurrency } from "@/state/CurrencyProvider";
import { cn } from "@/lib/utils";

export function AttributionRankingPanel({
  data,
}: {
  data?: Record<string, { total_pnl_usd: number; win_count: number; loss_count: number }>;
}) {
  const { fmt } = useCurrency();

  if (!data) return null;

  const entries = Object.entries(data).map(([tag, metrics]) => {
    const total_trades = metrics.win_count + metrics.loss_count;
    const win_rate = total_trades > 0 ? (metrics.win_count / total_trades) * 100 : 0;
    return { tag, ...metrics, total_trades, win_rate };
  });

  // Sort descending by total_pnl_usd
  entries.sort((a, b) => b.total_pnl_usd - a.total_pnl_usd);

  return (
    <Card padded={false}>
      <CardHeader
        title="Performance Attribution"
        subtitle="Historischer PnL nach Analyse-Profil (Source Tag)"
      />
      
      {entries.length === 0 ? (
        <div className="p-4 text-center text-sm text-fg-subtle">Keine Attribution-Daten verfügbar.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-fg-subtle text-2xs uppercase tracking-wider border-b border-line-subtle bg-bg-2/50">
                <th className="text-left font-semibold px-4 py-2">Source Tag</th>
                <th className="text-right font-semibold px-4 py-2">Total Trades</th>
                <th className="text-right font-semibold px-4 py-2">Win Rate</th>
                <th className="text-right font-semibold px-4 py-2">Total PnL</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry, idx) => (
                <tr key={entry.tag} className="border-b border-line-subtle/50 last:border-none hover:bg-bg-2">
                  <td className="px-4 py-2 font-mono font-medium flex items-center gap-2">
                    {idx === 0 && entry.total_pnl_usd > 0 && <span title="Top Performer">🏆</span>}
                    <span className="truncate max-w-[200px]" title={entry.tag}>{entry.tag}</span>
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-fg-subtle">
                    {entry.total_trades}
                    <span className="text-2xs opacity-50 ml-1">({entry.win_count}W/{entry.loss_count}L)</span>
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-fg-subtle">
                    {entry.win_rate.toFixed(1)}%
                  </td>
                  <td className={cn(
                    "px-4 py-2 text-right font-mono font-semibold",
                    entry.total_pnl_usd > 0 && "text-pos",
                    entry.total_pnl_usd < 0 && "text-neg",
                  )}>
                    {fmt(entry.total_pnl_usd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
