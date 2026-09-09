import { useState } from "react";
import {
  DatePicker,
  Form,
  Modal,
  Select,
  Space,
  Statistic,
  Table,
  Typography,
  type TableProps,
} from "antd";
import { useLanguage } from "../../i18n";
import { api } from "../../api/client";
import { useMutate } from "../../api/hooks";
import { formatDate, pickName } from "../../utils/format";

interface CustomerOption {
  id: string;
  name_en: string;
  name_zh: string;
}

interface StatementRow {
  order_id: string;
  order_number: string | null;
  delivery_date: string | null;
  description: string;
  quantity: number;
  unit_price: number;
  amount: number;
}

interface GenerateResult {
  rows: StatementRow[];
  total: number;
}

interface Props {
  open: boolean;
  customers: CustomerOption[];
  loadingCustomers?: boolean;
  onClose: () => void;
  onGenerated?: () => void;
}

/**
 * Generates an AR statement via POST /statements/generate (new backend endpoint
 * added for Guanmai MVP). Renders the computed rows + total inline so the
 * "Generate" action is genuinely functional rather than a silent GET.
 */
export default function StatementGenerateModal({
  open,
  customers,
  loadingCustomers,
  onClose,
  onGenerated,
}: Props) {
  const { t, lang } = useLanguage();
  const [form] = Form.useForm();
  const { loading, run } = useMutate();
  const [result, setResult] = useState<GenerateResult | null>(null);

  const handleOk = async () => {
    let values: { customer: string; from: unknown; to: unknown };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const customerId = values.customer as string;
    const fromStr = (values.from as { format: (f: string) => string })?.format("YYYY-MM-DD") ?? "";
    const toStr = (values.to as { format: (f: string) => string })?.format("YYYY-MM-DD") ?? "";
    if (!customerId || !fromStr || !toStr) return;

    await run(
      async () => {
        const res = await api.post<GenerateResult>("/statements/generate", {
          party_type: "customer",
          party_id: customerId,
          from_date: fromStr,
          to_date: toStr,
        });
        setResult(res);
      },
      {
        success: t("pages.finance.statements.generated"),
        onSuccess: onGenerated,
      },
    );
  };

  const handleClose = () => {
    setResult(null);
    form.resetFields();
    onClose();
  };

  const columns: TableProps<StatementRow>["columns"] = [
    { title: t("pages.finance.statements.colOrder"), dataIndex: "order_number", width: 170, render: (v: string | null) => v ?? "—" },
    {
      title: t("pages.finance.statements.colDeliveryDate"),
      dataIndex: "delivery_date",
      width: 130,
      render: (v: string | null) => (v ? formatDate(v) : "—"),
    },
    { title: t("pages.finance.statements.colDescription"), dataIndex: "description" },
    { title: t("pages.finance.statements.colQty"), dataIndex: "quantity", width: 90, align: "right" as const },
    {
      title: t("pages.finance.statements.colUnitPrice"),
      dataIndex: "unit_price",
      width: 110,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: t("pages.finance.statements.colAmount"),
      dataIndex: "amount",
      width: 120,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
  ];

  return (
    <Modal
      title={t("pages.finance.statements.title")}
      open={open}
      onCancel={handleClose}
      onOk={handleOk}
      confirmLoading={loading}
      okText={t("common.generate")}
      width={760}
      destroyOnClose
    >
      <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
        {t("pages.finance.statements.hint")}
      </Typography.Paragraph>
      <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
        <Form.Item
          name="customer"
          label={t("pages.finance.statements.customer")}
          rules={[{ required: true, message: t("pages.finance.statements.validationCustomer") }]}
        >
          <Select
            showSearch
            allowClear
            placeholder={t("pages.finance.statements.selectCustomer")}
            loading={loadingCustomers}
            options={customers.map((c) => ({
              value: c.id,
              label: pickName(lang, c.name_en, c.name_zh),
            }))}
            optionFilterProp="label"
          />
        </Form.Item>
        <Space size="large" style={{ display: "flex" }}>
          <Form.Item
            name="from"
            label={t("pages.finance.statements.from")}
            rules={[{ required: true, message: t("pages.finance.statements.validationFrom") }]}
            style={{ flex: 1 }}
          >
            <DatePicker style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="to"
            label={t("pages.finance.statements.to")}
            rules={[{ required: true, message: t("pages.finance.statements.validationTo") }]}
            style={{ flex: 1 }}
          >
            <DatePicker style={{ width: "100%" }} />
          </Form.Item>
        </Space>
      </Form>

      {result && (
        <div style={{ marginTop: 16 }}>
          <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 8 }}>
            <Typography.Text strong>{t("pages.finance.statements.title")}</Typography.Text>
            <Statistic
              title={t("pages.finance.statements.total")}
              value={result.total}
              precision={2}
            />
          </Space>
          <Table<StatementRow>
            rowKey={(_, i) => String(i)}
            size="small"
            pagination={false}
            columns={columns}
            dataSource={result.rows}
            locale={{ emptyText: t("pages.finance.statements.noData") }}
          />
        </div>
      )}
    </Modal>
  );
}
