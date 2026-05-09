// Geteiltes Source-Display-Mapping für die drei Source-Panels (V-DB5 I-4):
//   - ActivePrecisionCard
//   - PerSourcePrecisionPanel
//   - PerSourceStabilityPanel
//
// Mapping nicht in einer Component versteckt halten — Drift zwischen
// Panels ist exakt das, was V-DB5 H-1 als Architektur-Schmerz gemeldet
// hat. Backend-Source-Key bleibt als Suffix sichtbar (Forensik-Anker).

export type SourceDisplay = {
  label: string;
  hint?: string;
};

const SOURCE_DISPLAY: Record<string, SourceDisplay> = {
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

export function sourceLabel(key: string): SourceDisplay {
  return SOURCE_DISPLAY[key] ?? { label: key };
}
