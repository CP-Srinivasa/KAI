import { ThemeProvider } from "@/theme/ThemeProvider";
import { I18nProvider } from "@/i18n/I18nProvider";
import { AppStateProvider } from "@/state/AppState";
import { CurrencyProvider } from "@/state/CurrencyProvider";
import { RouterProvider } from "@/state/Router";
import { AppShell } from "@/layout/AppShell";
import { ToastProvider } from "@/components/Toast";

export default function App() {
  return (
    <ThemeProvider>
      <I18nProvider>
        <CurrencyProvider>
          <AppStateProvider>
            <RouterProvider>
              <ToastProvider>
                <AppShell />
              </ToastProvider>
            </RouterProvider>
          </AppStateProvider>
        </CurrencyProvider>
      </I18nProvider>
    </ThemeProvider>
  );
}
