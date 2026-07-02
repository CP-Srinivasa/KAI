import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { Card } from "@/components/ui/Primitives";

type Props = {
  name: string;
  children: ReactNode;
};

type State = {
  error: Error | null;
  attempt: number;
};

export class PanelErrorBoundary extends Component<Props, State> {
  state: State = { error: null, attempt: 0 };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    if (import.meta.env.DEV) {
      console.error(`[PanelErrorBoundary:${this.props.name}]`, error, info.componentStack);
    }
  }

  reset = () => {
    this.setState((s) => ({ error: null, attempt: s.attempt + 1 }));
  };

  render() {
    if (this.state.error) {
      return (
        <Card padded className="border-neg/30 bg-neg/5 attention-breathe-neg">
          <div className="flex items-start gap-3" role="alert">
            <AlertTriangle size={16} className="mt-0.5 shrink-0 text-neg" aria-hidden />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-neg">
                Panel „{this.props.name}" unerreichbar
              </div>
              <div className="mt-1 text-xs text-fg-muted break-words">
                {this.state.error.message || "Unbekannter Render-Fehler"}
              </div>
              {import.meta.env.DEV && this.state.error.stack && (
                <pre className="mt-2 max-h-40 overflow-auto rounded-sm bg-bg-2 p-2 text-2xs font-mono text-fg-subtle">
                  {this.state.error.stack}
                </pre>
              )}
              <button
                onClick={this.reset}
                className="mt-3 inline-flex items-center gap-1.5 rounded-sm border border-line bg-bg-1 hover:bg-bg-2 px-2.5 py-1 text-xs text-fg transition-colors"
              >
                <RotateCcw size={12} aria-hidden />
                Neu laden
              </button>
            </div>
          </div>
        </Card>
      );
    }
    return <PanelErrorBoundaryChild key={this.state.attempt}>{this.props.children}</PanelErrorBoundaryChild>;
  }
}

function PanelErrorBoundaryChild({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
