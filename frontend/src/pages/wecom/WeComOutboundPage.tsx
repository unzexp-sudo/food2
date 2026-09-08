import { useMemo, useState } from "react";
import { Alert, Button, Card, Select, Space, Table, Tag, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { gatewayApi, WECOM_GATEWAY_URL } from "../../api/client";
import { useList } from "../../api/hooks";
import { formatDateTime } from "../../utils/format";
import StatusTag from "../../components/StatusTag";
import type { WeComOutbound } from "./types";

const TEMPLATES = [
  "order_confirmed",
  "needs_customer_confirm",
  "parse_failed",
  "out_for_delivery",
  "delivered",
  "invoice_ready",
];

const OUTBOUND_STATUSES = ["sent", "mock", "pending", "skipped", "failed"];

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
      render: (v: string) => t(`pages.wecom.outbound.template_${v}`),
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
      {list.data === null && !list.loading && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={t("pages.wecom.gatewayUnreachable", { url: WECOM_GATEWAY_URL })}
        />
      )}

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
            label: t(`pages.wecom.outbound.template_${s}`),
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
            label: t(`status.wecomOutbound.${s}`),
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
