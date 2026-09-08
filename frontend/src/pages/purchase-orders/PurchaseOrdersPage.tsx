import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Card,
  DatePicker,
  Select,
  Space,
  Table,
} from "antd";
import { useLanguage } from "../../i18n";
import { api, type Page } from "../../api/client";
import { useList } from "../../api/hooks";
import { formatDate, formatDateTime, pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";

interface Wholesaler {
  id: string;
  name_en: string;
  name_zh: string;
  status: string;
}
interface PurchaseOrder {
  id: string;
  po_number: string;
  wholesaler_id: string;
  wholesaler_name_en: string;
  wholesaler_name_zh: string;
  category_id: string | null;
  category_name_en: string | null;
  category_name_zh: string | null;
  batch_id: string | null;
  status: string;
  total_amount: number;
  sent_at: string | null;
  notes: string | null;
  created_at: string;
  // delivery_date comes from the parent batch (filter param). Some APIs embed it on the PO.
  delivery_date?: string | null;
}

const PO_STATUSES = ["draft", "sent", "partially_received", "received", "closed", "cancelled"];

export default function PurchaseOrdersPage() {
  const { t, lang } = useLanguage();
  const navigate = useNavigate();

  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [wholesalerFilter, setWholesalerFilter] = useState<string | undefined>();
  const [deliveryFilter, setDeliveryFilter] = useState<string | undefined>();

  const params = useMemo(
    () => ({
      ...(statusFilter ? { status: statusFilter } : {}),
      ...(wholesalerFilter ? { wholesaler_id: wholesalerFilter } : {}),
      ...(deliveryFilter ? { delivery_date: deliveryFilter } : {}),
    }),
    [statusFilter, wholesalerFilter, deliveryFilter],
  );
  const list = useList<PurchaseOrder>("/purchase-orders", params);

  const [wholesalers, setWholesalers] = useState<Wholesaler[]>([]);
  useEffect(() => {
    api.get<Page<Wholesaler>>("/wholesalers", { page: 1, page_size: 100 }).then((r) => setWholesalers(r.items)).catch(() => setWholesalers([]));
  }, []);

  const columns = [
    {
      title: t("pages.purchaseOrders.colPONumber"),
      dataIndex: "po_number",
      width: 170,
      render: (v: string, r: PurchaseOrder) => (
        <a onClick={() => navigate(`/purchase-orders/${r.id}`)}>{v}</a>
      ),
    },
    {
      title: t("pages.purchaseOrders.colWholesaler"),
      key: "wholesaler",
      render: (_: unknown, r: PurchaseOrder) => pickName(lang, r.wholesaler_name_en, r.wholesaler_name_zh),
    },
    {
      title: t("pages.purchaseOrders.colCategory"),
      key: "category",
      render: (_: unknown, r: PurchaseOrder) =>
        pickName(lang, r.category_name_en, r.category_name_zh),
    },
    {
      title: t("pages.purchaseOrders.colStatus"),
      dataIndex: "status",
      width: 150,
      render: (v: string) => <StatusTag domain="po" value={v} />,
    },
    {
      title: t("pages.purchaseOrders.colTotal"),
      dataIndex: "total_amount",
      width: 120,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: t("pages.purchaseOrders.colSentAt"),
      dataIndex: "sent_at",
      width: 150,
      render: (v: string | null) => (v ? formatDateTime(v) : "—"),
    },
    {
      title: t("pages.purchaseOrders.colDeliveryDate"),
      dataIndex: "delivery_date",
      width: 130,
      render: (v: string | null | undefined) => (v ? formatDate(v) : "—"),
    },
  ];

  return (
    <Card title={t("pages.purchaseOrders.title")}>
      <Space wrap size="middle" style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder={t("pages.purchaseOrders.allStatuses")}
          style={{ width: 200 }}
          value={statusFilter}
          onChange={(v) => {
            setStatusFilter(v);
            list.setPage(1);
          }}
          options={PO_STATUSES.map((s) => ({ value: s, label: t(`status.po.${s}`) }))}
        />
        <Select
          allowClear
          placeholder={t("pages.purchaseOrders.allWholesalers")}
          style={{ width: 220 }}
          value={wholesalerFilter}
          onChange={(v) => {
            setWholesalerFilter(v);
            list.setPage(1);
          }}
          options={wholesalers.map((w) => ({
            value: w.id,
            label: pickName(lang, w.name_en, w.name_zh),
          }))}
        />
        <DatePicker
          placeholder={t("pages.purchaseOrders.filterDeliveryDate")}
          onChange={(d) => {
            setDeliveryFilter(d ? d.format("YYYY-MM-DD") : undefined);
            list.setPage(1);
          }}
        />
      </Space>
      <Table<PurchaseOrder>
        rowKey="id"
        loading={list.loading}
        dataSource={list.items}
        columns={columns}
        size="middle"
        onRow={(r) => ({ onClick: () => navigate(`/purchase-orders/${r.id}`), style: { cursor: "pointer" } })}
        pagination={{
          current: list.page,
          pageSize: list.pageSize,
          total: list.total,
          showSizeChanger: true,
          onChange: (p, ps) => {
            list.setPage(p);
            list.setPageSize(ps);
          },
        }}
      />
    </Card>
  );
}
