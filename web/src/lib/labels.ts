// 2026-05-10 DALI-K2: Snake-Case → Klartext-Map.
// Operator-Beschwerde: "gross_exposure / mark_to_market_status / last_cycle_id
// versteht kein Mensch". Diese Map löst das page-übergreifend in einem Schritt.
// Nur deutsche Labels — Operator arbeitet auf Deutsch (memory user_profile).
//
// Verwendung:
//   <span title={key}>{humanizeLabel(key)}</span>
//
// `title` behält den Snake-Case für Power-User; sichtbar wird nur das Klartext-Label.

export const LABEL_DE: Record<string, string> = {
  // Risk / Exposure
  gross_exposure: "Brutto-Exposure",
  net_exposure: "Netto-Exposure",
  priced_positions: "Positionen mit Preis",
  stale_positions: "Positionen mit altem Preis",
  unavailable_price: "Ohne Preis",
  largest_position: "Größte Position",
  largest_position_symbol: "Größte Position",
  largest_position_weight_pct: "Größte Position (Anteil)",
  mark_to_market: "MtM-Qualität",
  mark_to_market_status: "MtM-Qualität",
  position_count: "Positionen",
  total_equity_usd: "Gesamt-Equity",
  cash_usd: "Cash",
  realized_pnl_usd: "Realized PnL",
  unrealized_pnl_usd: "Unrealized PnL",

  // Operator-Readiness
  status: "Gesamt-Status",
  execution_enabled: "Real-Execution",
  write_back_allowed: "Write-Back",
  report_type: "Report-Typ",

  // Trading-Loop
  mode: "Mode",
  total_cycles: "Cycles gesamt",
  last_cycle_id: "Letzte Cycle-ID",
  last_cycle_symbol: "Letztes Symbol",
  last_cycle_status: "Letzter Status",
  last_cycle_completed_at: "Letzter Cycle-Abschluss",
  run_once_block_reason: "Run-Once-Block-Grund",
  auto_loop_enabled: "Auto-Loop",

  // Cycle-Status (Klartext + Erklärung getrennt — Erklärung in CYCLE_STATUS_EXPLAIN)
  completed: "Trade ausgeführt",
  no_signal: "Kein Signal",
  no_market_data: "Keine Markt-Daten",
  consensus_rejected: "Konsens abgelehnt",
  order_failed: "Order fehlgeschlagen",
  stale_data: "Veraltete Daten",
  blocked: "Blockiert",

  // Alerts
  document_id: "Dokument-ID",
  message_id: "Message-ID",
  channel: "Kanal",
  is_digest: "Digest",
  dispatched_at: "Versendet am",
  resolved_at: "Aufgelöst am",
  resolved_after_seconds: "Aufgelöst nach",
  outcome: "Trade-Vorhersage",
  hit: "Eingetroffen",
  miss: "Nicht eingetroffen",
  inconclusive: "Unklar",
  sentiment_label: "Sentiment",
  affected_assets: "Assets",
  priority: "Priorität",
  source_name: "Quelle",
  directional_block_reason: "Richtungs-Block-Grund",

  // Quality / Bayes
  active_precision_pct: "Active Precision",
  forward_precision_pct: "Forward Precision",
  priority_tier_lift_pct: "Priority Tier-Lift",
  paper_fills: "Paper-Fills",
  paper_cycles: "Paper-Cycles",
  active_hits: "Treffer (active)",
  active_misses: "Verfehlt (active)",
  active_resolved_count: "Resolved (active)",
  legacy_resolved_count: "Resolved (legacy)",
  forward_hits: "Treffer (forward)",
  forward_resolved: "Resolved (forward)",
  precision_pct: "Precision",
  hits: "Treffer",
  misses: "Verfehlt",
  resolved_count: "Resolved",
  legacy_unknown_cutoff: "Legacy-Cutoff",
  gate_status: "Gate-Status",
  blocking_reasons: "Blocking-Gründe",
};

// Erklärungs-Tooltips für Cycle-Status (für Hover/title-Attribute).
// Operator-Wunsch: "und wenn nein warum" — diese Map sagt es.
export const CYCLE_STATUS_EXPLAIN: Record<string, string> = {
  completed: "Cycle hat eine Order erzeugt und gefüllt.",
  no_signal: "Daten OK, aber Signal-Engine sah keinen Trade-Anlass.",
  no_market_data: "Provider lieferte keine Kerzen — Cycle wurde übersprungen.",
  consensus_rejected: "LLM-Konsens war uneins; Risk-Gate blockierte den Trade.",
  order_failed: "Signal + Risk OK, aber Exchange-Order schlug fehl. Notes prüfen.",
  stale_data: "Markt-Daten waren älter als das Freshness-Gate erlaubt.",
  blocked: "Cycle wurde vor Signalerkennung gestoppt (Run-Once / Mode-Lock).",
};

// Trade-Outcome-Klartext (für Alerts-Tabelle, getrennt von Send-Status).
export const OUTCOME_LABEL_DE: Record<string, string> = {
  hit: "Eingetroffen",
  miss: "Nicht eingetroffen",
  inconclusive: "Unklar",
};

/**
 * Liefert das deutsche Klartext-Label für einen Snake-Case-Key,
 * fällt auf den Original-Key zurück wenn keine Übersetzung definiert ist.
 * Verwendung in JSX: `<span title={key}>{humanizeLabel(key)}</span>`
 */
export function humanizeLabel(key: string): string {
  return LABEL_DE[key] ?? key;
}
