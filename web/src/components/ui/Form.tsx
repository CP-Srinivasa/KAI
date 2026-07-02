import { forwardRef, useState, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { Eye, EyeOff } from "lucide-react";
import { cn } from "@/lib/utils";

export function Field({
  label,
  hint,
  error,
  required,
  children,
  className,
}: {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("block", className)}>
      {label && (
        <div className="mb-1 flex items-baseline gap-2">
          <span className="text-2xs font-semibold uppercase tracking-[0.08em] text-fg-subtle">
            {label}
            {required && <span className="text-neg ml-0.5">*</span>}
          </span>
          {hint && !error && <span className="text-2xs text-fg-subtle">{hint}</span>}
        </div>
      )}
      {children}
      {error && <div className="mt-1 text-2xs text-neg font-medium">{error}</div>}
    </label>
  );
}

const baseInput =
  "w-full h-9 px-3 rounded-sm border border-line-subtle bg-bg-2 text-sm text-fg placeholder:text-fg-subtle focus:outline-none focus:border-accent/60 focus:bg-bg-1 transition-colors disabled:opacity-60 disabled:pointer-events-none font-mono";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest },
  ref,
) {
  return <input ref={ref} className={cn(baseInput, className)} {...rest} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, rows = 4, ...rest }, ref) {
    return (
      <textarea
        ref={ref}
        rows={rows}
        className={cn(baseInput, "h-auto py-2 min-h-[72px] resize-y leading-relaxed", className)}
        {...rest}
      />
    );
  },
);

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...rest },
  ref,
) {
  return (
    <select
      ref={ref}
      className={cn(baseInput, "pr-8 appearance-none bg-no-repeat bg-right font-sans", className)}
      style={{
        backgroundImage:
          "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='none' stroke='%23888' stroke-width='2'><path d='M2 4l4 4 4-4'/></svg>\")",
        backgroundPosition: "right 10px center",
      }}
      {...rest}
    >
      {children}
    </select>
  );
});

export function SecretInput({
  value,
  onChange,
  placeholder,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
}) {
  const [shown, setShown] = useState(false);
  return (
    <div className={cn("relative", className)}>
      <input
        type={shown ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="new-password"
        spellCheck={false}
        className={cn(baseInput, "pr-9")}
      />
      <button
        type="button"
        onClick={() => setShown((v) => !v)}
        className="absolute right-0 top-0 h-9 w-9 grid place-items-center text-fg-subtle hover:text-fg"
        aria-label="toggle visibility"
      >
        {shown ? <EyeOff size={14} /> : <Eye size={14} />}
      </button>
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className={cn("inline-flex items-center gap-2 text-xs select-none", disabled && "opacity-60")}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative h-[18px] w-[32px] rounded-full transition-colors",
          checked ? "bg-accent" : "bg-bg-3 border border-line",
        )}
      >
        <span
          className={cn(
            "absolute top-[2px] h-[14px] w-[14px] rounded-full bg-white shadow transition-transform",
            checked ? "translate-x-[15px]" : "translate-x-[2px]",
          )}
        />
      </button>
      {label && <span className="text-fg">{label}</span>}
    </label>
  );
}

export function SegmentedControl<T extends string>({
  value,
  onChange,
  options,
  className,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode; tone?: "pos" | "neg" | "warn" | "info"; hint?: string }[];
  className?: string;
}) {
  return (
    <div
      className={cn(
        "inline-flex items-center rounded-sm border border-line-subtle bg-bg-2 p-0.5 gap-0.5",
        className,
      )}
    >
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => onChange(o.value)}
            title={o.hint}
            className={cn(
              "h-7 px-2.5 text-2xs font-medium rounded-xs transition-colors whitespace-nowrap",
              active
                ? o.tone === "neg"
                  ? "bg-neg/15 text-neg ring-1 ring-neg/30"
                  : o.tone === "info"
                    ? "bg-info/15 text-info ring-1 ring-info/30"
                    : o.tone === "warn"
                      ? "bg-warn/15 text-warn ring-1 ring-warn/25"
                      : "bg-bg-1 text-fg shadow-panel"
                : "text-fg-muted hover:text-fg hover:bg-bg-3",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
