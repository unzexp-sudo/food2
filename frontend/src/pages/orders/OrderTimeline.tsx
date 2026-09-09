import { Timeline, Typography, Space } from "antd";
import { useLanguage } from "../../i18n";
import { formatDateTime } from "../../utils/format";
import StatusTag from "../../components/StatusTag";

/** Minimal order fields required to render the lifecycle timeline. */
interface OrderTimelineData {
  status: string;
  created_at: string;
  confirmed_at: string | null;
}

/**
 * Derives an ordered lifecycle timeline from the order's current status.
 * No history/events endpoint exists on the backend, so we map status ->
 * pipeline position and mark completed/current/pending stages.
 *
 * Pipeline: draft -> pending_confirmation -> confirmed -> consolidated ->
 * fulfilled -> invoiced.  `needs_clarification` is a hold at the
 * pending_confirmation step.
 */
const STAGES: { key: string; label: string }[] = [
  { key: "draft", label: "pages.orders.orderTimeline.draft" },
  { key: "pending_confirmation", label: "pages.orders.orderTimeline.pendingConfirmation" },
  { key: "confirmed", label: "pages.orders.orderTimeline.confirmed" },
  { key: "consolidated", label: "pages.orders.orderTimeline.consolidated" },
  { key: "fulfilled", label: "pages.orders.orderTimeline.fulfilled" },
  { key: "invoiced", label: "pages.orders.orderTimeline.invoiced" },
];

const STATUS_INDEX: Record<string, number> = {
  draft: 0,
  needs_clarification: 1,
  pending_confirmation: 1,
  confirmed: 2,
  consolidated: 3,
  fulfilled: 4,
  invoiced: 5,
};

export default function OrderTimeline({ order }: { order: OrderTimelineData }) {
  const { t } = useLanguage();
  const current = STATUS_INDEX[order.status] ?? 0;
  const isHold = order.status === "needs_clarification";

  const items = STAGES.map((stage, i) => {
    const done = i < current;
    const active = i === current;
    const color = done ? "green" : active ? "blue" : "gray";

    const ts =
      stage.key === "draft"
        ? order.created_at
        : stage.key === "confirmed"
          ? order.confirmed_at
          : null;

    return {
      color,
      children: (
        <Space direction="vertical" size={2}>
          <Space size={8} wrap>
            <Typography.Text strong={active}>{t(stage.label)}</Typography.Text>
            {active && <StatusTag domain="order" value={order.status} />}
          </Space>
          {ts && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {formatDateTime(ts)}
            </Typography.Text>
          )}
          {active && isHold && (
            <Typography.Text type="warning" style={{ fontSize: 12 }}>
              {t("pages.orders.orderTimeline.needsClarification")}
            </Typography.Text>
          )}
        </Space>
      ),
    };
  });

  return (
    <Timeline
      items={items}
      style={{ paddingTop: 8 }}
    />
  );
}
