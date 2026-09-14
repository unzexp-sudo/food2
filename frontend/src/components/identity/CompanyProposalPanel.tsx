import { Space, Tag, Typography, theme } from "antd";
import { useLanguage } from "../../i18n";
import ConfidenceTag from "../ConfidenceTag";
import type { CompanyProposal, FieldDraft } from "./types";

interface Props {
  proposal: CompanyProposal;
}

/**
 * The extraction's company proposal, shown as **pre-fill material only**.
 *
 * Every field is rendered with the source line it was cut from and its
 * confidence, and every field that has a value is visibly tagged
 * "extracted — not verified". There is deliberately no button here that writes
 * anything: this panel exists so a human can read the evidence, and the only
 * thing that turns a proposal into a customer is a person submitting a form.
 */
export default function CompanyProposalPanel({ proposal }: Props) {
  const { t } = useLanguage();
  const { token } = theme.useToken();

  const fields: { key: string; label: string; draft: FieldDraft }[] = [
    { key: "name", label: t("pages.identity.bind.fieldNameZh"), draft: proposal.name },
    { key: "address", label: t("pages.identity.bind.fieldAddress"), draft: proposal.address },
    { key: "phone", label: t("pages.identity.bind.fieldPhone"), draft: proposal.phone },
    { key: "contact", label: t("pages.identity.bind.fieldContact"), draft: proposal.contact },
    { key: "taxId", label: t("pages.identity.bind.taxId"), draft: proposal.tax_id },
  ];

  return (
    <div
      style={{
        border: `1px solid ${token.colorWarningBorder}`,
        borderRadius: token.borderRadius,
        padding: 12,
        background: token.colorWarningBg,
      }}
    >
      <Space direction="vertical" size={4} style={{ width: "100%" }}>
        <Space wrap size="small">
          <Typography.Text strong>{t("pages.identity.bind.proposalTitle")}</Typography.Text>
          <Tag color="orange">{t("pages.identity.bind.extractedNotVerified")}</Tag>
          {proposal.source_kind ? <Tag>{proposal.source_kind}</Tag> : null}
        </Space>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t("pages.identity.bind.proposalHint")}
        </Typography.Text>
      </Space>

      <Space direction="vertical" size="small" style={{ width: "100%", marginTop: 10 }}>
        {fields.map(({ key, label, draft }) => (
          <div key={key}>
            <Space wrap size="small">
              <Typography.Text style={{ fontSize: 12 }} type="secondary">
                {label}
              </Typography.Text>
              {draft && draft.value ? (
                <>
                  <Typography.Text strong>{draft.value}</Typography.Text>
                  <Tag color="orange">{t("pages.identity.bind.extractedNotVerified")}</Tag>
                  <ConfidenceTag value={draft.confidence} />
                </>
              ) : (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {t("pages.identity.bind.fieldNotFound")}
                </Typography.Text>
              )}
            </Space>
            {draft && draft.value ? (
              <div>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  {t("pages.identity.bind.evidence")}:{" "}
                </Typography.Text>
                <Typography.Text
                  style={{ fontSize: 11, fontFamily: "monospace" }}
                  type="secondary"
                >
                  {draft.evidence ?? t("pages.identity.bind.noEvidence")}
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  {" · "}
                  {t("pages.identity.bind.method")}: {draft.method}
                </Typography.Text>
              </div>
            ) : null}
          </div>
        ))}
      </Space>

      {proposal.raw_excerpt ? (
        <div style={{ marginTop: 10 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("pages.identity.bind.rawExcerpt")}
          </Typography.Text>
          <pre
            style={{
              margin: "4px 0 0",
              padding: 8,
              borderRadius: token.borderRadiusSM,
              border: `1px solid ${token.colorSplit}`,
              background: token.colorBgContainer,
              maxHeight: 160,
              overflow: "auto",
              fontSize: 11,
              whiteSpace: "pre-wrap",
            }}
          >
            {proposal.raw_excerpt}
          </pre>
        </div>
      ) : null}
    </div>
  );
}
