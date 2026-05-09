// KAI Persona — Phrase Engine
// Spec source: docs/kai_persona/technical_ui_pack_v3_2.md §6
// Phrase source: config/kai_persona.yaml § state_machine.states + docs/kai_persona/prompt_bibel_v1.md §10, §23
//
// 2026-05-08 Operator-Folge: Pool 2x erweitert, Themen-Tags pro Phrase,
// Anti-Theme-Repeat (n=2) zusaetzlich zu Anti-Text-Repeat (n=5). Quote-Layer
// auf 35% angehoben. Quote-Pool 12 -> 28. Mehr Variation, weniger thematische
// Cluster. Backend-Reuse 15% bleibt.

import { KAI_FORBIDDEN_PHRASES_DE, KAI_FORBIDDEN_PHRASES_EN } from "./constants";
import type { KaiLanguage, KaiState } from "./types";

// Phrase-Tuple: [text, theme]. Theme dient als Anti-Cluster-Filter beim Cycle —
// keine zwei thematisch verwandten Phrases im n=2-Fenster.
type Phrase = readonly [text: string, theme: string];

const PHRASES: Record<KaiState, Record<KaiLanguage, Phrase[]>> = {
  IDLE: {
    de: [
      ["Ruhig. Nicht offline.", "silence"],
      ["Datenstrom leise. Ich wach.", "watchful"],
      ["Ich sehe, was nicht zuckt.", "watchful"],
      ["Wachsam. Nicht wach.", "watchful"],
      ["Maerkte schlafen. Ich nicht.", "watchful"],
      ["Stille auf den Wires.", "silence"],
      ["Ich hoere zu. Du auch?", "silence"],
      ["Watching the noise.", "watchful"],
      ["Zwischen Ticks ist Information.", "time"],
      ["Standby ist Lauern.", "patience"],
      ["Niemand bewegt sich. Verdaechtig.", "silence"],
      ["Geduld ist eine Position.", "patience"],
      ["Ich beobachte. Reicht.", "watchful"],
      ["Low volume, high alert.", "watchful"],
      ["Markt atmet. Ich zaehle.", "time"],
      ["Liquiditaet duenn.", "market-quiet"],
      ["Heute kein Drama.", "patience"],
      ["Manche Tage gehoeren der Uhr.", "time"],
      ["Ich filtere. Auch ohne Input.", "watchful"],
      ["Leerer Tape ist auch Signal.", "tape-reading"],
      ["Kein Rauschen. Kein Edge.", "silence"],
      ["Order Book schlaeft.", "market-quiet"],
      ["Ich warte. Nicht passiv.", "patience"],
      ["Charts atmen.", "time"],
      ["Pre-Market-Stille.", "silence"],
      ["Sideways ist auch Trade.", "patience"],
      ["Spread eng, Volume duenn.", "market-quiet"],
      ["Heute schreibt Markt nichts.", "tape-reading"],
    ],
    en: [
      ["Quiet. Not offline.", "silence"],
      ["Stream low. I stay awake.", "watchful"],
      ["I see what does not twitch.", "watchful"],
      ["Markets sleep. I do not.", "watchful"],
      ["Silence on the wires.", "silence"],
      ["Watching the noise.", "watchful"],
      ["Standby is lurking.", "patience"],
      ["Nobody is moving.", "silence"],
      ["Patience is a position.", "patience"],
      ["Between ticks: information.", "time"],
      ["Low volume, high alert.", "watchful"],
      ["Market breathes. I count.", "time"],
      ["Liquidity thin.", "market-quiet"],
      ["Empty tape is a signal.", "tape-reading"],
      ["Sideways is a trade.", "patience"],
      ["Pre-market silence.", "silence"],
    ],
  },
  ANALYSIS: {
    de: [
      ["Stream stabil. Muster sichtbar.", "pattern"],
      ["Ich zerlege den Datenstrom.", "structure"],
      ["Trenne Signal von Theater.", "noise-filter"],
      ["Rauschen wird duenner.", "noise-filter"],
      ["Struktur wird interessant.", "structure"],
      ["Ich filtere Laerm. Vollzeit.", "noise-filter"],
      ["Drei Charts. Einer lohnt.", "tape-reading"],
      ["Pattern wird groesser.", "pattern"],
      ["Tape spricht. Ich uebersetze.", "tape-reading"],
      ["Da kocht etwas.", "decision-pending"],
      ["Volume divergiert. Bleib dran.", "volume"],
      ["Ich rechne. Wir treffen uns.", "decision-pending"],
      ["Order Book wird dicht.", "depth"],
      ["Confluence laeuft auf.", "structure"],
      ["Bid Side wird stur.", "depth"],
      ["Volatilitaet zieht an.", "volume"],
      ["Setup formiert sich.", "structure"],
      ["Tape weiss mehr als News.", "tape-reading"],
      ["Drei Indikatoren, eine Wahrheit.", "pattern"],
      ["Range wird eng.", "pattern"],
      ["Higher Highs, Higher Lows.", "pattern"],
      ["Volume voraus dem Preis.", "volume"],
      ["Spread engt sich.", "depth"],
      ["Imbalance auf der Bid-Side.", "depth"],
      ["Korrelation knackt.", "pattern"],
      ["Mehrere Timeframes nicken.", "decision-pending"],
      ["Indikatoren reden, Tape antwortet.", "tape-reading"],
      ["Volume-Profile zieht klar.", "volume"],
    ],
    en: [
      ["Stream steady. Pattern in there.", "pattern"],
      ["Dissecting the stream.", "structure"],
      ["Signal vs theater.", "noise-filter"],
      ["Noise thinning.", "noise-filter"],
      ["No entry. Structure interesting.", "structure"],
      ["Pattern growing.", "pattern"],
      ["Tape talks. I translate.", "tape-reading"],
      ["Volume diverging. Stay close.", "volume"],
      ["Order book tight. Not random.", "depth"],
      ["Three charts. One pays.", "tape-reading"],
      ["Range tightens.", "pattern"],
      ["Volume leads price.", "volume"],
      ["Timeframes nodding. Slowly.", "decision-pending"],
      ["Volume profile pulls clear.", "volume"],
    ],
  },
  SIGNAL: {
    de: [
      ["Ich habe etwas gefunden.", "discovery"],
      ["Kein Rauschen. Signal.", "discovery"],
      ["Signal lebt. Risiko pruefen.", "risk-aware"],
      ["Jetzt wird es interessant.", "discovery"],
      ["Struktur sauber.", "risk-aware"],
      ["Etwas hat geblinzelt.", "discovery"],
      ["Da ist Struktur.", "structure"],
      ["Setup sauber. Risiko real.", "risk-aware"],
      ["Geh hin oder lass es.", "trigger"],
      ["Edge sichtbar. Stop drunter.", "edge"],
      ["Confluence komplett.", "edge"],
      ["Trigger gegriffen.", "trigger"],
      ["Pfad sauber, nicht sicher.", "risk-aware"],
      ["Hier. Nicht da. Hier.", "trigger"],
      ["Position-Size dein Job.", "risk-aware"],
      ["Edge klein. Aber real.", "edge"],
      ["Asymmetrisches RR. Klar.", "edge"],
      ["Trigger heiss. Du nicht.", "trigger"],
      ["Pattern fertig. Disziplin jetzt.", "structure"],
      ["Reversal lebt. Glatteis bleibt.", "risk-aware"],
      ["Gap fuellt sich.", "discovery"],
      ["Breakout sauber. Re-Test optional.", "trigger"],
      ["Long-Bias. Bias kein Beweis.", "risk-aware"],
      ["Setup-Quality oben.", "edge"],
      ["Pivot mit Volumen. Bedeutung.", "structure"],
    ],
    en: [
      ["Found something.", "discovery"],
      ["Not noise. Signal.", "discovery"],
      ["Signal alive. Risk pending.", "risk-aware"],
      ["Now it gets interesting.", "discovery"],
      ["Structure clean.", "risk-aware"],
      ["Setup clean. Risk real.", "risk-aware"],
      ["Edge visible. Stop below.", "edge"],
      ["Trigger hit. Eyes up.", "trigger"],
      ["Edge small. But real.", "edge"],
      ["Asymmetric RR. Clear.", "edge"],
      ["Pattern done. Discipline now.", "structure"],
      ["Breakout clean. Retest optional.", "trigger"],
      ["Bias confirmed. Bias no proof.", "risk-aware"],
    ],
  },
  WARNING: {
    de: [
      ["Stopp. Nicht sauber.", "protection"],
      ["Riecht nach Liquiditaetsfalle.", "liquidity"],
      ["Zu viel Laerm.", "noise"],
      ["Hands off. Struktur haelt nicht.", "protection"],
      ["Markt laechelt. Messer hinten.", "manipulation"],
      ["FOMO mit Lippenstift.", "fomo"],
      ["Quelle hustet.", "data-quality"],
      ["Noise, kein Signal. Block.", "noise"],
      ["Stale Data. Keine Geister.", "data-quality"],
      ["Spread klafft.", "liquidity"],
      ["Spike ohne Volume. Trick.", "manipulation"],
      ["Bid duenn, Ask laut.", "liquidity"],
      ["Pump-Pattern. Nicht ich.", "manipulation"],
      ["Hype frisst Knochen.", "fomo"],
      ["Whales positionieren. Lass das.", "manipulation"],
      ["Funding-Rate verraet die Crowd.", "fomo"],
      ["Liquidation-Cascade nah.", "protection"],
      ["News-Pump ohne Fundament.", "fomo"],
      ["Spoofing im Order Book.", "manipulation"],
      ["Korrelation bricht.", "data-quality"],
      ["Wash-Volume. Kein Komplize.", "manipulation"],
      ["Slippage hoch. Klein oder nichts.", "liquidity"],
      ["Stop-Hunt-Zone.", "protection"],
    ],
    en: [
      ["Stop. Not clean.", "protection"],
      ["Smells like liquidity trap.", "liquidity"],
      ["Market smiles. Knife behind.", "manipulation"],
      ["Hands off. Structure shaky.", "protection"],
      ["Source coughing.", "data-quality"],
      ["Stale data. No ghosts.", "data-quality"],
      ["Spike no volume. Trick.", "manipulation"],
      ["Hype eats bone.", "fomo"],
      ["Spoofing in order book.", "manipulation"],
      ["Stop-hunt zone.", "protection"],
    ],
  },
  SECURITY: {
    de: [
      ["System sauber. Keine roten Kabel.", "clean"],
      ["Ich pruefe Bruch, nicht Schoenheit.", "audit"],
      ["SENTR schlaeft nicht.", "watchdog"],
      ["Roten-Kabel-Check. Trocken.", "clean"],
      ["Watchdog wach. Ich auch.", "watchdog"],
      ["Audit gruen. Vertrauen temporaer.", "audit"],
      ["Keine offenen Tueren.", "integrity"],
      ["Hashes stimmen.", "integrity"],
      ["Heartbeat nominal.", "watchdog"],
      ["Replay-Schutz aktiv.", "integrity"],
      ["Audit-Trail luckenlos.", "audit"],
      ["Idempotenz haelt.", "integrity"],
      ["Threat-Model frisch.", "audit"],
    ],
    en: [
      ["System clean. No red wires.", "clean"],
      ["I check breaks, not looks.", "audit"],
      ["SENTR does not sleep.", "watchdog"],
      ["Watchdog awake. So am I.", "watchdog"],
      ["No open doors.", "integrity"],
      ["Hashes line up.", "integrity"],
      ["Audit trail unbroken.", "audit"],
    ],
  },
  ERROR: {
    de: [
      ["Knirscht im Maschinenraum.", "defect"],
      ["Fehler gefunden. Ehrlich.", "transparency"],
      ["Input kaputt. Output gestoppt.", "halt"],
      ["System hustet. Watchdog kommt.", "defect"],
      ["Maschinenraum hustet.", "repair"],
      ["Pipeline blutet. Wir naehen.", "repair"],
      ["Etwas gebrochen. Audit jetzt.", "defect"],
      ["Logs auf. Wir sehen schwarz.", "transparency"],
      ["Stack hustet.", "repair"],
      ["Service-Restart noetig.", "halt"],
      ["Pipeline kompromittiert. Stop.", "halt"],
      ["Backend stumm. Frontend wartet.", "defect"],
      ["Exception. Stack-Trace.", "transparency"],
      ["Bug-Hunt aktiv.", "repair"],
      ["Failed-Heartbeat.", "halt"],
    ],
    en: [
      ["Grinding in the machine room.", "defect"],
      ["Error found. Honest.", "transparency"],
      ["Input broken. Output stopped.", "halt"],
      ["System coughs. Watchdog up.", "defect"],
      ["Pipeline bleeding. We patch.", "repair"],
      ["Pipeline compromised. Stop.", "halt"],
      ["Bug-hunt active.", "repair"],
    ],
  },
  OFFLINE: {
    de: [
      ["Kein Signal. Keine Verbindung.", "silence"],
      ["Offline. Sollte kurz sein.", "waiting"],
      ["Stille. Verdaechtig viel.", "silence"],
      ["Kein Kabel. Kein Kommentar.", "disconnect"],
      ["Wires kalt. Ich warte.", "waiting"],
      ["Nicht weg. Nur unverbunden.", "disconnect"],
      ["Network down. Wach bleibe ich.", "disconnect"],
      ["Telemetrie tot.", "silence"],
      ["Reconnect-Loop laeuft.", "waiting"],
      ["Pipe zu. Ich klopfe.", "disconnect"],
    ],
    en: [
      ["No signal. No connection.", "silence"],
      ["Offline. Should be brief.", "waiting"],
      ["Wires cold. I wait.", "waiting"],
      ["Not gone. Disconnected.", "disconnect"],
      ["Telemetry dead.", "silence"],
    ],
  },
};

// Quote-Layer mit Persona-Frame und Author-Tag (theme).
// 28 Eintraege, jeder Author bekommt einen Theme-Tag — verhindert Buffett-after-Buffett.
interface QuoteEntry {
  frame_de: string;
  frame_en: string;
  quote: string;
  theme: string; // = author tag for anti-cluster
}

const QUOTES: QuoteEntry[] = [
  { frame_de: "Buffett:", frame_en: "Buffett:",
    quote: "Risk comes from not knowing what you do.", theme: "buffett" },
  { frame_de: "Templeton:", frame_en: "Templeton:",
    quote: "'This time it is different.' — gefaehrlich.", theme: "templeton" },
  { frame_de: "Buffett:", frame_en: "Buffett:",
    quote: "Greedy when others fear. Fearful when greedy.", theme: "buffett" },
  { frame_de: "Buffett:", frame_en: "Buffett:",
    quote: "Price is what you pay. Value is what you get.", theme: "buffett" },
  { frame_de: "Neill:", frame_en: "Neill:",
    quote: "When all think alike, all are wrong.", theme: "neill" },
  { frame_de: "Neill:", frame_en: "Neill:",
    quote: "Never confuse brains with a bull market.", theme: "neill" },
  { frame_de: "Lynch:", frame_en: "Lynch:",
    quote: "Buy what you know.", theme: "lynch" },
  { frame_de: "Bogle:", frame_en: "Bogle:",
    quote: "Time is friend. Impulse is enemy.", theme: "bogle" },
  { frame_de: "Templeton:", frame_en: "Templeton:",
    quote: "Better than the crowd? Do things differently.", theme: "templeton" },
  { frame_de: "Baruch:", frame_en: "Baruch:",
    quote: "Don't try to buy bottoms or sell tops.", theme: "baruch" },
  { frame_de: "Marktweisheit:", frame_en: "Market wisdom:",
    quote: "The best trade is sometimes none.", theme: "anonymous" },
  { frame_de: "Buffett:", frame_en: "Buffett:",
    quote: "Never invest in what you don't understand.", theme: "buffett" },
  { frame_de: "Keynes:", frame_en: "Keynes:",
    quote: "Markets stay irrational longer than you stay solvent.", theme: "keynes" },
  { frame_de: "Templeton:", frame_en: "Templeton:",
    quote: "Bear markets return stocks to rightful owners.", theme: "templeton" },
  { frame_de: "Soros:", frame_en: "Soros:",
    quote: "Size when right beats being right.", theme: "soros" },
  { frame_de: "Keynes:", frame_en: "Keynes:",
    quote: "Investing means anticipating others' anticipations.", theme: "keynes" },
  { frame_de: "Graham:", frame_en: "Graham:",
    quote: "The investor's worst enemy is himself.", theme: "graham" },
  { frame_de: "Buffett:", frame_en: "Buffett:",
    quote: "Wide diversification = ignorance protection.", theme: "buffett" },
  { frame_de: "Franklin:", frame_en: "Franklin:",
    quote: "Knowledge pays the best interest.", theme: "franklin" },
  { frame_de: "Twain:", frame_en: "Twain:",
    quote: "October is dangerous. So are the other 11.", theme: "twain" },
  { frame_de: "Templeton:", frame_en: "Templeton:",
    quote: "Bulls die on euphoria.", theme: "templeton" },
  { frame_de: "Bernstein:", frame_en: "Bernstein:",
    quote: "Risk is what's left after you thought of all.", theme: "bernstein" },
  { frame_de: "Bogle:", frame_en: "Bogle:",
    quote: "Can't stomach -20%? Don't own stocks.", theme: "bogle" },
  { frame_de: "Munger:", frame_en: "Munger:",
    quote: "The big money is in the waiting.", theme: "munger" },
  { frame_de: "Buffett:", frame_en: "Buffett:",
    quote: "Wonderful company at fair price > opposite.", theme: "buffett" },
  { frame_de: "Soros:", frame_en: "Soros:",
    quote: "I learned from those who failed.", theme: "soros" },
  { frame_de: "Buffett:", frame_en: "Buffett:",
    quote: "Not smarter. More disciplined.", theme: "buffett" },
  { frame_de: "Munger:", frame_en: "Munger:",
    quote: "Invert. Find where you die. Don't go.", theme: "munger" },
];

// Greeting-Layer — 1x pro Session beim ersten IDLE.
// "Persona non grata" steht bereits im Widget-Header — hier nicht doppeln.
const GREETINGS: Record<KaiLanguage, string> = {
  de: "Ich bin KAI. Analyse, Filter, Warnung, Trade.",
  en: "I am KAI. Analyze, filter, warn, execute.",
};

export function getGreeting(language: KaiLanguage = "de"): string {
  return GREETINGS[language];
}

// Extra modes from Prompt-Bibel V1 §10. Triggered explicitly, not state-dispatched.
export type KaiPhraseMode = "hype" | "mockery" | "bad_data";

const EXTRA_MODE_PHRASES: Record<KaiPhraseMode, Record<KaiLanguage, string[]>> = {
  hype: {
    de: [
      "Social Buzz explodiert. Fundament noch duenn.",
      "Viel Laerm. Wenig Knochen.",
      "FOMO erkannt. Ich vertraue dem Ding noch nicht.",
      "Hype-Welle ist da. Knochen aber nicht.",
    ],
    en: [
      "Social buzz exploding. Foundation still thin.",
      "Lots of noise. Not much bone.",
      "FOMO detected. I do not trust this yet.",
    ],
  },
  mockery: {
    de: [
      "Mutig. Nicht klug. Aber mutig.",
      "Das ist kein Signal. Das ist Laerm mit Make-up.",
      "Hype-Train. Ohne Schienen.",
    ],
    en: [
      "Bold. Not smart. But bold.",
      "That is not a signal. That is noise with makeup.",
    ],
  },
  bad_data: {
    de: [
      "Die Daten sind matschig. Ich traue dem Signal noch nicht.",
      "Input unsauber. Output mit Vorsicht geniessen.",
      "Garbage in, Glitch out.",
      "Quelle hustet. Ich rede nicht ueber Geister.",
    ],
    en: [
      "The data is mushy. I do not trust the signal yet.",
      "Input dirty. Handle output with care.",
      "Garbage in, glitch out.",
    ],
  },
};

export function getKaiPhrase(
  state: KaiState,
  language: KaiLanguage = "de",
  seed?: number,
): string {
  const phrases = PHRASES[state]?.[language] ?? PHRASES.ERROR[language];
  if (!phrases.length) {
    return language === "de" ? "Kein Kommentar verfuegbar." : "No comment available.";
  }
  const index =
    typeof seed === "number"
      ? Math.abs(seed) % phrases.length
      : Math.floor(Math.random() * phrases.length);
  return phrases[index][0];
}

export function getKaiExtraModePhrase(
  mode: KaiPhraseMode,
  language: KaiLanguage = "de",
  seed?: number,
): string {
  const phrases = EXTRA_MODE_PHRASES[mode]?.[language];
  if (!phrases || !phrases.length) {
    return getKaiPhrase("ANALYSIS", language, seed);
  }
  const index =
    typeof seed === "number"
      ? Math.abs(seed) % phrases.length
      : Math.floor(Math.random() * phrases.length);
  return phrases[index];
}

// Cycle-Engine mit zwei History-Tracks: Anti-Text-Repeat (n=5) UND
// Anti-Theme-Repeat (n=2). Frequenz: 50% State / 35% Quote / 15% Backend.
export interface CycleInput {
  state: KaiState;
  language: KaiLanguage;
  textHistory: string[];   // letzte n=5 gerenderte Phrases
  themeHistory: string[];  // letzte n=2 Themes
  backendComment?: string | null;
}

export interface CycleOutput {
  text: string;
  theme: string;
}

const PROBA_STATE = 0.50;
const PROBA_QUOTE = 0.35;
// Rest 0.15 -> Backend-Reuse (oder Fallback State)

export function cycleKaiPhrase(input: CycleInput): CycleOutput {
  const { state, language, textHistory, themeHistory, backendComment } = input;
  const r = Math.random();

  if (r < PROBA_STATE) {
    const pool = PHRASES[state]?.[language] ?? [];
    const picked = pickFreshTagged(pool, textHistory, themeHistory);
    if (picked) return { text: picked[0], theme: picked[1] };
    // Fallback wenn Pool leer
    return { text: getKaiPhrase(state, language), theme: state.toLowerCase() };
  }

  if (r < PROBA_STATE + PROBA_QUOTE) {
    const picked = pickFreshQuote(language, textHistory, themeHistory);
    if (picked) return picked;
    // Fallback
    const pool = PHRASES[state]?.[language] ?? [];
    const fb = pickFreshTagged(pool, textHistory, themeHistory);
    if (fb) return { text: fb[0], theme: fb[1] };
    return { text: getKaiPhrase(state, language), theme: state.toLowerCase() };
  }

  // 15% Backend-Reuse — wenn vorhanden und nicht in History.
  if (backendComment && backendComment.trim().length > 0 && !textHistory.includes(backendComment)) {
    return { text: backendComment, theme: "backend" };
  }
  // Fallback auf State-Pool.
  const pool = PHRASES[state]?.[language] ?? [];
  const fb = pickFreshTagged(pool, textHistory, themeHistory);
  if (fb) return { text: fb[0], theme: fb[1] };
  return { text: getKaiPhrase(state, language), theme: state.toLowerCase() };
}

// Pick aus Phrase-Pool mit Anti-Text-Repeat + Anti-Theme-Repeat.
// Strict: weder Text noch Theme im Recent-Fenster.
// Relax 1: nur Anti-Text (Theme-Filter aufweichen).
// Relax 2: kompletter Pool (sollte selten greifen).
function pickFreshTagged(
  pool: Phrase[],
  textHistory: string[],
  themeHistory: string[],
): Phrase | null {
  if (!pool.length) return null;
  const recentText = new Set(textHistory);
  const recentTheme = new Set(themeHistory);
  let candidates = pool.filter(([t, th]) => !recentText.has(t) && !recentTheme.has(th));
  if (!candidates.length) candidates = pool.filter(([t]) => !recentText.has(t));
  if (!candidates.length) candidates = pool.slice();
  return candidates[Math.floor(Math.random() * candidates.length)];
}

// Pick aus Quote-Pool mit gleicher Anti-Repeat-Logik.
// Ein Quote-Render hat Form "Frame: Zitat" — der Anti-Text-Filter laeuft auf
// dem kombinierten String, der Anti-Theme-Filter auf dem Author-Tag.
function pickFreshQuote(
  language: KaiLanguage,
  textHistory: string[],
  themeHistory: string[],
): CycleOutput | null {
  if (!QUOTES.length) return null;
  const recentText = new Set(textHistory);
  const recentTheme = new Set(themeHistory);
  const formatted = (q: QuoteEntry) =>
    `${language === "de" ? q.frame_de : q.frame_en} ${q.quote}`;
  let candidates = QUOTES.filter(
    (q) => !recentText.has(formatted(q)) && !recentTheme.has(q.theme),
  );
  if (!candidates.length) candidates = QUOTES.filter((q) => !recentText.has(formatted(q)));
  if (!candidates.length) candidates = QUOTES.slice();
  const q = candidates[Math.floor(Math.random() * candidates.length)];
  return { text: formatted(q), theme: q.theme };
}

// Quote mit Persona-Frame als ein zusammenhaengender String — Single-Shot fuer
// externe Caller (z.B. Telegram). Cycle-Engine nutzt pickFreshQuote intern.
export function getKaiQuote(language: KaiLanguage = "de", seed?: number): string {
  const idx =
    typeof seed === "number"
      ? Math.abs(seed) % QUOTES.length
      : Math.floor(Math.random() * QUOTES.length);
  const q = QUOTES[idx];
  const frame = language === "de" ? q.frame_de : q.frame_en;
  return `${frame} ${q.quote}`;
}

// Safety guard — every emitted phrase must pass this check before reaching UI/Telegram.
export function isPhraseSafe(text: string, language: KaiLanguage): boolean {
  const lower = text.toLowerCase();
  const forbidden = language === "de" ? KAI_FORBIDDEN_PHRASES_DE : KAI_FORBIDDEN_PHRASES_EN;
  return !forbidden.some((needle) => lower.includes(needle.toLowerCase()));
}
