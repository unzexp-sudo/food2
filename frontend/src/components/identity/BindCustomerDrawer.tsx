import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App as AntdApp,
  Button,
  Col,
  Drawer,
  Popconfirm,
  Row,
  Space,
  Typography,
} from "antd";
import { LinkOutlined, PlusOutlined } from "@ant-design/icons";
import { api, getApiError } from "../../api/client";
import { useLanguage } from "../../i18n";
import { pickName } from "../../utils/format";
import { parseStoredUser } from "../../types";
import ChatEvidencePanel from "./ChatEvidencePanel";
import CompanyProposalPanel from "./CompanyProposalPanel";
import CustomerPicker, { CustomerSummary } from "./CustomerPicker";
import CreateCustomerFromProposal from "./CreateCustomerFromProposal";
import type {
  BindResult,
  CompanyProposal,
  Customer,
  UnboundChat,
} from "./types";

interface UnboundResponse {
  items: UnboundChat[];
  total: number;
}

interface Props {
  open: boolean;
  /** The conversation to bind. Any one of these identifies it. */
  kind?: string | null;
  value?: string | null;
  chatKey?: string | null;
  onClose: () => void;
  /** Fired after a successful bind, before `onClose`. */
  onBound?: (result: BindResult, customer: Customer) => void;
}

/**
 * THE BIND DRAWER — the one decision that matters, available where the work is.
 *
 * This is `BindChatPage`'s body made controlled, so the same decision can be
 * taken without leaving the intake inbox. It was extracted rather than
 * rewritten: the two rules below are the whole point of the screen, and they
 * had already been worked out.
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
 *
 * Two deliberate differences from the page version:
 *
 * - **Success reports the REAL released count.** `useMutate` evaluates its
 *   `success` string when the call is *constructed*, so a count assigned inside
 *   the async body was always 0 — the toast said "released 0" even when the
 *   bind had just released every held order. The count is the whole message
 *   here, so the message is issued after the response.
 * - **Failures are inline, not a toast.** A refused bind is a decision the
 *   operator has to act on (wrong customer, already bound elsewhere), and a
 *   toast that disappears is the wrong container for it.
 */
export default function BindCustomerDrawer({
  open,
  kind,
  value,
  chatKey,
  onClose,
  onBound,
}: Props) {
  const { t, lang } = useLanguage();
  const { message } = AntdApp.useApp();
  const user = parseStoredUser();
  const canBind = user?.role === "admin" || user?.role === "ops";

  const [chat, setChat] = useState<UnboundChat | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);

  const [proposal, setProposal] = useState<CompanyProposal | null>(null);
  const [proposalDocumentId, setProposalDocumentId] = useState<string | null>(null);

  // Deliberately null. Nothing in this component may set it except a click.
  const [selected, setSelected] = useState<Customer | null>(null);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [bindError, setBindError] = useState<string | null>(null);

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

  // Every open starts clean: no stale selection, no stale error, no stale
  // proposal from the conversation viewed a moment ago.
  useEffect(() => {
    if (!open) return;
    setSelected(null);
    setCreating(false);
    setBindError(null);
    setProposal(null);
    setProposalDocumentId(null);
    loadChat();
  }, [open, loadChat]);

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
    const customer = selected;
    setBindError(null);
    setSaving(true);
    try {
      const result = await api.post<BindResult>("/identity/bind", {
        kind: chat.kind,
        value: chat.value,
        customer_id: customer.id,
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
            id: customer.id,
            code: customer.code,
            name_en: customer.name_en,
            name_zh: customer.name_zh,
            delivery_zone: customer.delivery_zone,
          },
        },
      });
      // The real count, issued after the response rather than before it.
      message.success(
        t("pages.identity.bind.bindSuccess", { count: result.released ?? 0 }),
      );
      onBound?.(result, customer);
      onClose();
    } catch (err) {
      setBindError(getApiError(err) ?? t("common.error"));
    } finally {
      setSaving(false);
    }
  };

  const bindable = Boolean(chat?.kind && chat?.value);
  const canSubmit = canBind && bindable && selected !== null;
  const customerLabel = selected
    ? pickName(lang, selected.name_en, selected.name_zh)
    : "";

  return (
    <Drawer
      title={t("pages.identity.bind.title")}
      open={open}
      onClose={onClose}
      width={840}
      footer={
        <Space style={{ float: "right" }}>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Popconfirm
            title={t("pages.identity.bind.bindConfirmTitle", {
              chat: chat?.display_name ?? chat?.chat_key ?? "",
              customer: customerLabel,
            })}
            description={t("pages.identity.bind.bindConfirmBody")}
            okText={t("common.confirm")}
            cancelText={t("common.cancel")}
            disabled={!canSubmit || saving}
            onConfirm={handleBind}
          >
            <Button
              type="primary"
              icon={<LinkOutlined />}
              loading={saving}
              disabled={!canSubmit}
            >
              {t("pages.identity.bind.bindAction")}
            </Button>
          </Popconfirm>
        </Space>
      }
    >
      {loading ? (
        <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
      ) : loadError ? (
        <Alert type="error" showIcon message={loadError} />
      ) : missing || !chat ? (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Alert type="warning" showIcon message={t("pages.identity.bind.notFound")} />
        </Space>
      ) : (
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Alert type="info" showIcon message={t("pages.identity.bind.safetyNote")} />

          {/* What this single decision is worth. Binding is per conversation,
              so it releases every held order at once — saying so is the
              difference between one decision and seventeen. */}
          {chat.waiting_count > 0 ? (
            <Alert
              type="success"
              showIcon
              message={t("pages.identity.bind.releasesHint", {
                count: chat.waiting_count,
              })}
            />
          ) : null}

          {bindError ? (
            <Alert
              type="error"
              showIcon
              message={t("pages.identity.bind.bindFailed")}
              description={bindError}
            />
          ) : null}

          {!bindable ? (
            <Alert type="warning" showIcon message={t("pages.identity.bind.noChatKey")} />
          ) : null}

          <Row gutter={[16, 16]}>
            {/* LEFT — who is talking. Evidence only, nothing actionable. */}
            <Col xs={24} lg={12}>
              <ChatEvidencePanel chat={chat} />
            </Col>

            {/* RIGHT — who you are binding to. */}
            <Col xs={24} lg={12}>
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Typography.Text strong>
                  {t("pages.identity.bind.rightTitle")}
                </Typography.Text>

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
        </Space>
      )}
    </Drawer>
  );
}
