import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Form,
  InputNumber,
  Switch,
  TimePicker,
  Typography,
} from "antd";
import { useLanguage } from "../../i18n";
import client from "../../api/client";
import { getApiError } from "../../api/client";
import { getMessage } from "../../api/message";
import dayjs, { type Dayjs } from "dayjs";

interface SettingsMap {
  auto_confirm?: { enabled?: boolean; min_confidence?: number };
  cutoff_time?: string | null;
  auto_invoice?: { enabled?: boolean };
  [key: string]: unknown;
}

export default function SettingsPage() {
  const { t } = useLanguage();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => {
    setLoading(true);
    client
      .get<SettingsMap>("/settings")
      .then((res) => {
        const s = res.data;
        form.setFieldsValue({
          auto_confirm_enabled: s.auto_confirm?.enabled ?? false,
          auto_confirm_min_confidence: s.auto_confirm?.min_confidence ?? 0.95,
          cutoff_time: s.cutoff_time ? dayjs(s.cutoff_time, "HH:mm") : null,
          auto_invoice_enabled: s.auto_invoice?.enabled ?? false,
        });
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [form]);

  const handleSave = async () => {
    let values: {
      auto_confirm_enabled: boolean;
      auto_confirm_min_confidence: number;
      cutoff_time: Dayjs | null;
      auto_invoice_enabled: boolean;
    };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const body: SettingsMap = {
      auto_confirm: {
        enabled: values.auto_confirm_enabled,
        min_confidence: values.auto_confirm_min_confidence,
      },
      cutoff_time: values.cutoff_time ? values.cutoff_time.format("HH:mm") : null,
      auto_invoice: { enabled: values.auto_invoice_enabled },
    };
    setSaving(true);
    try {
      await client.put("/settings", body);
      getMessage()?.success(t("pages.system.settings.saved"));
    } catch (err) {
      getMessage()?.error(getApiError(err) ?? t("common.error"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title={t("pages.system.settings.title")}
      loading={loading}
      extra={
        <Button type="primary" loading={saving} onClick={handleSave}>
          {t("pages.system.settings.save")}
        </Button>
      }
    >
      <Alert
        type="info"
        showIcon
        message={t("pages.system.settings.hint")}
        style={{ marginBottom: 16 }}
      />
      <Form form={form} layout="vertical" style={{ maxWidth: 520 }}>
        <Typography.Title level={5}>
          {t("pages.system.settings.autoConfirm")}
        </Typography.Title>
        <Form.Item name="auto_confirm_enabled" label={t("pages.system.settings.autoConfirmEnabled")} valuePropName="checked">
          <Switch />
        </Form.Item>
        <Form.Item
          name="auto_confirm_min_confidence"
          label={t("pages.system.settings.autoConfirmMinConfidence")}
          rules={[{ required: true }]}
        >
          <InputNumber min={0} max={1} step={0.01} style={{ width: "100%" }} />
        </Form.Item>

        <Typography.Title level={5}>
          {t("pages.system.settings.cutoffTime")}
        </Typography.Title>
        <Form.Item name="cutoff_time" label={t("pages.system.settings.cutoffTime")}>
          <TimePicker format="HH:mm" style={{ width: "100%" }} />
        </Form.Item>

        <Typography.Title level={5}>
          {t("pages.system.settings.autoInvoice")}
        </Typography.Title>
        <Form.Item name="auto_invoice_enabled" label={t("pages.system.settings.autoInvoiceEnabled")} valuePropName="checked">
          <Switch />
        </Form.Item>
      </Form>
    </Card>
  );
}
