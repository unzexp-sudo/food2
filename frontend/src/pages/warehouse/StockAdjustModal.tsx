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


export default function StockAdjustModal({
  open,
  item,
  onClose,
  onSuccess,
}: StockAdjustModalProps) {
  const { t, lang } = useLanguage();
  const [form] = Form.useForm();
  const { loading, run } = useMutate();

  const REASONS = [
    { value: "damage", label: t("pages.warehouse.inventory.stockAdjust.damage") },
    { value: "count", label: t("pages.warehouse.inventory.stockAdjust.count") },
    { value: "transfer", label: t("pages.warehouse.inventory.stockAdjust.transfer") },
  ];

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
    const ok = await run(
      () =>
        api.post<InventoryMovement>("/inventory/adjust", {
          product_id: item.product_id,
          quantity_delta: values.delta,
          reason: values.reason,
        }),
      { success: t("pages.warehouse.inventory.stockAdjust.success") },
    );
    if (ok) {
      onSuccess();
      onClose();
    }
  };

  return (
    <Modal
      open={open}
      title={`${t("common.adjust")} · ${
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
          label={t("pages.warehouse.inventory.stockAdjust.reason")}
          rules={[{ required: true, message: t("pages.warehouse.inventory.stockAdjust.reasonRequired") }]}
        >
          <Select placeholder={t("pages.warehouse.inventory.stockAdjust.selectReason")} options={REASONS} />
        </Form.Item>
        <Form.Item
          name="delta"
          label={t("pages.warehouse.inventory.stockAdjust.quantity")}
          rules={[{ required: true, message: t("pages.warehouse.inventory.stockAdjust.quantityRequired") }]}
        >
          <InputNumber step={1} style={{ width: "100%" }} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
