import { useEffect } from "react";
import { Button, Form, InputNumber, Modal, Select, Space } from "antd";
import { useLanguage } from "../../i18n";
import { api } from "../../api/client";
import { useMutate } from "../../api/hooks";
import { pickName } from "../../utils/format";
import type { InventoryMovement } from "./InventoryPage";

interface StockAdjustModalProps {
  open: boolean;
  item: InventoryMovement | null;
  onClose: () => void;
  onSuccess: () => void;
}

const REASONS = [
  { value: "damage", label: "Damage" },
  { value: "count", label: "Count" },
  { value: "transfer", label: "Transfer" },
];

export default function StockAdjustModal({
  open,
  item,
  onClose,
  onSuccess,
}: StockAdjustModalProps) {
  const { t, lang } = useLanguage();
  const [form] = Form.useForm();
  const { loading, run } = useMutate();

  useEffect(() => {
    if (open) form.resetFields();
  }, [open, form]);

  const handleOk = async () => {
    if (!item) return;
    let values: { reason: string; delta: number };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    // GAP: No dedicated adjust endpoint exists in the backend
    // (only GET /inventory and POST /inventory/loss). We PATCH the inventory
    // line as a fallback; this will 404 until a backend adjust endpoint is added.
    const ok = await run(
      () =>
        api.patch<InventoryMovement>(`/inventory/${item.id}`, {
          quantity_delta: values.delta,
          reason: values.reason,
        }),
      { success: "Stock adjusted" },
    );
    if (ok) {
      onSuccess();
      onClose();
    }
  };

  return (
    <Modal
      open={open}
      title={`Adjust · ${
        item ? pickName(lang, item.product_name_en, item.product_name_zh) : ""
      }`}
      onCancel={onClose}
      footer={
        <Space style={{ float: "right" }}>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="primary" loading={loading} onClick={handleOk}>
            {t("common.submit")}
          </Button>
        </Space>
      }
      destroyOnClose
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="reason"
          label="Reason"
          rules={[{ required: true, message: "Reason is required" }]}
        >
          <Select placeholder="Select reason" options={REASONS} />
        </Form.Item>
        <Form.Item
          name="delta"
          label="Quantity"
          rules={[{ required: true, message: "Quantity is required" }]}
        >
          <InputNumber step={1} style={{ width: "100%" }} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
