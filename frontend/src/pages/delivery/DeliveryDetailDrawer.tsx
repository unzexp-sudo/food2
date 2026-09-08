import { Drawer, Descriptions, Table, Typography, type TableProps } from "antd";
import { useLanguage } from "../../i18n";
import { formatDateTime, pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";
import type { Delivery, DeliveryLine } from "./DeliveryPage";

interface DeliveryDetailDrawerProps {
  /** Delivery object (already fetched) or null while loading. */
  delivery: Delivery | null;
  open: boolean;
  onClose: () => void;
}

/**
 * Self-contained right-side Drawer showing a delivery's details: route/zone,
 * assigned driver, status (StatusTag), ETA, and a Table of its order stops
 * (delivery lines). The full Delivery is passed in from DeliveryPage (which
 * fetches GET /deliveries/{id}); the drawer itself stays presentational.
 */
export default function DeliveryDetailDrawer({
  delivery,
  open,
  onClose,
}: DeliveryDetailDrawerProps) {
  const { t, lang } = useLanguage();

  const lineColumns: TableProps<DeliveryLine>["columns"] = [
    {
      title: t("pages.delivery.colLineProduct"),
      key: "product",
      render: (_: unknown, r: DeliveryLine) =>
        pickName(lang, r.product_name_en, r.product_name_zh),
    },
    {
      title: t("pages.delivery.colLineQty"),
      dataIndex: "quantity",
      width: 110,
      align: "right" as const,
    },
    {
      title: t("pages.delivery.colLineDelivered"),
      dataIndex: "delivered_quantity",
      width: 110,
      align: "right" as const,
      render: (v: number) => v ?? 0,
    },
  ];

  return (
    <Drawer
      title={t("pages.delivery.details")}
      open={open}
      onClose={onClose}
      width={760}
    >
      {delivery ? (
        <>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="Route / Zone">
              {delivery.route ?? "—"}
            </Descriptions.Item>
            <Descriptions.Item label="Assigned Driver">
              {delivery.driver_name ?? "—"}
            </Descriptions.Item>
            <Descriptions.Item label="Status">
              <StatusTag domain="delivery" value={delivery.status} />
            </Descriptions.Item>
            <Descriptions.Item label="ETA">
              {delivery.scheduled_date ? formatDateTime(delivery.scheduled_date) : "—"}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.delivery.colDeliveryNumber")}>
              {delivery.delivery_number}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.delivery.colOrder")}>
              {delivery.order_number}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.delivery.colCustomer")}>
              {pickName(lang, delivery.customer_name_en, delivery.customer_name_zh)}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.delivery.colPickedAt")}>
              {delivery.picked_at ? formatDateTime(delivery.picked_at) : "—"}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.delivery.colOutAt")}>
              {delivery.out_at ? formatDateTime(delivery.out_at) : "—"}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.delivery.colDeliveredAt")}>
              {delivery.delivered_at ? formatDateTime(delivery.delivered_at) : "—"}
            </Descriptions.Item>
          </Descriptions>

          <Typography.Title level={5} style={{ marginTop: 16 }}>
            {t("pages.delivery.lines")}
          </Typography.Title>
          <Table<DeliveryLine>
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={delivery.lines}
            columns={lineColumns}
          />

          <Typography.Title level={5} style={{ marginTop: 16 }}>
            {t("pages.delivery.pod")}
          </Typography.Title>
          {delivery.pod ? (
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label={t("pages.delivery.podReceivedBy")}>
                {delivery.pod.received_by ?? "—"}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.delivery.podGps")}>
                {delivery.pod.gps_lat != null && delivery.pod.gps_lng != null
                  ? `${delivery.pod.gps_lat}, ${delivery.pod.gps_lng}`
                  : "—"}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.delivery.podDeliveredAt")}>
                {delivery.pod.delivered_at ? formatDateTime(delivery.pod.delivered_at) : "—"}
              </Descriptions.Item>
            </Descriptions>
          ) : (
            <Typography.Text type="secondary">{t("pages.delivery.noPod")}</Typography.Text>
          )}
        </>
      ) : (
        <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
      )}
    </Drawer>
  );
}
