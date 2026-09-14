import { useEffect, useState } from "react";
import { Alert, Button, Empty, Space, Tag, Typography, theme } from "antd";
import { DownloadOutlined, FileImageOutlined, EyeOutlined, EyeInvisibleOutlined } from "@ant-design/icons";
import { api, getApiError } from "../../api/client";
import client from "../../api/client";
import { useLanguage } from "../../i18n";
import { formatDateTime } from "../../utils/format";
import type { ExtractionPayload, IntakeDocumentSummary } from "./types";

interface Props {
  /** Summary fetched by the parent, so the bind screen only loads each doc once. */
  doc: IntakeDocumentSummary;
}

/**
 * The evidence for one held document: what arrived, the original file, and the
 * text the pipeline actually read.
 *
 * The point is that a human binding this chat can see the order itself — the
 * picture or the message — and not just a company name. The original file is
 * loaded on demand as a blob because `/intake/documents/{id}/file` sits behind
 * the Bearer token and a plain `<img src>` would send no Authorization header.
 */
export default function HeldDocumentEvidence({ doc }: Props) {
  const { t } = useLanguage();
  const { token } = theme.useToken();
  const [lines, setLines] = useState<string[] | null>(null);
  const [extractError, setExtractError] = useState<string | null>(null);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [showOriginal, setShowOriginal] = useState(false);
  const [originalError, setOriginalError] = useState<string | null>(null);
  const [originalLoading, setOriginalLoading] = useState(false);

  // The extracted text is the only readable form of a message document — the
  // document endpoint does not expose `document_meta`. Fetch it via the job.
  useEffect(() => {
    let cancelled = false;
    setLines(null);
    setExtractError(null);
    if (!doc.job_id) {
      setLines([]);
      return;
    }
    api
      .get<ExtractionPayload>(`/intake/extractions/${doc.job_id}`)
      .then((payload) => {
        if (cancelled) return;
        const raw = payload.raw_output?.lines ?? [];
        setLines(
          raw
            .map((l) => (l.raw_text ?? "").trim())
            .filter((s) => s.length > 0),
        );
      })
      .catch((err) => {
        if (!cancelled) setExtractError(getApiError(err) ?? t("common.error"));
      });
    return () => {
      cancelled = true;
    };
  }, [doc.job_id, t]);

  // Revoke the object URL when the document changes or the panel unmounts —
  // otherwise every visit to the bind screen leaks a blob.
  useEffect(() => {
    return () => {
      if (imgUrl) URL.revokeObjectURL(imgUrl);
    };
  }, [imgUrl]);

  const isImage = doc.source_type === "image";

  const loadOriginal = async () => {
    setOriginalLoading(true);
    setOriginalError(null);
    try {
      const res = await client.get(`/intake/documents/${doc.id}/file`, {
        responseType: "blob",
      });
      setImgUrl(URL.createObjectURL(res.data as Blob));
      setShowOriginal(true);
    } catch (err) {
      setOriginalError(getApiError(err) ?? t("common.error"));
    } finally {
      setOriginalLoading(false);
    }
  };

  const downloadOriginal = async () => {
    try {
      const res = await client.get(`/intake/documents/${doc.id}/file`, {
        responseType: "blob",
      });
      const url = URL.createObjectURL(res.data as Blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = doc.original_filename ?? "intake";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setOriginalError(getApiError(err) ?? t("common.error"));
    }
  };

  return (
    <Space direction="vertical" size="small" style={{ width: "100%" }}>
      <Space wrap size="small">
        <Tag>
          {t("pages.identity.bind.sourceType")}: {doc.source_type}
        </Tag>
        {doc.original_filename ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {doc.original_filename}
          </Typography.Text>
        ) : null}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {formatDateTime(doc.created_at)}
        </Typography.Text>
      </Space>

      <Space wrap size="small">
        {isImage ? (
          <Button
            size="small"
            icon={showOriginal ? <EyeInvisibleOutlined /> : <EyeOutlined />}
            loading={originalLoading}
            onClick={() => {
              if (showOriginal) {
                setShowOriginal(false);
              } else if (imgUrl) {
                setShowOriginal(true);
              } else {
                void loadOriginal();
              }
            }}
          >
            {showOriginal ? t("pages.identity.bind.hideOriginal") : t("pages.identity.bind.viewOriginal")}
          </Button>
        ) : null}
        <Button size="small" icon={<DownloadOutlined />} onClick={downloadOriginal}>
          {t("pages.intake.downloadOriginal")}
        </Button>
      </Space>

      {originalError ? <Alert type="error" showIcon message={originalError} /> : null}

      {isImage && showOriginal ? (
        imgUrl ? (
          <img
            src={imgUrl}
            alt={doc.original_filename ?? "intake document"}
            style={{
              maxWidth: "100%",
              maxHeight: 420,
              border: `1px solid ${token.colorBorder}`,
              borderRadius: token.borderRadius,
              background: token.colorFillQuaternary,
            }}
          />
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("pages.identity.bind.originalUnavailable")}
          </Typography.Text>
        )
      ) : null}

      <div>
        <Typography.Text strong style={{ fontSize: 12 }}>
          {t("pages.identity.bind.extractedText")}
        </Typography.Text>
        {extractError ? (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 6 }}
            message={t("pages.identity.bind.documentLoadError")}
            description={extractError}
          />
        ) : lines === null ? (
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t("common.loading")}
            </Typography.Text>
          </div>
        ) : lines.length === 0 ? (
          <div style={{ marginTop: 6 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t("pages.identity.bind.noExtractedText")}
            </Typography.Text>
          </div>
        ) : (
          <div
            style={{
              marginTop: 6,
              padding: 10,
              borderRadius: token.borderRadiusSM,
              background: token.colorFillQuaternary,
              border: `1px solid ${token.colorSplit}`,
              maxHeight: 220,
              overflow: "auto",
            }}
          >
            {lines.map((line, i) => (
              <div
                key={i}
                style={{ fontFamily: "monospace", fontSize: 12, whiteSpace: "pre-wrap" }}
              >
                {line}
              </div>
            ))}
          </div>
        )}
      </div>
    </Space>
  );
}

/** Placeholder for a chat whose held documents could not be fetched. */
export function HeldDocumentsEmpty({ message }: { message?: string }) {
  const { t } = useLanguage();
  return (
    <Empty
      image={<FileImageOutlined style={{ fontSize: 28 }} />}
      imageStyle={{ height: 32 }}
      description={
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {message ?? t("pages.identity.bind.noHeld")}
        </Typography.Text>
      }
    />
  );
}
