import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App as AntdApp,
  Button,
  Drawer,
  Image,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  type TableColumnsType,
} from "antd";
import {
  CheckOutlined,
  CompressOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ExpandOutlined,
  FileExcelOutlined,
  LinkOutlined,
  PlusOutlined,
  StopOutlined,
  UndoOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import client, { api, getApiError, type Page } from "../../api/client";
import { useLanguage } from "../../i18n";
import { pickName } from "../../utils/format";
import ConfidenceTag from "../ConfidenceTag";
import StatusTag from "../StatusTag";

interface ReviewLine {
  raw_text?: string;
  /** The name as the customer wrote it. Absent on rows written before it was
   *  stored, which is why every read of it has a fallback. */
  product_name?: string | null;
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

interface Rejection {
  reason: string;
  note?: string | null;
  by?: string | null;
  at?: string | null;
}

interface ExtractionPayload {
  raw_output?: RawOutput;
  overall_confidence?: number | null;
  parser_notes?: string;
  /** The source, returned alongside the parse by `_extraction_out`. */
  source_type?: string | null;
  source_text?: string | null;
  original_filename?: string | null;
  rejection?: Rejection | null;
  human_review?: unknown;
}

interface ProductLite {
  id: string;
  sku: string;
  name_en: string;
  name_zh: string;
}

interface UnitLite {
  id: string;
  code: string;
  name_en: string;
  name_zh: string;
}

/** Only the fields the reviewer actually touched. An absent key means "leave
 *  it as the extractor had it", which is what lets the payload carry a change
 *  rather than a whole line the operator may never have looked at. */
interface LineEdit {
  product_name?: string;
  quantity?: number | null;
  unit?: string | null;
}

interface AddedLine {
  key: string;
  product_name: string;
  quantity: number | null;
  unit: string | null;
}

interface ReviewRow {
  key: string;
  /** The extraction's 1-based line number, or null for a line the reviewer
   *  added. Shown as-is so the number in the UI is the number the server
   *  reports in a refusal. */
  line_no: number | null;
  product_name: string;
  quantity: number | null;
  unit: string | null;
  confidence: number | null;
  matched: boolean;
  review_reasons: string[];
  /** Struck through on the note itself — the customer's own cancellation. */
  customer_cancelled: boolean;
  /** Dropped by the reviewer on this screen. */
  removed: boolean;
  added: boolean;
  edited: boolean;
}

/** The reasons a reviewer can refuse an extraction. Must match
 *  `REJECTION_REASONS` in `backend/app/services/intake/service.py` — the
 *  server rejects anything else, and the codes are what get counted. */
const REJECTION_REASONS = [
  "duplicate",
  "not_an_order",
  "wrong_customer",
  "unreadable",
  "other",
] as const;

const TEXT_SOURCES = new Set(["text", "email_body"]);

/**
 * `file_url` arrives as an absolute API path (`/api/v1/intake/...`), but the
 * shared client already has `/api/v1` as its baseURL — axios concatenates the
 * two rather than treating a leading `/` as origin-absolute, so using the value
 * verbatim asks for `/api/v1/api/v1/intake/...` and gets a 404. Strip it.
 */
const apiPath = (url: string) => url.replace(/^\/api\/v1/, "");

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
  onRejected?: () => void;
}

/**
 * Human review screen for a `needs_review` intake job.
 *
 * Shows the original next to the OCR draft and lets a person settle the
 * question the pipeline refuses to answer for itself. It is the only place an
 * order can be created from an extraction, and — since rejections were added —
 * the only place one can be refused.
 *
 * Three things it must get right:
 *
 * 1. **The source has to be visible.** The original is fetched as a BLOB, not
 *    pointed at with an `<img src>`: the endpoint is behind a Bearer token in
 *    `localStorage`, and a bare `<img>`/`<iframe>` sends no Authorization
 *    header. The old code set `src` directly, so the request 401'd, the image
 *    errored, and every document — photos included — rendered "No preview image
 *    for this source type". A message about the source type, caused by auth.
 *
 * 2. **The reviewer can disagree.** Quantity, unit, product, remove a line, add
 *    a line. The corrections go to the ORDER; the stored extraction is left
 *    exactly as the extractor produced it, because the gap between the two is
 *    the only measurement of how often the machine is wrong.
 *
 * 3. **There is a way to say no.** Without it a duplicate had to either become
 *    a second order or sit in the queue forever.
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
  onRejected,
}: Props) {
  const { t, lang } = useLanguage();
  const { message } = AntdApp.useApp();

  const [data, setData] = useState<ExtractionPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  // The original, fetched with the session's credentials and turned into an
  // object URL. `previewError` is separate from `error` so a source that will
  // not load does not hide the parse — the reviewer can still act on the lines.
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);

  // The reviewer's changes.
  const [edits, setEdits] = useState<Record<number, LineEdit>>({});
  const [removed, setRemoved] = useState<number[]>([]);
  const [added, setAdded] = useState<AddedLine[]>([]);

  // Rejection.
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState<string>("duplicate");
  const [rejectNote, setRejectNote] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [rejectError, setRejectError] = useState<string | null>(null);

  // Catalog, for the product and unit pickers.
  const [products, setProducts] = useState<ProductLite[]>([]);
  const [units, setUnits] = useState<UnitLite[]>([]);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    setData(null);
    setConfirmError(null);
    setPreviewError(null);
    setBlobUrl(null);
    setExpanded(false);
    setEdits({});
    setRemoved([]);
    setAdded([]);
    setRejectOpen(false);
    setRejectNote("");
    setRejectReason("duplicate");
    setRejectError(null);
    api
      .get<ExtractionPayload>(`/intake/extractions/${jobId}`)
      .then((res) => setData(res))
      .catch((err) => setError(getApiError(err) ?? t("common.error")))
      .finally(() => setLoading(false));
  }, [open, jobId, t]);

  // The source, as a blob. Revoked when the drawer closes or the job changes —
  // an object URL is a leak until it is released.
  useEffect(() => {
    if (!open || !fileUrl) {
      setBlobUrl(null);
      return;
    }
    let cancelled = false;
    let created: string | null = null;
    setPreviewLoading(true);
    setPreviewError(null);
    client
      .get(apiPath(fileUrl), { params: { inline: 1 }, responseType: "blob" })
      .then((res) => {
        if (cancelled) return;
        created = URL.createObjectURL(res.data as Blob);
        setBlobUrl(created);
      })
      .catch((err) => {
        if (!cancelled) setPreviewError(getApiError(err) ?? t("common.error"));
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [open, fileUrl, t]);

  // Master data for the editors. `page_size` is capped at 100 server-side —
  // asking for more is a guaranteed 422 that renders as an empty picker.
  useEffect(() => {
    if (!open) return;
    api
      .get<Page<ProductLite>>("/products", { page_size: 100, is_active: true })
      .then((r) => setProducts(r.items))
      .catch(() => setProducts([]));
    api
      .get<Page<UnitLite>>("/units", { page_size: 100 })
      .then((r) => setUnits(r.items))
      .catch(() => setUnits([]));
  }, [open]);

  const raw = data?.raw_output;
  const lines = useMemo(() => raw?.lines ?? [], [raw]);
  const sourceType = data?.source_type ?? null;

  const rows = useMemo<ReviewRow[]>(() => {
    const out: ReviewRow[] = lines.map((l, i) => {
      const no = i + 1;
      const e = edits[no];
      return {
        key: `x${no}`,
        line_no: no,
        product_name:
          e?.product_name ??
          l.product_name ??
          l.matched_product_name ??
          l.raw_text ??
          "",
        quantity: e?.quantity !== undefined ? e.quantity : l.quantity ?? null,
        unit: e?.unit !== undefined ? e.unit : l.unit ?? null,
        confidence: l.confidence ?? null,
        matched: !!l.matched_product_name,
        review_reasons: l.review_reasons ?? [],
        customer_cancelled: !!l.cancelled,
        removed: removed.includes(no),
        added: false,
        edited: !!e,
      };
    });
    for (const a of added) {
      out.push({
        key: `a${a.key}`,
        line_no: null,
        product_name: a.product_name,
        quantity: a.quantity,
        unit: a.unit,
        confidence: null,
        matched: true,
        review_reasons: [],
        customer_cancelled: false,
        removed: false,
        added: true,
        edited: true,
      });
    }
    return out;
  }, [lines, edits, removed, added]);

  const kept = useMemo(
    () => rows.filter((r) => !r.removed && !r.customer_cancelled),
    [rows],
  );
  const removedCount = rows.length - kept.length;

  const productOptions = useMemo(() => {
    const opts = products.map((p) => ({
      value: p.name_zh || p.name_en,
      label: pickName(lang, p.name_en, p.name_zh) || p.sku,
    }));
    const seen = new Set(opts.map((o) => o.value));
    for (const r of rows) {
      const name = r.product_name.trim();
      if (name && !seen.has(name)) {
        seen.add(name);
        opts.push({
          value: name,
          label: `${name} — ${t("pages.intake.review.notInCatalog")}`,
        });
      }
    }
    return opts;
  }, [products, rows, lang, t]);

  const unitOptions = useMemo(() => {
    const opts = units.map((u) => ({
      value: u.code,
      label: pickName(lang, u.name_en, u.name_zh) || u.code,
    }));
    const seen = new Set(opts.map((o) => o.value));
    for (const r of rows) {
      const code = (r.unit ?? "").trim();
      if (code && !seen.has(code)) {
        seen.add(code);
        opts.push({ value: code, label: code });
      }
    }
    return opts;
  }, [units, rows, lang]);

  const editLine = useCallback((lineNo: number, patch: LineEdit) => {
    setEdits((prev) => ({ ...prev, [lineNo]: { ...prev[lineNo], ...patch } }));
  }, []);

  const dropLine = useCallback((lineNo: number) => {
    // A line cannot be both corrected and removed: two entries for one line is
    // a 400 from the server ("corrected twice"), and it would mean the payload
    // disagrees with itself about what the reviewer wanted.
    setEdits((prev) => {
      const next = { ...prev };
      delete next[lineNo];
      return next;
    });
    setRemoved((prev) => (prev.includes(lineNo) ? prev : [...prev, lineNo]));
  }, []);

  const restoreLine = useCallback((lineNo: number) => {
    setRemoved((prev) => prev.filter((n) => n !== lineNo));
  }, []);

  const addLine = useCallback(() => {
    setAdded((prev) => [
      ...prev,
      {
        key: `${Date.now()}-${prev.length}`,
        product_name: "",
        quantity: null,
        unit: null,
      },
    ]);
  }, []);

  const editAdded = useCallback((key: string, patch: Partial<AddedLine>) => {
    setAdded((prev) => prev.map((a) => (a.key === key ? { ...a, ...patch } : a)));
  }, []);

  const dropAdded = useCallback((key: string) => {
    setAdded((prev) => prev.filter((a) => a.key !== key));
  }, []);

  const hasEdits = Object.keys(edits).length > 0 || removed.length > 0 || added.length > 0;

  const resetEdits = () => {
    setEdits({});
    setRemoved([]);
    setAdded([]);
  };

  /**
   * The same rule the server enforces, checked before the round trip so the
   * operator reads it next to the field instead of waiting for a refusal.
   *
   * It has to be an explicit check: these cells are `useState`-driven, not
   * form-bound, so an AntD `rules` array on a `Form.Item` with no `name` would
   * be inert — it would typecheck, look right and never run.
   */
  const problem = useMemo(() => {
    if (!kept.length) return t("pages.intake.review.nothingToOrder");
    for (const r of kept) {
      const where = r.added
        ? t("pages.intake.review.addedLine")
        : t("pages.intake.review.lineNo", { line: r.line_no ?? "?" });
      if (!r.product_name.trim()) {
        return t("pages.intake.review.productRequired", { where });
      }
      // `0` is a mis-tapped field, not a small order. Refuse it rather than
      // clamp it — a clamped value records a number nobody typed.
      if (!(Number(r.quantity) > 0)) {
        return t("pages.intake.review.qtyRequired", { where });
      }
    }
    return null;
  }, [kept, t]);

  const buildEdits = () => ({
    lines: [
      ...Object.entries(edits)
        .filter(([no]) => !removed.includes(Number(no)))
        .map(([no, e]) => ({ line_no: Number(no), ...e })),
      ...removed.map((no) => ({ line_no: no, cancelled: true })),
    ],
    added_lines: added
      .filter((a) => a.product_name.trim())
      .map((a) => ({
        product_name: a.product_name.trim(),
        quantity: a.quantity,
        unit: a.unit,
      })),
  });

  const handleConfirm = async () => {
    // A held conversation cannot produce an order and the server will say so.
    // Offer the step that unblocks it instead of an error to decode.
    if (held) {
      onChooseCustomer?.();
      return;
    }
    if (problem) return;
    setConfirmError(null);
    setConfirming(true);
    try {
      await api.post(`/intake/jobs/${jobId}/confirm-review`, buildEdits());
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

  const handleReject = async () => {
    setRejectError(null);
    if (rejectReason === "other" && !rejectNote.trim()) {
      setRejectError(t("pages.intake.review.rejectNoteRequired"));
      return;
    }
    setRejecting(true);
    try {
      await api.post(`/intake/jobs/${jobId}/reject`, {
        reason: rejectReason,
        note: rejectNote.trim() || undefined,
      });
      message.success(t("pages.intake.review.rejected"));
      setRejectOpen(false);
      onRejected?.();
      onClose();
    } catch (err) {
      setRejectError(getApiError(err) ?? t("common.error"));
    } finally {
      setRejecting(false);
    }
  };

  const downloadOriginal = async () => {
    if (!fileUrl) return;
    try {
      const res = await client.get(apiPath(fileUrl), { responseType: "blob" });
      const url = URL.createObjectURL(res.data as Blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = data?.original_filename ?? "intake";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      message.error(getApiError(err) ?? t("common.error"));
    }
  };

  const columns: TableColumnsType<ReviewRow> = [
    {
      title: "#",
      key: "idx",
      width: 46,
      render: (_: unknown, r) =>
        r.line_no === null ? (
          <Tag color="blue" style={{ marginInlineEnd: 0 }}>
            +
          </Tag>
        ) : (
          <Typography.Text type="secondary">{r.line_no}</Typography.Text>
        ),
    },
    {
      title: t("pages.intake.review.colProduct"),
      key: "product",
      render: (_: unknown, r) => (
        <Space direction="vertical" size={2} style={{ width: "100%" }}>
          <Select
            size="small"
            showSearch
            style={{ width: "100%", minWidth: 140 }}
            optionFilterProp="label"
            value={r.product_name || undefined}
            placeholder={t("pages.intake.review.pickProduct")}
            options={productOptions}
            onChange={(v) => {
              if (r.added) {
                const found = added.find((a) => `a${a.key}` === r.key);
                if (found) editAdded(found.key, { product_name: v });
              } else if (r.line_no !== null) {
                editLine(r.line_no, { product_name: v });
              }
            }}
          />
          {/* An unmatched product is the single most common extraction fault,
              and it is invisible in a plain text cell. */}
          {!r.matched && !r.edited ? (
            <Tag color="orange">{t("pages.intake.review.unmatched")}</Tag>
          ) : null}
        </Space>
      ),
    },
    {
      title: t("pages.intake.review.colQty"),
      key: "qty",
      width: 108,
      render: (_: unknown, r) => (
        <InputNumber
          size="small"
          // `min` only keeps negatives out. The boundary value (0) belongs to
          // the explicit guard above — `min={0.01}` here would silently rewrite
          // a typed 0 into 0.01 and record a number nobody entered.
          min={0}
          style={{ width: "100%" }}
          value={r.quantity ?? undefined}
          onChange={(v) => {
            if (r.added) {
              const found = added.find((a) => `a${a.key}` === r.key);
              if (found) editAdded(found.key, { quantity: v ?? null });
            } else if (r.line_no !== null) {
              editLine(r.line_no, { quantity: v ?? null });
            }
          }}
        />
      ),
    },
    {
      title: t("pages.intake.review.colUnit"),
      key: "unit",
      width: 104,
      render: (_: unknown, r) => (
        <Select
          size="small"
          showSearch
          allowClear
          style={{ width: "100%" }}
          optionFilterProp="label"
          value={r.unit || undefined}
          options={unitOptions}
          onChange={(v) => {
            if (r.added) {
              const found = added.find((a) => `a${a.key}` === r.key);
              if (found) editAdded(found.key, { unit: v ?? null });
            } else if (r.line_no !== null) {
              editLine(r.line_no, { unit: v ?? null });
            }
          }}
        />
      ),
    },
    {
      title: t("pages.intake.review.colConf"),
      key: "conf",
      width: 104,
      render: (_: unknown, r) =>
        r.edited ? (
          <Tooltip title={t("pages.intake.review.editedHint")}>
            <Tag color="blue">{t("pages.intake.review.edited")}</Tag>
          </Tooltip>
        ) : typeof r.confidence === "number" ? (
          <ConfidenceTag value={r.confidence} />
        ) : (
          "—"
        ),
    },
    {
      title: t("pages.intake.review.flags"),
      key: "flags",
      render: (_: unknown, r) => (
        <Space direction="vertical" size={2}>
          {r.customer_cancelled ? (
            <Tag color="red">{t("pages.intake.review.customerCancelled")}</Tag>
          ) : null}
          {r.review_reasons.map((reason, i) => (
            <Tag key={i} color="orange">
              {reason}
            </Tag>
          ))}
          {!r.review_reasons.length && !r.customer_cancelled ? (
            <Tag color="green">ok</Tag>
          ) : null}
        </Space>
      ),
    },
    {
      title: "",
      key: "actions",
      width: 46,
      render: (_: unknown, r) => {
        if (r.added) {
          const found = added.find((a) => `a${a.key}` === r.key);
          return (
            <Tooltip title={t("pages.intake.review.removeLine")}>
              <Button
                size="small"
                type="text"
                danger
                icon={<DeleteOutlined />}
                onClick={() => found && dropAdded(found.key)}
              />
            </Tooltip>
          );
        }
        if (r.line_no === null) return null;
        return r.removed ? (
          <Tooltip title={t("pages.intake.review.undoRemove")}>
            <Button
              size="small"
              type="text"
              icon={<UndoOutlined />}
              onClick={() => restoreLine(r.line_no as number)}
            />
          </Tooltip>
        ) : (
          <Tooltip title={t("pages.intake.review.removeLine")}>
            <Button
              size="small"
              type="text"
              danger
              icon={<DeleteOutlined />}
              onClick={() => dropLine(r.line_no as number)}
            />
          </Tooltip>
        );
      },
    },
  ];

  const isLowConfidence =
    typeof raw?.ocr_overall_confidence === "number" &&
    raw.ocr_overall_confidence < 0.95;

  const previewHeight = expanded ? 620 : 400;

  /** The source pane. Never an `<img src>` — see the component docstring. */
  const renderSource = () => {
    if (previewLoading) {
      return (
        <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
      );
    }
    if (previewError) {
      return (
        <Alert
          type="warning"
          showIcon
          message={t("pages.intake.review.previewFailed")}
          description={previewError}
          action={
            <Button size="small" onClick={downloadOriginal}>
              {t("pages.intake.downloadOriginal")}
            </Button>
          }
        />
      );
    }
    // A note is text. Showing it as text is the whole preview — and it is what
    // replaces "No preview image for this source type" on a text order.
    if (TEXT_SOURCES.has(sourceType ?? "") || (data?.source_text && !blobUrl)) {
      return (
        <pre
          style={{
            margin: 0,
            padding: 12,
            background: "#fafafa",
            border: "1px solid #f0f0f0",
            borderRadius: 4,
            maxHeight: previewHeight,
            overflow: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            fontSize: 13,
            lineHeight: 1.7,
          }}
        >
          {data?.source_text ?? t("pages.intake.review.noSourceText")}
        </pre>
      );
    }
    if (!blobUrl) {
      return (
        <Typography.Text type="secondary">
          {t("pages.intake.review.noSource")}
        </Typography.Text>
      );
    }
    if (sourceType === "image") {
      return (
        <Space direction="vertical" size={6} style={{ width: "100%" }}>
          <Image
            src={blobUrl}
            alt={t("pages.intake.review.original")}
            style={{
              maxWidth: "100%",
              maxHeight: previewHeight,
              objectFit: "contain",
              border: "1px solid #f0f0f0",
              borderRadius: 4,
            }}
          />
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            {t("pages.intake.review.zoomHint")}
          </Typography.Text>
        </Space>
      );
    }
    if (sourceType === "pdf") {
      return (
        <Space direction="vertical" size={6} style={{ width: "100%" }}>
          <iframe
            src={blobUrl}
            title={t("pages.intake.review.original")}
            style={{
              width: "100%",
              height: previewHeight,
              border: "1px solid #f0f0f0",
              borderRadius: 4,
            }}
          />
          {/* Some browsers refuse to render a PDF in a frame at all, so the
              way out is offered rather than left to be discovered. */}
          <Space size={8}>
            <Button
              size="small"
              icon={<ExpandOutlined />}
              href={blobUrl}
              target="_blank"
              rel="noreferrer"
            >
              {t("pages.intake.review.openInNewTab")}
            </Button>
            <Button
              size="small"
              icon={<DownloadOutlined />}
              onClick={downloadOriginal}
            >
              {t("pages.intake.downloadOriginal")}
            </Button>
          </Space>
        </Space>
      );
    }
    // excel, or anything else we cannot render. Do not pretend.
    return (
      <Alert
        type="info"
        showIcon
        icon={<FileExcelOutlined />}
        message={data?.original_filename ?? sourceType ?? "—"}
        description={t("pages.intake.review.noInlinePreview")}
        action={
          <Button
            size="small"
            icon={<DownloadOutlined />}
            onClick={downloadOriginal}
          >
            {t("pages.intake.downloadOriginal")}
          </Button>
        }
      />
    );
  };

  return (
    <Drawer
      title={t("pages.intake.review.title")}
      open={open}
      onClose={onClose}
      // Expanded widens the WHOLE drawer, not just the source: the point of
      // expanding is to read the note and the lines together, so hiding the
      // table behind a zoom overlay would defeat it. The default is wide enough
      // for the source pane AND every column of the draft — at 1040 the Flags
      // column was clipped, which is the column that says which cells to
      // distrust.
      width={expanded ? "calc(100vw - 48px)" : 1160}
      footer={
        <Space direction="vertical" style={{ width: "100%" }} size={8}>
          {/* The refusal, where the button is — not a toast at the top of a
              screen the operator has already looked away from. */}
          {confirmError ? <Alert type="error" showIcon message={confirmError} /> : null}
          {problem ? <Alert type="warning" showIcon message={problem} /> : null}
          <Space style={{ width: "100%", justifyContent: "space-between" }}>
            <Space>
              <Button
                danger
                icon={<StopOutlined />}
                disabled={!canConfirm || loading || !raw}
                onClick={() => {
                  setRejectError(null);
                  setRejectOpen(true);
                }}
              >
                {t("pages.intake.review.reject")}
              </Button>
              {hasEdits ? (
                <Button onClick={resetEdits}>
                  {t("pages.intake.review.resetEdits")}
                </Button>
              ) : null}
            </Space>
            <Space>
              <Button onClick={onClose}>{t("common.cancel")}</Button>
              <Button
                type="primary"
                icon={held ? <LinkOutlined /> : <CheckOutlined />}
                loading={confirming}
                disabled={!canConfirm || !!problem}
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

          {data?.rejection ? (
            <Alert
              type="error"
              showIcon
              message={t("pages.intake.review.wasRejected", {
                reason: t(
                  `pages.intake.review.rejectReason.${data.rejection.reason}`,
                ),
              })}
              description={data.rejection.note ?? undefined}
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
            {sourceType && <Tag>{sourceType}</Tag>}
            <Tooltip title={t("pages.intake.review.expandHint")}>
              <Button
                size="small"
                icon={expanded ? <CompressOutlined /> : <ExpandOutlined />}
                onClick={() => setExpanded((v) => !v)}
              >
                {expanded
                  ? t("pages.intake.review.collapse")
                  : t("pages.intake.review.expand")}
              </Button>
            </Tooltip>
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

          {/* A plain flex row, not antd's `<Space>`. `Space` wraps every child
              in a shrink-to-fit `.ant-space-item`, so a PERCENTAGE flex-basis
              inside it resolves against an indefinite width and collapses to
              the content — which is exactly what the expanded source pane did,
              staying ~100px wide while the table took the rest. An absolute
              basis (360px) worked fine, which is what hid the bug: the narrow
              default looked correct and only the expanded state was broken. */}
          <div
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 16,
              width: "100%",
            }}
          >
            <div style={{ flex: expanded ? "0 0 52%" : "0 0 360px", minWidth: 0 }}>
              <Space
                style={{ width: "100%", justifyContent: "space-between" }}
                size={4}
              >
                <Typography.Text strong>
                  {t("pages.intake.review.original")}
                </Typography.Text>
                {data?.original_filename ? (
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    {data.original_filename}
                  </Typography.Text>
                ) : null}
              </Space>
              <div style={{ marginTop: 8 }}>{renderSource()}</div>
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <Space
                style={{ width: "100%", justifyContent: "space-between" }}
                size={4}
              >
                <Typography.Text strong>
                  {t("pages.intake.review.draft")}
                </Typography.Text>
                <Button size="small" icon={<PlusOutlined />} onClick={addLine}>
                  {t("pages.intake.review.addLine")}
                </Button>
              </Space>
              <Table<ReviewRow>
                rowKey="key"
                size="small"
                pagination={false}
                columns={columns}
                dataSource={rows}
                style={{ marginTop: 8 }}
                scroll={{ x: "max-content" }}
                onRow={(r) => ({
                  style: r.removed
                    ? {
                        background: "#fff1f0",
                        textDecoration: "line-through",
                        color: "#cf1322",
                      }
                    : r.customer_cancelled
                      ? { background: "#fff1f0", color: "#cf1322" }
                      : r.edited
                        ? { background: "#e6f4ff" }
                        : r.review_reasons.length
                          ? { background: "#fff7e6" }
                          : undefined,
                })}
              />
            </div>
          </div>

          <Alert
            type="info"
            showIcon
            message={t("pages.intake.review.willOrder", {
              count: kept.length,
              cancelled: removedCount,
            })}
            description={hasEdits ? t("pages.intake.review.editsApplied") : undefined}
          />
        </Space>
      ) : (
        <Typography.Text type="secondary">
          {t("pages.intake.extractionEmpty")}
        </Typography.Text>
      )}

      <Modal
        title={t("pages.intake.review.rejectTitle")}
        open={rejectOpen}
        onCancel={() => setRejectOpen(false)}
        onOk={handleReject}
        confirmLoading={rejecting}
        okText={t("pages.intake.review.rejectConfirm")}
        okButtonProps={{ danger: true }}
        cancelText={t("common.cancel")}
        destroyOnClose
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          {/* Say what a rejection does, before it is done. It is not a delete:
              the message and the parse are both kept. */}
          <Alert
            type="info"
            showIcon
            message={t("pages.intake.review.rejectBody")}
          />
          <div>
            <Typography.Text>
              {t("pages.intake.review.rejectReasonLabel")}
            </Typography.Text>
            <Select
              style={{ width: "100%", marginTop: 6 }}
              value={rejectReason}
              onChange={setRejectReason}
              options={REJECTION_REASONS.map((r) => ({
                value: r,
                label: t(`pages.intake.review.rejectReason.${r}`),
              }))}
            />
          </div>
          <div>
            <Typography.Text>
              {t("pages.intake.review.rejectNote")}
              {rejectReason === "other" ? "" : ` (${t("pages.intake.optional")})`}
            </Typography.Text>
            <Input.TextArea
              rows={3}
              style={{ marginTop: 6 }}
              value={rejectNote}
              onChange={(e) => setRejectNote(e.target.value)}
              placeholder={t("pages.intake.review.rejectNotePlaceholder")}
            />
          </div>
          {rejectError ? <Alert type="error" showIcon message={rejectError} /> : null}
        </Space>
      </Modal>
    </Drawer>
  );
}
