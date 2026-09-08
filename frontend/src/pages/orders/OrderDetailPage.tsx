import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import {
  EditOutlined,
  DownloadOutlined,
  EyeOutlined,
  CheckOutlined,
  CloseOutlined,
  QuestionCircleOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { api, getApiError, type Page } from "../../api/client";
import { useDetail, useMutate } from "../../api/hooks";
import { formatDate, formatDateTime, pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";
import ConfidenceTag from "../../components/ConfidenceTag";
import client from "../../api/client";
import { parseStoredUser } from "../../types";

/** Entity shapes per AGENT_CONTRACTS §4. */
interface OrderLine {
  id: string;
  line_no: number;
  raw_text: string | null;
  product_id: string | null;
  product_display: string | null;
  quantity: number;
  unit_id: string | null;
  unit_code: string | null;
  unit_price: number | null;
  confidence: number | null;
  match_method: string | null;
}
interface Order {
  id: string;
  order_number: string;
  customer_id: string;
  customer_name_en: string;
  customer_name_zh: string;
  status: string;
  delivery_date: string;
  source_type: string | null;
  intake_document_id: string | null;
  standing_template_id: string | null;
  overall_confidence: number | null;
  confirmed_by: string | null;
  confirmed_at: string | null;
  notes: string | null;
  line_count: number;
  created_at: string;
  lines?: OrderLine[];
}

interface LineageLine {
  order_line_id: string;
  raw_text: string | null;
  product_name_en: string | null;
  product_name_zh: string | null;
  quantity: number;
  unit_code: string | null;
  batch_number: string | null;
  po_number: string | null;
  po_quantity: number | null;
  received_quantity: number | null;
  picked_quantity: number | null;
  delivered_quantity: number | null;
  invoice_id: string | null;
  invoice_number: string | null;
}
interface Lineage {
  order_id: string;
  order_number: string;
  lines: LineageLine[];
}

interface Product {
  id: string;
  name_en: string;
  name_zh: string;
  default_unit_id: string | null;
}
interface Unit {
  id: string;
  code: string;
}
interface ExtractionResponse {
  raw_output?: unknown;
  confidence?: number | null;
  [key: string]: unknown;
}

const EDITABLE_STATUSES = new Set(["draft", "pending_confirmation", "needs_clarification"]);

export default function OrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { t, lang } = useLanguage();
  const user = parseStoredUser();
  const canMutate = user?.role === "admin" || user?.role === "ops";

  const detail = useDetail<Order>(id ? `/orders/${id}` : null);
  const order = detail.data;

  const [activeTab, setActiveTab] = useState("details");

  // Lineage.
  const [lineage, setLineage] = useState<Lineage | null>(null);
  const [lineageLoading, setLineageLoading] = useState(false);
  useEffect(() => {
    if (!id) return;
    setLineageLoading(true);
    api
      .get<Lineage>(`/orders/${id}/lineage`)
      .then(setLineage)
      .catch(() => setLineage(null))
      .finally(() => setLineageLoading(false));
  }, [id, order?.status]);

  // Master data.
  const [products, setProducts] = useState<Product[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  useEffect(() => {
    api.get<Page<Product>>("/products", { page: 1, page_size: 200 }).then((r) => setProducts(r.items)).catch(() => setProducts([]));
    api.get<Page<Unit>>("/units", { page: 1, page_size: 50 }).then((r) => setUnits(r.items)).catch(() => setUnits([]));
  }, []);

  // Edit lines drawer.
  const [editOpen, setEditOpen] = useState(false);
  const [form] = Form.useForm();
  const { loading: mutateLoading, run } = useMutate();

  const openEdit = () => {
    if (!order?.lines) return;
    form.setFieldValue(
      "lines",
      order.lines.map((l) => ({
        product_id: l.product_id ?? undefined,
        product_display: l.product_display ?? "",
        quantity: l.quantity,
        unit_id: l.unit_id ?? undefined,
        unit_price: l.unit_price ?? undefined,
        raw_text: l.raw_text ?? "",
      })),
    );
    setEditOpen(true);
  };

  const saveLines = async () => {
    let values: { lines: Array<{ product_id?: string; product_display?: string; quantity: number; unit_id?: string; unit_price?: number; raw_text?: string }> };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const body = {
      lines: values.lines.map((l) => ({
        product_id: l.product_id || undefined,
        product_display: l.product_display || undefined,
        quantity: l.quantity,
        unit_id: l.unit_id || undefined,
        unit_price: l.unit_price ?? undefined,
        raw_text: l.raw_text ?? undefined,
      })),
    };
    await run(() => api.patch(`/orders/${id}/lines`, body), {
      success: t("pages.orders.linesSaved"),
      onSuccess: () => {
        setEditOpen(false);
        detail.refresh();
      },
    });
  };

  // Action handlers.
  const handleConfirm = () =>
    run(() => api.post(`/orders/${id}/confirm`, {}), {
      success: t("pages.orders.confirmed"),
      onSuccess: detail.refresh,
    });

  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const handleReject = async () => {
    if (!rejectReason.trim()) {
      return;
    }
    const ok = await run(() => api.post(`/orders/${id}/reject`, { reason: rejectReason }), {
      success: t("pages.orders.rejected"),
      onSuccess: () => {
        setRejectOpen(false);
        setRejectReason("");
        detail.refresh();
      },
    });
    void ok;
  };

  const [clarifyOpen, setClarifyOpen] = useState(false);
  const [clarifyNote, setClarifyNote] = useState("");
  const handleClarify = async () => {
    if (!clarifyNote.trim()) return;
    await run(() => api.post(`/orders/${id}/request-clarification`, { note: clarifyNote }), {
      success: t("pages.orders.clarificationRequested"),
      onSuccess: () => {
        setClarifyOpen(false);
        setClarifyNote("");
        detail.refresh();
      },
    });
  };

  const handleResubmit = () =>
    run(() => api.post(`/orders/${id}/resubmit`, {}), {
      success: t("pages.orders.resubmitted"),
      onSuccess: detail.refresh,
    });

  // Original document helpers.
  const handleDownloadOriginal = async () => {
    if (!order?.intake_document_id) return;
    try {
      const res = await client.get(`/intake/documents/${order.intake_document_id}/file`, { responseType: "blob" });
      const url = URL.createObjectURL(res.data as Blob);
      const a = document.createElement("a");
      a.href = url;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      window.alert(getApiError(err) ?? t("common.error"));
    }
  };

  // Extraction drawer.
  const [extractionOpen, setExtractionOpen] = useState(false);
  const [extractionData, setExtractionData] = useState<ExtractionResponse | null>(null);
  const [extractionLoading, setExtractionLoading] = useState(false);
  const [extractionError, setExtractionError] = useState<string | null>(null);
  const handleViewExtraction = async () => {
    // We need the job_id. Intake document_id is on the order; jobs are fetchable via documents list,
    // but the contract gives us GET /intake/extractions/{job_id}. The order entity does not embed
    // job_id directly — we resolve it from the intake document's most recent job.
    if (!order?.intake_document_id) return;
    setExtractionOpen(true);
    setExtractionData(null);
    setExtractionError(null);
    setExtractionLoading(true);
    try {
      // Try to find a job for this document.
      const jobs = await api.get<{ items: { id: string; document_id: string; status: string }[] }>(
        "/intake/jobs",
        { document_id: order.intake_document_id },
      );
      const job = jobs.items?.[0];
      if (!job) {
        setExtractionError(t("pages.intake.extractionEmpty"));
        return;
      }
      const data = await api.get<ExtractionResponse>(`/intake/extractions/${job.id}`);
      setExtractionData(data);
    } catch (err) {
      setExtractionError(getApiError(err) ?? t("common.error"));
    } finally {
      setExtractionLoading(false);
    }
  };

  if (detail.loading && !order) {
    return (
      <Card>
        <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
      </Card>
    );
  }
  if (!order) {
    return (
      <Card>
        <Typography.Text type="secondary">{t("common.noData")}</Typography.Text>
      </Card>
    );
  }

  const editable = canMutate && EDITABLE_STATUSES.has(order.status);
  const showActions = canMutate;

  const lineColumns = [
    { title: t("pages.orders.colLineNo"), dataIndex: "line_no", width: 50 },
    {
      title: t("pages.orders.colRawText"),
      dataIndex: "raw_text",
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.orders.colProduct"),
      key: "product",
      render: (_: unknown, l: OrderLine) => {
        if (l.product_id) {
          const p = products.find((x) => x.id === l.product_id);
          if (p) return pickName(lang, p.name_en, p.name_zh);
        }
        return l.product_display ?? "—";
      },
    },
    {
      title: t("pages.orders.colQuantity"),
      dataIndex: "quantity",
      width: 90,
      align: "right" as const,
    },
    {
      title: t("pages.orders.colUnit"),
      key: "unit",
      width: 90,
      render: (_: unknown, l: OrderLine) => l.unit_code ?? "—",
    },
    {
      title: t("pages.orders.colUnitPrice"),
      dataIndex: "unit_price",
      width: 100,
      align: "right" as const,
      render: (v: number | null) => (v == null ? "—" : v.toFixed(2)),
    },
    {
      title: t("pages.orders.colLineConfidence"),
      dataIndex: "confidence",
      width: 110,
      render: (v: number | null) => (v == null ? "—" : <ConfidenceTag value={v} />),
    },
    {
      title: t("pages.orders.colMatchMethod"),
      dataIndex: "match_method",
      width: 140,
      render: (v: string | null) =>
        v ? t(`pages.orders.matchMethod${v}`) : "—",
    },
  ];

  const lineageColumns = [
    {
      title: t("pages.orders.lineageColRawText"),
      dataIndex: "raw_text",
      render: (v: string | null) => v ?? <Muted>—</Muted>,
    },
    {
      title: t("pages.orders.lineageColProduct"),
      key: "product",
      render: (_: unknown, l: LineageLine) => pickName(lang, l.product_name_en, l.product_name_zh),
    },
    {
      title: t("pages.orders.lineageColQty"),
      dataIndex: "quantity",
      width: 70,
      align: "right" as const,
    },
    { title: t("pages.orders.lineageColUnit"), dataIndex: "unit_code", width: 70, render: (v: string | null) => v ?? <Muted>—</Muted> },
    {
      title: t("pages.orders.lineageColBatch"),
      dataIndex: "batch_number",
      width: 160,
      render: (v: string | null) => v ?? <Muted>—</Muted>,
    },
    {
      title: t("pages.orders.lineageColPO"),
      dataIndex: "po_number",
      width: 160,
      render: (v: string | null) => v ?? <Muted>—</Muted>,
    },
    {
      title: t("pages.orders.lineageColPOQty"),
      dataIndex: "po_quantity",
      width: 80,
      align: "right" as const,
      render: (v: number | null) => (v == null ? <Muted>—</Muted> : v),
    },
    {
      title: t("pages.orders.lineageColReceived"),
      dataIndex: "received_quantity",
      width: 80,
      align: "right" as const,
      render: (v: number | null) => (v == null ? <Muted>—</Muted> : v),
    },
    {
      title: t("pages.orders.lineageColPicked"),
      dataIndex: "picked_quantity",
      width: 80,
      align: "right" as const,
      render: (v: number | null) => (v == null ? <Muted>—</Muted> : v),
    },
    {
      title: t("pages.orders.lineageColDelivered"),
      dataIndex: "delivered_quantity",
      width: 80,
      align: "right" as const,
      render: (v: number | null) => (v == null ? <Muted>—</Muted> : v),
    },
    {
      title: t("pages.orders.lineageColInvoice"),
      dataIndex: "invoice_number",
      width: 160,
      render: (v: string | null) => v ?? <Muted>—</Muted>,
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card
        title={
          <Space>
            <Button onClick={() => navigate("/orders")}>{t("common.back")}</Button>
            <Typography.Text strong>{order.order_number}</Typography.Text>
            <StatusTag domain="order" value={order.status} />
          </Space>
        }
        extra={
          showActions ? (
            <Space wrap>
              {editable && (
                <Button icon={<EditOutlined />} onClick={openEdit}>
                  {t("pages.orders.editLines")}
                </Button>
              )}
              {EDITABLE_STATUSES.has(order.status) && (
                <Popconfirm
                  title={t("pages.orders.confirmConfirm")}
                  onConfirm={handleConfirm}
                  disabled={mutateLoading}
                >
                  <Button type="primary" icon={<CheckOutlined />} loading={mutateLoading}>
                    {t("pages.orders.confirmAction")}
                  </Button>
                </Popconfirm>
              )}
              {EDITABLE_STATUSES.has(order.status) && (
                <Button danger icon={<CloseOutlined />} onClick={() => setRejectOpen(true)} loading={mutateLoading}>
                  {t("pages.orders.rejectAction")}
                </Button>
              )}
              {EDITABLE_STATUSES.has(order.status) && (
                <Button icon={<QuestionCircleOutlined />} onClick={() => setClarifyOpen(true)} loading={mutateLoading}>
                  {t("pages.orders.requestClarification")}
                </Button>
              )}
              {order.status === "needs_clarification" && (
                <Button icon={<ReloadOutlined />} onClick={handleResubmit} loading={mutateLoading}>
                  {t("pages.orders.resubmit")}
                </Button>
              )}
            </Space>
          ) : null
        }
      >
        <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
          {
            key: "details",
            label: t("pages.orders.details"),
            children: (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Descriptions column={3} size="small" bordered>
                  <Descriptions.Item label={t("pages.orders.colCustomer")}>
                    {pickName(lang, order.customer_name_en, order.customer_name_zh)}
                  </Descriptions.Item>
                  <Descriptions.Item label={t("pages.orders.colDeliveryDate")}>
                    {formatDate(order.delivery_date)}
                  </Descriptions.Item>
                  <Descriptions.Item label={t("pages.orders.colSourceType")}>
                    {order.source_type ?? "—"}
                  </Descriptions.Item>
                  <Descriptions.Item label={t("pages.orders.colConfidence")}>
                    {order.overall_confidence == null ? "—" : <ConfidenceTag value={order.overall_confidence} />}
                  </Descriptions.Item>
                  <Descriptions.Item label={t("pages.orders.confirmedBy")}>
                    {order.confirmed_by ?? "—"}
                  </Descriptions.Item>
                  <Descriptions.Item label={t("pages.orders.confirmedAt")}>
                    {order.confirmed_at ? formatDateTime(order.confirmed_at) : "—"}
                  </Descriptions.Item>
                  <Descriptions.Item label={t("pages.orders.notes")} span={3}>
                    {order.notes ?? "—"}
                  </Descriptions.Item>
                </Descriptions>

                {order.intake_document_id ? (
                  <Card size="small" title={t("pages.orders.originalDocument")}>
                    <Space>
                      <Tag>{order.source_type ?? "—"}</Tag>
                      <Button
                        size="small"
                        icon={<DownloadOutlined />}
                        onClick={handleDownloadOriginal}
                      >
                        {t("pages.orders.viewOriginal")}
                      </Button>
                      <Button
                        size="small"
                        icon={<EyeOutlined />}
                        onClick={handleViewExtraction}
                      >
                        {t("pages.orders.viewExtraction")}
                      </Button>
                    </Space>
                  </Card>
                ) : (
                  <Typography.Text type="secondary">
                    {t("pages.orders.noOriginalDocument")}
                  </Typography.Text>
                )}

                <Card size="small" title={`${t("pages.orders.lines")} (${order.lines?.length ?? 0})`}>
                  <Table<OrderLine>
                    rowKey="id"
                    size="small"
                    dataSource={order.lines ?? []}
                    columns={lineColumns}
                    pagination={false}
                  />
                </Card>
              </Space>
            ),
          },
          {
            key: "lineage",
            label: t("pages.orders.lineage"),
            children: lineageLoading ? (
              <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
            ) : lineage && lineage.lines.length > 0 ? (
              <Table<LineageLine>
                rowKey="order_line_id"
                size="small"
                dataSource={lineage.lines}
                columns={lineageColumns}
                pagination={false}
                scroll={{ x: "max-content" }}
              />
            ) : (
              <Typography.Text type="secondary">{t("pages.orders.lineageEmpty")}</Typography.Text>
            ),
          },
        ]} />
      </Card>

      {/* Edit lines drawer */}
      <Drawer
        title={t("pages.orders.editLines")}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        width={860}
        footer={
          <Space style={{ float: "right" }}>
            <Button onClick={() => setEditOpen(false)}>{t("common.cancel")}</Button>
            <Button type="primary" loading={mutateLoading} onClick={saveLines}>
              {t("common.save")}
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph type="secondary">{t("pages.orders.editLinesHint")}</Typography.Paragraph>
        <Form form={form} layout="vertical">
          <Form.List name="lines">
            {(fields) => (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {fields.map((field) => (
                  <Row key={field.key} gutter={8}>
                    <Col span={6}>
                      <Form.Item {...field} name={[field.name, "product_id"]} label={t("pages.orders.newOrderLineProduct")}>
                        <Select
                          showSearch
                          allowClear
                          placeholder={t("pages.orders.newOrderLineProduct")}
                          options={products.map((p) => ({
                            value: p.id,
                            label: pickName(lang, p.name_en, p.name_zh),
                          }))}
                          optionFilterProp="label"
                        />
                      </Form.Item>
                    </Col>
                    <Col span={5}>
                      <Form.Item {...field} name={[field.name, "product_display"]} label={t("pages.orders.newOrderLineProductDisplay")}>
                        <Input />
                      </Form.Item>
                    </Col>
                    <Col span={4}>
                      <Form.Item {...field} name={[field.name, "quantity"]} label={t("pages.orders.newOrderLineQuantity")}>
                        <InputNumber min={0} style={{ width: "100%" }} />
                      </Form.Item>
                    </Col>
                    <Col span={4}>
                      <Form.Item {...field} name={[field.name, "unit_id"]} label={t("pages.orders.newOrderLineUnit")}>
                        <Select
                          allowClear
                          options={units.map((u) => ({ value: u.id, label: u.code }))}
                        />
                      </Form.Item>
                    </Col>
                    <Col span={5}>
                      <Form.Item {...field} name={[field.name, "unit_price"]} label={t("pages.orders.newOrderLineUnitPrice")}>
                        <InputNumber min={0} step={0.01} style={{ width: "100%" }} />
                      </Form.Item>
                    </Col>
                  </Row>
                ))}
              </div>
            )}
          </Form.List>
        </Form>
      </Drawer>

      {/* Reject modal */}
      <Modal
        title={t("pages.orders.rejectTitle")}
        open={rejectOpen}
        onCancel={() => setRejectOpen(false)}
        onOk={handleReject}
        confirmLoading={mutateLoading}
        okText={t("common.submit")}
        cancelText={t("common.cancel")}
        okButtonProps={{ danger: true }}
      >
        <Form layout="vertical">
          <Form.Item label={t("pages.orders.rejectReason")} required>
            <Input.TextArea
              rows={3}
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder={t("pages.orders.rejectReasonRequired")}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* Clarification modal */}
      <Modal
        title={t("pages.orders.requestClarificationTitle")}
        open={clarifyOpen}
        onCancel={() => setClarifyOpen(false)}
        onOk={handleClarify}
        confirmLoading={mutateLoading}
        okText={t("common.submit")}
        cancelText={t("common.cancel")}
      >
        <Form layout="vertical">
          <Form.Item label={t("pages.orders.clarificationNote")} required>
            <Input.TextArea
              rows={3}
              value={clarifyNote}
              onChange={(e) => setClarifyNote(e.target.value)}
              placeholder={t("pages.orders.clarificationNoteRequired")}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* Extraction drawer */}
      <Drawer
        title={t("pages.orders.viewExtraction")}
        open={extractionOpen}
        onClose={() => setExtractionOpen(false)}
        width={620}
      >
        {extractionLoading ? (
          <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
        ) : extractionError ? (
          <Alert type="error" message={extractionError} showIcon />
        ) : extractionData ? (
          <Space direction="vertical" style={{ width: "100%" }}>
            {typeof extractionData.confidence === "number" && (
              <div>
                <Typography.Text strong>{t("pages.intake.confidence")}: </Typography.Text>
                <ConfidenceTag value={extractionData.confidence} />
              </div>
            )}
            <pre
              style={{
                background: "#f5f5f5",
                padding: 12,
                borderRadius: 4,
                maxHeight: 480,
                overflow: "auto",
                fontSize: 12,
              }}
            >
              {JSON.stringify(extractionData, null, 2)}
            </pre>
          </Space>
        ) : (
          <Typography.Text type="secondary">{t("pages.intake.extractionEmpty")}</Typography.Text>
        )}
      </Drawer>
    </Space>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <Typography.Text type="secondary">{children}</Typography.Text>;
}
