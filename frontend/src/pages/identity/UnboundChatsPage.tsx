import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Button, Card, Space, Table, Tag, Typography } from "antd";
import { ReloadOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { api, getApiError } from "../../api/client";
import { LIST_POLL_MS } from "../../api/hooks";
import { useLanguage } from "../../i18n";
import { formatDateTime } from "../../utils/format";
import { parseStoredUser } from "../../types";
import type { UnboundChat } from "../../components/identity/types";

interface UnboundResponse {
  items: UnboundChat[];
  total: number;
}

/**
 * GATE 0 — the unbound chats queue.
 *
 * One row per **conversation**, never per message: "Chat X — 4 messages
 * waiting" is a single decision, and four rows for it would turn a queue into
 * noise that people stop reading.
 *
 * Nothing on this screen binds anything. It exists to make the missing decision
 * visible — every document behind these rows is held and cannot become an
 * order until a person picks a customer.
 */
export default function UnboundChatsPage() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const user = parseStoredUser();
  const canBind = user?.role === "admin" || user?.role === "ops";

  const [items, setItems] = useState<UnboundChat[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    (silent = false) => {
      if (!silent) setLoading(true);
      api
        .get<UnboundResponse>("/identity/unbound")
        .then((res) => {
          setItems(res.items ?? []);
          setTotal(res.total ?? 0);
          setError(null);
        })
        .catch((err) => {
          // A failed poll keeps the rows already on screen: they are still the
          // best information available, and clearing them would look like the
          // queue had been worked.
          if (!silent) setError(getApiError(err) ?? t("pages.identity.queue.loadError"));
        })
        .finally(() => {
          if (!silent) setLoading(false);
        });
    },
    [t],
  );

  useEffect(() => {
    load();
  }, [load]);

  // Polled, not loaded once. A conversation becomes unbound the moment a
  // message arrives from a chat nobody has identified — an event with no
  // connection to this tab — and this screen is the only place that decision is
  // visible. Without the poll the operator is reading a snapshot and has no way
  // to know it is stale.
  useEffect(() => {
    const id = setInterval(() => {
      if (!document.hidden) load(true);
    }, LIST_POLL_MS);
    const onVisible = () => {
      if (!document.hidden) load(true);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  const openBind = (chat: UnboundChat) => {
    const params = new URLSearchParams();
    if (chat.kind) params.set("kind", chat.kind);
    if (chat.value) params.set("value", chat.value);
    params.set("chat_key", chat.chat_key);
    navigate(`/identity/chats/bind?${params.toString()}`);
  };

  const columns = [
    {
      title: t("pages.identity.queue.colChat"),
      key: "chat",
      render: (_: unknown, r: UnboundChat) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>
            {r.display_name ?? t("pages.identity.queue.unnamed")}
          </Typography.Text>
          <Typography.Text
            type="secondary"
            style={{ fontSize: 11, fontFamily: "monospace", wordBreak: "break-all" }}
          >
            {r.chat_key}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: t("pages.identity.queue.colCorp"),
      dataIndex: "corp_name",
      width: 220,
      render: (v: string | null) => v ?? <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: t("pages.identity.queue.colWaiting"),
      dataIndex: "waiting_count",
      width: 130,
      render: (v: number) => (
        <Tag color={v > 1 ? "warning" : "default"}>
          {t("pages.identity.queue.waitingCount", { count: v })}
        </Tag>
      ),
    },
    {
      title: t("pages.identity.queue.colLastMessage"),
      dataIndex: "last_message_at",
      width: 170,
      render: (v: string | null) =>
        v ? formatDateTime(v) : <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: t("pages.identity.queue.colAction"),
      key: "action",
      width: 190,
      render: (_: unknown, r: UnboundChat) => (
        <Button
          type="primary"
          icon={<SafetyCertificateOutlined />}
          disabled={!canBind || !r.kind || !r.value}
          onClick={() => openBind(r)}
        >
          {t("pages.identity.queue.bind")}
        </Button>
      ),
    },
  ];

  return (
    <Card
      title={t("pages.identity.queue.title")}
      extra={
        <Button icon={<ReloadOutlined />} onClick={() => load()} loading={loading}>
          {t("common.refresh")}
        </Button>
      }
    >
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 12 }}
        message={t("pages.identity.queue.intro")}
        description={t("pages.identity.queue.introBody")}
      />

      {error ? <Alert type="error" showIcon style={{ marginBottom: 12 }} message={error} /> : null}

      <Table<UnboundChat>
        rowKey="chat_key"
        loading={loading}
        dataSource={items}
        columns={columns}
        size="middle"
        pagination={false}
        locale={{
          emptyText: (
            <Space direction="vertical" size={0}>
              <Typography.Text>{t("pages.identity.queue.empty")}</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t("pages.identity.queue.emptyHint")}
              </Typography.Text>
            </Space>
          ),
        }}
      />

      <div style={{ marginTop: 12 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t("pages.identity.bind.safetyNote")}
        </Typography.Text>
      </div>

      {total > 0 ? (
        <div style={{ marginTop: 4 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("common.total")}: {total}
          </Typography.Text>
        </div>
      ) : null}
    </Card>
  );
}
