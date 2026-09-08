import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Card, Popconfirm, Select, Space, Table, Tag, Tooltip, Typography } from "antd";
import { PaperClipOutlined, ReloadOutlined } from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { gatewayApi, WECOM_GATEWAY_URL, type Page } from "../../api/client";
import { useList, useMutate } from "../../api/hooks";
import { api } from "../../api/client";
import { formatDateTime, pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";
import { parseStoredUser } from "../../types";
import type { ErpCustomer, WeComMessage } from "./types";

const MESSAGE_STATUSES = ["received", "handed_off", "ignored", "failed", "duplicate"];

/** Inbound WeCom message log (GET {gateway}/wecom/messages). */
export default function WeComMessagesPage() {
  const { t, lang } = useLanguage();
  const user = parseStoredUser();
  const canMutate = user?.role === "admin" || user?.role === "ops";

  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [customerFilter, setCustomerFilter] = useState<string | undefined>();

  const params = useMemo(
    () => ({
      ...(statusFilter ? { status: statusFilter } : {}),
      ...(customerFilter ? { customer_id: customerFilter } : {}),
    }),
    [statusFilter, customerFilter],
  );

  const list = useList<WeComMessage>("/wecom/messages", params, gatewayApi);
  const { loading: mutateLoading, run } = useMutate();

  // ERP customers, for the filter and for displaying a name instead of a raw id.
  const [customers, setCustomers] = useState<ErpCustomer[]>([]);
  useEffect(() => {
    api
      .get<Page<ErpCustomer>>("/customers", { page: 1, page_size: 200 })
      .then((res) => setCustomers(res.items))
      .catch(() => setCustomers([]));
  }, []);

  const customerName = (id: string | null) => {
    if (!id) return null;
    const c = customers.find((x) => x.id === id);
    return c ? pickName(lang, c.name_en, c.name_zh) : id;
  };

  const handleRehand = (row: WeComMessage) =>
    run(() => gatewayApi.post(`/wecom/messages/${row.id}/rehand`), {
      success: t("pages.wecom.messages.rehandSuccess"),
      onSuccess: list.refresh,
    });

  const columns = [
    {
      title: t("pages.wecom.messages.colReceivedAt"),
      dataIndex: "received_at",
      width: 150,
      render: (v: string | null) => (v ? formatDateTime(v) : "—"),
    },
    {
      title: t("pages.wecom.messages.colSender"),
      dataIndex: "external_userid",
      width: 170,
      render: (v: string | null, r: WeComMessage) => (
        <Space direction="vertical" size={0}>
          <span>{v ?? r.sender_userid ?? "—"}</span>
          {r.chat_id && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {r.chat_id}
            </Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: t("pages.wecom.messages.colType"),
      dataIndex: "source_type",
      width: 110,
      render: (v: string | null, r: WeComMessage) => <Tag>{v ?? r.msgtype}</Tag>,
    },
    {
      title: t("pages.wecom.messages.colContent"),
      dataIndex: "content_text",
      render: (v: string | null, r: WeComMessage) => (
        <Space size={4}>
          <span>{v?.trim() ? v : t("pages.wecom.messages.noContent")}</span>
          {r.file_url && (
            <Tooltip title={t("pages.wecom.messages.viewMedia")}>
              <Button
                size="small"
                type="text"
                icon={<PaperClipOutlined />}
                href={r.file_url}
                target="_blank"
                rel="noreferrer"
              />
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: t("pages.wecom.messages.colCustomer"),
      dataIndex: "customer_id",
      width: 160,
      render: (v: string | null) => customerName(v) ?? t("pages.wecom.messages.unresolved"),
    },
    {
      title: t("pages.wecom.messages.colBind"),
      dataIndex: "bind_status",
      width: 120,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    {
      title: t("pages.wecom.messages.colStatus"),
      dataIndex: "status",
      width: 130,
      render: (v: string) => <StatusTag domain="wecomMessage" value={v} />,
    },
    {
      title: t("pages.wecom.messages.colJob"),
      dataIndex: "intake_job_id",
      width: 130,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("common.actions"),
      key: "actions",
      width: 130,
      render: (_: unknown, r: WeComMessage) =>
        canMutate && (r.status === "failed" || r.status === "received") ? (
          <Popconfirm
            title={t("pages.wecom.messages.rehandConfirm")}
            onConfirm={() => handleRehand(r)}
            disabled={mutateLoading}
          >
            <Button size="small" icon={<ReloadOutlined />} loading={mutateLoading}>
              {t("pages.wecom.messages.rehand")}
            </Button>
          </Popconfirm>
        ) : (
          "—"
        ),
    },
  ];

  return (
    <Card
      title={t("pages.wecom.messages.title")}
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
          placeholder={t("pages.wecom.messages.allStatuses")}
          style={{ width: 170 }}
          value={statusFilter}
          onChange={(v) => {
            setStatusFilter(v);
            list.setPage(1);
          }}
          options={MESSAGE_STATUSES.map((s) => ({
            value: s,
            label: t(`status.wecomMessage.${s}`),
          }))}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder={t("pages.wecom.messages.allCustomers")}
          style={{ width: 240 }}
          value={customerFilter}
          onChange={(v) => {
            setCustomerFilter(v);
            list.setPage(1);
          }}
          options={customers.map((c) => ({
            value: c.id,
            label: pickName(lang, c.name_en, c.name_zh),
          }))}
        />
      </Space>

      <Table<WeComMessage>
        rowKey="id"
        loading={list.loading}
        dataSource={list.items}
        columns={columns}
        size="middle"
        scroll={{ x: 1100 }}
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
        expandable={{
          expandedRowRender: (r) => (
            <Space direction="vertical" size={2}>
              <Typography.Text type="secondary">msgid: {r.msgid}</Typography.Text>
              {r.document_id && (
                <Typography.Text type="secondary">document: {r.document_id}</Typography.Text>
              )}
              {r.reply_to_msgid && (
                <Typography.Text type="secondary">reply_to: {r.reply_to_msgid}</Typography.Text>
              )}
              {r.error && (
                <Typography.Text type="danger">
                  {t("pages.wecom.messages.errorPrefix")}
                  {r.error}
                </Typography.Text>
              )}
            </Space>
          ),
          rowExpandable: (r) => Boolean(r.error || r.document_id || r.reply_to_msgid),
        }}
      />
    </Card>
  );
}
