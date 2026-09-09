import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import i18n from "i18next";
import { initReactI18next, useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import en from "./en";
import zh from "./zh";
import enExtra from "./extra";
import zhExtra from "./extraZh";

export const LANG_STORAGE_KEY = "erp_lang";
export type Lang = "en" | "zh";

const initialLang: Lang = localStorage.getItem(LANG_STORAGE_KEY) === "zh" ? "zh" : "en";

/** Deep-merge plain objects so module-contributed keys (./extra) extend the base. */
function mergeDeep(
  target: Record<string, unknown>,
  source: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...target };
  for (const key of Object.keys(source)) {
    const sv = source[key];
    if (sv && typeof sv === "object" && !Array.isArray(sv)) {
      const tv = out[key];
      out[key] = mergeDeep(
        tv && typeof tv === "object" && !Array.isArray(tv)
          ? (tv as Record<string, unknown>)
          : {},
        sv as Record<string, unknown>,
      );
    } else {
      out[key] = sv;
    }
  }
  return out;
}

const enMerged = mergeDeep(JSON.parse(JSON.stringify(en)), enExtra) as typeof en;
const zhMerged = mergeDeep(JSON.parse(JSON.stringify(zh)), zhExtra) as typeof zh;

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: enMerged },
    zh: { translation: zhMerged },
  },
  lng: initialLang,
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

function applySideEffects(lang: Lang): void {
  document.documentElement.lang = lang;
  dayjs.locale(lang === "zh" ? "zh-cn" : "en");
}

applySideEffects(initialLang);

interface LanguageContextValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: TFunction;
}

const LanguageContext = createContext<LanguageContextValue | null>(null);

export function LanguageProvider({ children }: { children: ReactNode }) {
  const { t, i18n: instance } = useTranslation();
  const [lang, setLangState] = useState<Lang>(instance.language === "zh" ? "zh" : "en");

  const setLang = useCallback(
    (next: Lang) => {
      void instance.changeLanguage(next);
      localStorage.setItem(LANG_STORAGE_KEY, next);
      setLangState(next);
    },
    [instance],
  );

  // Keep dayjs + <html lang> in sync (also on first mount).
  useEffect(() => {
    applySideEffects(lang);
  }, [lang]);

  const value = useMemo<LanguageContextValue>(() => ({ lang, setLang, t }), [lang, setLang, t]);

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage(): LanguageContextValue {
  const ctx = useContext(LanguageContext);
  if (!ctx) {
    throw new Error("useLanguage must be used inside <LanguageProvider>");
  }
  return ctx;
}
