import { Tag } from "antd";

/**
 * Confidence display rule (AGENT_CONTRACTS §7):
 * API gives 0–1 → show as percent; ≥0.95 green, ≥0.7 orange, else red.
 */
export default function ConfidenceTag({ value }: { value: number }) {
  const pct = `${Math.round(value * 100)}%`;
  const color = value >= 0.95 ? "green" : value >= 0.7 ? "orange" : "red";
  return <Tag color={color}>{pct}</Tag>;
}
