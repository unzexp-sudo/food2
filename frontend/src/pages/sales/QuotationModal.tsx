import { useEffect } from "react";
import { Drawer, Form, Select, Input, InputNumber, Button, Space, Typography } from "antd";
import { PlusOutlined, MinusCircleOutlined } from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { api } from "../../api/client";
import { useMutate } from "../../api/hooks";
import { pickName } from "../../utils/format";
import type { Customer, Product, Unit, Quotation } from "./types";

interface Props {
  open: boolean;
  initial: Quotation | null;
  customers: Customer[];
  products: Product[];
  units: Unit[];
  onClose: () => void;
  onSaved: () => void;
}

interface FormLine {
  product_id?: string;
  product_display?: string;
  quantity: number;
  unit_id?: string;
  unit_price?: number;
}

function serviceTimeOptions(t: (k: string) => string) {
  return [
    { value: "default", label: t("pages.quotations.serviceTimeDefault") },
    { value: "morning", label: t("pages.quotations.serviceTimeMorning") },
    { value: "afternoon", label: t("pages.quotations.serviceTimeAfternoon") },
    { value: "evening", label: t("pages.quotations.serviceTimeEvening") },
  ];
}

function pricingCycleOptions(t: (k: string) => string) {
  return [
    { value: "daily", label: t("pages.quotations.pricingDaily") },
    { value: "weekly", label: t("pages.quotations.pricingWeekly") },
    { value: "monthly", label: t("pages.quotations.pricingMonthly") },
  ];
}

/** Create / edit a quotation. Reused for both "New quotation" and "Batch new". */
export default function QuotationModal({
  open,
  initial,
  customers,
  products,
  units,
  onClose,
  onSaved,
}: Props) {
  const { t, lang } = useLanguage();
  const [form] = Form.useForm();
  const { loading, run } = useMutate();

  useEffect(() => {
    if (!open) return;
    if (initial) {
      form.setFieldsValue({
        customer_id: initial.customer_id,
        external_name: initial.external_name ?? "",
        service_time: initial.service_time,
        pricing_cycle: initial.pricing_cycle ?? undefined,
        tags: initial.tags?.join(", "),
        description: initial.description ?? "",
        lines: initial.lines.map((l) => ({
          product_id: l.product_id ?? undefined,
          product_display: l.product_display ?? undefined,
          quantity: l.quantity,
          unit_id: l.unit_id ?? undefined,
          unit_price: l.unit_price ?? undefined,
        })),
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ service_time: "default", lines: [{ quantity: 1 }] });
    }
  }, [open, initial, form]);

  const handleSubmit = async () => {
    let values: Record<string, unknown>;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const lines = (Array.isArray(values.lines) ? values.lines : []) as FormLine[];
    const body = {
      customer_id: values.customer_id as string,
      external_name: (values.external_name as string) || null,
      service_time: (values.service_time as string) || "default",
      pricing_cycle: (values.pricing_cycle as string) || null,
      tags: values.tags
        ? String(values.tags)
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        : [],
      description: (values.description as string) || null,
      lines: lines.map((l) => ({
        product_id: l.product_id || undefined,
        product_display: l.product_display || undefined,
        quantity: l.quantity,
        unit_id: l.unit_id || undefined,
        unit_price: l.unit_price ?? undefined,
      })),
    };

    const finish = () => {
      onSaved();
      onClose();
    };
    if (initial) {
      await run(() => api.patch(`/quotations/${initial.id}`, body), { onSuccess: finish });
    } else {
      await run(() => api.post("/quotations", body), { onSuccess: finish });
    }
  };

  return (
    <Drawer
      title={initial ? t("pages.quotations.editTitle") : t("pages.quotations.newTitle")}
      open={open}
      onClose={onClose}
      width={720}
      footer={
        <Space style={{ float: "right" }}>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="primary" loading={loading} onClick={handleSubmit}>
            {t("common.submit")}
          </Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical">
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
              optionFilterProp="label"
              options={customers.map((c) => ({
                value: c.id,
                label: pickName(lang, c.name_en, c.name_zh),
              }))}
            />
          </Form.Item>
          <Form.Item name="service_time" label={t("pages.quotations.serviceTime")} style={{ flex: 1 }}>
            <Select options={serviceTimeOptions(t)} />
          </Form.Item>
        </Space.Compact>
        <Space.Compact block>
          <Form.Item
            name="external_name"
            label={t("pages.quotations.externalName")}
            style={{ flex: 1, marginRight: 8 }}
          >
            <Input placeholder={t("pages.quotations.externalNamePlaceholder")} />
          </Form.Item>
          <Form.Item
            name="pricing_cycle"
            label={t("pages.quotations.pricingCycle")}
            style={{ flex: 1 }}
          >
            <Select
              allowClear
              placeholder={t("pages.quotations.pricingCyclePlaceholder")}
              options={pricingCycleOptions(t)}
            />
          </Form.Item>
        </Space.Compact>
        <Form.Item name="tags" label={t("pages.quotations.tags")}>
          <Input placeholder={t("pages.quotations.tagsPlaceholder")} />
        </Form.Item>
        <Form.Item name="description" label={t("pages.quotations.description")}>
          <Input.TextArea rows={2} />
        </Form.Item>

        <Typography.Text strong>{t("pages.quotations.lines")}</Typography.Text>
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
                      optionFilterProp="label"
                      options={products.map((p) => ({
                        value: p.id,
                        label: pickName(lang, p.name_en, p.name_zh),
                      }))}
                      onChange={(v) => {
                        if (v) {
                          const prod = products.find((p) => p.id === v);
                          if (prod?.default_unit_id) {
                            const lines = form.getFieldValue("lines") as FormLine[];
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
                    rules={[{ required: true }]}
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
              <Button
                type="dashed"
                icon={<PlusOutlined />}
                onClick={() => add({ quantity: 1 })}
                block
              >
                {t("pages.orders.addLine")}
              </Button>
            </>
          )}
        </Form.List>
      </Form>
    </Drawer>
  );
}
