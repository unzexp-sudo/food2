import { useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Drawer,
  Empty,
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
import { LIST_POLL_MS, useDetail, useList, useMutate } from "../../api/hooks";
import { formatDate, formatDateTime, pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";

/**
 * INBOUND RECEIPTS — finance's gate.
 *
 * Two tables, in this order, and the order is the point:
 *
 *  1. **Awaiting receipt** — the purchase orders we are owed. This is what the
 *     nav badge counts, and it reads the same service function, so the number
 *     and the screen cannot disagree.
 *  2. **Receipts recorded** — history.
 *
 * The page used to render only table 2 while the badge counted table 1. The
 * badge read 2 and the screen was empty, which is indistinguishable from a
 * broken badge, and the operator had no way to tell which it was. Worse, the
 * PO picker in the drawer was fed by `GET /purchase-orders` — an ops/finance
 * route — so for the role that was supposed to receive goods it was a 403 that
 * a `.catch()` swallowed, leaving a permanently empty dropdown and no way to
 * record anything at all.
 *
 * Moved here from `pages/warehouse` on 2026-09-19 by decision: the receipt is
 * what creates stock and what the wholesaler's invoice is reconciled against.
 */
interface AwaitingPO {
  id: string;
  po_number: string;
  status: string;
  wholesaler_id: string;
  wholesaler_name_en: string;
  wholesaler_name_zh: string;
  delivery_date: string | null;
  sent_at: string | null;
  line_count: number;
  total_ordered: number;
  total_received: number;
  quantity_outstanding: number;
}

interface ReceiptLine {
  po_line_id: string;
  product_id: string;
  product_name_en: string;
  product_name_zh: string;
  quantity_ordered: number;
  quantity_received: number;
  quantity_damaged: number | null;
  discrepancy: boolean;
}

/** `GET /purchase-orders/{id}/received` — the receiving view of a PO. */
interface POReceiving {
  po_id: string;
  po_number: string;
  status: string;
  total_ordered: number;
  total_received: number;
  total_damaged: number;
  lines: ReceiptLine[];
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
  discrepancy: boolean;
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

  // The work. `page_size=100` (the API cap) because this is a queue, not a
  // report — nobody wants page 2 of what is owed, and the count is shown so a
  // truncated list is never silent.
  const awaiting = useDetail<Page<AwaitingPO>>(
    "/inbound-receipts/awaiting?page_size=100",
    { pollMs: LIST_POLL_MS },
  );

  // History. Polled too: a colleague may post a receipt while this is open.
  const [poFilter, setPoFilter] = useState<string | undefined>();
  const params = useMemo(() => (poFilter ? { po_id: poFilter } : {}), [poFilter]);
  const receipts = useList<InboundReceipt>("/inbound-receipts", params, {
    pollMs: LIST_POLL_MS,
  });

  const owed = awaiting.data?.items ?? [];
  const owedTotal = awaiting.data?.total ?? 0;

  // Filter options for the history table. Built from what is owed plus the POs
  // already on screen — best-effort on purpose: the list endpoint pages, so
  // "every PO ever" is not something this page can enumerate, and a filter that
  // claimed to would be the page-1 trap in a dropdown.
  const filterOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const p of owed) {
      seen.set(p.id, `${p.po_number} · ${pickName(lang, p.wholesaler_name_en, p.wholesaler_name_zh)}`);
    }
    for (const r of receipts.items) {
      if (!seen.has(r.po_id)) seen.set(r.po_id, r.po_number);
    }
    return [...seen].map(([value, label]) => ({ value, label }));
  }, [owed, receipts.items, lang]);

  // New inbound drawer
  const [newOpen, setNewOpen] = useState(false);
  const [poId, setPoId] = useState<string | undefined>();
  const [poDetail, setPoDetail] = useState<POReceiving | null>(null);
  const [poLoading, setPoLoading] = useState(false);
  const [lines, setLines] = useState<NewLine[]>([]);
  const { loading: mutateLoading, run } = useMutate();

  // View-lines drawer
  const [viewOpen, setViewOpen] = useState(false);
  const [viewDetail, setViewDetail] = useState<InboundReceiptDetail | null>(null);
  const [viewLoading, setViewLoading] = useState(false);
  const [viewError, setViewError] = useState<string | null>(null);

  const loadPO = (id: string) => {
    setPoId(id);
    setPoLoading(true);
    // `/received` rather than `/purchase-orders/{id}`: this form needs ordered
    // vs already-received per line, and has no business carrying cost prices.
    api
      .get<POReceiving>(`/purchase-orders/${id}/received`)
      .then((d) => {
        setPoDetail(d);
        setLines(
          d.lines.map((l) => ({
            // Default to what is still owed, not to the full ordered quantity —
            // on a second delivery against a partly received PO, defaulting to
            // "ordered" silently books the goods twice.
            po_line_id: l.po_line_id,
            quantity_received: Math.max(l.quantity_ordered - l.quantity_received, 0),
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

  const openReceive = (po: AwaitingPO) => {
    setNewOpen(true);
    loadPO(po.id);
  };

  const resetNew = () => {
    setPoId(undefined);
    setPoDetail(null);
    setLines([]);
  };

  const closeNew = () => {
    setNewOpen(false);
    resetNew();
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
    const ok = await run(() => api.post("/inbound-receipts", body), {
      success: t("pages.finance.inbound.saved"),
    });
    if (ok) {
      closeNew();
      // Both tables move: the PO leaves "awaiting" and appears in the history.
      awaiting.refresh();
      receipts.refresh();
    }
  };

  const handleView = (id: string) => {
    setViewOpen(true);
    setViewLoading(true);
    setViewDetail(null);
    setViewError(null);
    api
      .get<InboundReceiptDetail>(`/inbound-receipts/${id}`)
      .then((d) => setViewDetail(d))
      .catch(() => setViewError(t("common.error")))
      .finally(() => setViewLoading(false));
  };

  const awaitingCols: TableProps<AwaitingPO>["columns"] = [
    {
      title: t("pages.finance.inbound.colPONumber"),
      dataIndex: "po_number",
      width: 175,
    },
    {
      title: t("pages.finance.inbound.colWholesaler"),
      key: "wholesaler",
      render: (_: unknown, r: AwaitingPO) =>
        pickName(lang, r.wholesaler_name_en, r.wholesaler_name_zh) || "—",
    },
    {
      title: t("pages.finance.inbound.colDeliveryDate"),
      dataIndex: "delivery_date",
      width: 130,
      render: (v: string | null) => (v ? formatDate(v) : "—"),
    },
    {
      title: t("pages.finance.inbound.colStatus"),
      dataIndex: "status",
      width: 150,
      render: (v: string) => <StatusTag domain="po" value={v} />,
    },
    {
      title: t("pages.finance.inbound.colOrdered"),
      dataIndex: "total_ordered",
      width: 100,
      align: "right" as const,
    },
    {
      title: t("pages.finance.inbound.colReceived"),
      dataIndex: "total_received",
      width: 100,
      align: "right" as const,
    },
    {
      title: t("pages.finance.inbound.colOutstanding"),
      dataIndex: "quantity_outstanding",
      width: 100,
      align: "right" as const,
      render: (v: number) => <Typography.Text strong>{v}</Typography.Text>,
    },
    {
      title: t("pages.finance.inbound.colActions"),
      key: "actions",
      width: 120,
      render: (_: unknown, r: AwaitingPO) => (
        <Button size="small" type="primary" onClick={() => openReceive(r)}>
          {t("pages.finance.inbound.receive")}
        </Button>
      ),
    },
  ];

  const columns: TableProps<InboundReceipt>["columns"] = [
    {
      title: t("pages.finance.inbound.colReceiptNumber"),
      dataIndex: "receipt_number",
      width: 180,
    },
    {
      title: t("pages.finance.inbound.colPONumber"),
      dataIndex: "po_number",
      width: 170,
    },
    {
      title: t("pages.finance.inbound.colReceivedBy"),
      dataIndex: "received_by",
      width: 140,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.finance.inbound.colReceivedAt"),
      dataIndex: "received_at",
      width: 160,
      render: (v: string) => formatDateTime(v),
    },
    {
      title: t("pages.finance.inbound.colStatus"),
      dataIndex: "status",
      width: 150,
      render: (v: string) => <StatusTag domain="po" value={v} />,
    },
    {
      title: t("pages.finance.inbound.colLines"),
      dataIndex: "line_count",
      width: 80,
      align: "right" as const,
    },
    {
      title: t("pages.finance.inbound.colActions"),
      key: "actions",
      width: 120,
      render: (_: unknown, r: InboundReceipt) => (
        <Button size="small" onClick={() => handleView(r.id)}>
          {t("pages.finance.inbound.viewLines")}
        </Button>
      ),
    },
  ];

  const poLineCols: TableProps<ReceiptLine>["columns"] = [
    {
      title: t("pages.finance.inbound.colLineProduct"),
      key: "product",
      render: (_: unknown, r: ReceiptLine) =>
        pickName(lang, r.product_name_en, r.product_name_zh),
    },
    {
      title: t("pages.finance.inbound.colLineOrdered"),
      dataIndex: "quantity_ordered",
      width: 110,
      align: "right" as const,
    },
    {
      title: t("pages.finance.inbound.colLineReceived"),
      key: "qty_received",
      width: 150,
      render: (_: unknown, r: ReceiptLine) => (
        <InputNumber
          min={0}
          step={1}
          value={lines.find((l) => l.po_line_id === r.po_line_id)?.quantity_received ?? 0}
          onChange={(v) =>
            setLines((prev) =>
              prev.map((l) =>
                l.po_line_id === r.po_line_id ? { ...l, quantity_received: v ?? 0 } : l,
              ),
            )
          }
        />
      ),
    },
    {
      title: t("pages.finance.inbound.colLineDamaged"),
      key: "qty_damaged",
      width: 130,
      render: (_: unknown, r: ReceiptLine) => (
        <InputNumber
          min={0}
          step={1}
          value={lines.find((l) => l.po_line_id === r.po_line_id)?.quantity_damaged ?? 0}
          onChange={(v) =>
            setLines((prev) =>
              prev.map((l) =>
                l.po_line_id === r.po_line_id ? { ...l, quantity_damaged: v ?? 0 } : l,
              ),
            )
          }
        />
      ),
    },
    {
      title: t("pages.finance.inbound.colLineNotes"),
      key: "notes",
      width: 200,
      render: (_: unknown, r: ReceiptLine) => (
        <Input
          value={lines.find((l) => l.po_line_id === r.po_line_id)?.notes ?? ""}
          onChange={(e) =>
            setLines((prev) =>
              prev.map((l) =>
                l.po_line_id === r.po_line_id ? { ...l, notes: e.target.value } : l,
              ),
            )
          }
        />
      ),
    },
    {
      title: t("pages.finance.inbound.colLineDiscrepancy"),
      key: "discrepancy",
      width: 200,
      render: (_: unknown, r: ReceiptLine) => {
        const recv = lines.find((l) => l.po_line_id === r.po_line_id)?.quantity_received ?? 0;
        // Compared against what is STILL DUE, not against the original order:
        // on a second delivery the latter would flag every correct receipt.
        const due = Math.max(r.quantity_ordered - r.quantity_received, 0);
        const diff = recv - due;
        if (diff === 0) return null;
        const key = diff < 0 ? "discrepancyWarn" : "overWarn";
        return (
          <Alert
            type={diff < 0 ? "warning" : "error"}
            showIcon
            message={t(`pages.finance.inbound.${key}`, { r: recv, o: due })}
            style={{ padding: "2px 8px" }}
          />
        );
      },
    },
  ];

  const viewLineCols: TableProps<InboundReceiptLine>["columns"] = [
    {
      title: t("pages.finance.inbound.colLineProduct"),
      key: "product",
      render: (_: unknown, r: InboundReceiptLine) =>
        pickName(lang, r.product_name_en, r.product_name_zh),
    },
    {
      title: t("pages.finance.inbound.colLineOrdered"),
      dataIndex: "quantity_ordered",
      width: 110,
      align: "right" as const,
    },
    {
      title: t("pages.finance.inbound.colLineReceived"),
      dataIndex: "quantity_received",
      width: 110,
      align: "right" as const,
    },
    {
      title: t("pages.finance.inbound.colLineDamaged"),
      dataIndex: "quantity_damaged",
      width: 100,
      align: "right" as const,
      render: (v: number | null) => v ?? 0,
    },
    {
      title: t("pages.finance.inbound.colLineDiscrepancy"),
      dataIndex: "discrepancy",
      width: 110,
      align: "center" as const,
      render: (v: boolean) => (v ? "⚠" : "—"),
    },
    {
      title: t("pages.finance.inbound.colLineNotes"),
      dataIndex: "notes",
      render: (v: string | null) => v ?? "—",
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ display: "flex" }}>
      <Card
        title={`${t("pages.finance.inbound.awaitingTitle")}${owedTotal > 0 ? ` (${owedTotal})` : ""}`}
        extra={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              setNewOpen(true);
              resetNew();
            }}
          >
            {t("pages.finance.inbound.newInbound")}
          </Button>
        }
      >
        <Typography.Paragraph type="secondary" style={{ marginTop: -4 }}>
          {t("pages.finance.inbound.awaitingHint")}
        </Typography.Paragraph>
        <Table<AwaitingPO>
          rowKey="id"
          loading={awaiting.loading}
          dataSource={owed}
          columns={awaitingCols}
          size="middle"
          pagination={false}
          locale={{
            emptyText: <Empty description={t("pages.finance.inbound.awaitingEmpty")} />,
          }}
        />
        {owedTotal > owed.length && (
          <Typography.Text type="secondary">
            {t("pages.finance.inbound.awaitingTruncated", {
              shown: owed.length,
              total: owedTotal,
            })}
          </Typography.Text>
        )}
      </Card>

      <Card title={t("pages.finance.inbound.receiptsTitle")}>
        <Space wrap size="middle" style={{ marginBottom: 12 }}>
          <Select
            allowClear
            showSearch
            placeholder={t("pages.finance.inbound.filterPO")}
            style={{ width: 320 }}
            value={poFilter}
            onChange={(v) => {
              setPoFilter(v);
              receipts.setPage(1);
            }}
            options={filterOptions}
            optionFilterProp="label"
          />
        </Space>
        <Table<InboundReceipt>
          rowKey="id"
          loading={receipts.loading}
          dataSource={receipts.items}
          columns={columns}
          size="middle"
          pagination={{
            current: receipts.page,
            pageSize: receipts.pageSize,
            total: receipts.total,
            showSizeChanger: true,
            onChange: (p, ps) => {
              receipts.setPage(p);
              receipts.setPageSize(ps);
            },
          }}
        />
      </Card>

      <Drawer
        title={t("pages.finance.inbound.newInboundTitle")}
        open={newOpen}
        onClose={closeNew}
        width={960}
        footer={
          <Space style={{ float: "right" }}>
            <Button onClick={closeNew}>{t("common.cancel")}</Button>
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
          <Form.Item label={t("pages.finance.inbound.filterPO")} required>
            <Select
              showSearch
              placeholder={t("pages.finance.inbound.selectPO")}
              style={{ width: "100%" }}
              value={poId}
              onChange={(v) => (v ? loadPO(v) : resetNew())}
              options={owed.map((p) => ({
                value: p.id,
                label: `${p.po_number} · ${pickName(lang, p.wholesaler_name_en, p.wholesaler_name_zh)}`,
              }))}
              optionFilterProp="label"
              // Only POs that are actually receivable: anything else would be
              // refused by the API with a 409, and a picker that offers choices
              // which can only fail is the anti-pattern this repo keeps
              // rediscovering.
              notFoundContent={t("pages.finance.inbound.awaitingEmpty")}
            />
          </Form.Item>
        </Form>
        {poLoading ? (
          <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
        ) : poDetail ? (
          <>
            <Typography.Text strong>
              {t("pages.finance.inbound.poLines")} · {poDetail.po_number}
            </Typography.Text>
            <Table<ReceiptLine>
              rowKey="po_line_id"
              size="small"
              pagination={false}
              dataSource={poDetail.lines}
              columns={poLineCols}
              style={{ marginTop: 8 }}
            />
          </>
        ) : (
          <Typography.Text type="secondary">
            {t("pages.finance.inbound.noPOSelected")}
          </Typography.Text>
        )}
      </Drawer>

      <Drawer
        title={t("pages.finance.inbound.linesTitle")}
        open={viewOpen}
        onClose={() => setViewOpen(false)}
        width={820}
      >
        {viewLoading ? (
          <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
        ) : viewError ? (
          // Stated, not swallowed. This drawer used to 404 on every receipt
          // (the route did not exist) and render "No lines." — an error dressed
          // as an empty result.
          <Alert type="error" showIcon message={viewError} />
        ) : viewDetail ? (
          <Table<InboundReceiptLine>
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={viewDetail.lines}
            columns={viewLineCols}
          />
        ) : (
          <Typography.Text type="secondary">
            {t("pages.finance.inbound.noLines")}
          </Typography.Text>
        )}
      </Drawer>
    </Space>
  );
}
