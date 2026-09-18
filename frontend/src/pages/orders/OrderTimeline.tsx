import { Timeline, Typography, Space } from "antd";
import { useLanguage } from "../../i18n";
import { formatDateTime } from "../../utils/format";
import StatusTag from "../../components/StatusTag";

/** The order's delivery leg. `out_for_delivery` lives here, not on the order. */
export interface OrderDeliveryData {
  id: string;
  delivery_number: string;
  status: string; // scheduled|picked|out_for_delivery|delivered|partial|failed
  driver_id: string | null;
  scheduled_date: string | null;
  picked_at: string | null;
  out_at: string | null;
  delivered_at: string | null;
}

/** Minimal order fields required to render the lifecycle timeline. */
interface OrderTimelineData {
  status: string;
  created_at: string;
  confirmed_at: string | null;
  delivery?: OrderDeliveryData | null;
}

/**
 * The order's own pipeline. Note `out_for_delivery` is in this list but is NOT
 * an `Order.status` value — it is a *Delivery* status, and its position here is
 * the only place the two lifecycles are stitched together. See `stageState`.
 *
 * Pipeline: draft -> pending_confirmation -> confirmed -> consolidated ->
 * out_for_delivery -> fulfilled -> invoiced.  `needs_clarification` is a hold
 * at the pending_confirmation step.
 */
const STAGES: { key: string; label: string }[] = [
  { key: "draft", label: "pages.orders.orderTimeline.draft" },
  { key: "pending_confirmation", label: "pages.orders.orderTimeline.pendingConfirmation" },
  { key: "confirmed", label: "pages.orders.orderTimeline.confirmed" },
  { key: "consolidated", label: "pages.orders.orderTimeline.consolidated" },
  { key: "out_for_delivery", label: "pages.orders.orderTimeline.outForDelivery" },
  { key: "fulfilled", label: "pages.orders.orderTimeline.fulfilled" },
  { key: "invoiced", label: "pages.orders.orderTimeline.invoiced" },
];

/**
 * Position of an order status on the pipeline above.
 *
 * `fulfilled` is 5, not 4, on purpose: index 4 belongs to the delivery leg, and
 * an order can reach `fulfilled` straight from the pick list
 * (`_maybe_fulfill_orders`) without a delivery ever being dispatched. Mapping it
 * to 4 would light up "Out for delivery" for goods that never left the depot.
 */
const STATUS_INDEX: Record<string, number> = {
  draft: 0,
  needs_clarification: 1,
  pending_confirmation: 1,
  confirmed: 2,
  consolidated: 3,
  fulfilled: 5,
  invoiced: 6,
};

const DELIVERY_INDEX = 4;

type StageState = "done" | "active" | "pending";

/**
 * How far the delivery leg has actually got, read from the delivery record —
 * never inferred from the order status.
 *
 * `failed` stays pending rather than done: nothing was delivered, so a green
 * tick would be a lie. `partial` is done — the goods did arrive, short.
 */
function deliveryStageState(delivery: OrderDeliveryData | null | undefined): StageState {
  if (!delivery) return "pending";
  if (delivery.status === "out_for_delivery") return "active";
  if (delivery.status === "delivered" || delivery.status === "partial") return "done";
  return "pending";
}

const COLOR: Record<StageState, string> = {
  done: "green",
  active: "blue",
  pending: "gray",
};

export default function OrderTimeline({ order }: { order: OrderTimelineData }) {
  const { t } = useLanguage();
  // `rejected` is a terminal branch, not a step on the pipeline. Falling back
  // to index 0 would mark "Order created" as the *current* stage of a dead
  // order; -1 leaves every stage pending and the banner says why.
  const isRejected = order.status === "rejected";
  const current = isRejected ? -1 : (STATUS_INDEX[order.status] ?? 0);
  const isHold = order.status === "needs_clarification";
  const delivery = order.delivery ?? null;

  const items = STAGES.map((stage, i) => {
    const isDeliveryStage = i === DELIVERY_INDEX;
    const state: StageState = isDeliveryStage
      ? deliveryStageState(delivery)
      : i < current
        ? "done"
        : i === current
          ? "active"
          : "pending";

    const ts =
      stage.key === "draft"
        ? order.created_at
        : stage.key === "confirmed"
          ? order.confirmed_at
          : stage.key === "out_for_delivery"
            ? (delivery?.out_at ?? null)
            : stage.key === "fulfilled"
              ? (delivery?.delivered_at ?? null)
              : null;

    // The delivery leg is the one stage whose absence is worth explaining: an
    // order that is already `fulfilled` with no delivery row went out on the
    // pick list alone, and a blank step there reads as a missing feature.
    const showNoDeliveryRecord =
      isDeliveryStage && !delivery && current > DELIVERY_INDEX;

    return {
      color: COLOR[state],
      children: (
        <Space direction="vertical" size={2}>
          <Space size={8} wrap>
            <Typography.Text strong={state === "active"}>{t(stage.label)}</Typography.Text>
            {state === "active" && !isDeliveryStage && (
              <StatusTag domain="order" value={order.status} />
            )}
          </Space>
          {ts && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {formatDateTime(ts)}
            </Typography.Text>
          )}
          {state === "active" && isHold && (
            <Typography.Text type="warning" style={{ fontSize: 12 }}>
              {t("pages.orders.orderTimeline.needsClarification")}
            </Typography.Text>
          )}
          {showNoDeliveryRecord && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t("pages.orders.orderTimeline.noDeliveryRecord")}
            </Typography.Text>
          )}
        </Space>
      ),
    };
  });

  return (
    <>
      {isRejected && (
        <Typography.Text type="danger" style={{ fontSize: 12 }}>
          {t("pages.orders.orderTimeline.rejected")}
        </Typography.Text>
      )}
      <Timeline
        items={items}
        style={{ paddingTop: 8 }}
      />
    </>
  );
}
