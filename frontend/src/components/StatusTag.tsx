import { Tag } from "antd";
import { useLanguage } from "../i18n";

/**
 * Status domains matching AGENT_CONTRACTS §3 enum families.
 * The i18n key is `status.<domain>.<value>`.
 */
export type StatusDomain =
  | "order"
  | "po"
  | "intake"
  | "pick"
  | "delivery"
  | "invoice"
  | "payment"
  | "consolidation"
  | "master"
  | "customerType"
  | "wecomMessage"
  | "wecomOutbound"
  | "quotation";

/** Generic color per raw status value (shared across domains). */
const COLOR_BY_VALUE: Record<string, string> = {
  // order
  draft: "default",
  pending_confirmation: "processing",
  needs_clarification: "warning",
  confirmed: "success",
  consolidated: "cyan",
  fulfilled: "success",
  invoiced: "purple",
  rejected: "error",
  // intake
  queued: "default",
  processing: "processing",
  completed: "success",
  failed: "error",
  // po / pick / consolidation / master
  sent: "processing",
  partially_received: "warning",
  received: "success",
  closed: "default",
  cancelled: "error",
  open: "default",
  picking: "processing",
  picked: "success",
  short: "warning",
  // delivery
  scheduled: "default",
  out_for_delivery: "processing",
  delivered: "success",
  partial: "warning",
  // invoice
  issued: "processing",
  paid: "success",
  void: "error",
  // payment direction
  inbound: "green",
  outbound: "orange",
  // master data status
  active: "success",
  inactive: "default",
  // wecom message (docs/WECOM_CONTRACTS.md §3)
  handed_off: "success",
  ignored: "default",
  duplicate: "default",
  // wecom outbound
  mock: "orange",
  skipped: "warning",
  pending: "processing",
  blocked: "error", // refused by WECOM_SEND_ALLOWLIST before it reached WeCom
};

/**
 * Per-domain colour overrides. Only needed where the same raw value means
 * something different in another domain — e.g. WeCom `received` is "just
 * arrived", while a PO `received` is a completed state.
 */
const COLOR_BY_DOMAIN: Partial<Record<StatusDomain, Record<string, string>>> = {
  wecomMessage: { received: "processing" },
  wecomOutbound: { sent: "success" },
};

/** Renders a translated, color-coded status Tag. Use for ALL status values. */
export default function StatusTag({ domain, value }: { domain: StatusDomain; value: string }) {
  const { t } = useLanguage();
  const color = COLOR_BY_DOMAIN[domain]?.[value] ?? COLOR_BY_VALUE[value] ?? "default";
  return <Tag color={color}>{t(`status.${domain}.${value}`)}</Tag>;
}
