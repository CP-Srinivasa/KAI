import { useState } from "react";
import { useT } from "@/i18n/I18nProvider";
import { useTheme } from "@/theme/ThemeProvider";
import { useAppState, type TradingMode } from "@/state/AppState";
import { Badge, Card, CardHeader } from "@/components/ui/Primitives";
import { Field, Input, Toggle, SegmentedControl } from "@/components/ui/Form";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/layout/PageHeader";
import { PreparedPanel } from "@/components/panels/PreparedPanel";

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
      <PageHeader title={t("pages.settings.title")} sub={t("pages.settings.sub")} />

      <div className="inline-flex items-center rounded-sm border border-line-subtle bg-bg-2 p-0.5 gap-0.5 overflow-x-auto">
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
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label={t("pages.settings.general.operator")}><Input defaultValue="Sascha" /></Field>
        <Field label={t("pages.settings.general.role")}><Input defaultValue="Principal Engineer" /></Field>
        <Field label={t("pages.settings.general.timezone")}><Input defaultValue="Europe/Berlin" /></Field>
        <Field label={t("pages.settings.general.email")}><Input defaultValue="ops@example.local" /></Field>
      </div>
    </Card>
  );
}

function ApisTab() {
  return (
    <PreparedPanel
      title="Exchange- & Provider-API-Keys"
      reason="API-Key-Management für Binance, Bybit, Kraken, Coinbase, CoinGecko, CryptoPanic, TradingView ist UI-seitig vorgesehen, hat aber noch keinen Backend-CRUD-Endpoint mit sicherer Secret-Ablage."
      detail="Aktuelle Provider-Secrets werden über .env + KAI_* Variablen geladen (siehe RUNBOOK). Phase 2: verschlüsselte Secret-Vault-Integration + Test-Endpoint."
    />
  );
}

function IntegrationsTab() {
  const { t } = useT();
  return (
    <Card>
      <CardHeader title={t("pages.settings.integrations_tab.title")} subtitle={t("pages.settings.integrations_tab.sub")} />
      <div className="space-y-3">
        <IntegrationRow label={t("pages.settings.integrations_tab.tg_label")} provider={t("pages.settings.integrations_tab.tg_provider")} status="ok" note={t("pages.settings.integrations_tab.tg_note")} />
        <IntegrationRow label={t("pages.settings.integrations_tab.llm_label")} provider={t("pages.settings.integrations_tab.llm_provider")} status="ok" note={t("pages.settings.integrations_tab.llm_note")} />
        <IntegrationRow label={t("pages.settings.integrations_tab.tv_label")} provider={t("pages.settings.integrations_tab.tv_provider")} status="prepared" note={t("pages.settings.integrations_tab.tv_note")} />
        <IntegrationRow label={t("pages.settings.integrations_tab.email_label")} provider={t("pages.settings.integrations_tab.email_provider")} status="prepared" note={t("pages.settings.integrations_tab.email_note")} />
      </div>
    </Card>
  );
}

function IntegrationRow({ label, provider, status, note }: { label: string; provider: string; status: "ok" | "warn" | "prepared"; note: string }) {
  const { t } = useT();
  return (
    <div className="flex items-center justify-between gap-4 rounded-sm border border-line-subtle bg-bg-2 p-3">
      <div>
        <div className="text-sm font-semibold text-fg">{label}</div>
        <div className="text-2xs text-fg-subtle mt-0.5 font-mono">{provider} · {note}</div>
      </div>
      <Badge tone={status === "ok" ? "pos" : status === "warn" ? "warn" : "muted"} dot>
        {status === "ok" ? t("pages.settings.integrations_tab.state_active") : status === "warn" ? t("pages.settings.integrations_tab.state_warn") : t("pages.settings.integrations_tab.state_prepared")}
      </Badge>
    </div>
  );
}

function TradingTab() {
  const { t } = useT();
  const { mode, setMode, confirmLive, setConfirmLive, sizeCapPct, setSizeCapPct, cooldownSec, setCooldownSec } = useAppState();

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
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
