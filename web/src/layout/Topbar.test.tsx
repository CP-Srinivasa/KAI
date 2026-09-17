import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

// Layout-Vertrag der Topbar (gemessen 17.09. per CDP gegen das ausgelieferte
// Bundle 74b33cd3): bei 1280 px mit Scrollbar lief die Leiste noch 8 px ueber den
// Rand. Der Suchknopf ist das einzige flexible Element (`flex-1`), konnte aber
// nicht unter seine Inhaltsbreite schrumpfen, weil `min-width: auto` galt. Der
// rechte Cluster ist `shrink-0` und darf nicht gestaucht werden.
vi.mock("@/theme/ThemeProvider", () => ({ useTheme: () => ({ theme: "dark", toggle: () => {} }) }));
vi.mock("@/i18n/I18nProvider", () => ({ useT: () => ({ t: (k: string) => k, lang: "de", setLang: () => {} }) }));
vi.mock("@/state/CurrencyProvider", () => ({ useCurrency: () => ({ currency: "USD", setCurrency: () => {} }) }));
vi.mock("@/state/AppState", () => ({
  TIMEFRAMES: ["24h", "7d", "30d", "90d"],
  nextDensity: (d: string) => d,
  useAppState: () => ({ timeframe: "30d", setTimeframe: () => {}, density: "comfortable", setDensity: () => {} }),
}));
vi.mock("@/state/Router", () => ({ useRouter: () => ({ route: "dashboard", navigate: () => {} }) }));
vi.mock("@/components/trading/ModeSelector", () => ({ ModeSelector: () => null }));
vi.mock("./NotificationsBell", () => ({ NotificationsBell: () => null }));
vi.mock("./BackendStatusPill", () => ({ BackendStatusPill: () => null }));

import { Topbar } from "./Topbar";

describe("Topbar", () => {
  afterEach(cleanup);

  it("Suchknopf darf schrumpfen (min-w-0), damit der rechte Cluster nie ueber den Rand laeuft", () => {
    render(<Topbar />);
    const search = screen.getByRole("button", { name: "topbar.search" });
    const cls = search.className.split(/\s+/);
    expect(cls).toEqual(expect.arrayContaining(["flex-1", "min-w-0"]));
  });

  it("im engen xl-Bereich (1280-1535 px) ohne ⌘K-Hinweis und mit kleinem Innenabstand", () => {
    // Bei 1280 px stand der Knopf mit min-w-0 schon auf seiner Padding-Untergrenze
    // (98 px); eine 15-px-Windows-Scrollbar haette wieder 5 px Ueberlauf erzeugt.
    render(<Topbar />);
    const search = screen.getByRole("button", { name: "topbar.search" });
    expect(search.className.split(/\s+/)).toEqual(expect.arrayContaining(["xl:pr-3", "2xl:pr-16"]));
    const kbd = search.querySelector("kbd");
    expect(kbd?.className.split(/\s+/)).toEqual(expect.arrayContaining(["xl:hidden", "2xl:block"]));
  });
});
