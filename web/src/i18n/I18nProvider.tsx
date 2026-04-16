import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { interp, lookup, type Lang } from "./strings";

type I18nCtx = {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
};

const Ctx = createContext<I18nCtx | null>(null);
const KEY = "kai-lang";

function initial(): Lang {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "de" || v === "en") return v;
  } catch {}
  if (typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("de")) return "de";
  return "de";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, _setLang] = useState<Lang>(initial);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, lang);
    } catch {}
    document.documentElement.lang = lang;
  }, [lang]);
  const setLang = useCallback((l: Lang) => _setLang(l), []);
  const t = useCallback(
    (key: string, vars?: Record<string, string | number>) => {
      const s = lookup(lang, key);
      return vars ? interp(s, vars) : s;
    },
    [lang],
  );
  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useT() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useT outside I18nProvider");
  return c;
}
