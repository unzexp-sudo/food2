import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Table,
  Typography,
  type TableProps,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { api, type Page } from "../../api/client";
import { useList, useMutate } from "../../api/hooks";
import { formatDateTime, pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";

/** Entity shapes per AGENT_CONTRACTS §4. */
interface PurchaseOrder {
  id: string;
  po_number: string;
  wholesaler_name_en: string;
  wholesaler_name_zh: string;
  status: string;
}
interface POLine {
  id: string;
  product_id: string;
  product_name_en: string;
  product_name_zh: string;
  quantity_ordered: number;
  unit_code: string | null;
  quantity_received: number;
  cost_price: number;
}
interface PODetail extends PurchaseOrder {
  lines: POLine[];
}
interface InboundReceipt {
  id: string;
  receipt_number: string;
  po_id: string;
  po_number: string;
  received_by: string | null;
  received_at: string;
  status: string;
  line_count: number;
}
interface InboundReceiptLine {
  id: string;
  po_line_id: string;
  product_name_en: string;
  product_name_zh: string;
  quantity_ordered: number;
  quantity_received: number;
  quantity_damaged: number | null;
  discrepancy: number | null;
  notes: string | null;
}
interface InboundReceiptDetail extends InboundReceipt {
  lines: InboundReceiptLine[];
}

interface NewLine {
  po_line_id: string;
  quantity_received: number;
  quantity_damaged?: number;
  notes?: string;
}

export default function InboundPage() {
  const { t, lang } = useLanguage();
  const [poFilter, setPoFilter] = useState<string | undefined>();
  const params = useMemo(() => (poFilter ? { po_id: poFilter } : {}), [poFilter]);
  const list = useList<InboundReceipt>("/inbound-receipts", params);

  const [sentPOs, setSentPOs] = useState<PurchaseOrder[]>([]);
  useEffect(() => {
    api
      .get<Page<PurchaseOrder>>("/purchase-orders", { status: "sent", page: 1, page_size: 100 })
      .then((r) => setSentPOs(r.items))
      .catch(() => setSentPOs([]));
    api
      .get<Page<PurchaseOrder>>("/purchase-orders", {
        status: "partially_received",
        page: 1,
        page_size: 100,
      })
      .then((r) => setSentPOs((prev) => [...prev, ...r.items]))
      .catch(() => {});
  }, []);

  // New inbound drawer
  const [newOpen, setNewOpen] = useState(false);
  const [poId, setPoId] = useState<string | undefined>();
  const [poDetail, setPoDetail] = useState<PODetail | null>(null);
  const [poLoading, setPoLoading] = useState(false);
  const [lines, setLines] = useState<NewLine[]>([]);
  const { loading: mutateLoading, run } = useMutate();

  // View-lines drawer
  const [viewOpen, setViewOpen] = useState(false);
  const [viewDetail, setViewDetail] = useState<InboundReceiptDetail | null>(null);
  const [viewLoading, setViewLoading] = useState(false);

  const loadPO = (id: string) => {
    setPoId(id);
    setPoLoading(true);
    api
      .get<PODetail>(`/purchase-orders/${id}`)
      .then((d) => {
        setPoDetail(d);
        setLines(
          d.lines.map((l) => ({
            po_line_id: l.id,
            quantity_received: l.quantity_ordered - l.quantity_received,
            quantity_damaged: 0,
            notes: "",
          })),
        );
      })
      .catch(() => {
        setPoDetail(null);
        setLines([]);
      })
      .finally(() => setPoLoading(false));
  };

  const resetNew = () => {
    setPoId(undefined);
    setPoDetail(null);
    setLines([]);
  };

  const handleCreate = async () => {
    if (!poId) return;
    if (lines.length === 0) return;
    const hasInvalid = lines.some((l) => l.quantity_received == null || l.quantity_received < 0);
    if (hasInvalid) return;
    const body = {
      po_id: poId,
      lines: lines.map((l) => ({
        po_line_id: l.po_line_id,
        quantity_received: l.quantity_received,
        ...(l.quantity_damaged ? { quantity_damaged: l.quantity_damaged } : {}),
        ...(l.notes ? { notes: l.notes } : {}),
      })),
    };
    await run(() => api.post("/inbound-receipts", body), {
      success: t("pages.warehouse.inbound.saved"),
      onSuccess: () => {
        list.refresh();
        setNewOpen(false);
        resetNew();
      },
    });
  };

  const handleView = (id: string) => {
    setViewOpen(true);
    setViewLoading(true);
    setViewDetail(null);
    api
      .get<InboundReceiptDetail>(`/inbound-receipts/${id}`)
      .then((d) => setViewDetail(d))
      .catch(() => setViewDetail(null))
      .finally(() => setViewLoading(false));
  };

  const columns: TableProps<InboundReceipt>["columns"] = [
    {
      title: t("pages.warehouse.inbound.colReceiptNumber"),
      dataIndex: "receipt_number",
      width: 180,
    },
    {
      title: t("pages.warehouse.inbound.colPONumber"),
      dataIndex: "po_number",
      width: 170,
    },
    {
      title: t("pages.warehouse.inbound.colReceivedBy"),
      dataIndex: "received_by",
      width: 140,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.warehouse.inbound.colReceivedAt"),
      dataIndex: "received_at",
      width: 160,
      render: (v: string) => formatDateTime(v),
    },
    {
      title: t("pages.warehouse.inbound.colStatus"),
      dataIndex: "status",
      width: 150,
      render: (v: string) => <StatusTag domain="po" value={v} />,
    },
    {
      title: t("pages.warehouse.inbound.colLines"),
      dataIndex: "line_count",
      width: 80,
      align: "right" as const,
    },
    {
      title: t("pages.warehouse.inbound.colActions"),
      key: "actions",
      width: 120,
      render: (_: unknown, r: InboundReceipt) => (
        <Button size="small" onClick={() => handleView(r.id)}>
          {t("pages.warehouse.inbound.viewLines")}
        </Button>
      ),
    },
  ];

  const poLineCols: TableProps<POLine>["columns"] = [
    {
      title: t("pages.warehouse.inbound.colLineProduct"),
      key: "product",
      render: (_: unknown, r: POLine) => pickName(lang, r.product_name_en, r.product_name_zh),
    },
    {
      title: t("pages.warehouse.inbound.colLineOrdered"),
      dataIndex: "quantity_ordered",
      width: 110,
      align: "right" as const,
    },
    {
      title: t("pages.warehouse.inbound.colLineReceived"),
      key: "qty_received",
      width: 160,
      render: (_: unknown, r: POLine) => (
        <InputNumber
          min={0}
          step={1}
          value={lines.find((l) => l.po_line_id === r.id)?.quantity_received ?? 0}
          onChange={(v) =>
            setLines((prev) =>
              prev.map((l) =>
                l.po_line_id === r.id ? { ...l, quantity_received: v ?? 0 } : l,
              ),
            )
          }
        />
      ),
    },
    {
      title: t("pages.warehouse.inbound.colLineDamaged"),
      key: "qty_damaged",
      width: 130,
      render: (_: unknown, r: POLine) => (
        <InputNumber
          min={0}
          step={1}
          value={lines.find((l) => l.po_line_id === r.id)?.quantity_damaged ?? 0}
          onChange={(v) =>
            setLines((prev) =>
              prev.map((l) =>
                l.po_line_id === r.id ? { ...l, quantity_damaged: v ?? 0 } : l,
              ),
            )
          }
        />
      ),
    },
    {
      title: t("pages.warehouse.inbound.colLineNotes"),
      key: "notes",
      width: 200,
      render: (_: unknown, r: POLine) => (
        <Input
          value={lines.find((l) => l.po_line_id === r.id)?.notes ?? ""}
          onChange={(e) =>
            setLines((prev) =>
              prev.map((l) =>
                l.po_line_id === r.id ? { ...l, notes: e.target.value } : l,
              ),
            )
          }
        />
      ),
    },
    {
      title: t("pages.warehouse.inbound.colLineDiscrepancy"),
      key: "discrepancy",
      width: 200,
      render: (_: unknown, r: POLine) => {
        const recv = lines.find((l) => l.po_line_id === r.id)?.quantity_received ?? 0;
        const diff = recv - r.quantity_ordered;
        if (diff === 0) return null;
        if (diff < 0)
          return (
            <Alert
              type="warning"
              showIcon
              message={t("pages.warehouse.inbound.discrepancyWarn", {
                r: recv,
                o: r.quantity_ordered,
              })}
              style={{ padding: "2px 8px" }}
            />
          );
        return (
          <Alert
            type="error"
            showIcon
            message={t("pages.warehouse.inbound.overWarn", { r: recv, o: r.quantity_ordered })}
            style={{ padding: "2px 8px" }}
          />
        );
      },
    },
  ];

  const viewLineCols: TableProps<InboundReceiptLine>["columns"] = [
    {
      title: t("pages.warehouse.inbound.colLineProduct"),
      key: "product",
      render: (_: unknown, r: InboundReceiptLine) =>
        pickName(lang, r.product_name_en, r.product_name_zh),
    },
    {
      title: t("pages.warehouse.inbound.colLineOrdered"),
      dataIndex: "quantity_ordered",
      width: 110,
      align: "right" as const,
    },
    {
      title: t("pages.warehouse.inbound.colLineReceived"),
      dataIndex: "quantity_received",
      width: 110,
      align: "right" as const,
    },
    {
      title: t("pages.warehouse.inbound.colLineDamaged"),
      dataIndex: "quantity_damaged",
      width: 100,
      align: "right" as const,
      render: (v: number | null) => v ?? 0,
    },
    {
      title: t("pages.warehouse.inbound.colLineDiscrepancy"),
      dataIndex: "discrepancy",
      width: 110,
      align: "right" as const,
      render: (v: number | null) => (v == null ? "—" : v),
    },
    {
      title: t("pages.warehouse.inbound.colLineNotes"),
      dataIndex: "notes",
      render: (v: string | null) => v ?? "—",
    },
  ];

  return (
    <Card
      title={t("pages.warehouse.inbound.title")}
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setNewOpen(true)}>
          {t("pages.warehouse.inbound.newInbound")}
        </Button>
      }
    >
      <Space wrap size="middle" style={{ marginBottom: 12 }}>
        <Select
          allowClear
          showSearch
          placeholder={t("pages.warehouse.inbound.allPOs")}
          style={{ width: 280 }}
          value={poFilter}
          onChange={(v) => {
            setPoFilter(v);
            list.setPage(1);
          }}
          options={sentPOs.map((p) => ({
            value: p.id,
            label: `${p.po_number} · ${pickName(lang, p.wholesaler_name_en, p.wholesaler_name_zh)}`,
          }))}
          optionFilterProp="label"
        />
      </Space>
      <Table<InboundReceipt>
        rowKey="id"
        loading={list.loading}
        dataSource={list.items}
        columns={columns}
        size="middle"
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

      <Drawer
        title={t("pages.warehouse.inbound.newInboundTitle")}
        open={newOpen}
        onClose={() => {
          setNewOpen(false);
          resetNew();
        }}
        width={920}
        footer={
          <Space style={{ float: "right" }}>
            <Button
              onClick={() => {
                setNewOpen(false);
                resetNew();
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button
              type="primary"
              loading={mutateLoading}
              disabled={!poId || lines.length === 0}
              onClick={handleCreate}
            >
              {t("common.submit")}
            </Button>
          </Space>
        }
      >
        <Form layout="vertical">
          <Form.Item label={t("pages.warehouse.inbound.filterPO")} required>
            <Select
              showSearch
              placeholder={t("pages.warehouse.inbound.selectPO")}
              style={{ width: "100%" }}
              value={poId}
              onChange={(v) => (v ? loadPO(v) : resetNew())}
              options={sentPOs.map((p) => ({
                value: p.id,
                label: `${p.po_number} · ${pickName(lang, p.wholesaler_name_en, p.wholesaler_name_zh)}`,
              }))}
              optionFilterProp="label"
            />
          </Form.Item>
        </Form>
        {poLoading ? (
          <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
        ) : poDetail ? (
          <>
            <Typography.Text strong>{t("pages.warehouse.inbound.poLines")}</Typography.Text>
            <Table<POLine>
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={poDetail.lines}
              columns={poLineCols}
              style={{ marginTop: 8 }}
            />
          </>
        ) : (
          <Typography.Text type="secondary">
            {t("pages.warehouse.inbound.noPOSelected")}
          </Typography.Text>
        )}
      </Drawer>

      <Drawer
        title={t("pages.warehouse.inbound.linesTitle")}
        open={viewOpen}
        onClose={() => setViewOpen(false)}
        width={820}
      >
        {viewLoading ? (
          <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
        ) : viewDetail ? (
          <Table<InboundReceiptLine>
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={viewDetail.lines}
            columns={viewLineCols}
          />
        ) : (
          <Typography.Text type="secondary">{t("pages.warehouse.inbound.noLines")}</Typography.Text>
        )}
      </Drawer>
    </Card>
  );
}
