import { useMemo, useState } from "react";
import {
  Card,
  Drawer,
  Input,
  Space,
  Table,
  Typography,
  type TableProps,
} from "antd";
import { useLanguage } from "../../i18n";
import { useList } from "../../api/hooks";
import { formatDateTime } from "../../utils/format";

interface AuditLog {
  id: string;
  entity_type: string;
  entity_id: string;
  action: string;
  actor_name: string;
  summary: string | null;
  created_at: string;
  before?: unknown;
  after?: unknown;
}

export default function AuditPage() {
  const { t } = useLanguage();
  const [entityType, setEntityType] = useState<string>("");
  const [entityId, setEntityId] = useState<string>("");
  const [actorId, setActorId] = useState<string>("");

  const params = useMemo(
    () => ({
      ...(entityType ? { entity_type: entityType } : {}),
      ...(entityId ? { entity_id: entityId } : {}),
      ...(actorId ? { actor_id: actorId } : {}),
    }),
    [entityType, entityId, actorId],
  );
  const list = useList<AuditLog>("/audit-logs", params);

  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<AuditLog | null>(null);

  const columns: TableProps<AuditLog>["columns"] = [
    {
      title: t("pages.system.audit.colCreatedAt"),
      dataIndex: "created_at",
      width: 160,
      render: (v: string) => formatDateTime(v),
    },
    {
      title: t("pages.system.audit.colActor"),
      dataIndex: "actor_name",
      width: 140,
    },
    {
      title: t("pages.system.audit.colEntityType"),
      dataIndex: "entity_type",
      width: 150,
    },
    {
      title: t("pages.system.audit.colEntityId"),
      dataIndex: "entity_id",
      width: 180,
      render: (v: string) => <span style={{ fontFamily: "monospace", fontSize: 12 }}>{v}</span>,
    },
    {
      title: t("pages.system.audit.colAction"),
      dataIndex: "action",
      width: 130,
    },
    {
      title: t("pages.system.audit.colSummary"),
      dataIndex: "summary",
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.system.audit.colActions"),
      key: "actions",
      width: 100,
      render: (_: unknown, r: AuditLog) => (
        <a
          onClick={() => {
            setDetail(r);
            setDetailOpen(true);
          }}
        >
          {t("pages.system.audit.details")}
        </a>
      ),
    },
  ];

  const renderJson = (label: string, data: unknown) => (
    <div>
      <Typography.Text strong>{label}</Typography.Text>
      {data != null ? (
        <pre
          style={{
            background: "#f5f5f5",
            padding: 12,
            borderRadius: 4,
            maxHeight: 300,
            overflow: "auto",
            fontSize: 12,
          }}
        >
          {JSON.stringify(data, null, 2)}
        </pre>
      ) : (
        <Typography.Text type="secondary">—</Typography.Text>
      )}
    </div>
  );

  return (
    <Card title={t("pages.system.audit.title")}>
      <Space wrap size="middle" style={{ marginBottom: 12 }}>
        <Input.Search
          allowClear
          placeholder={t("pages.system.audit.filterEntityType")}
          style={{ width: 200 }}
          onSearch={(v) => {
            setEntityType(v);
            list.setPage(1);
          }}
        />
        <Input.Search
          allowClear
          placeholder={t("pages.system.audit.filterEntityId")}
          style={{ width: 280 }}
          onSearch={(v) => {
            setEntityId(v);
            list.setPage(1);
          }}
        />
        <Input.Search
          allowClear
          placeholder={t("pages.system.audit.filterActorId")}
          style={{ width: 280 }}
          onSearch={(v) => {
            setActorId(v);
            list.setPage(1);
          }}
        />
      </Space>
      <Table<AuditLog>
        rowKey="id"
        loading={list.loading}
        dataSource={list.items}
        columns={columns}
        size="middle"
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

      <Drawer
        title={t("pages.system.audit.details")}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={620}
      >
        {detail ? (
          <Space direction="vertical" style={{ width: "100%" }} size="middle">
            <div>
              <Typography.Text strong>{t("pages.system.audit.colAction")}: </Typography.Text>
              <Typography.Text>{detail.action}</Typography.Text>
            </div>
            <div>
              <Typography.Text strong>{t("pages.system.audit.colEntityType")}: </Typography.Text>
              <Typography.Text>{detail.entity_type}</Typography.Text>
            </div>
            <div>
              <Typography.Text strong>{t("pages.system.audit.colEntityId")}: </Typography.Text>
              <Typography.Text style={{ fontFamily: "monospace", fontSize: 12 }}>
                {detail.entity_id}
              </Typography.Text>
            </div>
            <div>
              <Typography.Text strong>{t("pages.system.audit.colActor")}: </Typography.Text>
              <Typography.Text>{detail.actor_name}</Typography.Text>
            </div>
            {detail.summary && (
              <div>
                <Typography.Text strong>{t("pages.system.audit.colSummary")}: </Typography.Text>
                <Typography.Text>{detail.summary}</Typography.Text>
              </div>
            )}
            {detail.before != null || detail.after != null ? (
              <>
                {renderJson(t("pages.system.audit.before"), detail.before)}
                {renderJson(t("pages.system.audit.after"), detail.after)}
              </>
            ) : (
              <Typography.Text type="secondary">
                {t("pages.system.audit.noDetail")}
              </Typography.Text>
            )}
          </Space>
        ) : (
          <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
        )}
      </Drawer>
    </Card>
  );
}
