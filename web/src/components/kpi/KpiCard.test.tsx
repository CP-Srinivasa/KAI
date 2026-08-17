import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import { KpiCard } from "./KpiCard";
import { CurrencyProvider } from "@/state/CurrencyProvider";

/**
 * Befund 2026-08-17: Der Fortschrittsbalken der KpiCard log bei genau den
 * Werten, die am meisten weh tun.
 *
 * `progressPct` wurde VOR dem Rendern auf [0,100] geclamped — ein negativer
 * Wert wurde damit zu 0. Anschliessend machte
 * `progressPct === 0 && (tone === "warn" || tone === "neg")` daraus eine
 * Fuellbreite von **100 %**. Ergebnis: 0 % Forward-Precision und ein
 * Tier-Lift von -6pp rendern einen VOLLEN Balken — optisch nicht von
 * "Ziel erreicht" zu unterscheiden.
 *
 * Genau dieser Fall ist in `ProgressBar` (Primitives.tsx) laengst geloest
 * ("Bug 2026-05-08, Tier-Lift bei -6pp"): dort clamped nur die Breite, und
 * ein Wert unter null bekommt einen eigenen, gestreiften Sub-Zero-Balken.
 * Die KpiCard hatte eine zweite, aeltere Balken-Implementierung — inklusive
 * `aria-hidden`, also ohne jede Screen-Reader-Semantik.
 */

function renderCard(props: Parameters<typeof KpiCard>[0]) {
  return render(
    <CurrencyProvider>
      <KpiCard {...props} />
    </CurrencyProvider>,
  );
}

function bar() {
  return screen.getByRole("progressbar");
}

afterEach(cleanup);

describe("KpiCard Fortschrittsbalken", () => {
  it("zeichnet bei Wert 0 keinen vollen Balken", () => {
    renderCard({
      label: "Forward Precision",
      value: "0.0",
      unit: "%",
      target: 60,
      valueNumeric: 0,
      tone: "warn",
    });

    const el = bar();
    expect(el).toHaveAttribute("aria-valuenow", "0");
    expect(el).toHaveAttribute("aria-valuemax", "60");
    // Der Fuell-Balken darf nicht die volle Breite haben.
    const fill = el.querySelector<HTMLElement>("[style*='width']");
    expect(fill?.style.width).toBe("0%");
  });

  it("stellt einen negativen Wert als Sub-Zero dar, nicht als vollen Balken", () => {
    renderCard({
      label: "Priority Tier Lift",
      value: "-6.0",
      unit: "pp",
      target: 15,
      valueNumeric: -6,
      tone: "neg",
    });

    const el = bar();
    expect(el).toHaveAttribute("aria-valuenow", "-6");
    // WCAG: valuenow darf nicht unter valuemin liegen.
    expect(Number(el.getAttribute("aria-valuemin"))).toBeLessThanOrEqual(-6);
    expect(el.getAttribute("aria-valuetext")).toContain("unter Schwelle");
  });

  it("fuellt proportional, wenn das Ziel teilweise erreicht ist", () => {
    renderCard({
      label: "Resolved",
      value: "25",
      target: 50,
      valueNumeric: 25,
      tone: "warn",
    });

    const fill = bar().querySelector<HTMLElement>("[style*='width']");
    expect(fill?.style.width).toBe("50%");
  });

  it("fuellt voll, wenn das Ziel wirklich erreicht ist", () => {
    renderCard({
      label: "Resolved",
      value: "60",
      target: 50,
      valueNumeric: 60,
      tone: "pos",
    });

    const fill = bar().querySelector<HTMLElement>("[style*='width']");
    expect(fill?.style.width).toBe("100%");
  });

  it("zeigt keinen Balken, wenn kein Ziel gesetzt ist", () => {
    renderCard({ label: "Ohne Ziel", value: "42" });

    expect(screen.queryByRole("progressbar")).toBeNull();
  });
});
