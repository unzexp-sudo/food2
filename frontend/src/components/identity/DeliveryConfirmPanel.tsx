import { useEffect, useState } from "react";
import { Alert, Button, Card, Form, Input, Space, Tag, Typography } from "antd";
import { CheckCircleOutlined } from "@ant-design/icons";
import { api } from "../../api/client";
import { useMutate } from "../../api/hooks";
import { useLanguage } from "../../i18n";
import { formatDateTime } from "../../utils/format";
import type { OrderDeliveryState } from "./types";

interface Props {
  order: OrderDeliveryState;
  /** ops/admin — the endpoint refuses anyone else, so the button is hidden. */
  canConfirm: boolean;
  onConfirmed?: () => void;
}

/**
 * GATE 4 — where is this order actually going?
 *
 * The address shown here was copied from the customer record when the order was
 * created. That copy is a **proposal**: customer addresses sit unverified for
 * years, and `prefill_delivery_from_customer` deliberately leaves
 * `delivery_confirmed_at` empty, which is what the backend gate reads.
 *
 * So the panel says "not confirmed" until a person presses the button, and the
 * order's own Confirm action is blocked until then. Pre-filling is a
 * convenience for the person confirming; it is never a confirmation.
 */
export default function DeliveryConfirmPanel({ order, canConfirm, onConfirmed }: Props) {
  const { t } = useLanguage();
  const { loading, run } = useMutate();
  const [address, setAddress] = useState(order.delivery_address ?? "");
  const [contactName, setContactName] = useState(order.delivery_contact_name ?? "");
  const [contactPhone, setContactPhone] = useState(order.delivery_contact_phone ?? "");
  const [error, setError] = useState<string | null>(null);

  // Re-sync after a refresh (e.g. once the confirmation lands, the server's
  // values are the truth and the local edits are no longer interesting).
  useEffect(() => {
    setAddress(order.delivery_address ?? "");
    setContactName(order.delivery_contact_name ?? "");
    setContactPhone(order.delivery_contact_phone ?? "");
  }, [
    order.id,
    order.delivery_address,
    order.delivery_contact_name,
    order.delivery_contact_phone,
    order.delivery_confirmed_at,
  ]);

  const confirmed = Boolean(order.delivery_confirmed_at);

  const handleConfirm = async () => {
    if (!address.trim()) {
      setError(t("pages.identity.delivery.addressRequired"));
      return;
    }
    setError(null);
    await run(
      () =>
        api.post(`/orders/${order.id}/confirm-delivery`, {
          delivery_address: address.trim(),
          contact_name: contactName.trim() || null,
          contact_phone: contactPhone.trim() || null,
        }),
      {
        success: t("pages.identity.delivery.confirmSuccess"),
        onSuccess: onConfirmed,
      },
    );
  };

  return (
    <Card
      size="small"
      title={t("pages.identity.delivery.title")}
      extra={
        confirmed ? (
          <Tag color="success" icon={<CheckCircleOutlined />}>
            {t("pages.identity.delivery.confirmed")}
          </Tag>
        ) : (
          <Tag color="warning">{t("pages.identity.delivery.unconfirmed")}</Tag>
        )
      }
    >
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        {confirmed ? (
          <Alert
            type="success"
            showIcon
            message={t("pages.identity.delivery.confirmedAt", {
              at: formatDateTime(order.delivery_confirmed_at),
            })}
          />
        ) : (
          <Alert
            type="warning"
            showIcon
            message={t("pages.identity.delivery.unconfirmed")}
            description={`${t("pages.identity.delivery.unconfirmedHint")} ${t(
              "pages.identity.delivery.blockedHint",
            )}`}
          />
        )}

        {!confirmed && order.delivery_address ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("pages.identity.delivery.prefilledWarning")}
          </Typography.Text>
        ) : null}
        {!order.delivery_address && !confirmed ? (
          <Typography.Text type="warning" style={{ fontSize: 12 }}>
            {t("pages.identity.delivery.noAddress")}
          </Typography.Text>
        ) : null}

        <Form layout="vertical" size="small">
          <Form.Item label={t("pages.identity.delivery.address")} required style={{ marginBottom: 12 }}>
            <Input.TextArea
              rows={2}
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              placeholder={t("pages.identity.delivery.addressPlaceholder")}
              disabled={!canConfirm}
            />
          </Form.Item>
          <Space size="middle" wrap>
            <Form.Item label={t("pages.identity.delivery.contactName")} style={{ marginBottom: 0 }}>
              <Input
                value={contactName}
                onChange={(e) => setContactName(e.target.value)}
                disabled={!canConfirm}
                style={{ width: 220 }}
              />
            </Form.Item>
            <Form.Item label={t("pages.identity.delivery.contactPhone")} style={{ marginBottom: 0 }}>
              <Input
                value={contactPhone}
                onChange={(e) => setContactPhone(e.target.value)}
                disabled={!canConfirm}
                style={{ width: 220 }}
              />
            </Form.Item>
          </Space>
        </Form>

        {error ? <Alert type="error" showIcon message={error} /> : null}

        <Space wrap>
          <Button
            type={confirmed ? "default" : "primary"}
            icon={<CheckCircleOutlined />}
            loading={loading}
            disabled={!canConfirm}
            onClick={handleConfirm}
          >
            {confirmed
              ? t("pages.identity.delivery.reConfirmAction")
              : t("pages.identity.delivery.confirmAction")}
          </Button>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("pages.identity.delivery.editHint")}
          </Typography.Text>
        </Space>
      </Space>
    </Card>
  );
}
