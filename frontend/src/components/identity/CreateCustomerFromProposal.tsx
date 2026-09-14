import { useEffect, useState } from "react";
import {
  Alert,
  App as AntdApp,
  Button,
  Checkbox,
  Form,
  Input,
  Select,
  Space,
  Tag,
  Typography,
  theme,
} from "antd";
import { api, getApiError } from "../../api/client";
import { useLanguage } from "../../i18n";
import ConfidenceTag from "../ConfidenceTag";
import type { CompanyProposal, Customer, FieldDraft } from "./types";

interface Props {
  /** The extraction's proposal, or null when the document proposed nothing. */
  proposal: CompanyProposal | null;
  onCreated: (customer: Customer) => void;
  onCancel: () => void;
}

interface FormValues {
  code: string;
  name_en: string;
  name_zh: string;
  type: string;
  address?: string;
  contact_name?: string;
  contact_phone?: string;
  delivery_zone?: string;
  notes?: string;
}

/**
 * The "this is a new customer" branch.
 *
 * The form is pre-filled from the extraction, but pre-filled is not verified:
 * every value that came from the document carries an "extracted — not verified"
 * tag, the exact source line it was cut from, and its confidence. Fields the
 * extractor cannot produce (customer code, English name) are left empty and
 * labelled as things a human must type — inventing them would be exactly the
 * false assumption this whole flow exists to prevent.
 *
 * Nothing is sent until the operator ticks that they checked the values against
 * the original, and only then is the customer created.
 */
export default function CreateCustomerFromProposal({
  proposal,
  onCreated,
  onCancel,
}: Props) {
  const { t } = useLanguage();
  const { message } = AntdApp.useApp();
  const { token } = theme.useToken();
  const [form] = Form.useForm<FormValues>();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);

  const extracted = t("pages.identity.bind.extractedNotVerified");

  // The proposal is fetched asynchronously, so it can land after this form
  // mounted. Without this the "pre-fill" would silently be an empty form.
  // It only runs when the proposal itself changes, never on every keystroke.
  useEffect(() => {
    form.setFieldsValue({
      name_zh: proposal?.name?.value ?? "",
      address: proposal?.address?.value ?? "",
      contact_name: proposal?.contact?.value ?? "",
      contact_phone: proposal?.phone?.value ?? "",
    });
  }, [proposal, form]);

  /** Label + the unverified tag + the evidence line, for one extracted field. */
  const extractedLabel = (label: string, draft: FieldDraft | undefined) => (
    <Space direction="vertical" size={0}>
      <Space wrap size="small">
        <span>{label}</span>
        {draft?.value ? <Tag color="orange">{extracted}</Tag> : null}
        {draft?.value ? <ConfidenceTag value={draft.confidence} /> : null}
      </Space>
      {draft?.value ? (
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          {t("pages.identity.bind.evidence")}: {draft.evidence ?? t("pages.identity.bind.noEvidence")}
        </Typography.Text>
      ) : null}
    </Space>
  );

  const humanLabel = (label: string) => (
    <Space direction="vertical" size={0}>
      <span>{label}</span>
      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
        {t("pages.identity.bind.requiredFromHuman")}
      </Typography.Text>
    </Space>
  );

  const handleSubmit = async () => {
    let values: FormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    if (!checked) {
      setError(t("pages.identity.bind.confirmCheckedRequired"));
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const created = await api.post<Customer>("/customers", {
        code: values.code.trim(),
        name_en: values.name_en.trim(),
        name_zh: values.name_zh.trim(),
        type: values.type,
        address: values.address?.trim() || null,
        contact_name: values.contact_name?.trim() || null,
        contact_phone: values.contact_phone?.trim() || null,
        delivery_zone: values.delivery_zone?.trim() || null,
        notes: values.notes?.trim() || null,
        status: "active",
      });
      message.success(t("pages.identity.bind.createSuccess"));
      onCreated(created);
    } catch (err) {
      setError(getApiError(err) ?? t("common.error"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      style={{
        border: `1px solid ${token.colorBorder}`,
        borderRadius: token.borderRadius,
        padding: 12,
      }}
    >
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        <Typography.Text strong>{t("pages.identity.bind.newCustomerTitle")}</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t("pages.identity.bind.newCustomerHint", { tag: extracted })}
        </Typography.Text>

        {!proposal ? (
          <Alert type="info" showIcon message={t("pages.identity.bind.proposalHint")} />
        ) : null}

        <Form<FormValues>
          form={form}
          layout="vertical"
          initialValues={{
            code: "",
            // The document is in Chinese, so the extracted name belongs in the
            // Chinese field. Copying it into the English field as well would be
            // a wrong value dressed up as a filled-in one.
            name_en: "",
            name_zh: proposal?.name?.value ?? "",
            type: "other",
            address: proposal?.address?.value ?? "",
            contact_name: proposal?.contact?.value ?? "",
            contact_phone: proposal?.phone?.value ?? "",
            delivery_zone: "",
            notes: "",
          }}
        >
          <Form.Item
            name="code"
            label={humanLabel(t("pages.identity.bind.fieldCode"))}
            rules={[{ required: true, message: t("pages.identity.bind.fillRequired") }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="name_en"
            label={humanLabel(t("pages.identity.bind.fieldNameEn"))}
            rules={[{ required: true, message: t("pages.identity.bind.fillRequired") }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="name_zh"
            label={extractedLabel(t("pages.identity.bind.fieldNameZh"), proposal?.name)}
            rules={[{ required: true, message: t("pages.identity.bind.fillRequired") }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="type" label={t("pages.identity.bind.fieldType")}>
            <Select
              options={["school", "restaurant", "canteen", "other"].map((v) => ({
                value: v,
                label: t(`status.customerType.${v}`),
              }))}
            />
          </Form.Item>
          <Form.Item
            name="address"
            label={extractedLabel(t("pages.identity.bind.fieldAddress"), proposal?.address)}
          >
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item
            name="contact_name"
            label={extractedLabel(t("pages.identity.bind.fieldContact"), proposal?.contact)}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="contact_phone"
            label={extractedLabel(t("pages.identity.bind.fieldPhone"), proposal?.phone)}
          >
            <Input />
          </Form.Item>
          <Form.Item name="delivery_zone" label={t("pages.identity.bind.fieldZone")}>
            <Input />
          </Form.Item>
        </Form>

        {error ? <Alert type="error" showIcon message={error} /> : null}

        <Checkbox checked={checked} onChange={(e) => setChecked(e.target.checked)}>
          {t("pages.identity.bind.confirmChecked")}
        </Checkbox>

        <Space>
          <Button type="primary" loading={saving} disabled={!checked} onClick={handleSubmit}>
            {t("common.create")}
          </Button>
          <Button onClick={onCancel}>{t("common.cancel")}</Button>
        </Space>
      </Space>
    </div>
  );
}
