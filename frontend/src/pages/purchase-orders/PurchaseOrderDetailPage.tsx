import { useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import {
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Form,
  InputNumber,
  Popconfirm,
  Row,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import {
  ArrowLeftOutlined,
  EditOutlined,
  SendOutlined,
  CloseCircleOutlined,
} from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { api } from "../../api/client";
import { useDetail, useMutate } from "../../api/hooks";
import { formatDateTime, pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";
import { parseStoredUser } from "../../types";

interface POLine {
  id: string;
  po_id: string;
  product_id: string | null;
  product_name_en: string | null;
  product_name_zh: string | null;
  quantity_ordered: number;
  unit_id: string | null;
  unit_code: string | null;
  cost_price: number;
  quantity_received: number;
  source_order_count: number;
  // Some backends embed the source order ids/names directly on each line for the FE.
  source_orders?: Array<{ order_id: string; order_number: string }>;
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
  lines?: POLine[];
}

export default function PurchaseOrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { t, lang } = useLanguage();
  const user = parseStoredUser();
  const canMutate = user?.role === "admin" || user?.role === "ops";

  const detail = useDetail<PurchaseOrder>(id ? `/purchase-orders/${id}` : null);
  const po = detail.data;
  const { loading: mutateLoading, run } = useMutate();

  // Edit lines drawer.
  const [editOpen, setEditOpen] = useState(false);
  const [form] = Form.useForm();
  const openEdit = () => {
    if (!po?.lines) return;
    form.setFieldValue(
      "lines",
      po.lines.map((l) => ({
        quantity_ordered: l.quantity_ordered,
        cost_price: l.cost_price,
      })),
    );
    setEditOpen(true);
  };

  const saveLines = async () => {
    let values: { lines: Array<{ quantity_ordered: number; cost_price: number }> };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    await run(() => api.patch(`/purchase-orders/${id}/lines`, { lines: values.lines }), {
      success: t("pages.purchaseOrders.linesSaved"),
      onSuccess: () => {
        setEditOpen(false);
        detail.refresh();
      },
    });
  };

  const handleSend = () =>
    run(() => api.post(`/purchase-orders/${id}/send`, {}), {
      success: t("pages.purchaseOrders.sent"),
      onSuccess: detail.refresh,
    });

  const handleCancel = () =>
    run(() => api.post(`/purchase-orders/${id}/cancel`, {}), {
      success: t("pages.purchaseOrders.cancelled"),
      onSuccess: detail.refresh,
    });

  if (detail.loading && !po) {
    return (
      <Card>
        <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
      </Card>
    );
  }
  if (!po) {
    return (
      <Card>
        <Typography.Text type="secondary">{t("common.noData")}</Typography.Text>
      </Card>
    );
  }

  const isDraft = po.status === "draft" && canMutate;

  const lineColumns = [
    {
      title: t("pages.purchaseOrders.colLineProduct"),
      key: "product",
      render: (_: unknown, l: POLine) => pickName(lang, l.product_name_en, l.product_name_zh),
    },
    {
      title: t("pages.purchaseOrders.colLineQtyOrdered"),
      dataIndex: "quantity_ordered",
      width: 110,
      align: "right" as const,
    },
    {
      title: t("pages.purchaseOrders.colLineUnit"),
      dataIndex: "unit_code",
      width: 90,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.purchaseOrders.colLineCostPrice"),
      dataIndex: "cost_price",
      width: 120,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: t("pages.purchaseOrders.colLineQtyReceived"),
      dataIndex: "quantity_received",
      width: 120,
      align: "right" as const,
    },
    {
      title: t("pages.purchaseOrders.colLineSourceOrders"),
      key: "source_orders",
      render: (_: unknown, l: POLine) =>
        l.source_orders && l.source_orders.length > 0 ? (
          <Space size={4} wrap>
            {l.source_orders.map((o) => (
              <Link key={o.order_id} to={`/orders/${o.order_id}`}>
                <Tag color="blue">{o.order_number}</Tag>
              </Link>
            ))}
          </Space>
        ) : (
          <Typography.Text type="secondary">{l.source_order_count ?? 0}</Typography.Text>
        ),
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card
        title={
          <Space>
            <Button onClick={() => navigate("/purchase-orders")} icon={<ArrowLeftOutlined />}>
              {t("common.back")}
            </Button>
            <Typography.Text strong>{po.po_number}</Typography.Text>
            <StatusTag domain="po" value={po.status} />
          </Space>
        }
        extra={
          isDraft ? (
            <Space wrap>
              <Button icon={<EditOutlined />} onClick={openEdit}>
                {t("pages.purchaseOrders.editLines")}
              </Button>
              <Popconfirm
                title={t("pages.purchaseOrders.sendConfirm")}
                onConfirm={handleSend}
                disabled={mutateLoading}
              >
                <Button type="primary" icon={<SendOutlined />} loading={mutateLoading}>
                  {t("pages.purchaseOrders.send")}
                </Button>
              </Popconfirm>
              <Popconfirm
                title={t("pages.purchaseOrders.cancelConfirm")}
                onConfirm={handleCancel}
                disabled={mutateLoading}
              >
                <Button danger icon={<CloseCircleOutlined />} loading={mutateLoading}>
                  {t("pages.purchaseOrders.cancel")}
                </Button>
              </Popconfirm>
            </Space>
          ) : null
        }
      >
        <Descriptions column={3} size="small" bordered>
          <Descriptions.Item label={t("pages.purchaseOrders.wholesaler")}>
            {pickName(lang, po.wholesaler_name_en, po.wholesaler_name_zh)}
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.purchaseOrders.category")}>
            {pickName(lang, po.category_name_en, po.category_name_zh)}
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.purchaseOrders.status")}>
            <StatusTag domain="po" value={po.status} />
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.purchaseOrders.total")}>
            {po.total_amount.toFixed(2)}
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.purchaseOrders.sentAt")}>
            {po.sent_at ? formatDateTime(po.sent_at) : t("pages.purchaseOrders.notSent")}
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.consolidation.colCreatedAt")}>
            {formatDateTime(po.created_at)}
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.purchaseOrders.notes")} span={3}>
            {po.notes ?? "—"}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card size="small" title={`${t("pages.purchaseOrders.lines")} (${po.lines?.length ?? 0})`}>
        <Table<POLine>
          rowKey="id"
          size="small"
          dataSource={po.lines ?? []}
          columns={lineColumns}
          pagination={false}
          scroll={{ x: "max-content" }}
        />
      </Card>

      {/* Edit lines drawer */}
      <Drawer
        title={t("pages.purchaseOrders.editLines")}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        width={620}
        footer={
          <Space style={{ float: "right" }}>
            <Button onClick={() => setEditOpen(false)}>{t("common.cancel")}</Button>
            <Button type="primary" loading={mutateLoading} onClick={saveLines}>
              {t("common.save")}
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph type="secondary">
          {t("pages.purchaseOrders.editLinesHint")}
        </Typography.Paragraph>
        <Form form={form} layout="vertical">
          <Form.List name="lines">
            {(fields) =>
              fields.map((field) => (
                <Row key={field.key} gutter={8}>
                  <Col span={12}>
                    <Form.Item
                      {...field}
                      name={[field.name, "quantity_ordered"]}
                      label={t("pages.purchaseOrders.editLineQty")}
                    >
                      <InputNumber min={0} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item
                      {...field}
                      name={[field.name, "cost_price"]}
                      label={t("pages.purchaseOrders.editLineCost")}
                    >
                      <InputNumber min={0} step={0.01} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                </Row>
              ))
            }
          </Form.List>
        </Form>
      </Drawer>
    </Space>
  );
}
