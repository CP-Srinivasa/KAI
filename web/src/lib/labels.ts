// 2026-05-10 DALI-K2: Snake-Case zu Klartext-Map.
// 2026-05-11 DALI-T6: + CYCLE_STATUS_TITLE/REASON/TAB, PIPELINE_STEP_LABELS, TERM_EXPLAIN.
// 2026-05-11 DALI-T7: + PASTE_STATUS_LABEL, ENVELOPE_STAGE_LABEL, ENVELOPE_SOURCE_LABEL,
//                     PASTE_STEPPER_STEPS; TERM_EXPLAIN ergaenzt um Envelope/Idempotency/Stage.
//
// Verwendung: <span title={key}>{humanizeLabel(key)}</span>

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

  // Cycle-Status
  completed: "Trade ausgeführt",
  no_signal: "Kein Signal",
  no_market_data: "Keine Markt-Daten",
  consensus_rejected: "Konsens abgelehnt",
  priority_rejected: "Priorität zu niedrig",
  order_failed: "Order fehlgeschlagen",
  stale_data: "Veraltete Daten",
  blocked: "Blockiert",
  gate_blocked: "Quality-Gate blockiert",
  sl_failed: "SL-Order fehlgeschlagen",
  risk_rejected: "Risk-Gate abgelehnt",
  signal_below_threshold: "Signal unter Schwelle",

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

// Erklaerungen fuer Cycle-Status (Hover/title-Attribute).
export const CYCLE_STATUS_EXPLAIN: Record<string, string> = {
  completed: "Cycle hat eine Order erzeugt und gefüllt.",
  no_signal: "Daten OK, aber Signal-Engine sah keinen Trade-Anlass.",
  no_market_data: "Provider lieferte keine Kerzen - Cycle wurde übersprungen.",
  consensus_rejected: "LLM-Konsens war uneins; Risk-Gate blockierte den Trade.",
  priority_rejected: "Signal-Priorität war zu niedrig für die aktuelle Quality-Bar - Operator-Setting im Risk/Settings.",
  order_failed: "Signal + Risk OK, aber Exchange-Order schlug fehl. Notes prüfen.",
  stale_data: "Markt-Daten waren älter als das Freshness-Gate erlaubt.",
  blocked: "Cycle wurde vor Signalerkennung gestoppt (Run-Once / Mode-Lock).",
  gate_blocked: "Quality-Gate hat den Cycle blockiert - entweder Active-Precision unter Schwelle oder Volatility-Window-Pause.",
  sl_failed: "Stop-Loss-Order konnte nach Main-Order nicht platziert werden - Main wurde zurückgenommen.",
  risk_rejected: "Risk-Engine hat das Signal abgelehnt - z.B. zu hohes Konzentrationsrisiko oder Drawdown-Limit erreicht.",
  signal_below_threshold: "Signal-Confidence war unter dem konfigurierten Mindestwert.",
};

// 2026-05-11 DALI-T6: Title (Haupt-Zeile, vollstaendig) + Reason (Sub-Zeile).
// Getrennt von LABEL_DE (Kurzform fuer Tabs/Badges).
export const CYCLE_STATUS_TITLE: Record<string, string> = {
  completed: "Trade erfolgreich verarbeitet",
  no_signal: "Kein aktives Handelssignal erkannt",
  no_market_data: "Keine aktuellen Marktdaten verfügbar",
  consensus_rejected: "Signal durch KI-/Strategieprüfung abgelehnt",
  priority_rejected: "Signal-Priorität zu niedrig",
  order_failed: "Order konnte nicht an die Börse übermittelt werden",
  stale_data: "Markt-Daten zu alt",
  blocked: "Cycle blockiert",
  gate_blocked: "Quality-Gate aktiv - Cycle blockiert",
  sl_failed: "Stop-Loss-Order fehlgeschlagen",
  risk_rejected: "Risk-Gate hat Signal abgelehnt",
  signal_below_threshold: "Signal-Confidence unter Schwelle",
};

export const CYCLE_STATUS_REASON: Record<string, string> = {
  completed: "Signal vollständig abgeschlossen",
  no_signal: "Markt erfüllt aktuell keine Strategiebedingungen",
  no_market_data: "Datenfeed unterbrochen oder verzögert",
  consensus_rejected: "Marktbedingungen nicht eindeutig genug",
  priority_rejected: "Quality-Bar greift - siehe Operator-Settings",
  order_failed: "Ausführung fehlgeschlagen - Details siehe Notes",
  stale_data: "Freshness-Gate hat den Cycle übersprungen",
  blocked: "Run-Once oder Mode-Lock hat den Cycle vor Signal-Erkennung gestoppt",
  gate_blocked: "Active-Precision unter Schwelle oder Volatility-Window-Pause",
  sl_failed: "Main-Order zurückgenommen, da SL nicht platziert werden konnte",
  risk_rejected: "Konzentrations- oder Drawdown-Limit erreicht",
  signal_below_threshold: "Signal-Confidence unter konfiguriertem Mindestwert",
};

// Kurze Tab-Labels - Operator-Vorgabe: Fehler-Tabs vorne.
export const CYCLE_STATUS_TAB: Record<string, string> = {
  all: "Alle",
  order_failed: "Order fehlgeschlagen",
  consensus_rejected: "Konsens abgelehnt",
  no_market_data: "Keine Markt-Daten",
  no_signal: "Kein Signal",
  completed: "Trade ausgeführt",
};

// 2026-05-11 DALI-T6: Pipeline-Steps fuer Signals-Page.
// 5. Step Position-Management ist KEIN Cycle-Feld - separater Folgeprozess.
export type PipelineStepKey = "data" | "signal" | "risk" | "order" | "position";

export const PIPELINE_STEP_LABELS: Record<PipelineStepKey, { short: string; long: string; explain: string }> = {
  data: {
    short: "Marktdaten",
    long: "Marktdaten geladen",
    explain: "Aktuelle Kerzen, Orderbook-Snapshot und Indikatoren vom Provider eingeholt.",
  },
  signal: {
    short: "Signal-Analyse",
    long: "Signal-Analyse abgeschlossen",
    explain: "Strategie- und KI-Konsens hat einen Trade-Vorschlag erzeugt oder bewusst keinen.",
  },
  risk: {
    short: "Risiko-Prüfung",
    long: "Risiko-Prüfung bestanden",
    explain: "Konzentrations-, Drawdown- und Volatility-Gates haben das Signal freigegeben.",
  },
  order: {
    short: "Order-Ausführung",
    long: "Order an Börse übermittelt",
    explain: "Order wurde an die Exchange gesendet und akzeptiert.",
  },
  position: {
    short: "Position-Management",
    long: "Position-Management",
    explain: "Trailing, SL/TP-Anpassung und Close erfolgen nach Order-Ausführung in einem separaten Folgeprozess - nicht Teil dieses Cycle-Schemas.",
  },
};

// 2026-05-11 DALI-T7: Paste-Pipeline-Stepper fuer Externe-Signale-Page.
// Sechs operative Schritte, die der externe Signal-Paste durchlaeuft. Bewusst
// nicht 1:1 die Backend-Stages, sondern operator-verstaendlich abstrahiert:
// was passiert MIT meinem Signal?
export type PasteStepKey =
  | "analyse"
  | "extract"
  | "validate"
  | "dedupe"
  | "audit"
  | "handoff";

export const PASTE_STEPPER_STEPS: { key: PasteStepKey; short: string; explain: string }[] = [
  { key: "analyse",  short: "Nachricht analysieren",        explain: "KAI liest den Text und erkennt, ob es ein Trading-Signal, eine News oder eine Exchange-Antwort ist." },
  { key: "extract",  short: "Trading-Daten erkennen",       explain: "Symbol, Richtung (LONG/SHORT), Entry-Zone, Stop Loss, Targets und Leverage werden extrahiert." },
  { key: "validate", short: "Format & Sicherheit pruefen",  explain: "Schema-Check, Plausibilitaet, Risk-Gates. Unvollstaendige Signale gehen in den Ergaenzungs-Modus." },
  { key: "dedupe",   short: "Duplikate verhindern",         explain: "Idempotency-Key erkennt, ob dasselbe Signal bereits empfangen wurde - keine doppelte Verarbeitung." },
  { key: "audit",    short: "Audit-Log speichern",          explain: "Jeder Schritt wird unveraenderlich protokolliert - vollstaendige Forensik-Spur." },
  { key: "handoff",  short: "An Trading-System uebergeben", explain: "Akzeptierte Signale gehen in die Trading-Pipeline und koennen Orders ausloesen." },
];

// 2026-05-11 DALI-T7: Status-Klartext fuer /signals/paste-Response.
// Operator sieht statt rohen Status-Codes eine humane Erklaerung.
export const PASTE_STATUS_LABEL: Record<string, string> = {
  accepted: "Akzeptiert — Signal wurde verarbeitet",
  duplicate: "Duplikat — bereits empfangen, keine neue Verarbeitung",
  needs_completion: "Ergänzung nötig — bitte fehlende Felder ausfüllen",
  rejected: "Abgelehnt — siehe Fehler unten",
};

export const PASTE_STATUS_SHORT: Record<string, string> = {
  accepted: "Akzeptiert",
  duplicate: "Duplikat",
  needs_completion: "Ergänzung nötig",
  rejected: "Abgelehnt",
};

// 2026-05-11 DALI-T7: Envelope-Stage-Klartext.
export const ENVELOPE_STAGE_LABEL: Record<string, string> = {
  // 2026-06-04 RC-5: "accepted" = Parser + Schema-Validierung bestanden, Envelope
  // gespeichert. Das ist KEINE Execution. Das alte Label "In Trading-Pipeline
  // übergeben" suggerierte einen Trade, der nicht stattfand. Der Premium-Trail
  // zeigt den echten Execution-State (Bridge/Paper/Position).
  accepted: "Geparst & gespeichert (noch nicht ausgeführt)",
  idempotency_gate: "Duplikat-Erkennung",
  parse: "Nachricht analysiert",
  schema_validation: "Format-Prüfung",
  execution_gate: "Ausführungs-Gate",
  completion_gate: "Ergänzung erforderlich",
  voice_confirm_gate: "Wartet auf Sprach-Bestätigung",
  draft_pending: "Entwurf — wartet auf Bestätigung",
};

// 2026-05-11 DALI-T7: Quelle der Envelope-Eintraege humanisiert.
export const ENVELOPE_SOURCE_LABEL: Record<string, string> = {
  telegram: "Telegram",
  dashboard: "Dashboard",
  api: "API",
  manual: "Manuell",
  external: "Externe Schnittstelle",
};

// Fachbegriff-Tooltips fuer InfoHint (DALI-T6).
export const TERM_EXPLAIN: Record<string, string> = {
  konsens: "Mehrere KI-/Strategie-Komponenten stimmen über die Richtung ab. Bei Uneinigkeit wird der Trade nicht eröffnet.",
  quality_bar: "Adaptive Mindest-Precision pro Priority-Tier. Liegt die historische Precision unter dieser Schwelle, werden neue Signale dieses Tiers blockiert.",
  freshness_gate: "Maximales Alter der Marktdaten in Sekunden. Sind die letzten Kerzen älter, wird der Cycle übersprungen statt mit veralteten Preisen zu handeln.",
  risk_gate: "Prüft Konzentrations-, Drawdown- und Volatility-Limits, bevor eine Order ausgelöst werden darf.",
  // DALI-T7 Begriffe rund um externe Signale.
  envelope: "Ein Envelope ist die unveränderliche Hülle um ein eingehendes Signal. Sie enthält Rohtext, Parse-Ergebnis, Status und Audit-Spur. Quelle: Telegram, Dashboard, API — alles landet im selben Envelope-Format.",
  idempotency: "Schutz vor Doppelverarbeitung. KAI erzeugt aus jedem Signal einen eindeutigen Schlüssel (Idempotency-Key). Trifft dasselbe Signal erneut ein, wird es als Duplikat erkannt und nicht doppelt verarbeitet.",
  stage: "Aktuelle Position des Signals in der Verarbeitungs-Pipeline (z.B. parse, schema_validation, accepted). Sagt dir, WO im Prozess das Signal zuletzt war.",
  needs_completion: "Das Signal ist heuristisch lesbar, aber Pflichtfelder fehlen (z.B. Exchange, Stop Loss). Anstatt mit stillen Defaults zu raten, fragt KAI den Operator explizit nach den fehlenden Werten.",
  exchange_scope: "Liste der Exchanges, auf denen dieses Signal ausgeführt werden soll. Mehrere Exchanges möglich — KAI fächert die Order entsprechend.",
  audit_log: "Unveränderlicher Protokoll-Strom aller verarbeiteten Envelopes. Dashboard und Telegram teilen denselben Audit-Log — forensische Quelle der Wahrheit.",
};

export const OUTCOME_LABEL_DE: Record<string, string> = {
  hit: "Eingetroffen",
  miss: "Nicht eingetroffen",
  inconclusive: "Unklar",
};

/**
 * Liefert das deutsche Klartext-Label fuer einen Snake-Case-Key,
 * faellt auf den Original-Key zurueck wenn keine Uebersetzung definiert ist.
 */
export function humanizeLabel(key: string): string {
  return LABEL_DE[key] ?? key;
}

// 2026-06-04 DALI Truth-Visibility-Sprint: P0 Truth-Layer-Microcopy.
// Die Backend-Wahrheit (scope/stale_status/verdict) war typisiert, aber im UI
// kaum sichtbar. Diese Maps uebersetzen die rohen Truth-Layer-Codes in
// operator-lesbare Klartext-Labels — ohne den Neon-Cyberpunk-Stil zu aendern.

// Scope eines Metrik-Werts: was misst diese Zahl ZEITLICH?
export const METRIC_SCOPE_LABEL: Record<string, string> = {
  rolling_24h: "rollende 24h",
  cutoff_since: "seit Cutoff",
  lifetime: "Lifetime",
  historical: "historisch",
  all_time: "gesamt",
  session: "Session",
};

// Frische-Status eines Artefakts.
export const STALE_STATUS_LABEL: Record<string, string> = {
  fresh: "frisch",
  ok: "frisch",
  stale: "veraltet",
  critical: "kritisch alt",
  unverified: "unbestätigt",
  unknown: "unbekannt",
};

// Priority-Gate-Verdict (dashboard.py priority_quality.current_quality_verdict
// + heartbeat_status). 0 filled darf NICHT gesund wirken.
export const PRIORITY_VERDICT_LABEL: Record<string, string> = {
  priority_validated: "ACTIVE BLOCKING",
  priority_underperforming: "UNDERPERFORMING",
  priority_unproven: "UNPROVEN",
  insufficient_data: "INSUFFICIENT",
  unverified: "UNKNOWN",
};

export function metricScopeLabel(scope: string | null | undefined): string {
  if (!scope) return "—";
  return METRIC_SCOPE_LABEL[scope] ?? scope;
}

export function staleStatusLabel(status: string | null | undefined): string {
  if (!status) return "—";
  return STALE_STATUS_LABEL[status] ?? status;
}

// --- metric_contract Helper (DALI Truth-Sprint) ---
// Der metric_contract ist API-seitig vollstaendig typisiert (api.ts), wurde
// aber visuell nirgends konsumiert. Diese schlanken Helfer machen ihn an den
// kritischen Stellen nutzbar, ohne einen vollen Contract-Renderer zu bauen
// (Rest = P2). Sie sind tolerant: fehlt der Contract, kommt null/"" zurueck.
type MetricContractMap = NonNullable<
  import("@/lib/api").DashboardQuality["metric_contract"]
>;
export type MetricContractEntry = MetricContractMap[string];

export function getMetricContract(
  contract: MetricContractMap | undefined,
  metricKey: string,
): MetricContractEntry | null {
  return contract?.[metricKey] ?? null;
}

export function getMetricWarning(
  contract: MetricContractMap | undefined,
  metricKey: string,
): string | null {
  return contract?.[metricKey]?.warning ?? null;
}

export function getMetricScopeLabel(
  contract: MetricContractMap | undefined,
  metricKey: string,
): string {
  return metricScopeLabel(contract?.[metricKey]?.scope);
}
