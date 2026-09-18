import { useEffect, useState } from "react";
import {
  Alert,
  App as AntdApp,
  Button,
  Drawer,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { CheckOutlined, LinkOutlined, WarningOutlined } from "@ant-design/icons";
import { api, getApiError } from "../../api/client";
import { useLanguage } from "../../i18n";
import ConfidenceTag from "../ConfidenceTag";
import StatusTag from "../StatusTag";

interface ReviewLine {
  raw_text?: string;
  matched_product_name?: string | null;
  quantity?: number | null;
  unit?: string | null;
  confidence?: number | null;
  match_method?: string;
  review_reasons?: string[];
  cancelled?: boolean;
}

interface RawOutput {
  form_type?: string | null;
  is_handwritten?: boolean;
  ocr_overall_confidence?: number | null;
  requires_human_review?: boolean;
  cancelled_lines?: unknown[];
  lines?: ReviewLine[];
  parser_notes?: string;
}

interface ExtractionPayload {
  raw_output?: RawOutput;
  overall_confidence?: number | null;
  parser_notes?: string;
}

interface Props {
  open: boolean;
  jobId: string;
  fileUrl?: string | null;
  canConfirm: boolean;
  /** The conversation has no customer, so no order can be created yet. */
  held?: boolean;
  /** The bound customer's name, when there is one. */
  customerLabel?: string | null;
  /** Open the bind step. The caller owns that drawer. */
  onChooseCustomer?: () => void;
  onClose: () => void;
  onConfirmed?: () => void;
}

/**
 * Human review screen for a `needs_review` intake job (handwritten / low-
 * confidence notes). Shows the original note next to the OCR draft, highlights
 * every flagged/cancelled line, and exposes a single explicit "Confirm &
 * submit" action. The pipeline NEVER auto-submits — only a person clicking
 * this button creates the order.
 *
 * It also owns the FIRST question a reviewer hits: does this conversation even
 * have a customer? The server refuses to create an order without one, so this
 * screen states the binding up front and, when it is missing, turns Confirm
 * into the step that fixes it rather than a button that can only fail.
 */
export default function IntakeReviewDrawer({
  open,
  jobId,
  fileUrl,
  canConfirm,
  held = false,
  customerLabel = null,
  onChooseCustomer,
  onClose,
  onConfirmed,
}: Props) {
  const { t } = useLanguage();
  const { message } = AntdApp.useApp();
  const [data, setData] = useState<ExtractionPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [imgError, setImgError] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    setData(null);
    setImgError(false);
    setConfirmError(null);
    api
      .get<ExtractionPayload>(`/intake/extractions/${jobId}`)
      .then((res) => setData(res))
      .catch((err) => setError(getApiError(err) ?? t("common.error")))
      .finally(() => setLoading(false));
  }, [open, jobId, t]);

  const raw = data?.raw_output;
  const lines = raw?.lines ?? [];
  const orderedCount = lines.filter((l) => !l.cancelled).length;
  const cancelledCount = lines.filter((l) => l.cancelled).length;
  // Matches ConfidenceTag: ≥0.95 is green. Below that we may honestly warn
  // that the parse is uncertain; at or above it, asking for a check is still
  // right but claiming low confidence would be a lie.
  const isLowConfidence =
    typeof raw?.ocr_overall_confidence === "number" &&
    raw.ocr_overall_confidence < 0.95;

  const handleConfirm = async () => {
    // A held conversation cannot produce an order and the server will say so.
    // Offer the step that unblocks it instead of an error to decode: the
    // operator asked to create this order, so take them to the one thing
    // standing in the way rather than to a dead end.
    if (held) {
      onChooseCustomer?.();
      return;
    }
    setConfirmError(null);
    setConfirming(true);
    try {
      await api.post(`/intake/jobs/${jobId}/confirm-review`);
      message.success(t("pages.intake.review.confirmed"));
      onConfirmed?.();
      onClose();
    } catch (err) {
      // Inline, not a toast: a refusal here is something the operator has to
      // act on, and a toast that vanishes takes the reason with it.
      setConfirmError(getApiError(err) ?? t("common.error"));
    } finally {
      setConfirming(false);
    }
  };

  const columns = [
    {
      title: "#",
      key: "idx",
      width: 40,
      render: (_: unknown, _r: ReviewLine, i: number) => i + 1,
    },
    {
      title: t("pages.intake.review.colProduct"),
      dataIndex: "matched_product_name",
      render: (_: unknown, r: ReviewLine) => r.matched_product_name || r.raw_text || "—",
    },
    {
      title: t("pages.intake.review.colQty"),
      dataIndex: "quantity",
      width: 80,
      render: (v: number | null) => (v ?? "—"),
    },
    {
      title: t("pages.intake.review.colUnit"),
      dataIndex: "unit",
      width: 70,
      render: (v: string | null) => v || "—",
    },
    {
      title: t("pages.intake.review.colConf"),
      dataIndex: "confidence",
      width: 100,
      render: (v: number | null) =>
        typeof v === "number" ? <ConfidenceTag value={v} /> : "—",
    },
    {
      title: t("pages.intake.review.flags"),
      key: "flags",
      render: (_: unknown, r: ReviewLine) =>
        r.review_reasons && r.review_reasons.length ? (
          <Space direction="vertical" size={2}>
            {r.review_reasons.map((reason, i) => (
              <Tag key={i} color={r.cancelled ? "red" : "orange"}>
                {reason}
              </Tag>
            ))}
          </Space>
        ) : (
          <Tag color="green">ok</Tag>
        ),
    },
  ];

  return (
    <Drawer
      title={t("pages.intake.review.title")}
      open={open}
      onClose={onClose}
      width={840}
      footer={
        <Space direction="vertical" style={{ width: "100%" }} size={8}>
          {/* The refusal, where the button is — not a toast at the top of a
              screen the operator has already looked away from. */}
          {confirmError ? <Alert type="error" showIcon message={confirmError} /> : null}
          <Space style={{ width: "100%", justifyContent: "flex-end" }}>
            <Button onClick={onClose}>{t("common.cancel")}</Button>
            <Button
              type="primary"
              icon={held ? <LinkOutlined /> : <CheckOutlined />}
              loading={confirming}
              disabled={!canConfirm}
              onClick={handleConfirm}
            >
              {/* When the conversation has no customer this button cannot
                  succeed, so it must not pretend to. It still does something:
                  it opens the one step that unblocks the order. */}
              {held
                ? t("pages.intake.bind.bindToContinue")
                : t("pages.intake.review.confirm")}
            </Button>
          </Space>
        </Space>
      }
    >
      {loading ? (
        <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
      ) : error ? (
        <Alert type="error" message={error} showIcon />
      ) : raw ? (
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          {/* The gate, stated first. Without it the reviewer only discovers the
              missing customer by clicking Confirm and reading a refusal. */}
          {held ? (
            <Alert
              type="warning"
              showIcon
              message={t("pages.intake.bind.notBound")}
              description={t("pages.intake.bind.notBoundHint")}
              action={
                onChooseCustomer ? (
                  <Button
                    size="small"
                    type="primary"
                    icon={<LinkOutlined />}
                    onClick={onChooseCustomer}
                  >
                    {t("pages.intake.bind.chooseCustomer")}
                  </Button>
                ) : null
              }
            />
          ) : customerLabel ? (
            <Alert
              type="success"
              showIcon
              message={t("pages.intake.bind.boundTo", { customer: customerLabel })}
            />
          ) : null}

          <Space wrap>
            <StatusTag domain="intake" value="needs_review" />
            <Typography.Text strong>
              {/* Only claim uncertainty when the parse actually was uncertain.
                  Every order stops here now, confident or not. */}
              {isLowConfidence
                ? t("pages.intake.review.needsReview")
                : t("pages.intake.review.verifyTitle")}
            </Typography.Text>
            {typeof raw.ocr_overall_confidence === "number" && (
              <ConfidenceTag value={raw.ocr_overall_confidence} />
            )}
            {raw.form_type && <Tag color="blue">{raw.form_type}</Tag>}
            {raw.is_handwritten && <Tag color="volcano">handwritten</Tag>}
          </Space>

          <Alert
            type={isLowConfidence ? "warning" : "info"}
            showIcon
            icon={isLowConfidence ? <WarningOutlined /> : undefined}
            message={
              isLowConfidence
                ? t("pages.intake.review.needsReview")
                : t("pages.intake.review.verifyTitle")
            }
            description={
              isLowConfidence
                ? t("pages.intake.review.lowConfidence")
                : t("pages.intake.review.verifyBody")
            }
          />

          <Space align="start" size="large" style={{ width: "100%" }}>
            <div style={{ flex: "0 0 320px" }}>
              <Typography.Text strong>{t("pages.intake.review.original")}</Typography.Text>
              <div style={{ marginTop: 8 }}>
                {fileUrl && !imgError ? (
                  <img
                    src={fileUrl}
                    alt="original note"
                    style={{
                      maxWidth: "100%",
                      border: "1px solid #f0f0f0",
                      borderRadius: 4,
                    }}
                    onError={() => setImgError(true)}
                  />
                ) : (
                  <Typography.Text type="secondary">
                    {t("pages.intake.review.noImage")}
                  </Typography.Text>
                )}
              </div>
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <Typography.Text strong>{t("pages.intake.review.draft")}</Typography.Text>
              <Table<ReviewLine>
                rowKey={(_, i) => String(i)}
                size="small"
                pagination={false}
                columns={columns}
                dataSource={lines}
                style={{ marginTop: 8 }}
                onRow={(r) => ({
                  style: r.cancelled
                    ? {
                        background: "#fff1f0",
                        textDecoration: "line-through",
                        color: "#cf1322",
                      }
                    : r.review_reasons && r.review_reasons.length
                    ? { background: "#fff7e6" }
                    : undefined,
                })}
              />
            </div>
          </Space>

          <Alert
            type="info"
            showIcon
            message={t("pages.intake.review.willOrder", {
              count: orderedCount,
              cancelled: cancelledCount,
            })}
          />
        </Space>
      ) : (
        <Typography.Text type="secondary">{t("pages.intake.extractionEmpty")}</Typography.Text>
      )}
    </Drawer>
  );
}
