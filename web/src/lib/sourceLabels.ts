// V-DB5 audit I-4 (2026-05-09):
// Source-Display-Mapping aus ActivePrecisionCard extrahiert, damit alle drei
// Source-Panels (ActivePrecisionCard, PerSourcePrecisionPanel, PerSourceStability-
// Panel) dieselben humanisierten Labels und Tooltips zeigen — gegen Naming-Drift
// und Operator-Verwirrung ueber Backend-Source-Keys.

export type SourceLabel = {
  label: string;
  hint?: string;
};

const SOURCE_DISPLAY: Record<string, SourceLabel> = {
  unknown: {
    label: "Legacy (vor Provenance V1)",
    hint: "Alerts aus der Zeit, bevor die Source-Tagging-Pipeline live war. Druecken die Baseline nach unten — verschwinden ueber Zeit.",
  },
  rss: {
    label: "RSS-Feeds (gemischt)",
    hint: "Klassische News-Feeds: Coindesk, Cointelegraph-RSS etc. Sammelbucket fuer alles per RSS.",
  },
  tradingview_webhook: {
    label: "TradingView Webhook",
    hint: "Direkt von TradingView-Alerts an unseren Webhook. Hier landet die TV-Pivot-Pipeline.",
  },
  cointelegraph: {
    label: "Cointelegraph",
    hint: "Cointelegraph als eigene Source (jenseits der gemischten RSS-Bucket).",
  },
  decrypt: {
    label: "Decrypt News",
    hint: "decrypt.co — eigener Feed.",
  },
  tradingview: {
    label: "TradingView (legacy)",
    hint: "Alte TV-Eintraege vor Webhook-Cutover. Wird mit der Zeit von tradingview_webhook abgeloest.",
  },
};

/**
 * Liefert Anzeige-Label und Tooltip-Hint fuer einen Backend-Source-Key.
 * Fallback: roher Key, kein Hint — so bleibt der Forensik-Anker sichtbar.
 */
export function formatSourceLabel(key: string): SourceLabel {
  return SOURCE_DISPLAY[key] ?? { label: key };
}
