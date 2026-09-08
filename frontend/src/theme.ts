import { theme as antdTheme, type ThemeConfig } from "antd";

export type ThemeMode = "light" | "dark";

/**
 * Modern / sleek design tokens for the FoodSupply ERP.
 * One primary accent, generous radii, soft shadows, comfortable control height.
 * Dark mode reuses the same accent via antd's darkAlgorithm.
 */
export function buildTheme(mode: ThemeMode): ThemeConfig {
  const isDark = mode === "dark";
  return {
    algorithm: isDark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: {
      colorPrimary: "#5B5BD6",
      colorInfo: "#5B5BD6",
      colorLink: "#5B5BD6",
      borderRadius: 10,
      borderRadiusLG: 14,
      borderRadiusSM: 8,
      controlHeight: 38,
      fontSize: 14,
      fontFamily:
        '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "PingFang SC", "Microsoft YaHei", sans-serif',
      colorBgLayout: isDark ? "#0f1115" : "#f4f5f7",
      colorBgContainer: isDark ? "#171a21" : "#ffffff",
      colorBgElevated: isDark ? "#1d212b" : "#ffffff",
      boxShadowSecondary: isDark
        ? "0 8px 28px rgba(0, 0, 0, 0.45)"
        : "0 6px 24px rgba(15, 23, 42, 0.08)",
      ...(isDark
        ? { colorBorder: "#2a2f3a", colorSplit: "#23272f" }
        : { colorBorder: "#e6e8ec", colorSplit: "#eef0f3" }),
    },
    components: {
      Card: { borderRadiusLG: 14 },
      Button: { borderRadius: 10, fontWeight: 500 },
      Menu: { itemBorderRadius: 8, itemHeight: 40 },
      Table: { headerBorderRadius: 10 },
      Layout: { headerBg: isDark ? "#171a21" : "#ffffff" },
    },
  };
}
