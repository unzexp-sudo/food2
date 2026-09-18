import { useMemo, useState } from "react";
import { Button, Card, Select, Space, Table, Tag, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { gatewayApi } from "../../api/client";
import { useList } from "../../api/hooks";
import { formatDateTime } from "../../utils/format";
import StatusTag from "../../components/StatusTag";
import GatewayUnreachableAlert from "../../components/GatewayUnreachableAlert";
import type { WeComOutbound } from "./types";

/**
 * Mirrors the gateway's `TemplateName` literal (WeCom1 `app/schemas/wecom.py`),
 * which is the set the gateway will *accept*. Keep all seven: the ERP sends
 * every one of them, including `intake_needs_review`, which is the internal
 * "an order is waiting for a human" ping and lands in this very log. A missing
 * entry does not hide the row — it only makes it impossible to filter to, so
 * the row you are looking for is the one you cannot select.
 *
 * Both lists below are hand-maintained mirrors of a schema that lives in
 * another repo, and nothing can compare them automatically. That is why the
 * renderers fall back to the raw value (see `label`): a drifted list then shows
 * `intake_needs_review`, which is readable, instead of the untranslated i18n
 * key, which is not.
 */
const TEMPLATES = [
  "order_confirmed",
  "needs_customer_confirm",
  "parse_failed",
  "intake_needs_review",
  "out_for_delivery",
  "delivered",
  "invoice_ready",
];

/**
 * Mirrors the gateway's `OutboundStatus` literal. `blocked` is not an edge
 * case: it is what a guard refusal looks like — a destination the gateway
 * resolved and then declined to send to. While the allowlist is in force it is
 * the *expected* status of every real customer notification, so a filter that
 * cannot select it hides exactly the rows an operator needs to act on.
 */
const OUTBOUND_STATUSES = ["sent", "mock", "pending", "skipped", "failed", "blocked"];

/** Outbound notification log (GET {gateway}/wecom/outbound). */
export default function WeComOutboundPage() {
  const { t } = useLanguage();

  const [templateFilter, setTemplateFilter] = useState<string | undefined>();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();

  const params = useMemo(
    () => ({
      ...(templateFilter ? { template: templateFilter } : {}),
      ...(statusFilter ? { status: statusFilter } : {}),
    }),
    [templateFilter, statusFilter],
  );

  const list = useList<WeComOutbound>("/wecom/outbound", params, gatewayApi);

  const columns = [
    {
      title: t("pages.wecom.outbound.colCreatedAt"),
      dataIndex: "created_at",
      width: 150,
      render: (v: string) => formatDateTime(v),
    },
    {
      title: t("pages.wecom.outbound.colTemplate"),
      dataIndex: "template",
      width: 190,
      // `defaultValue` matters: i18next returns the *key* for a missing
      // translation, so an unknown template would render as
      // "pages.wecom.outbound.template_xxx" — which reads as a bug in the page
      // rather than as a template this build does not know about.
      render: (v: string) => t(`pages.wecom.outbound.template_${v}`, { defaultValue: v }),
    },
    {
      title: t("pages.wecom.outbound.colTo"),
      key: "to",
      width: 200,
      render: (_: unknown, r: WeComOutbound) =>
        r.to_id ? (
          <Space direction="vertical" size={0}>
            <Typography.Text code>{r.to_id}</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {r.to_type ? t(`pages.wecom.outbound.to_${r.to_type}`) : "—"}
            </Typography.Text>
          </Space>
        ) : (
          t("pages.wecom.outbound.to_none")
        ),
    },
    {
      title: t("pages.wecom.outbound.colCustomer"),
      dataIndex: "customer_id",
      width: 200,
      render: (v: string | null) => (v ? <Typography.Text code>{v}</Typography.Text> : "—"),
    },
    {
      title: t("pages.wecom.outbound.colOrder"),
      dataIndex: "order_id",
      width: 200,
      render: (v: string | null) => (v ? <Typography.Text code>{v}</Typography.Text> : "—"),
    },
    {
      title: t("pages.wecom.outbound.colLocale"),
      dataIndex: "locale",
      width: 90,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    {
      title: t("pages.wecom.outbound.colStatus"),
      dataIndex: "status",
      width: 120,
      render: (v: string) => <StatusTag domain="wecomOutbound" value={v} />,
    },
    {
      title: t("pages.wecom.outbound.colMessage"),
      dataIndex: "rendered_text",
      render: (v: string | null, r: WeComOutbound) => (
        <Space direction="vertical" size={0}>
          <span style={{ whiteSpace: "pre-wrap" }}>{v ?? "—"}</span>
          {r.error && <Typography.Text type="danger">{r.error}</Typography.Text>}
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={t("pages.wecom.outbound.title")}
      extra={
        <Button icon={<ReloadOutlined />} onClick={list.refresh} loading={list.loading}>
          {t("common.refresh")}
        </Button>
      }
    >
      {list.data === null && !list.loading && <GatewayUnreachableAlert />}

      <Space wrap size="middle" style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder={t("pages.wecom.outbound.allTemplates")}
          style={{ width: 220 }}
          value={templateFilter}
          onChange={(v) => {
            setTemplateFilter(v);
            list.setPage(1);
          }}
          options={TEMPLATES.map((s) => ({
            value: s,
            label: t(`pages.wecom.outbound.template_${s}`, { defaultValue: s }),
          }))}
        />
        <Select
          allowClear
          placeholder={t("pages.wecom.outbound.allStatuses")}
          style={{ width: 170 }}
          value={statusFilter}
          onChange={(v) => {
            setStatusFilter(v);
            list.setPage(1);
          }}
          options={OUTBOUND_STATUSES.map((s) => ({
            value: s,
            label: t(`status.wecomOutbound.${s}`, { defaultValue: s }),
          }))}
        />
      </Space>

      <Table<WeComOutbound>
        rowKey="id"
        loading={list.loading}
        dataSource={list.items}
        columns={columns}
        size="middle"
        scroll={{ x: 1200 }}
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
    </Card>
  );
}
