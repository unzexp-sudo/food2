import dayjs, { type Dayjs } from "dayjs";

/** Date display convention: YYYY-MM-DD (AGENT_CONTRACTS §7). */
export function formatDate(value: string | Date | Dayjs | null | undefined): string {
  if (!value) return "-";
  return dayjs(value).format("YYYY-MM-DD");
}

/** Datetime display convention: YYYY-MM-DD HH:mm (AGENT_CONTRACTS §7). */
export function formatDateTime(value: string | Date | Dayjs | null | undefined): string {
  if (!value) return "-";
  return dayjs(value).format("YYYY-MM-DD HH:mm");
}

/** Pick name_en / name_zh per current language. */
export function pickName(
  lang: string,
  en: string | null | undefined,
  zh: string | null | undefined,
): string {
  if (lang === "zh") return zh || en || "-";
  return en || zh || "-";
}
