import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

// Last line of defence around the whole AppShell. A render error anywhere that
// is NOT caught by a closer (route/panel) boundary would otherwise blank the
// entire app with no recovery path. This renders a full-screen fallback with a
// hard reload — state may be corrupt, so we reload the document rather than
// attempt an in-place reset.

type Props = { children: ReactNode };
type State = { error: Error | null };

export class RootErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    if (import.meta.env.DEV) {
      console.error("[RootErrorBoundary]", error, info.componentStack);
    }
  }

  render() {
    if (this.state.error) {
      return (
        <div
          className="min-h-screen flex items-center justify-center bg-bg-0 p-6 text-fg"
          role="alert"
        >
          <div className="max-w-md w-full rounded-md border border-neg/30 bg-neg/5 p-5">
            <div className="flex items-start gap-3">
              <AlertTriangle size={18} className="mt-0.5 shrink-0 text-neg" aria-hidden />
              <div className="min-w-0">
                <div className="text-sm font-semibold text-neg">Dashboard-Fehler</div>
                <div className="mt-1 text-xs text-fg-muted break-words">
                  {this.state.error.message || "Unbekannter Render-Fehler"}
                </div>
                <button
                  onClick={() => window.location.reload()}
                  className="mt-3 inline-flex items-center gap-1.5 rounded-sm border border-line bg-bg-1 hover:bg-bg-2 px-2.5 py-1 text-xs text-fg transition-colors"
                >
                  <RotateCcw size={12} aria-hidden />
                  App neu laden
                </button>
              </div>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
