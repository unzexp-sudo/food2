import { useEffect, useMemo, useState } from "react";
import { Alert, Descriptions, Select, Space, Tag, Typography, theme } from "antd";
import { api } from "../../api/client";
import { useLanguage } from "../../i18n";
import { formatDateTime } from "../../utils/format";
import HeldDocumentEvidence, { HeldDocumentsEmpty } from "./HeldDocumentEvidence";
import type { IntakeDocumentSummary, UnboundChat } from "./types";

interface Props {
  chat: UnboundChat;
}

/**
 * LEFT COLUMN of the bind screen — who is talking.
 *
 * Everything here comes from WeCom or from the held documents. Nothing is
 * inferred and nothing is filled in: a missing display name renders as
 * "not provided by WeCom", because a plausible-looking invented name is how a
 * wrong bind starts.
 *
 * The chat key is the only field the binding is ever made on, so it is shown
 * even though it looks like noise — it is what support will ask for.
 */
export default function ChatEvidencePanel({ chat }: Props) {
  const { t } = useLanguage();
  const { token } = theme.useToken();
  const [docs, setDocs] = useState<IntakeDocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const documentIds = useMemo(
    () => chat.document_ids ?? [],
    [chat.document_ids],
  );
  const documentKey = documentIds.join(",");

  useEffect(() => {
    let cancelled = false;
    setDocs(null);
    setError(null);
    if (documentIds.length === 0) {
      setDocs([]);
      return;
    }
    Promise.all(
      documentIds.map((id) =>
        api
          .get<IntakeDocumentSummary>(`/intake/documents/${id}`)
          .catch(() => null),
      ),
    ).then((results) => {
      if (cancelled) return;
      const ok = results.filter((r): r is IntakeDocumentSummary => r !== null);
      if (ok.length === 0) {
        setError(t("pages.identity.bind.documentLoadError"));
      }
      setDocs(ok);
    });
    return () => {
      cancelled = true;
    };
    // documentIds is derived from documentKey; depending on the array itself
    // would re-fetch on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentKey, t]);

  // Newest first: the document a person most likely needs to look at.
  const ordered = useMemo(() => {
    const list = [...(docs ?? [])];
    list.sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
    return list;
  }, [docs]);

  const activeId = selectedId ?? ordered[0]?.id ?? null;
  const active = ordered.find((d) => d.id === activeId) ?? null;

  const firstSeen = useMemo(() => {
    const stamps = (docs ?? [])
      .map((d) => d.created_at)
      .filter((s): s is string => Boolean(s))
      .sort();
    return stamps[0] ?? null;
  }, [docs]);

  const unknown = (
    <Typography.Text type="secondary">
      {t("pages.identity.bind.unknown")}
    </Typography.Text>
  );

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Descriptions
        size="small"
        column={1}
        bordered
        title={t("pages.identity.bind.leftTitle")}
      >
        <Descriptions.Item label={t("pages.identity.bind.identityName")}>
          {chat.display_name ? (
            <Typography.Text strong>{chat.display_name}</Typography.Text>
          ) : (
            unknown
          )}
        </Descriptions.Item>
        <Descriptions.Item label={t("pages.identity.bind.alias")}>
          {/* The WeCom remark the gateway forwards with the handoff. Shown as
              evidence for the human to read; it is user-editable and is never
              used to resolve a customer. */}
          {chat.alias ? chat.alias : unknown}
        </Descriptions.Item>
        <Descriptions.Item label={t("pages.identity.bind.corp")}>
          {chat.corp_name ? chat.corp_name : unknown}
        </Descriptions.Item>
        <Descriptions.Item label={t("pages.identity.bind.phone")}>
          {/* WeCom does not hand us a phone number, so there is nothing honest
              to show here yet. Saying so beats an empty cell that looks like a
              loading failure. */}
          {unknown}
        </Descriptions.Item>
        <Descriptions.Item label={t("pages.identity.bind.chatKey")}>
          <div style={{ fontFamily: "monospace", fontSize: 12, wordBreak: "break-all" }}>
            {chat.chat_key}
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            {chat.kind ?? "—"} · {t("pages.identity.bind.chatKeyHint")}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label={t("pages.identity.bind.firstSeen")}>
          {firstSeen ? formatDateTime(firstSeen) : unknown}
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {t("pages.identity.bind.firstSeenHint")}
            </Typography.Text>
          </div>
        </Descriptions.Item>
        <Descriptions.Item label={t("pages.identity.bind.waiting")}>
          <Tag color="warning">
            {t("pages.identity.queue.waitingCount", { count: chat.waiting_count })}
          </Tag>
        </Descriptions.Item>
      </Descriptions>

      <div
        style={{
          border: `1px solid ${token.colorBorder}`,
          borderRadius: token.borderRadius,
          padding: 12,
        }}
      >
        <Typography.Text strong>
          {t("pages.identity.bind.heldTitle", { count: chat.waiting_count })}
        </Typography.Text>
        <div style={{ marginTop: 4, marginBottom: 10 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("pages.identity.bind.heldHint")}
          </Typography.Text>
        </div>

        {error ? <Alert type="warning" showIcon message={error} /> : null}

        {docs === null ? (
          <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
        ) : ordered.length === 0 ? (
          <HeldDocumentsEmpty />
        ) : (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            {ordered.length > 1 ? (
              <Select
                style={{ width: "100%" }}
                value={activeId ?? undefined}
                onChange={setSelectedId}
                placeholder={t("pages.identity.bind.selectDocument")}
                options={ordered.map((d, i) => ({
                  value: d.id,
                  label: `#${ordered.length - i} · ${d.source_type}${
                    d.original_filename ? ` · ${d.original_filename}` : ""
                  }`,
                }))}
              />
            ) : null}
            {active ? <HeldDocumentEvidence doc={active} /> : null}
          </Space>
        )}
      </div>
    </Space>
  );
}
