import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { ThemeMode } from "./theme";

/**
 * Holds the active light/dark mode and persists it to localStorage so the
 * choice survives reloads. Both `App.tsx` (to build the antd ThemeConfig) and
 * `AdminLayout.tsx` (to render the toggle) read from this single source.
 */
const THEME_STORAGE_KEY = "erp_theme";

const initialMode: ThemeMode =
  (localStorage.getItem(THEME_STORAGE_KEY) as ThemeMode | null) ?? "light";

interface ThemeModeContextValue {
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
  toggle: () => void;
}

const ThemeModeContext = createContext<ThemeModeContextValue | null>(null);

export function ThemeModeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(initialMode);

  const setMode = (next: ThemeMode) => {
    localStorage.setItem(THEME_STORAGE_KEY, next);
    setModeState(next);
  };

  const toggle = () => setMode(mode === "dark" ? "light" : "dark");

  // Expose the mode on <html> for any raw CSS that wants to react to it.
  useEffect(() => {
    document.documentElement.dataset.theme = mode;
  }, [mode]);

  const value = useMemo<ThemeModeContextValue>(
    () => ({ mode, setMode, toggle }),
    [mode, setMode, toggle],
  );

  return <ThemeModeContext.Provider value={value}>{children}</ThemeModeContext.Provider>;
}

export function useThemeMode(): ThemeModeContextValue {
  const ctx = useContext(ThemeModeContext);
  if (!ctx) {
    throw new Error("useThemeMode must be used inside <ThemeModeProvider>");
  }
  return ctx;
}
