import { useEffect, useState } from "react";
import { Alert, Button, Drawer, Form, Input, Select, Space, Typography } from "antd";
import { useLanguage } from "../../i18n";
import { api } from "../../api/client";
import { useMutate } from "../../api/hooks";
import { pickName } from "../../utils/format";
import type { Customer, Product } from "./types";

interface Props {
  open: boolean;
  customers: Customer[];
  products: Product[];
  onClose: () => void;
  onSaved: () => void;
}

/**
 * Bulk create (Guanmai "批量新建销售规格"): pick one customer + many products at
 * once; each selected product becomes one line (qty 1, product's default unit).
 * Submits a single quotation via POST /quotations.
 */
export default function BulkQuotationModal({ open, customers, products, onClose, onSaved }: Props) {
  const { t, lang } = useLanguage();
  const [form] = Form.useForm();
  const { loading, run } = useMutate();
  const [selected, setSelected] = useState<string[]>([]);

  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue({ service_time: "default" });
      setSelected([]);
    }
  }, [open, form]);

  const handleSubmit = async () => {
    let values: Record<string, unknown>;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    if (selected.length === 0) return;
    const lines = selected.map((pid) => {
      const prod = products.find((p) => p.id === pid);
      return {
        product_id: pid,
        product_display: prod ? `${prod.name_en} / ${prod.name_zh}` : undefined,
        quantity: 1,
        unit_id: prod?.default_unit_id ?? undefined,
        unit_price: undefined,
      };
    });
    const body = {
      customer_id: values.customer_id as string,
      external_name: (values.external_name as string) || null,
      service_time: (values.service_time as string) || "default",
      pricing_cycle: null,
      tags: values.tags
        ? String(values.tags)
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        : [],
      description: null,
      lines,
    };
    await run(() => api.post("/quotations", body), {
      onSuccess: () => {
        onSaved();
        onClose();
      },
    });
  };

  const serviceOptions = [
    { value: "default", label: t("pages.quotations.serviceTimeDefault") },
    { value: "morning", label: t("pages.quotations.serviceTimeMorning") },
    { value: "afternoon", label: t("pages.quotations.serviceTimeAfternoon") },
    { value: "evening", label: t("pages.quotations.serviceTimeEvening") },
  ];

  return (
    <Drawer
      title={t("pages.quotations.batchNew")}
      open={open}
      onClose={onClose}
      width={560}
      footer={
        <Space style={{ float: "right" }}>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button
            type="primary"
            loading={loading}
            disabled={selected.length === 0}
            onClick={handleSubmit}
          >
            {t("common.submit")}
          </Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical">
        <Form.Item name="customer_id" label={t("pages.intake.customer")} rules={[{ required: true }]}>
          <Select
            showSearch
            placeholder={t("pages.intake.selectCustomer")}
            optionFilterProp="label"
            options={customers.map((c) => ({
              value: c.id,
              label: pickName(lang, c.name_en, c.name_zh),
            }))}
          />
        </Form.Item>
        <Form.Item name="service_time" label={t("pages.quotations.serviceTime")}>
          <Select options={serviceOptions} />
        </Form.Item>
        <Form.Item name="external_name" label={t("pages.quotations.externalName")}>
          <Input placeholder={t("pages.quotations.externalNamePlaceholder")} />
        </Form.Item>
        <Form.Item name="tags" label={t("pages.quotations.tags")}>
          <Input placeholder={t("pages.quotations.tagsPlaceholder")} />
        </Form.Item>
        <Form.Item label={t("pages.quotations.batchProducts")} required>
          <Select
            mode="multiple"
            showSearch
            placeholder={t("pages.quotations.batchProductsPlaceholder")}
            value={selected}
            onChange={setSelected}
            optionFilterProp="label"
            options={products.map((p) => ({
              value: p.id,
              label: pickName(lang, p.name_en, p.name_zh),
            }))}
          />
        </Form.Item>
        {selected.length > 0 && (
          <Alert
            type="info"
            showIcon
            message={t("pages.quotations.batchSummary", { count: selected.length })}
          />
        )}
        <Typography.Text type="secondary">
          {t("pages.quotations.batchHint")}
        </Typography.Text>
      </Form>
    </Drawer>
  );
}
