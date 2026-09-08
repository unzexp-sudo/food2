import { Form, Modal, Select, DatePicker, Typography } from "antd";
import { useLanguage } from "../../i18n";
import { api } from "../../api/client";
import { useMutate } from "../../api/hooks";
import { pickName } from "../../utils/format";

interface CustomerOption {
  id: string;
  name_en: string;
  name_zh: string;
}

/**
 * GAP NOTE: backend has NO `POST /statements/generate` (and no
 * `POST /invoices/generate` overload for customer + date range — that endpoint
 * only accepts `{ order_id }`). The closest existing endpoint that actually
 * returns a customer statement for a date range is the read endpoint
 * `GET /statements/customer/{id}?from&to`, so we call that through useMutate.
 */
interface GenerateResult {
  rows: unknown[];
  total: number;
}

interface Props {
  open: boolean;
  customers: CustomerOption[];
  loadingCustomers?: boolean;
  onClose: () => void;
  onGenerated: () => void;
}

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

    const ok = await run(
      () =>
        api.get<GenerateResult>(`/statements/customer/${customerId}`, { from: fromStr, to: toStr }),
      {
        success: "Statement generated",
        onSuccess: () => {
          form.resetFields();
          onGenerated();
        },
      },
    );
    if (ok) onClose();
  };

  return (
    <Modal
      title="Generate Statement"
      open={open}
      onCancel={onClose}
      onOk={handleOk}
      confirmLoading={loading}
      okText="Generate"
      destroyOnClose
    >
      <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
        Generate a customer statement for the selected date range.
      </Typography.Paragraph>
      <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
        <Form.Item
          name="customer"
          label="Customer"
          rules={[{ required: true, message: "Please select a customer" }]}
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
        <Form.Item
          name="from"
          label={t("pages.finance.statements.from")}
          rules={[{ required: true, message: "Please select a start date" }]}
        >
          <DatePicker style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item
          name="to"
          label="To"
          rules={[{ required: true, message: "Please select an end date" }]}
        >
          <DatePicker style={{ width: "100%" }} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
