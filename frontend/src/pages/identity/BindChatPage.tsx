import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Col,
  Popconfirm,
  Row,
  Space,
  Typography,
} from "antd";
import { ArrowLeftOutlined, LinkOutlined, PlusOutlined } from "@ant-design/icons";
import { api, getApiError } from "../../api/client";
import { useMutate } from "../../api/hooks";
import { useLanguage } from "../../i18n";
import { pickName } from "../../utils/format";
import { parseStoredUser } from "../../types";
import ChatEvidencePanel from "../../components/identity/ChatEvidencePanel";
import CompanyProposalPanel from "../../components/identity/CompanyProposalPanel";
import CustomerPicker, { CustomerSummary } from "../../components/identity/CustomerPicker";
import CreateCustomerFromProposal from "../../components/identity/CreateCustomerFromProposal";
import type {
  BindResult,
  CompanyProposal,
  Customer,
  UnboundChat,
} from "../../components/identity/types";

interface UnboundResponse {
  items: UnboundChat[];
  total: number;
}

/**
 * THE BIND SCREEN — the one decision that matters.
 *
 * Two columns, side by side, on purpose: the human looks at the actual
 * conversation (left) and the actual customer (right) at the same moment. The
 * sanity check runs in both directions — the held order tells you which
 * customer this should be, and the customer's last three orders tell you
 * whether that makes sense.
 *
 * Two rules shape every line of this file:
 *
 * 1. **No pre-selected customer, ever.** `selected` starts as `null` and is
 *    only ever set by a click. A filled-in field is indistinguishable from a
 *    verified one, and a wrong customer can take the company down.
 * 2. **Nothing here writes a customer.** The extraction proposal is displayed
 *    with its evidence and marked "not verified"; the only ways a customer
 *    comes into existence are the picker (an existing one) or a form a human
 *    filled in and explicitly confirmed.
 */
export default function BindChatPage() {
  const { t, lang } = useLanguage();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const user = parseStoredUser();
  const canBind = user?.role === "admin" || user?.role === "ops";

  const kind = searchParams.get("kind");
  const value = searchParams.get("value");
  const chatKey = searchParams.get("chat_key");

  const [chat, setChat] = useState<UnboundChat | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);

  const [proposal, setProposal] = useState<CompanyProposal | null>(null);
  const [proposalDocumentId, setProposalDocumentId] = useState<string | null>(null);

  // Deliberately null. Nothing in this component may set it except a click.
  const [selected, setSelected] = useState<Customer | null>(null);
  const [creating, setCreating] = useState(false);
  const { loading: binding, run } = useMutate();

  const loadChat = useCallback(() => {
    setLoading(true);
    setLoadError(null);
    setMissing(false);
    api
      .get<UnboundResponse>("/identity/unbound")
      .then((res) => {
        const items = res.items ?? [];
        const found =
          items.find(
            (c) =>
              (chatKey && c.chat_key === chatKey) ||
              (kind && value && c.kind === kind && c.value === value),
          ) ?? null;
        if (!found) {
          setMissing(true);
          setChat(null);
        } else {
          setChat(found);
        }
      })
      .catch((err) => setLoadError(getApiError(err) ?? t("common.error")))
      .finally(() => setLoading(false));
  }, [chatKey, kind, value, t]);

  useEffect(() => {
    loadChat();
  }, [loadChat]);

  // Look for a company proposal on any of the held documents. The endpoint
  // returns null when nothing was proposed, so we take the first document that
  // actually has one — no proposal is a normal outcome, not an error.
  useEffect(() => {
    let cancelled = false;
    setProposal(null);
    setProposalDocumentId(null);
    const ids = chat?.document_ids ?? [];
    if (ids.length === 0) return;
    Promise.all(
      ids.map((id) =>
        api
          .get<CompanyProposal | null>(`/intake/documents/${id}/company-proposal`)
          .catch(() => null),
      ),
    ).then((results) => {
      if (cancelled) return;
      const idx = results.findIndex((r) => r !== null);
      if (idx >= 0) {
        setProposal(results[idx]);
        setProposalDocumentId(ids[idx]);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [chat]);

  const handleBind = async () => {
    if (!chat || !selected || !chat.kind || !chat.value) return;
    let released = 0;
    await run(
      async () => {
        const result = await api.post<BindResult>("/identity/bind", {
          kind: chat.kind,
          value: chat.value,
          customer_id: selected.id,
          // What the human actually saw, frozen with the decision. This is the
          // audit trail for a bind that later turns out to be wrong.
          evidence: {
            chat_key: chat.chat_key,
            display_name: chat.display_name,
            corp_name: chat.corp_name,
            waiting_count: chat.waiting_count,
            document_ids: chat.document_ids,
            company_proposal_document_id: proposalDocumentId,
            company_proposal: proposal
              ? {
                  name: proposal.name?.value,
                  address: proposal.address?.value,
                  phone: proposal.phone?.value,
                  contact: proposal.contact?.value,
                  tax_id: proposal.tax_id?.value,
                  source_kind: proposal.source_kind,
                }
              : null,
            customer: {
              id: selected.id,
              code: selected.code,
              name_en: selected.name_en,
              name_zh: selected.name_zh,
              delivery_zone: selected.delivery_zone,
            },
          },
        });
        released = result.released ?? 0;
      },
      {
        success: t("pages.identity.bind.bindSuccess", { count: released }),
        onSuccess: () => navigate("/identity/chats"),
      },
    );
  };

  if (loading) {
    return (
      <Card>
        <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
      </Card>
    );
  }

  if (loadError) {
    return (
      <Card title={t("pages.identity.bind.title")}>
        <Alert type="error" showIcon message={loadError} />
      </Card>
    );
  }

  if (missing || !chat) {
    return (
      <Card title={t("pages.identity.bind.title")}>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Alert type="warning" showIcon message={t("pages.identity.bind.notFound")} />
          <Button onClick={() => navigate("/identity/chats")}>
            {t("pages.identity.bind.back")}
          </Button>
        </Space>
      </Card>
    );
  }

  const bindable = Boolean(chat.kind && chat.value);
  const canSubmit = canBind && bindable && selected !== null;
  const customerLabel = selected
    ? pickName(lang, selected.name_en, selected.name_zh)
    : "";

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card
        title={
          <Space wrap>
            <Button
              icon={<ArrowLeftOutlined />}
              onClick={() => navigate("/identity/chats")}
            >
              {t("pages.identity.bind.back")}
            </Button>
            <Typography.Text strong>{t("pages.identity.bind.title")}</Typography.Text>
          </Space>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={t("pages.identity.bind.safetyNote")}
        />
        {!bindable ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message={t("pages.identity.bind.noChatKey")}
          />
        ) : null}

        <Row gutter={[16, 16]}>
          {/* LEFT — who is talking. Evidence only, nothing actionable. */}
          <Col xs={24} lg={12}>
            <ChatEvidencePanel chat={chat} />
          </Col>

          {/* RIGHT — who you are binding to. */}
          <Col xs={24} lg={12}>
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Typography.Text strong>{t("pages.identity.bind.rightTitle")}</Typography.Text>

              {proposal ? <CompanyProposalPanel proposal={proposal} /> : null}

              {creating ? (
                <CreateCustomerFromProposal
                  proposal={proposal}
                  onCreated={(created) => {
                    // Selecting the customer the human just created is not a
                    // guess: they typed every field and ticked the check.
                    setSelected(created);
                    setCreating(false);
                  }}
                  onCancel={() => setCreating(false)}
                />
              ) : (
                <>
                  <CustomerPicker value={selected} onChange={setSelected} />

                  {selected ? (
                    <CustomerSummary customer={selected} />
                  ) : (
                    <Alert
                      type="info"
                      showIcon
                      message={t("pages.identity.bind.noneSelected")}
                      description={t("pages.identity.bind.noneSelectedHint")}
                    />
                  )}

                  <Button icon={<PlusOutlined />} onClick={() => setCreating(true)}>
                    {t("pages.identity.bind.createNew")}
                  </Button>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {t("pages.identity.bind.createNewHint")}
                  </Typography.Text>
                </>
              )}
            </Space>
          </Col>
        </Row>
      </Card>

      <Card size="small">
        <Space wrap>
          <Popconfirm
            title={t("pages.identity.bind.bindConfirmTitle", {
              chat: chat.display_name ?? chat.chat_key,
              customer: customerLabel,
            })}
            description={t("pages.identity.bind.bindConfirmBody")}
            okText={t("common.confirm")}
            cancelText={t("common.cancel")}
            disabled={!canSubmit || binding}
            onConfirm={handleBind}
          >
            <Button
              type="primary"
              size="large"
              icon={<LinkOutlined />}
              loading={binding}
              disabled={!canSubmit}
            >
              {t("pages.identity.bind.bindAction")}
            </Button>
          </Popconfirm>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("pages.identity.bind.safetyNote")}
          </Typography.Text>
        </Space>
      </Card>
    </Space>
  );
}
