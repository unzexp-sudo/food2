import { useEffect, useMemo, useState } from "react";
import {
  Button,
  Card,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
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
interface Customer {
  id: string;
  name_en: string;
  name_zh: string;
  status: string;
}
interface Order {
  id: string;
  order_number: string;
  customer_id: string;
  customer_name_en: string;
  customer_name_zh: string;
  status: string;
  delivery_date: string;
  line_count: number;
}
interface Invoice {
  id: string;
  invoice_number: string;
  customer_id: string;
  customer_name_en: string;
  customer_name_zh: string;
  order_id: string;
  order_number: string;
  status: string;
  total_amount: number;
  paid_amount: number;
  issued_at: string | null;
  created_at: string;
}
interface InvoiceLine {
  id: string;
  order_line_id: string | null;
  description: string;
  quantity: number;
  unit_price: number;
  amount: number;
}
interface Payment {
  id: string;
  number: string;
  invoice_id: string | null;
  direction: string;
  counterparty: string | null;
  amount: number;
  method: string | null;
  paid_at: string | null;
  note: string | null;
}
interface InvoiceDetail extends Invoice {
  lines: InvoiceLine[];
  payments: Payment[];
}

const INVOICE_STATUSES = ["draft", "issued", "partial", "paid", "void"];

export default function InvoicesPage() {
  const { t, lang } = useLanguage();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [customerFilter, setCustomerFilter] = useState<string | undefined>();
  const params = useMemo(
    () => ({
      ...(statusFilter ? { status: statusFilter } : {}),
      ...(customerFilter ? { customer_id: customerFilter } : {}),
    }),
    [statusFilter, customerFilter],
  );
  const list = useList<Invoice>("/invoices", params);

  const [customers, setCustomers] = useState<Customer[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  useEffect(() => {
    api.get<Page<Customer>>("/customers", { page: 1, page_size: 100 }).then((r) => setCustomers(r.items)).catch(() => setCustomers([]));
    api.get<Page<Order>>("/orders", { page: 1, page_size: 200 }).then((r) => setOrders(r.items)).catch(() => setOrders([]));
  }, []);

  // Detail drawer
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<InvoiceDetail | null>(null);

  const handleRow = (id: string) => {
    setDetailOpen(true);
    setDetail(null);
    api
      .get<InvoiceDetail>(`/invoices/${id}`)
      .then((d) => setDetail(d))
      .catch(() => setDetail(null));
  };

  // Generate-invoice modal
  const [genOpen, setGenOpen] = useState(false);
  const [orderId, setOrderId] = useState<string | undefined>();
  const { loading: mutateLoading, run } = useMutate();

  const handleGenerate = async () => {
    if (!orderId) return;
    const ok = await run(() => api.post("/invoices/generate", { order_id: orderId }), {
      success: t("pages.finance.invoices.generateSuccess"),
    });
    if (ok) {
      setGenOpen(false);
      setOrderId(undefined);
      list.refresh();
    }
  };

  // Payment modal
  const [payOpen, setPayOpen] = useState(false);
  const [payInvoiceId, setPayInvoiceId] = useState<string | null>(null);
  const [payAmount, setPayAmount] = useState<number>(0);
  const [payMethod, setPayMethod] = useState<string>("");
  const [payNote, setPayNote] = useState<string>("");

  const openPay = (invoiceId: string, total: number) => {
    setPayInvoiceId(invoiceId);
    setPayAmount(total);
    setPayMethod("");
    setPayNote("");
    setPayOpen(true);
  };

  const handlePay = async () => {
    if (!payInvoiceId) return;
    const ok = await run(
      () =>
        api.post(`/invoices/${payInvoiceId}/payments`, {
          amount: payAmount,
          method: payMethod,
          note: payNote,
        }),
      { success: t("pages.finance.invoices.paymentSuccess") },
    );
    if (ok) {
      setPayOpen(false);
      list.refresh();
      // Refresh detail if open
      if (detail?.id === payInvoiceId) {
        api.get<InvoiceDetail>(`/invoices/${payInvoiceId}`).then((d) => setDetail(d)).catch(() => {});
      }
    }
  };

  const columns: TableProps<Invoice>["columns"] = [
    {
      title: t("pages.finance.invoices.colInvoiceNumber"),
      dataIndex: "invoice_number",
      width: 170,
      render: (v: string, r: Invoice) => <a onClick={() => handleRow(r.id)}>{v}</a>,
    },
    {
      title: t("pages.finance.invoices.colCustomer"),
      key: "customer",
      render: (_: unknown, r: Invoice) => pickName(lang, r.customer_name_en, r.customer_name_zh),
    },
    {
      title: t("pages.finance.invoices.colOrder"),
      dataIndex: "order_number",
      width: 170,
    },
    {
      title: t("pages.finance.invoices.colStatus"),
      dataIndex: "status",
      width: 130,
      render: (v: string) => <StatusTag domain="invoice" value={v} />,
    },
    {
      title: t("pages.finance.invoices.colTotal"),
      dataIndex: "total_amount",
      width: 120,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: t("pages.finance.invoices.colPaid"),
      dataIndex: "paid_amount",
      width: 120,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: t("pages.finance.invoices.colIssuedAt"),
      dataIndex: "issued_at",
      width: 160,
      render: (v: string | null) => (v ? formatDateTime(v) : "—"),
    },
    {
      title: t("pages.finance.invoices.colActions"),
      key: "actions",
      width: 160,
      render: (_: unknown, r: Invoice) =>
        r.status !== "paid" && r.status !== "void" ? (
          <Button size="small" onClick={() => openPay(r.id, r.total_amount - r.paid_amount)}>
            {t("pages.finance.invoices.recordPayment")}
          </Button>
        ) : null,
    },
  ];

  const lineCols: TableProps<InvoiceLine>["columns"] = [
    {
      title: t("pages.finance.invoices.colLineDescription"),
      dataIndex: "description",
    },
    {
      title: t("pages.finance.invoices.colLineQty"),
      dataIndex: "quantity",
      width: 90,
      align: "right" as const,
    },
    {
      title: t("pages.finance.invoices.colLineUnitPrice"),
      dataIndex: "unit_price",
      width: 110,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: t("pages.finance.invoices.colLineAmount"),
      dataIndex: "amount",
      width: 110,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
  ];

  const paymentCols: TableProps<Payment>["columns"] = [
    { title: t("pages.finance.invoices.colPayNumber"), dataIndex: "number", width: 160 },
    {
      title: t("pages.finance.invoices.colPayMethod"),
      dataIndex: "method",
      width: 120,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.finance.invoices.colPayAmount"),
      dataIndex: "amount",
      width: 110,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: t("pages.finance.invoices.colPayPaidAt"),
      dataIndex: "paid_at",
      width: 150,
      render: (v: string | null) => (v ? formatDateTime(v) : "—"),
    },
    {
      title: t("pages.finance.invoices.colPayNote"),
      dataIndex: "note",
      render: (v: string | null) => v ?? "—",
    },
  ];

  return (
    <Card
      title={t("pages.finance.invoices.title")}
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setGenOpen(true)}>
          {t("pages.finance.invoices.newInvoice")}
        </Button>
      }
    >
      <Space wrap size="middle" style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder={t("pages.finance.invoices.allStatuses")}
          style={{ width: 200 }}
          value={statusFilter}
          onChange={(v) => {
            setStatusFilter(v);
            list.setPage(1);
          }}
          options={INVOICE_STATUSES.map((s) => ({ value: s, label: t(`status.invoice.${s}`) }))}
        />
        <Select
          allowClear
          showSearch
          placeholder={t("pages.finance.invoices.allCustomers")}
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
          optionFilterProp="label"
        />
      </Space>
      <Table<Invoice>
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
        title={t("pages.finance.invoices.details")}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={820}
      >
        {detail ? (
          <>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label={t("pages.finance.invoices.colInvoiceNumber")}>
                {detail.invoice_number}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.finance.invoices.colStatus")}>
                <StatusTag domain="invoice" value={detail.status} />
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.finance.invoices.colCustomer")}>
                {pickName(lang, detail.customer_name_en, detail.customer_name_zh)}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.finance.invoices.colOrder")}>
                {detail.order_number}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.finance.invoices.colTotal")}>
                {detail.total_amount.toFixed(2)}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.finance.invoices.colPaid")}>
                {detail.paid_amount.toFixed(2)}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.finance.invoices.colIssuedAt")}>
                {detail.issued_at ? formatDateTime(detail.issued_at) : "—"}
              </Descriptions.Item>
            </Descriptions>

            <Typography.Title level={5} style={{ marginTop: 16 }}>
              {t("pages.finance.invoices.lines")}
            </Typography.Title>
            <Table<InvoiceLine>
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={detail.lines}
              columns={lineCols}
            />

            <Typography.Title level={5} style={{ marginTop: 16 }}>
              {t("pages.finance.invoices.payments")}
            </Typography.Title>
            {detail.payments.length > 0 ? (
              <Table<Payment>
                rowKey="id"
                size="small"
                pagination={false}
                dataSource={detail.payments}
                columns={paymentCols}
              />
            ) : (
              <Typography.Text type="secondary">
                {t("pages.finance.invoices.noPayments")}
              </Typography.Text>
            )}
            {detail.status !== "paid" && detail.status !== "void" && (
              <Button
                style={{ marginTop: 12 }}
                onClick={() =>
                  openPay(detail.id, detail.total_amount - detail.paid_amount)
                }
              >
                {t("pages.finance.invoices.recordPayment")}
              </Button>
            )}
          </>
        ) : (
          <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
        )}
      </Drawer>

      <Modal
        title={t("pages.finance.invoices.newInvoiceTitle")}
        open={genOpen}
        onCancel={() => setGenOpen(false)}
        onOk={handleGenerate}
        confirmLoading={mutateLoading}
        okText={t("common.submit")}
      >
        <Form layout="vertical">
          <Form.Item
            label={t("pages.finance.invoices.selectOrder")}
            required
            rules={[{ required: true, message: t("pages.finance.invoices.orderRequired") }]}
          >
            <Select
              showSearch
              placeholder={t("pages.finance.invoices.selectOrder")}
              style={{ width: "100%" }}
              value={orderId}
              onChange={setOrderId}
              options={orders.map((o) => ({
                value: o.id,
                label: `${o.order_number} · ${pickName(lang, o.customer_name_en, o.customer_name_zh)}`,
              }))}
              optionFilterProp="label"
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={t("pages.finance.invoices.recordPaymentTitle")}
        open={payOpen}
        onCancel={() => setPayOpen(false)}
        onOk={handlePay}
        confirmLoading={mutateLoading}
        okText={t("common.submit")}
      >
        <Form layout="vertical">
          <Form.Item
            label={t("pages.finance.invoices.payAmount")}
            required
            rules={[{ required: true, message: t("pages.finance.invoices.payAmountRequired") }]}
          >
            <InputNumber
              min={0}
              step={0.01}
              value={payAmount}
              onChange={(v) => setPayAmount(v ?? 0)}
              style={{ width: "100%" }}
            />
          </Form.Item>
          <Form.Item label={t("pages.finance.invoices.payMethod")}>
            <Input value={payMethod} onChange={(e) => setPayMethod(e.target.value)} />
          </Form.Item>
          <Form.Item label={t("pages.finance.invoices.payNote")}>
            <Input.TextArea value={payNote} onChange={(e) => setPayNote(e.target.value)} rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
