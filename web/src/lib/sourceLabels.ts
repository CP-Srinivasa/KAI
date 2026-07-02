// V-DB5 audit I-4 (2026-05-09):
// Geteiltes Source-Display-Mapping fuer die drei Source-Panels.
// 2026-05-11 DALI: Source-Keys erweitert um thedefiant, coindesk, blockworks,
// theblock, dlnews, newsdata, x_twitter, telegram, youtube. Fallback bleibt
// roher Key als Forensik-Anker, aber UI zeigt dann mindestens menschlichen
// Tag statt nur "thedefiant".

export type SourceLabel = {
  label: string;
  hint?: string;
};

const SOURCE_DISPLAY: Record<string, SourceLabel> = {
  unknown: {
    label: "Legacy (vor Provenance V1)",
    hint: "Alerts aus der Zeit, bevor die Source-Tagging-Pipeline live war. Druecken die Baseline nach unten und verschwinden ueber Zeit.",
  },
  rss: {
    label: "RSS-Feeds (gemischt)",
    hint: "Klassische News-Feeds (Coindesk, Cointelegraph-RSS etc.). Sammelbucket fuer alles per RSS.",
  },
  tradingview_webhook: {
    label: "TradingView Webhook",
    hint: "Direkt von TradingView-Alerts an unseren Webhook. Hier landet die TV-Pivot-Pipeline.",
  },
  tradingview: {
    label: "TradingView (legacy)",
    hint: "Alte TV-Eintraege vor Webhook-Cutover. Wird mit der Zeit von tradingview_webhook abgeloest.",
  },
  cointelegraph: {
    label: "Cointelegraph",
    hint: "Cointelegraph als eigene Source (jenseits der gemischten RSS-Bucket).",
  },
  decrypt: {
    label: "Decrypt News",
    hint: "decrypt.co als eigener Feed.",
  },
  coindesk: {
    label: "CoinDesk",
    hint: "CoinDesk als eigene Source. Trafilatura-Fallback seit 2026-05-08 wieder aktiv.",
  },
  thedefiant: {
    label: "The Defiant",
    hint: "thedefiant.io. DeFi-fokussierte News.",
  },
  blockworks: {
    label: "Blockworks",
    hint: "blockworks.co. Markt- und Regulatorik-fokussierte News.",
  },
  theblock: {
    label: "The Block",
    hint: "theblock.co. Branchen-News und Research.",
  },
  dlnews: {
    label: "DL News",
    hint: "dlnews.com. DeFi- und Onchain-fokussierte News.",
  },
  newsdata: {
    label: "NewsData API",
    hint: "NewsData.io als News-Aggregator-API.",
  },
  x_twitter: {
    label: "X / Twitter",
    hint: "Tweets aus dem X-Listener (Watchlist-Accounts).",
  },
  telegram: {
    label: "Telegram",
    hint: "Telegram-Channels via MTProto-Listener.",
  },
  youtube: {
    label: "YouTube",
    hint: "YouTube-Channel-Updates (Titel + Transcripts wo verfuegbar).",
  },
};

/**
 * Liefert Anzeige-Label und Tooltip-Hint fuer einen Backend-Source-Key.
 * Fallback: roher Key, kein Hint - so bleibt der Forensik-Anker sichtbar.
 */
export function sourceLabel(key: string): SourceLabel {
  return SOURCE_DISPLAY[key] ?? { label: key };
}

/** Backwards-compat alias - beide Namen sind in V-DB5-Code in Verwendung. */
export const formatSourceLabel = sourceLabel;
