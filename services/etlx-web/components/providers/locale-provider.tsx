"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  dictionaries,
  LOCALES,
  type Locale,
  type Messages,
} from "@/lib/i18n/messages";

const STORAGE_KEY = "etlx.locale";
const DEFAULT_LOCALE: Locale = "ko";

type TranslateKey = keyof Messages;

interface LocaleContextValue {
  locale: Locale;
  setLocale: (l: Locale) => void;
  /** Translate a key for the active locale, with optional {placeholder} fill. */
  t: (key: TranslateKey, vars?: Record<string, string | number>) => string;
}

/** Exported so Storybook can build a fake Provider — see auth-provider.tsx. */
export const LocaleContext = createContext<LocaleContextValue | null>(null);
export type { LocaleContextValue, Locale };

function isLocale(value: string | null): value is Locale {
  return value !== null && (LOCALES as string[]).includes(value);
}

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(DEFAULT_LOCALE);

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (isLocale(stored)) {
      setLocaleState(stored);
      document.documentElement.setAttribute("lang", stored);
    } else {
      // No stored preference — guess from the browser, default to Korean.
      const guess: Locale = navigator.language?.startsWith("en") ? "en" : "ko";
      setLocaleState(guess);
      document.documentElement.setAttribute("lang", guess);
    }
  }, []);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    window.localStorage.setItem(STORAGE_KEY, l);
    document.documentElement.setAttribute("lang", l);
  }, []);

  const t = useCallback<LocaleContextValue["t"]>(
    (key, vars) => {
      const table = dictionaries[locale] as Messages;
      let str = table[key] ?? key;
      if (vars) {
        for (const [name, val] of Object.entries(vars)) {
          str = str.replace(new RegExp(`\\{${name}\\}`, "g"), String(val));
        }
      }
      return str;
    },
    [locale],
  );

  const value = useMemo<LocaleContextValue>(
    () => ({ locale, setLocale, t }),
    [locale, setLocale, t],
  );

  return (
    <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
  );
}

export function useLocale(): LocaleContextValue {
  const ctx = useContext(LocaleContext);
  if (!ctx) throw new Error("useLocale must be used within <LocaleProvider>");
  return ctx;
}
