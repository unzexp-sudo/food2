import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Drawer,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Table,
  type TableProps,
} from "antd";
import { PlusOutlined, MinusCircleOutlined } from "@ant-design/icons";
import { type Dayjs } from "dayjs";
import { useLanguage } from "../../i18n";
import { api, type Page } from "../../api/client";
import { LIST_POLL_MS, useList, useMutate } from "../../api/hooks";
import { formatDate, pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";
import ConfidenceTag from "../../components/ConfidenceTag";
import { parseStoredUser } from "../../types";

/** Entity shapes per AGENT_CONTRACTS §4. */
interface Customer {
  id: string;
  name_en: string;
  name_zh: string;
  status: string;
}
interface Product {
  id: string;
  sku: string;
  name_en: string;
  name_zh: string;
  default_unit_id: string | null;
  default_unit_code: string | null;
  is_active: boolean;
}
interface Unit {
  id: string;
  code: string;
  name_en: string;
  name_zh: string;
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
  overall_confidence: number | null;
  line_count: number;
  created_at: string;
}

const ORDER_STATUSES = [
  "draft",
  "pending_confirmation",
  "needs_clarification",
  "confirmed",
  "consolidated",
  "fulfilled",
  "invoiced",
  "rejected",
];

/**
 * Mirrors the backend `CONFIRMABLE` set in
 * `app/services/orders/orders.py` — the statuses a person can still confirm.
 *
 * These are the rows nobody has signed off on. Nothing confirms an order on
 * its own any more, so this set *is* the work queue: keep the two lists in
 * step or the UI will claim an order is settled when it is not.
 */
const AWAITING_HUMAN = ["draft", "pending_confirmation", "needs_clarification"];
const AWAITING_CSV = AWAITING_HUMAN.join(",");

function isAwaitingHuman(status: string): boolean {
  return AWAITING_HUMAN.includes(status);
}

interface NewLine {
  product_id?: string;
  product_display?: string;
  quantity: number;
  unit_id?: string;
  unit_price?: number;
  raw_text?: string;
}

export default function OrdersPage() {
  const { t, lang } = useLanguage();
  const navigate = useNavigate();
  const user = parseStoredUser();
  const canMutate = user?.role === "admin" || user?.role === "ops";

  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [customerFilter, setCustomerFilter] = useState<string | undefined>();
  const [deliveryFilter, setDeliveryFilter] = useState<string | undefined>();
  const [q, setQ] = useState<string>("");
  // "Unread" view: only the orders still waiting on a person.
  const [awaitingOnly, setAwaitingOnly] = useState(false);

  const params = useMemo(
    () => ({
      // Unconfirmed orders float to the top so the queue is the first thing
      // an operator sees.
      awaiting_first: true,
      // The queue toggle replaces the single-status filter rather than
      // stacking with it — "status=draft AND statuses=..." would intersect to
      // whatever overlaps, which silently hides orders.
      ...(awaitingOnly
        ? { statuses: AWAITING_CSV }
        : statusFilter
          ? { status: statusFilter }
          : {}),
      ...(customerFilter ? { customer_id: customerFilter } : {}),
      ...(deliveryFilter ? { delivery_date: deliveryFilter } : {}),
      ...(q ? { q } : {}),
    }),
    [statusFilter, customerFilter, deliveryFilter, q, awaitingOnly],
  );

  // Polled: orders are created by intake (a WeCom webhook) and change status
  // from other screens, so this list has to keep up on its own.
  const list = useList<Order>("/orders", params, { pollMs: LIST_POLL_MS });

  // Badge count for the "waiting for confirmation" banner. Polled: an order
  // can arrive from WeCom while this tab is open, and nobody should have to
  // hit reload to find out.
  const [awaitingCount, setAwaitingCount] = useState<number | null>(null);
  const refreshAwaitingCount = useCallback(() => {
    api
      .get<{ awaiting_confirmation: number }>("/orders/confirm-count")
      .then((r) => setAwaitingCount(r.awaiting_confirmation))
      .catch(() => setAwaitingCount(null));
  }, []);
  useEffect(() => {
    refreshAwaitingCount();
    const id = setInterval(refreshAwaitingCount, 30_000);
    return () => clearInterval(id);
  }, [refreshAwaitingCount, list.items]);

  const [customers, setCustomers] = useState<Customer[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  useEffect(() => {
    api.get<Page<Customer>>("/customers", { page: 1, page_size: 100 }).then((r) => setCustomers(r.items)).catch(() => setCustomers([]));
    api.get<Page<Product>>("/products", { page: 1, page_size: 100 }).then((r) => setProducts(r.items)).catch(() => setProducts([]));
    api.get<Page<Unit>>("/units", { page: 1, page_size: 50 }).then((r) => setUnits(r.items)).catch(() => setUnits([]));
  }, []);

  // New order drawer.
  const [newOpen, setNewOpen] = useState(false);
  const [form] = Form.useForm();
  const { loading: mutateLoading, run } = useMutate();

  // The server requires `lines` to hold at least one entry
  // (`OrderCreate.lines = Field(min_length=1)`), so an empty list is a
  // guaranteed 422. `handleCreate` reads `values.lines.map(...)` unguarded, so
  // submitting with every line removed also threw a client-side TypeError
  // before the request was even sent. Disable Submit and say what is missing —
  // the "Add line" button under the list is the way out.
  const noLines =
    (Form.useWatch<NewLine[] | undefined>("lines", form) ?? []).length === 0;

  const handleCreate = async () => {
    let values: { customer_id: string; delivery_date: Dayjs; notes?: string; lines: NewLine[] };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const body = {
      customer_id: values.customer_id,
      delivery_date: values.delivery_date.format("YYYY-MM-DD"),
      notes: values.notes ?? "",
      lines: values.lines.map((l) => ({
        product_id: l.product_id || undefined,
        product_display: l.product_display || undefined,
        quantity: l.quantity,
        unit_id: l.unit_id || undefined,
        unit_price: l.unit_price ?? undefined,
        raw_text: l.raw_text ?? undefined,
      })),
    };
    await run(() => api.post("/orders", body), {
      success: t("common.success"),
      onSuccess: () => {
        list.refresh();
        setNewOpen(false);
        form.resetFields();
      },
    });
  };

  const columns: TableProps<Order>["columns"] = [
    {
      title: t("pages.orders.colOrderNumber"),
      dataIndex: "order_number",
      width: 170,
      render: (v: string, r: Order) => (
        <a onClick={() => navigate(`/orders/${r.id}`)}>{v}</a>
      ),
    },
    {
      title: t("pages.orders.colCustomer"),
      key: "customer",
      render: (_: unknown, r: Order) => pickName(lang, r.customer_name_en, r.customer_name_zh),
    },
    {
      title: t("pages.orders.colStatus"),
      dataIndex: "status",
      width: 150,
      render: (v: string) => <StatusTag domain="order" value={v} />,
    },
    {
      title: t("pages.orders.colDeliveryDate"),
      dataIndex: "delivery_date",
      width: 130,
      render: (v: string) => formatDate(v),
    },
    {
      title: t("pages.orders.colSourceType"),
      dataIndex: "source_type",
      width: 110,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.orders.colConfidence"),
      dataIndex: "overall_confidence",
      width: 120,
      render: (v: number | null) => (v == null ? "—" : <ConfidenceTag value={v} />),
    },
    {
      title: t("pages.orders.colLineCount"),
      dataIndex: "line_count",
      width: 80,
      align: "right" as const,
    },
    {
      title: t("pages.orders.colCreatedAt"),
      dataIndex: "created_at",
      width: 150,
      render: (v: string) => formatDate(v),
    },
  ];

  const filterRow = (
    <Space wrap size="middle" style={{ marginBottom: 12 }}>
      <Select
        allowClear
        placeholder={t("pages.orders.allStatuses")}
        style={{ width: 200 }}
        // The queue toggle already filters by status; leaving this live would
        // look like it is doing something when it is being ignored.
        disabled={awaitingOnly}
        value={awaitingOnly ? undefined : statusFilter}
        onChange={(v) => {
          setStatusFilter(v);
          list.setPage(1);
        }}
        options={ORDER_STATUSES.map((s) => ({ value: s, label: t(`status.order.${s}`) }))}
      />
      <Select
        allowClear
        placeholder={t("pages.orders.allCustomers")}
        style={{ width: 220 }}
        value={customerFilter}
        onChange={(v) => {
          setCustomerFilter(v);
          list.setPage(1);
        }}
        options={customers.map((c) => ({
          value: c.id,
          label: pickName(lang, c.name_en, c.name_zh),
        }))}
      />
      <DatePicker
        placeholder={t("pages.orders.filterDeliveryDate")}
        onChange={(d) => {
          setDeliveryFilter(d ? d.format("YYYY-MM-DD") : undefined);
          list.setPage(1);
        }}
      />
      <Input.Search
        allowClear
        placeholder={t("pages.orders.filterKeyword")}
        style={{ width: 200 }}
        onSearch={(v) => {
          setQ(v);
          list.setPage(1);
        }}
      />
    </Space>
  );

  return (
    <Card
      title={t("pages.orders.title")}
      extra={
        canMutate ? (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setNewOpen(true)}>
            {t("pages.orders.newOrder")}
          </Button>
        ) : null
      }
    >
      {awaitingCount ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={t("pages.orders.awaitingBanner", { count: awaitingCount })}
          description={t("pages.orders.awaitingHint")}
          action={
            <Button size="small" onClick={() => setAwaitingOnly((v) => !v)}>
              {awaitingOnly ? t("pages.intake.showAll") : t("pages.intake.pendingOnly")}
            </Button>
          }
        />
      ) : null}
      {filterRow}
      <Table<Order>
        rowKey="id"
        loading={list.loading}
        dataSource={list.items}
        columns={columns}
        size="middle"
        rowClassName={(r: Order) => (isAwaitingHuman(r.status) ? "order-row-awaiting" : "")}
        onRow={(r) => ({ onClick: () => navigate(`/orders/${r.id}`), style: { cursor: "pointer" } })}
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
        title={t("pages.orders.newOrderTitle")}
        open={newOpen}
        onClose={() => setNewOpen(false)}
        width={720}
        footer={
          <Space direction="vertical" style={{ width: "100%" }} size={8}>
            {noLines ? (
              <Typography.Text type="warning">{t("common.noLines")}</Typography.Text>
            ) : null}
            <Space style={{ width: "100%", justifyContent: "flex-end" }}>
              <Button onClick={() => setNewOpen(false)}>{t("common.cancel")}</Button>
              <Button
                type="primary"
                loading={mutateLoading}
                disabled={noLines}
                onClick={handleCreate}
              >
                {t("common.submit")}
              </Button>
            </Space>
          </Space>
        }
      >
        <Form form={form} layout="vertical" initialValues={{ lines: [{ quantity: 1 }] }}>
          <Space.Compact block>
            <Form.Item
              name="customer_id"
              label={t("pages.intake.customer")}
              rules={[{ required: true }]}
              style={{ flex: 1, marginRight: 8 }}
            >
              <Select
                showSearch
                placeholder={t("pages.intake.selectCustomer")}
                options={customers.map((c) => ({
                  value: c.id,
                  label: pickName(lang, c.name_en, c.name_zh),
                }))}
                optionFilterProp="label"
              />
            </Form.Item>
            <Form.Item
              name="delivery_date"
              label={t("pages.intake.deliveryDate")}
              rules={[{ required: true }]}
              style={{ flex: 1 }}
            >
              <DatePicker style={{ width: "100%" }} />
            </Form.Item>
          </Space.Compact>
          <Form.Item name="notes" label={t("pages.orders.notes")}>
            <Input.TextArea rows={2} />
          </Form.Item>

          <Typography.Text strong>{t("pages.orders.lines")}</Typography.Text>
          <Form.List name="lines">
            {(fields, { add, remove }) => (
              <>
                {fields.map((field) => (
                  <div
                    key={field.key}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "1fr 1fr 110px 120px 120px 32px",
                      gap: 8,
                      alignItems: "end",
                      marginBottom: 8,
                    }}
                  >
                    <Form.Item
                      {...field}
                      name={[field.name, "product_id"]}
                      label={t("pages.orders.newOrderLineProduct")}
                    >
                      <Select
                        showSearch
                        allowClear
                        placeholder={t("pages.orders.newOrderLineProduct")}
                        options={products.map((p) => ({
                          value: p.id,
                          label: pickName(lang, p.name_en, p.name_zh),
                        }))}
                        optionFilterProp="label"
                        onChange={(v, opt) => {
                          if (v && opt) {
                            const prod = products.find((p) => p.id === v);
                            // auto-fill unit_id when a product is chosen
                            if (prod?.default_unit_id) {
                              const lines = form.getFieldValue("lines") as NewLine[];
                              lines[field.name] = {
                                ...lines[field.name],
                                unit_id: prod.default_unit_id ?? undefined,
                              };
                              form.setFieldValue("lines", lines);
                            }
                          }
                        }}
                      />
                    </Form.Item>
                    <Form.Item
                      {...field}
                      name={[field.name, "product_display"]}
                      label={t("pages.orders.newOrderLineProductDisplay")}
                    >
                      <Input placeholder={t("pages.orders.productDisplayPlaceholder")} />
                    </Form.Item>
                    <Form.Item
                      {...field}
                      name={[field.name, "quantity"]}
                      label={t("pages.orders.newOrderLineQuantity")}
                      rules={[{ required: true, message: t("pages.orders.qtyRequired") }]}
                    >
                      <InputNumber min={0} step={1} style={{ width: "100%" }} />
                    </Form.Item>
                    <Form.Item
                      {...field}
                      name={[field.name, "unit_id"]}
                      label={t("pages.orders.newOrderLineUnit")}
                    >
                      <Select
                        allowClear
                        placeholder={t("pages.orders.newOrderLineUnit")}
                        options={units.map((u) => ({ value: u.id, label: u.code }))}
                      />
                    </Form.Item>
                    <Form.Item
                      {...field}
                      name={[field.name, "unit_price"]}
                      label={t("pages.orders.newOrderLineUnitPrice")}
                    >
                      <InputNumber min={0} step={0.01} style={{ width: "100%" }} />
                    </Form.Item>
                    <Button
                      type="text"
                      danger
                      icon={<MinusCircleOutlined />}
                      onClick={() => remove(field.name)}
                      style={{ marginBottom: 24 }}
                    />
                  </div>
                ))}
                <Button type="dashed" icon={<PlusOutlined />} onClick={() => add({ quantity: 1 })} block>
                  {t("pages.orders.addLine")}
                </Button>
              </>
            )}
          </Form.List>
        </Form>
      </Drawer>
    </Card>
  );
}

// Local Typography.Strong shim (antd Typography.Strong exists; keep import minimal)
import { Typography } from "antd";
