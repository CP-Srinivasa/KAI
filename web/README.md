# KAI Control Center — Frontend

Modernes React/TypeScript/Tailwind Dashboard für KAI. Eigenständiges Frontend unter `web/`;
bindet sich später über REST an das FastAPI-Backend (`app/api/routers/dashboard.py`) an.

## Stack

- React 18 + TypeScript
- Vite
- Tailwind CSS (themed via CSS-Variablen)
- Recharts für Visualisierungen
- lucide-react für Icons

## Dev

```bash
cd web
npm install
npm run dev   # http://localhost:5173
```

Build:

```bash
npm run build
npm run preview
```

## Architektur

```
src/
  main.tsx                    # Bootstrap
  App.tsx                     # ThemeProvider + Shell + Page
  index.css                   # Tailwind + CSS-Variablen (Dark/Light)
  theme/ThemeProvider.tsx     # Theme-State (localStorage persisted)
  layout/
    AppShell.tsx              # Sidebar + Topbar + Content
    Sidebar.tsx               # Nav · Env-Selector · Collapsible
    Topbar.tsx                # Search · Timeframe · Theme · Profile
  components/
    ui/Primitives.tsx         # Card, Badge, Button, StatusDot, SectionLabel
    kpi/KpiCard.tsx           # KPI-Karte mit Delta, Sparkline, Tone
    charts/                   # Equity, SignalFrequency, Allocation, Sentiment
    panels/                   # QualityBar, RiskMeter, SystemStatus, AIInsights
    activity/ActivityFeed.tsx # Timeline-Feed
    tables/                   # SignalsTable, TopAssets
  data/mock.ts                # Produktnahe Mockdaten (KAI-Domain)
  lib/utils.ts                # cn, money, relTime, clamp, pct
  pages/Dashboard.tsx         # Main view
```

## Theme-Logik

Farben werden als CSS-Variablen im `:root` bzw. `.dark`-Scope definiert (`src/index.css`).
Tailwind konsumiert sie als `colors.bg.0`, `fg`, `fg-muted`, `accent`, `pos`, `neg`, `warn`,
`info`, `ai` usw. Theme-Umschaltung: `ThemeProvider` toggelt die `dark`-Klasse am
`<html>` und persistiert in `localStorage["kai-theme"]`. Inline-Script in
`index.html` verhindert Theme-Flash beim Initialload.

**Farbsemantik:**

- `pos` — Gewinn / stabil / hit
- `neg` — Verlust / Risiko / miss / Block
- `warn` — Aufmerksamkeit / moderates Risiko
- `info` — Info / neutral-aktiv
- `ai` — Modell / AI-generierter Output / Spezialstatus
- `accent` — Brand / Primäraktion

## Daten

Aktuell vollständig über `src/data/mock.ts`. Werte sind an den realen KAI-Stand
aus dem DECISION_LOG angelehnt (D-137: Forward-Precision 88.89% auf 27 resolved,
Bearish-Gate disabled, Source-Gates decrypt/bitcoin_magazine aktiv, 3 Paper-Fills).

### Live-Anbindung (später)

Jede Panel-Komponente erwartet ihre Daten als Prop oder Import. Für Live-Daten
reicht ein Query-Hook (z. B. TanStack Query) der aus
`/dashboard/api/quality`, `/api/signals`, `/api/alerts` usw. lädt — die
Komponenten-Signaturen bleiben unverändert.

## Design-Prinzipien

1. Datenzentriert statt dekorativ — jedes Panel trägt Aussage.
2. Ruhige, hochwertige Ästhetik — keine Landingpage, keine Krypto-Neon.
3. Farbe nur funktional — Grün/Rot/Amber semantisch, sonst Graustufen + 1 Accent.
4. Tabellen klar, scanbar, mit Status-Badges.
5. Subtile `kai-fade` Animation beim Mount; keine aufdringliche Motion.
6. Monospace (`font-mono`) für alle Zahlen, Beträge, IDs → Tabular-Look.

## Erweiterung

Neue Seiten: weitere `pages/*.tsx` erstellen, in `App.tsx` einhängen (Router
nach Bedarf — z. B. `react-router-dom`). Der `AppShell` bleibt stabil.
