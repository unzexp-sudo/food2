import { useEffect, useState } from "react";
import { Descriptions, Drawer, Table, Typography, type TableProps } from "antd";
import { useLanguage } from "../../i18n";
import { api } from "../../api/client";
import { pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";

export interface CustomerDetail {
  id: string;
  code: string;
  name_en: string;
  name_zh: string;
  type: string;
  contact_name: string | null;
  contact_phone: string | null;
  address: string | null;
  delivery_zone: string | null;
  notes: string | null;
  status: string;
}

interface OrderRow {
  id: string;
  order_number: string;
  customer_name_en: string | null;
  customer_name_zh: string | null;
  status: string;
  created_at: string | null;
}

interface Props {
  open: boolean;
  customer: CustomerDetail | null;
  onClose: () => void;
}

export default function CustomerDetailDrawer({ open, customer, onClose }: Props) {
  const { t, lang } = useLanguage();
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !customer) return;
    setLoading(true);
    setOrders([]);
    api
      .get<{ items: OrderRow[] }>("/orders", {
        customer_id: customer.id,
        page_size: 5,
      })
      .then((r) => setOrders(r.items ?? []))
      .catch(() => setOrders([]))
      .finally(() => setLoading(false));
  }, [open, customer]);

  const orderColumns: TableProps<OrderRow>["columns"] = [
    {
      title: t("pages.master.customers.code"),
      dataIndex: "order_number",
      render: (v: string) => v || "—",
    },
    {
      title: t("pages.master.customers.nameEn"),
      key: "customer",
      render: (_: unknown, r: OrderRow) =>
        pickName(lang, r.customer_name_en, r.customer_name_zh) || "—",
    },
    {
      title: t("pages.master.customers.status"),
      dataIndex: "status",
      render: (v: string) => <StatusTag domain="order" value={v} />,
    },
    {
      title: "Created at",
      dataIndex: "created_at",
      render: (v: string | null) => (v ? new Date(v).toLocaleString() : "—"),
    },
  ];

  return (
    <Drawer
      title={customer ? pickName(lang, customer.name_en, customer.name_zh) : "View"}
      open={open}
      onClose={onClose}
      width={640}
    >
      {customer && (
        <>
          <Descriptions column={1} size="middle" bordered>
            <Descriptions.Item label={t("pages.master.customers.code")}>
              {customer.code}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.master.customers.nameEn")}>
              {customer.name_en}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.master.customers.nameZh")}>
              {customer.name_zh}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.master.customers.type")}>
              <StatusTag domain="customerType" value={customer.type} />
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.master.customers.contactName")}>
              {customer.contact_name ?? "—"}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.master.customers.contactPhone")}>
              {customer.contact_phone ?? "—"}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.master.customers.deliveryZone")}>
              {customer.delivery_zone ?? "—"}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.master.customers.address")}>
              {customer.address ?? "—"}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.master.customers.status")}>
              <StatusTag domain="master" value={customer.status} />
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.master.customers.notes")}>
              {customer.notes ?? "—"}
            </Descriptions.Item>
          </Descriptions>

          <Typography.Title level={5} style={{ marginTop: 24 }}>
            Order history
          </Typography.Title>
          <Table<OrderRow>
            rowKey="id"
            loading={loading}
            dataSource={orders}
            columns={orderColumns}
            size="small"
            pagination={false}
          />
        </>
      )}
    </Drawer>
  );
}
