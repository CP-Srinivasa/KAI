import { useState } from "react";
import { Settings as SettingsIcon } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { useTheme } from "@/theme/ThemeProvider";
import { useAppState, type TradingMode } from "@/state/AppState";
import { Badge, Card, CardHeader } from "@/components/ui/Primitives";
import { Field, Input, Toggle, SegmentedControl } from "@/components/ui/Form";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/layout/PageHeader";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { useApi } from "@/lib/useApi";
import { fetchIntegrations, type IntegrationStatus } from "@/lib/api";

type TabId = "general" | "apis" | "integrations" | "trading" | "display";

export function SettingsPage() {
  const { t } = useT();
  const [tab, setTab] = useState<TabId>("general");

  const tabs: { id: TabId; label: string }[] = [
    { id: "general", label: t("pages.settings.tabs.general") },
    { id: "apis", label: t("pages.settings.tabs.apis") },
    { id: "integrations", label: t("pages.settings.tabs.integrations") },
    { id: "trading", label: t("pages.settings.tabs.trading") },
    { id: "display", label: t("pages.settings.tabs.display") },
  ];

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.settings.title")}
        sub={t("pages.settings.sub")}
        icon={<SettingsIcon size={18} />}
      />

      <div className="relative max-w-full">
        <div className="inline-flex items-center rounded-sm border border-line-subtle bg-bg-2 p-0.5 gap-0.5 overflow-x-auto max-w-full">
          {tabs.map((tb) => (
            <button
              key={tb.id}
              onClick={() => setTab(tb.id)}
              className={cn(
                "h-7 px-3 text-2xs font-medium rounded-xs whitespace-nowrap",
                tab === tb.id ? "bg-bg-1 text-fg shadow-panel" : "text-fg-muted hover:text-fg hover:bg-bg-3",
              )}
            >
              {tb.label}
            </button>
          ))}
        </div>
        <div
          className="pointer-events-none absolute inset-y-0 right-0 w-6 bg-gradient-to-l from-bg-1 to-transparent rounded-sm sm:hidden"
          aria-hidden
        />
      </div>

      {tab === "general" && <GeneralTab />}
      {tab === "apis" && <ApisTab />}
      {tab === "integrations" && <IntegrationsTab />}
      {tab === "trading" && <TradingTab />}
      {tab === "display" && <DisplayTab />}
    </div>
  );
}

function GeneralTab() {
  const { t } = useT();
  return (
    <Card>
      <CardHeader title={t("pages.settings.general.profile")} subtitle={t("pages.settings.general.profile_sub")} />
      <div className="mb-3 rounded-sm border border-line-subtle bg-bg-2 px-3 py-2 text-2xs text-fg-subtle">
        Demo-Profil — nur Anzeige, wird NICHT gespeichert (kein Backend-Persist). Read-only, bis ein
        Profil-Endpoint existiert.
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label={t("pages.settings.general.operator")}><Input defaultValue="Operator" placeholder="Operator" readOnly /></Field>
        <Field label={t("pages.settings.general.role")}><Input defaultValue="Principal Engineer" readOnly /></Field>
        <Field label={t("pages.settings.general.timezone")}><Input defaultValue="Europe/Berlin" readOnly /></Field>
        <Field label={t("pages.settings.general.email")}><Input defaultValue="ops@example.local" readOnly /></Field>
      </div>
    </Card>
  );
}

function ApisTab() {
  return (
    <PreparedPanel
      title="Exchange- & Provider-API-Keys"
      reason="API-Key-Management für Binance, Bybit, Kraken, Coinbase, CoinGecko, CryptoPanic, TradingView ist UI-seitig vorgesehen, hat aber noch keinen Backend-CRUD-Endpoint mit sicherer Secret-Ablage."
      detail="Aktuelle Provider-Secrets werden über .env + KAI_* Variablen geladen (siehe RUNBOOK). Dashboard-Roadmap: verschlüsselte Secret-Vault-Integration + Test-Endpoint."
    />
  );
}

// Status kommt jetzt LIVE aus /dashboard/api/integrations (echte Settings-Flags),
// nicht mehr aus hartkodierten Literalen. "active" → aktiv, "disabled" →
// vorbereitet (gebaut, aber nicht konfiguriert/aktiviert), "unavailable" →
// Backend nicht erreichbar. So spiegelt das Badge die Realität (No-Fake-Doktrin).
function IntegrationsTab() {
  const { t } = useT();
  const tk = (k: string) => t(`pages.settings.integrations_tab.${k}`);
  const state = useApi(fetchIntegrations, 60_000);
  const it = state.state === "ready" ? state.data.integrations : null;
  const unreachable = state.state === "error";

  const rowStatus = (s: IntegrationStatus | undefined): IntegrationStatus =>
    unreachable ? "unavailable" : (s ?? "disabled");

  // Generische Notiz: aktiv → spezifischer Original-Text, sonst ehrlicher Hinweis.
  const note = (activeKey: string, active: boolean): string => {
    if (unreachable) return tk("note_unreachable");
    return active ? tk(activeKey) : tk("note_disabled");
  };

  // TradingView: dynamische Live-Notiz inkl. Auth-Mode, Auto-Promote + Events.
  const tvNote = (): string => {
    if (unreachable) return tk("note_unreachable");
    const tv = it?.tradingview;
    if (!tv || tv.status !== "active") return tk("tv_note");
    const parts = [
      tk("tv_note_active"),
      tv.auth_mode.toUpperCase(),
      tv.auto_promote_enabled ? tk("tv_auto_promote_on") : tk("tv_auto_promote_off"),
    ];
    if (tv.pipeline) parts.push(`${tv.pipeline.real_events} ${tk("tv_live_events")}`);
    return parts.join(" · ");
  };

  return (
    <Card>
      <CardHeader title={tk("title")} subtitle={tk("sub")} />
      <div className="space-y-3">
        <IntegrationRow
          label={tk("tg_label")}
          provider={tk("tg_provider")}
          status={rowStatus(it?.telegram.status)}
          note={note("tg_note", it?.telegram.status === "active")}
        />
        <IntegrationRow
          label={tk("llm_label")}
          provider={tk("llm_provider")}
          status={rowStatus(it?.llm.status)}
          note={note("llm_note", it?.llm.status === "active")}
        />
        <IntegrationRow
          label={tk("tv_label")}
          provider={tk("tv_provider")}
          status={rowStatus(it?.tradingview.status)}
          note={tvNote()}
        />
        <IntegrationRow
          label={tk("email_label")}
          provider={tk("email_provider")}
          status={rowStatus(it?.email.status)}
          note={unreachable ? tk("note_unreachable") : tk("email_note")}
        />
      </div>
    </Card>
  );
}

function IntegrationRow({ label, provider, status, note }: { label: string; provider: string; status: IntegrationStatus; note: string }) {
  const { t } = useT();
  const tone = status === "active" ? "pos" : status === "unavailable" ? "warn" : "muted";
  const stateLabel =
    status === "active"
      ? t("pages.settings.integrations_tab.state_active")
      : status === "unavailable"
        ? t("pages.settings.integrations_tab.state_unavailable")
        : t("pages.settings.integrations_tab.state_prepared");
  return (
    <div className="flex items-center justify-between gap-4 rounded-sm border border-line-subtle bg-bg-2 p-3">
      <div className="min-w-0">
        <div className="text-sm font-semibold text-fg truncate">{label}</div>
        <div className="text-2xs text-fg-subtle mt-0.5 font-mono break-words">{provider} · {note}</div>
      </div>
      <Badge tone={tone} dot>
        {stateLabel}
      </Badge>
    </div>
  );
}

function TradingTab() {
  const { t } = useT();
  const { mode, setMode, confirmLive, setConfirmLive, sizeCapPct, setSizeCapPct, cooldownSec, setCooldownSec } = useAppState();

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div className="md:col-span-2 rounded-sm border border-warn/30 bg-warn/10 px-3 py-2 text-2xs leading-relaxed text-warn">
        <span className="font-semibold">⚠ Lokale Anzeige-Präferenz — kein Backend-Effekt.</span>{" "}
        Diese Schalter (Modus inkl. „live", Live-Bestätigung, Size-Cap, Cooldown) ändern nur die
        Ansicht dieses Browsers. Sie schalten das echte Execution-Gate NICHT scharf und setzen
        KEINE Risk-Guards. Paper-/Live-Steuerung läuft serverseitig (.env + Operator-Endpoints);
        das Dashboard zeigt sie read-only.
      </div>
      <Card>
        <CardHeader title={t("pages.settings.trading_page.mode_title")} subtitle={t("pages.settings.trading_page.mode_sub")} />
        <SegmentedControl<TradingMode>
          value={mode}
          onChange={setMode}
          options={[
            { value: "paper", label: t("topbar.mode_paper"), tone: "warn" },
            { value: "sim", label: t("topbar.mode_sim"), tone: "info" },
            { value: "live", label: t("topbar.mode_live"), tone: "neg" },
          ]}
        />
        <div className="mt-4 space-y-3">
          <Toggle checked={confirmLive} onChange={setConfirmLive} label={t("pages.settings.trading.confirm_live")} />
        </div>
      </Card>
      <Card>
        <CardHeader title={t("pages.settings.trading_page.guards_title")} subtitle={t("pages.settings.trading_page.guards_sub")} />
        <div className="grid grid-cols-1 gap-3">
          <Field label={t("pages.settings.trading.size_cap")}>
            <Input type="number" value={sizeCapPct} onChange={(e) => setSizeCapPct(Number(e.target.value))} />
          </Field>
          <Field label={t("pages.settings.trading.cooldown")}>
            <Input type="number" value={cooldownSec} onChange={(e) => setCooldownSec(Number(e.target.value))} />
          </Field>
        </div>
      </Card>
    </div>
  );
}

function DisplayTab() {
  const { t, lang, setLang } = useT();
  const { theme, set } = useTheme();

  return (
    <Card>
      <CardHeader title={t("pages.settings.tabs.display")} />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label={t("pages.settings.display.theme")}>
          <SegmentedControl<"dark" | "light">
            value={theme}
            onChange={(v) => set(v)}
            options={[
              { value: "dark", label: t("topbar.theme_dark") },
              { value: "light", label: t("topbar.theme_light") },
            ]}
          />
        </Field>
        <Field label={t("pages.settings.display.language")}>
          <SegmentedControl<"de" | "en">
            value={lang}
            onChange={(v) => setLang(v)}
            options={[
              { value: "de", label: "Deutsch" },
              { value: "en", label: "English" },
            ]}
          />
        </Field>
      </div>
    </Card>
  );
}
