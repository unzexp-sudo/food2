import { useEffect, useState } from "react";
import { Alert, Button, Drawer, Space, Table, Tag, Typography } from "antd";
import { CheckOutlined, WarningOutlined } from "@ant-design/icons";
import { api, getApiError } from "../../api/client";
import { useMutate } from "../../api/hooks";
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
  onClose: () => void;
  onConfirmed?: () => void;
}

/**
 * Human review screen for a `needs_review` intake job (handwritten / low-
 * confidence notes). Shows the original note next to the OCR draft, highlights
 * every flagged/cancelled line, and exposes a single explicit "Confirm &
 * submit" action. The pipeline NEVER auto-submits — only a person clicking
 * this button creates the order.
 */
export default function IntakeReviewDrawer({
  open,
  jobId,
  fileUrl,
  canConfirm,
  onClose,
  onConfirmed,
}: Props) {
  const { t } = useLanguage();
  const [data, setData] = useState<ExtractionPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [imgError, setImgError] = useState(false);
  const { loading: confirming, run } = useMutate();

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    setData(null);
    setImgError(false);
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

  const handleConfirm = async () => {
    await run(
      () => api.post(`/intake/jobs/${jobId}/confirm-review`),
      {
        success: t("pages.intake.review.confirmed"),
        onSuccess: () => {
          onConfirmed?.();
          onClose();
        },
      },
    );
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
        <Space style={{ float: "right" }}>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button
            type="primary"
            icon={<CheckOutlined />}
            loading={confirming}
            disabled={!canConfirm}
            onClick={handleConfirm}
          >
            {t("pages.intake.review.confirm")}
          </Button>
        </Space>
      }
    >
      {loading ? (
        <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
      ) : error ? (
        <Alert type="error" message={error} showIcon />
      ) : raw ? (
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Space wrap>
            <StatusTag domain="intake" value="needs_review" />
            <Typography.Text strong>{t("pages.intake.review.needsReview")}</Typography.Text>
            {typeof raw.ocr_overall_confidence === "number" && (
              <ConfidenceTag value={raw.ocr_overall_confidence} />
            )}
            {raw.form_type && <Tag color="blue">{raw.form_type}</Tag>}
            {raw.is_handwritten && <Tag color="volcano">handwritten</Tag>}
          </Space>

          <Alert
            type="warning"
            showIcon
            icon={<WarningOutlined />}
            message={t("pages.intake.review.needsReview")}
            description={t("pages.intake.review.lowConfidence")}
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
