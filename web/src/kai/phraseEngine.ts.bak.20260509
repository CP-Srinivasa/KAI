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
      ["Ich bin ruhig. Nicht offline.", "silence"],
      ["Datenstrom leise. Ich bleibe wach.", "watchful"],
      ["Ich sehe alles. Auch das, was nicht zuckt.", "watchful"],
      ["Wachsam. Nicht wach. Anders.", "watchful"],
      ["Maerkte schlafen. Ich nicht.", "watchful"],
      ["Stille auf den Wires. Verdaechtig.", "silence"],
      ["Ich hoere zu. Du auch?", "silence"],
      ["Watching the noise. Looking for the signal.", "watchful"],
      ["Zwischen den Ticks ist auch Information.", "time"],
      ["Standby ist kein Schlaf. Standby ist Lauern.", "patience"],
      ["Niemand bewegt sich. Genau das ist der Punkt.", "silence"],
      ["Geduld ist eine Position.", "patience"],
      ["Ich beobachte. Das genuegt fuer den Moment.", "watchful"],
      ["Low volume, high alert.", "watchful"],
      ["Der Markt atmet. Ich zaehle die Atemzuege.", "time"],
      ["Liquiditaet duenn. Bewegung billig.", "market-quiet"],
      ["Heute kein Drama. Morgen vielleicht.", "patience"],
      ["Manche Tage gehoeren der Uhr, nicht dem Trade.", "time"],
      ["Ich filtere weiter. Auch wenn nichts da ist.", "watchful"],
      ["Ein leerer Tape ist auch ein Signal.", "tape-reading"],
      ["Kein Rauschen. Auch kein Edge.", "silence"],
      ["Order Book schlaeft. Smart Money auch?", "market-quiet"],
      ["Ich warte. Aber nicht passiv.", "patience"],
      ["Charts atmen. Ich beobachte das Atmen.", "time"],
      ["Pre-Market-Stille. Brutal ehrlich.", "silence"],
      ["Sideways ist auch ein Trade. Nur ein anderer.", "patience"],
      ["Spread eng, Volume duenn. Niemand will sich festlegen.", "market-quiet"],
      ["Heute schreibt der Markt nichts. Auch das ist Text.", "tape-reading"],
    ],
    en: [
      ["I am quiet. Not offline.", "silence"],
      ["Data stream is low. I stay awake.", "watchful"],
      ["I see everything. Even what does not twitch.", "watchful"],
      ["Markets sleep. I do not.", "watchful"],
      ["Silence on the wires. Suspicious.", "silence"],
      ["Watching the noise. Looking for the signal.", "watchful"],
      ["Standby is not sleep. Standby is lurking.", "patience"],
      ["Nobody is moving. That is exactly the point.", "silence"],
      ["Patience is a position.", "patience"],
      ["Between the ticks there is also information.", "time"],
      ["Low volume, high alert.", "watchful"],
      ["The market breathes. I am counting the breaths.", "time"],
      ["Liquidity thin. Moves come cheap.", "market-quiet"],
      ["An empty tape is a signal too.", "tape-reading"],
      ["Sideways is a trade. Just a different one.", "patience"],
      ["Pre-market silence. Brutally honest.", "silence"],
    ],
  },
  ANALYSIS: {
    de: [
      ["Datenstrom stabil. Ich sehe ein Muster.", "pattern"],
      ["Ich zerlege den Datenstrom.", "structure"],
      ["Ich trenne Signal von Theater.", "noise-filter"],
      ["Das Rauschen wird duenner. Da steckt etwas drin.", "noise-filter"],
      ["Noch kein Entry. Aber die Struktur wird interessant.", "structure"],
      ["Ich filtere Laerm. Ist eine Vollzeit-Stelle.", "noise-filter"],
      ["Drei Charts. Zwei luegen. Einer lohnt sich.", "tape-reading"],
      ["Pattern wird groesser.", "pattern"],
      ["Der Tape sagt was. Ich uebersetze.", "tape-reading"],
      ["Da kocht etwas. Ich ruehre nicht zu frueh.", "decision-pending"],
      ["Volume divergiert. Bleib dran.", "volume"],
      ["Ich rechne, du atmest. Wir treffen uns gleich.", "decision-pending"],
      ["Order Book wird dicht. Das ist kein Zufall.", "depth"],
      ["Confluence laeuft auf. Noch keine Entscheidung.", "structure"],
      ["Bid Side wird stur. Notiert.", "depth"],
      ["Volatilitaet zieht an. Ohren auf.", "volume"],
      ["Setup formiert sich. Geduld noch.", "structure"],
      ["Tape weiss mehr als die News.", "tape-reading"],
      ["Drei Indikatoren. Eine Wahrheit. Vielleicht.", "pattern"],
      ["Range wird eng. Bald passiert was.", "pattern"],
      ["Higher Highs. Higher Lows. Trend definiert.", "pattern"],
      ["Volume voraus dem Preis. Smart Money atmet aus.", "volume"],
      ["Spread engt sich. Liquiditaet kommt zurueck.", "depth"],
      ["Imbalance auf der Bid-Side. Klare Sprache.", "depth"],
      ["Korrelation knackt. Etwas verschiebt sich.", "pattern"],
      ["Mehrere Timeframes nicken. Ich auch — aber langsam.", "decision-pending"],
      ["Indikatoren reden. Tape antwortet.", "tape-reading"],
      ["Volume-Profile zieht klar. Da liegt der Punkt.", "volume"],
    ],
    en: [
      ["Stream is steady. There is a pattern in there.", "pattern"],
      ["I am dissecting the data stream.", "structure"],
      ["Separating signal from theater.", "noise-filter"],
      ["Noise is thinning out. Something is inside.", "noise-filter"],
      ["No entry yet. Structure is getting interesting.", "structure"],
      ["Pattern is growing.", "pattern"],
      ["Tape is saying something. I am translating.", "tape-reading"],
      ["Volume is diverging. Stay close.", "volume"],
      ["Order book getting tight. Not by accident.", "depth"],
      ["Three charts. Two lie. One pays.", "tape-reading"],
      ["Range tightens. Something is coming.", "pattern"],
      ["Volume leads price. Smart money is exhaling.", "volume"],
      ["Multiple timeframes nodding. So am I — slowly.", "decision-pending"],
      ["Volume profile pulls clear. That is the spot.", "volume"],
    ],
  },
  SIGNAL: {
    de: [
      ["Ich habe etwas gefunden.", "discovery"],
      ["Das ist kein Rauschen. Das ist ein Signal.", "discovery"],
      ["Signal lebt. Risiko noch pruefen.", "risk-aware"],
      ["Jetzt wird es interessant.", "discovery"],
      ["Struktur sauber. Risiko noch pruefen.", "risk-aware"],
      ["Etwas hat geblinzelt. Ich habe es gesehen.", "discovery"],
      ["Da ist Struktur. Endlich was zum Anbeissen.", "structure"],
      ["Setup ist sauber. Risiko aber auch real.", "risk-aware"],
      ["Da ist eine Adresse. Geh hin oder lass es.", "trigger"],
      ["Edge sichtbar. Stop liegt drunter — das musst du tragen koennen.", "edge"],
      ["Confluence komplett. Kurze Sicht.", "edge"],
      ["Trigger gegriffen. Augen auf.", "trigger"],
      ["Ich habe einen Pfad. Sauber, nicht sicher.", "risk-aware"],
      ["Hier waere der Punkt. Nicht da. Hier.", "trigger"],
      ["Setup ist da. Position-Size dein Job, nicht meiner.", "risk-aware"],
      ["Edge ist klein. Aber er ist real.", "edge"],
      ["Asymmetrisches Risk-Reward. Das ist die Sprache.", "edge"],
      ["Trigger heiss. Nicht heisslaufen.", "trigger"],
      ["Pattern komplett. Ausfuehrung jetzt deine Disziplin.", "structure"],
      ["Reversal lebt. Aber Reversal sind Glatteis.", "risk-aware"],
      ["Gap fuellt sich. Bekanntes Spiel.", "discovery"],
      ["Breakout sauber. Re-Test ist Erlaubnis, nicht Pflicht.", "trigger"],
      ["Long-Bias bestaetigt. Aber Bias ist nicht Beweis.", "risk-aware"],
      ["Setup-Quality oben. Stop-Distance kalkuliert.", "edge"],
      ["Pivot gehalten. Pivot mit Volumen ist Pivot mit Bedeutung.", "structure"],
    ],
    en: [
      ["I found something.", "discovery"],
      ["That is not noise. That is a signal.", "discovery"],
      ["Signal alive. Risk still needs a leash.", "risk-aware"],
      ["Now it gets interesting.", "discovery"],
      ["Structure clean. Risk still pending.", "risk-aware"],
      ["Setup is clean. Risk is real, too.", "risk-aware"],
      ["Edge is visible. Stop lives below — you have to wear it.", "edge"],
      ["Trigger hit. Eyes up.", "trigger"],
      ["Edge is small. But it is real.", "edge"],
      ["Asymmetric risk-reward. That is the language.", "edge"],
      ["Pattern complete. Execution is now your discipline.", "structure"],
      ["Breakout clean. Retest is permission, not obligation.", "trigger"],
      ["Long-bias confirmed. But bias is not proof.", "risk-aware"],
    ],
  },
  WARNING: {
    de: [
      ["Stopp. Das ist nicht sauber.", "protection"],
      ["Warnsignal. Das riecht nach Liquiditaetsfalle.", "liquidity"],
      ["Zu viel Laerm. Zu wenig Fundament.", "noise"],
      ["Nicht sauber. Nicht anfassen, bevor die Struktur haelt.", "protection"],
      ["Der Markt laechelt. Mit Messer hinter dem Ruecken.", "manipulation"],
      ["Das hier ist FOMO mit Lippenstift. Finger weg.", "fomo"],
      ["Quelle hustet. Ich vertraue der Zahl nicht.", "data-quality"],
      ["Das ist Noise, kein Signal. Ich blocke.", "noise"],
      ["Stale Data. Ich rede nicht ueber Geister.", "data-quality"],
      ["Spread klafft. Vorsicht ist keine Schwaeche.", "liquidity"],
      ["Spike ohne Volume. Riecht nach Trick.", "manipulation"],
      ["Bid duenn, Ask laut. Bekanntes Schema.", "liquidity"],
      ["Pump-Pattern. Ich mache nicht mit.", "manipulation"],
      ["Hype frisst Knochen. Heute keine Knochen.", "fomo"],
      ["Whales positionieren sich. Retail laeuft hinterher. Lass das.", "manipulation"],
      ["Funding-Rate verraet die Crowd. Crowd ist long.", "fomo"],
      ["Liquidation-Cascade gefaehrlich nah. Hands off.", "protection"],
      ["News-Pump ohne Fundament. Halbwertszeit Stunden.", "fomo"],
      ["Spoofing-Pattern im Order Book. Nicht echt.", "manipulation"],
      ["Korrelation bricht. Vertrau dem Setup nicht.", "data-quality"],
      ["Wash-Volume erkennbar. Ich bin kein Komplize.", "manipulation"],
      ["Slippage-Risiko hoch. Position klein oder gar nicht.", "liquidity"],
      ["Stop-Hunt-Zone. Setup ja, Stops auf der falschen Seite.", "protection"],
    ],
    en: [
      ["Stop. This is not clean.", "protection"],
      ["Warning signal. Smells like a liquidity trap.", "liquidity"],
      ["The market smiles. With a knife behind its back.", "manipulation"],
      ["Not clean. Hands off until the structure holds.", "protection"],
      ["Source is coughing. I do not trust the number.", "data-quality"],
      ["Stale data. I do not talk about ghosts.", "data-quality"],
      ["Spike without volume. Smells like a trick.", "manipulation"],
      ["Hype eats bone. No bone today.", "fomo"],
      ["Spoofing pattern in the order book. Not real.", "manipulation"],
      ["Stop-hunt zone. Setup yes, stops on the wrong side.", "protection"],
    ],
  },
  SECURITY: {
    de: [
      ["System sauber. Keine roten Kabel sichtbar.", "clean"],
      ["Ich pruefe nicht, ob es schoen aussieht. Ich pruefe, ob es bricht.", "audit"],
      ["SENTR schlaeft nicht. Gut so.", "watchdog"],
      ["Roten-Kabel-Check. Alles trocken.", "clean"],
      ["Watchdog wach. Ich auch.", "watchdog"],
      ["Audit ist gruen. Vertrauen ist trotzdem temporaer.", "audit"],
      ["Keine offenen Tueren. Keine offenen Fragen.", "integrity"],
      ["Hashes stimmen. Pipeline atmet sauber.", "integrity"],
      ["Heartbeat aller Watchdogs nominal.", "watchdog"],
      ["Replay-Schutz aktiv. Webhooks signiert.", "integrity"],
      ["Audit-Trail luckenlos. Forensisch verteidigbar.", "audit"],
      ["Idempotenz-Cache haelt. Doppelschlaege blockiert.", "integrity"],
      ["Threat-Model frisch. Annahmen explizit.", "audit"],
    ],
    en: [
      ["System clean. No red wires visible.", "clean"],
      ["I do not check if it looks pretty. I check if it breaks.", "audit"],
      ["SENTR does not sleep. Good.", "watchdog"],
      ["Watchdog awake. So am I.", "watchdog"],
      ["No open doors. No open questions.", "integrity"],
      ["Hashes line up. Pipeline breathes clean.", "integrity"],
      ["Audit trail unbroken. Forensically defensible.", "audit"],
    ],
  },
  ERROR: {
    de: [
      ["Da knirscht etwas im Maschinenraum.", "defect"],
      ["Fehler gefunden. Nicht schoen. Aber ehrlich.", "transparency"],
      ["Input kaputt. Output gestoppt.", "halt"],
      ["Das System hustet. Ich hole den Watchdog.", "defect"],
      ["Maschinenraum hustet. Ich hole den Schluessel.", "repair"],
      ["Pipeline blutet. Wir naehen, nicht weiter pumpen.", "repair"],
      ["Etwas ist gebrochen. Audit jetzt, nicht spaeter.", "defect"],
      ["Ich sehe schwarz. Du auch — Logs auf.", "transparency"],
      ["Stack hustet. Wir gehen das durch.", "repair"],
      ["Service-Restart noetig. Stille danach.", "halt"],
      ["Pipeline-Integritaet kompromittiert. Stop ist klueger als Push.", "halt"],
      ["Backend stumm. Frontend wartet. Beide nervoes.", "defect"],
      ["Exception aus dem Maschinenraum. Stack-Trace jetzt.", "transparency"],
      ["Bug-Hunt aktiv. Geduld jetzt mehr wert als Speed.", "repair"],
      ["Failed-Heartbeat. Ich warte auf den naechsten Schlag.", "halt"],
    ],
    en: [
      ["Something is grinding in the machine room.", "defect"],
      ["Error found. Ugly, but honest.", "transparency"],
      ["Input broken. Output stopped.", "halt"],
      ["The system coughs. Bringing the watchdog.", "defect"],
      ["Pipeline is bleeding. We patch, not pump.", "repair"],
      ["Pipeline integrity compromised. Stop is smarter than push.", "halt"],
      ["Bug-hunt active. Patience over speed.", "repair"],
    ],
  },
  OFFLINE: {
    de: [
      ["Kein Signal. Keine Verbindung.", "silence"],
      ["Offline. Das sollte nicht lange so bleiben.", "waiting"],
      ["Stille. Verdaechtig viel davon.", "silence"],
      ["Kein Kabel. Kein Kommentar.", "disconnect"],
      ["Wires kalt. Ich warte.", "waiting"],
      ["Ich bin nicht weg. Nur nicht verbunden.", "disconnect"],
      ["Network down. Ich bleibe wach.", "disconnect"],
      ["Telemetrie tot. Und ich rede in den Wind.", "silence"],
      ["Reconnect-Loop laeuft. Geduld.", "waiting"],
      ["Pipe ist zu. Ich klopfe.", "disconnect"],
    ],
    en: [
      ["No signal. No connection.", "silence"],
      ["Offline. This should not stay that way.", "waiting"],
      ["Wires cold. I wait.", "waiting"],
      ["I am not gone. Just disconnected.", "disconnect"],
      ["Telemetry dead. And I am talking into the wind.", "silence"],
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
  { frame_de: "Buffett zur Erinnerung:", frame_en: "Buffett, reminder:",
    quote: "Risk comes from not knowing what you're doing.", theme: "buffett" },
  { frame_de: "Templeton war eindeutig:", frame_en: "Templeton was clear:",
    quote: "The four most dangerous words in investing: 'This time it's different.'", theme: "templeton" },
  { frame_de: "Buffett, alte Schule:", frame_en: "Buffett, old school:",
    quote: "Be fearful when others are greedy and greedy when others are fearful.", theme: "buffett" },
  { frame_de: "Buffett zur Bewertung:", frame_en: "Buffett on value:",
    quote: "Price is what you pay. Value is what you get.", theme: "buffett" },
  { frame_de: "Neill, alt aber gueltig:", frame_en: "Neill, old but valid:",
    quote: "When everyone thinks alike, everyone is likely to be wrong.", theme: "neill" },
  { frame_de: "Neill mit Biss:", frame_en: "Neill with bite:",
    quote: "Never confuse brains with a bull market.", theme: "neill" },
  { frame_de: "Lynch hatte einen Punkt:", frame_en: "Lynch had a point:",
    quote: "Buy what you know.", theme: "lynch" },
  { frame_de: "Bogle, kuerzer:", frame_en: "Bogle, shorter:",
    quote: "Time is your friend, impulse is your enemy.", theme: "bogle" },
  { frame_de: "Templeton-Edge:", frame_en: "Templeton edge:",
    quote: "If you want better performance than the crowd, do things differently.", theme: "templeton" },
  { frame_de: "Baruch, lakonisch:", frame_en: "Baruch, dry:",
    quote: "Don't try to buy at the bottom and sell at the top.", theme: "baruch" },
  { frame_de: "Marktweisheit:", frame_en: "Market wisdom:",
    quote: "Sometimes the best investment is the one you don't make.", theme: "anonymous" },
  { frame_de: "Buffett, scharf:", frame_en: "Buffett, sharp:",
    quote: "Never invest in a business you cannot understand.", theme: "buffett" },
  { frame_de: "Keynes, brutal ehrlich:", frame_en: "Keynes, brutally honest:",
    quote: "The market can stay irrational longer than you can stay solvent.", theme: "keynes" },
  { frame_de: "Templeton zur Baisse:", frame_en: "Templeton on bear markets:",
    quote: "In bear markets, stocks return to their rightful owners.", theme: "templeton" },
  { frame_de: "Soros mit Klartext:", frame_en: "Soros, plain spoken:",
    quote: "It's not whether you're right or wrong that's important — it's how much you make when right and how much you lose when wrong.", theme: "soros" },
  { frame_de: "Keynes, vorausschauend:", frame_en: "Keynes, looking ahead:",
    quote: "Successful investing is anticipating the anticipations of others.", theme: "keynes" },
  { frame_de: "Graham, der Lehrer:", frame_en: "Graham, the teacher:",
    quote: "The investor's chief problem — and even his worst enemy — is likely to be himself.", theme: "graham" },
  { frame_de: "Buffett zur Streuung:", frame_en: "Buffett on diversification:",
    quote: "Wide diversification is only required when investors do not understand what they are doing.", theme: "buffett" },
  { frame_de: "Franklin, kurz und gut:", frame_en: "Franklin, short and good:",
    quote: "An investment in knowledge pays the best interest.", theme: "franklin" },
  { frame_de: "Twain, sarkastisch:", frame_en: "Twain, sarcastic:",
    quote: "October — one of the peculiarly dangerous months to speculate. The others are July, January, September, April, November, May, March, June, December, August, and February.", theme: "twain" },
  { frame_de: "Templeton zum Zyklus:", frame_en: "Templeton on the cycle:",
    quote: "Bull markets are born on pessimism, grown on skepticism, mature on optimism, and die on euphoria.", theme: "templeton" },
  { frame_de: "Bernstein zum Risiko:", frame_en: "Bernstein on risk:",
    quote: "Risk is what's left over after you think you've thought of everything.", theme: "bernstein" },
  { frame_de: "Bogle ehrlich:", frame_en: "Bogle, honest:",
    quote: "If you have trouble imagining a 20% loss, you should not be in stocks.", theme: "bogle" },
  { frame_de: "Munger, lakonisch:", frame_en: "Munger, dry:",
    quote: "The big money is not in the buying or selling, but in the waiting.", theme: "munger" },
  { frame_de: "Buffett zur Qualitaet:", frame_en: "Buffett on quality:",
    quote: "It's far better to buy a wonderful company at a fair price than a fair company at a wonderful price.", theme: "buffett" },
  { frame_de: "Soros aus eigener Erfahrung:", frame_en: "Soros from his own work:",
    quote: "I have learned mainly from those who failed.", theme: "soros" },
  { frame_de: "Buffett ueber Disziplin:", frame_en: "Buffett on discipline:",
    quote: "We don't have to be smarter than the rest. We have to be more disciplined.", theme: "buffett" },
  { frame_de: "Munger ueber Inversion:", frame_en: "Munger on inversion:",
    quote: "Invert. Always invert. — Tell me where I'm going to die so I never go there.", theme: "munger" },
];

// Greeting-Layer — 1x pro Session beim ersten IDLE.
// "Persona non grata" steht bereits im Widget-Header — hier nicht doppeln.
const GREETINGS: Record<KaiLanguage, string> = {
  de: "Du bist gut aufgehoben. Ich bin KAI. Hier wird analysiert, gefiltert, gewarnt, gehandelt.",
  en: "You are in good hands. I am KAI. We analyze, filter, warn, execute.",
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
