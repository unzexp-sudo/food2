import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Alert,
  Badge,
  Button,
  Card,
  DatePicker,
  Drawer,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  type UploadFile,
  type UploadProps,
} from "antd";
import {
  DownloadOutlined,
  EyeOutlined,
  FileOutlined,
  LinkOutlined,
  ReloadOutlined,
  RollbackOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { type Dayjs } from "dayjs";
import { useLanguage } from "../../i18n";
import { api, getApiError, type Page } from "../../api/client";
import { useList, useMutate } from "../../api/hooks";
import { formatDateTime, pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";
import ConfidenceTag from "../../components/ConfidenceTag";
import IntakeReviewDrawer from "../../components/intake/IntakeReviewDrawer";
import BindCustomerDrawer from "../../components/identity/BindCustomerDrawer";
import client from "../../api/client";
import { parseStoredUser } from "../../types";

/** Entity shapes per AGENT_CONTRACTS §4. */
interface Customer {
  id: string;
  code: string;
  name_en: string;
  name_zh: string;
  type: string;
  status: string;
}
interface IntakeDocument {
  id: string;
  customer_id: string | null;
  source_type: string;
  original_filename: string | null;
  file_url: string | null;
  uploaded_by: string | null;
  created_at: string;
}
interface IntakeJob {
  id: string;
  document_id: string;
  status: string;
  error: string | null;
  retry_count: number;
  draft_order_id: string | null;
  created_at: string;
  finished_at: string | null;
}
/**
 * `GET /intake/documents` flattens the latest job onto the document
 * (`job_id`, `job_status`, `draft_order_id`) — it does NOT nest a `job`
 * object. Reading a nested `job` here silently rendered "—" for the status
 * and draft-order columns and hid the review button entirely, so the queue
 * looked empty even while orders were parked.
 */
interface IntakeDocWithJob extends IntakeDocument {
  job_id: string | null;
  job_status: string | null;
  draft_order_id: string | null;
  customer_name_en?: string | null;
  customer_name_zh?: string | null;
  identity?: DocIdentity | null;
  /** Why a human refused this document, when one did. */
  rejection?: DocRejection | null;
}

/**
 * A reviewer's refusal, from `_doc_out`.
 *
 * The status tag alone says `rejected` and nothing more, which leaves the one
 * question anyone will actually ask — "why is this customer's order missing?" —
 * unanswerable from the screen. `duplicate` is the answer that matters most: it
 * is what stops the order being placed again by hand.
 */
interface DocRejection {
  reason: string;
  note?: string | null;
  by?: string | null;
  at?: string | null;
}
/**
 * The conversation's binding state, from `_doc_out`.
 *
 * The server refuses to create an order from an unbound conversation, so the
 * inbox has to be able to see that state — otherwise the only signal is a
 * banner telling the operator to bind a conversation the list cannot name.
 */
interface DocIdentity {
  status: string | null;
  chat_key: string | null;
  kind: string | null;
  value: string | null;
  method: string | null;
  reason: string | null;
  /** Who WeCom says is talking. Display only — never used to resolve anyone. */
  display_name: string | null;
  corp_name: string | null;
  alias: string | null;
}

/** A document is held when its conversation has no customer binding. */
const isHeld = (r: IntakeDocWithJob) =>
  !r.customer_id && r.identity?.status === "unbound";

/**
 * The best human-readable name for the other end of a held conversation.
 *
 * Order matters: the WeCom *alias* is the remark staff themselves set, so it
 * usually reads like the customer's trading name; `corp_name` is the registered
 * company; `display_name` is a person. Returns null when WeCom told us nothing,
 * because an invented label is how a wrong bind starts.
 */
const contactLabel = (r: IntakeDocWithJob): string | null =>
  r.identity?.alias || r.identity?.corp_name || r.identity?.display_name || null;
interface ExtractionResponse {
  raw_output?: unknown;
  confidence?: number | null;
  [key: string]: unknown;
}

const SOURCE_TYPES = ["text", "image", "pdf", "excel", "email_body"];
const FILE_SOURCE_TYPES = new Set(["image", "pdf", "excel"]);

export default function IntakePage() {
  const { t, lang } = useLanguage();
  const user = parseStoredUser();
  const canMutate = user?.role === "admin" || user?.role === "ops";

  const [customerFilter, setCustomerFilter] = useState<string | undefined>();
  const [sourceFilter, setSourceFilter] = useState<string | undefined>();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  // "Unread" view: only the orders still parked for a human.
  const [pendingOnly, setPendingOnly] = useState(false);
  // "Parked" view: messages the parser set aside as definitely-not-an-order.
  // Hidden from the inbox by default, so this toggle is the only way in.
  const [parkedOnly, setParkedOnly] = useState(false);
  // "Needs a customer" view: held rows, which nobody can finish until the
  // conversation is bound. Filtered SERVER-side — doing it in the browser would
  // hide every match on the pages that were not fetched and quietly lie.
  const [unboundOnly, setUnboundOnly] = useState(false);

  const params = useMemo(
    () => ({
      // Unreviewed orders float to the top so the queue is the first thing
      // a reviewer sees.
      pending_first: true,
      ...(customerFilter ? { customer_id: customerFilter } : {}),
      ...(sourceFilter ? { source_type: sourceFilter } : {}),
      ...(statusFilter ? { status: statusFilter } : {}),
      ...(pendingOnly ? { status: "needs_review" } : {}),
      ...(parkedOnly ? { status: "parked" } : {}),
      ...(unboundOnly ? { unbound_only: true } : {}),
    }),
    [customerFilter, sourceFilter, statusFilter, pendingOnly, parkedOnly, unboundOnly],
  );

  const list = useList<IntakeDocWithJob>("/intake/documents", params);

  // Badge count for the "waiting for review" banner. Polled: an order can
  // land from WeCom while this tab is open, and nobody should have to hit
  // reload to find out. `parked` is polled alongside it for the same reason —
  // a wrongly-parked message is an invisible lost order.
  const [pendingCount, setPendingCount] = useState<number | null>(null);
  const [parkedCount, setParkedCount] = useState<number | null>(null);
  const [unboundCount, setUnboundCount] = useState<number | null>(null);
  const refreshPendingCount = useCallback(() => {
    api
      .get<{ pending_review: number; parked?: number; unbound?: number }>("/intake/review-count")
      .then((r) => {
        setPendingCount(r.pending_review);
        setParkedCount(r.parked ?? 0);
        setUnboundCount(r.unbound ?? null);
      })
      .catch(() => {
        setPendingCount(null);
        setParkedCount(null);
        setUnboundCount(null);
      });
  }, []);
  useEffect(() => {
    refreshPendingCount();
    const id = setInterval(refreshPendingCount, 30_000);
    return () => clearInterval(id);
  }, [refreshPendingCount, list.items]);

  // Master data for filters + form selects.
  const [customers, setCustomers] = useState<Customer[]>([]);
  useEffect(() => {
    api
      .get<Page<Customer>>("/customers", { page: 1, page_size: 100 })
      .then((res) => setCustomers(res.items))
      .catch(() => setCustomers([]));
  }, []);

  // New-intake drawer state.
  const [newOpen, setNewOpen] = useState(false);
  const [form] = Form.useForm();
  const [sourceType, setSourceType] = useState<string>("text");
  const [fileList, setFileList] = useState<File | null>(null);
  const [submitMsg, setSubmitMsg] = useState<string | null>(null);
  const [pollingJob, setPollingJob] = useState<string | null>(null);
  const [pollStatus, setPollStatus] = useState<string | null>(null);
  const [draftOrderId, setDraftOrderId] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Extraction drawer state.
  const [extractionOpen, setExtractionOpen] = useState(false);
  const [extractionData, setExtractionData] = useState<ExtractionResponse | null>(null);
  const [extractionLoading, setExtractionLoading] = useState(false);
  const [extractionError, setExtractionError] = useState<string | null>(null);

  // Human-review drawer state (handwritten / low-confidence notes).
  const [reviewOpen, setReviewOpen] = useState(false);
  const [reviewJobId, setReviewJobId] = useState<string | null>(null);
  const [reviewFileUrl, setReviewFileUrl] = useState<string | null>(null);
  // The document currently open in the review drawer, so the drawer can show
  // its binding state and offer to clear it.
  const [reviewDoc, setReviewDoc] = useState<IntakeDocWithJob | null>(null);

  // Bind-customer drawer state. `resumeJobId` remembers a confirm the operator
  // asked for and could not have: binding is the step that unblocks it, so on
  // success we finish the job they actually started instead of making them
  // click again.
  const [bindOpen, setBindOpen] = useState(false);
  const [bindTarget, setBindTarget] = useState<IntakeDocWithJob | null>(null);
  const [resumeJobId, setResumeJobId] = useState<string | null>(null);

  const openBindFor = (r: IntakeDocWithJob, resume?: string | null) => {
    setBindTarget(r);
    setResumeJobId(resume ?? null);
    setBindOpen(true);
  };

  const openReview = (r: IntakeDocWithJob) => {
    setReviewDoc(r);
    setReviewJobId(r.job_id ?? "");
    setReviewFileUrl(r.file_url);
    setReviewOpen(true);
  };

  const { loading: mutateLoading, run } = useMutate();

  const resetDrawer = () => {
    form.resetFields();
    setSourceType("text");
    setFileList(null);
    setSubmitMsg(null);
    setPollingJob(null);
    setPollStatus(null);
    setDraftOrderId(null);
    if (pollTimer.current) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  };

  useEffect(() => {
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, []);

  const pollJob = (jobId: string) => {
    setPollingJob(jobId);
    setPollStatus("queued");
    const tick = () => {
      api
        .get<IntakeJob>(`/intake/jobs/${jobId}`)
        .then((job) => {
          setPollStatus(job.status);
          if (job.status === "completed") {
            setDraftOrderId(job.draft_order_id);
            setSubmitMsg(t("pages.intake.jobCompleted"));
            list.refresh();
            pollTimer.current = null;
          } else if (job.status === "failed") {
            setSubmitMsg(t("pages.intake.jobFailed"));
            pollTimer.current = null;
          } else {
            pollTimer.current = setTimeout(tick, 1500);
          }
        })
        .catch(() => {
          setSubmitMsg(t("pages.intake.jobFailed"));
          pollTimer.current = null;
        });
    };
    tick();
  };

  const handleSubmit = async () => {
    let values: Record<string, unknown>;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const sourceTypeVal = values.source_type as string;
    const deliveryDate = values.delivery_date ? (values.delivery_date as Dayjs).format("YYYY-MM-DD") : undefined;
    setSubmitMsg(null);
    setDraftOrderId(null);

    try {
      let res: { document_id: string; job_id: string };
      if (FILE_SOURCE_TYPES.has(sourceTypeVal) && fileList) {
        const fd = new FormData();
        fd.append("source_type", sourceTypeVal);
        if (values.customer_id) fd.append("customer_id", values.customer_id as string);
        if (deliveryDate) fd.append("delivery_date", deliveryDate);
        fd.append("file", fileList);
        const r = await client.post<{ document_id: string; job_id: string }>("/intake/submit", fd, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        res = r.data;
      } else {
        const body: Record<string, unknown> = {
          source_type: sourceTypeVal,
          ...(values.customer_id ? { customer_id: values.customer_id } : {}),
          ...(deliveryDate ? { delivery_date: deliveryDate } : {}),
        };
        if (sourceTypeVal === "text" || sourceTypeVal === "email_body") {
          body.raw_text = (values.raw_text as string) ?? "";
        }
        res = await api.post<{ document_id: string; job_id: string }>("/intake/submit", body);
      }
      setSubmitMsg(t("pages.intake.jobStarted", { jobId: res.job_id }));
      pollJob(res.job_id);
    } catch (err) {
      setSubmitMsg(getApiError(err) ?? t("common.error"));
    }
  };

  const handleRetry = async (jobId: string) => {
    await run(() => api.post(`/intake/jobs/${jobId}/retry`), {
      success: t("pages.intake.retrySuccess"),
      onSuccess: list.refresh,
    });
  };

  // Un-park: put a message the parser set aside back into the queue.
  const handlePromote = async (jobId: string) => {
    await run(() => api.post(`/intake/jobs/${jobId}/promote`), {
      success: t("pages.intake.promoteSuccess"),
      onSuccess: () => {
        list.refresh();
        refreshPendingCount();
      },
    });
  };

  const handleDownload = async (docId: string) => {
    try {
      const res = await client.get(`/intake/documents/${docId}/file`, { responseType: "blob" });
      const url = URL.createObjectURL(res.data as Blob);
      const a = document.createElement("a");
      a.href = url;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      window.alert(getApiError(err) ?? t("common.error"));
    }
  };

  const handleViewExtraction = async (jobId: string) => {
    setExtractionOpen(true);
    setExtractionData(null);
    setExtractionError(null);
    setExtractionLoading(true);
    try {
      const data = await api.get<ExtractionResponse>(`/intake/extractions/${jobId}`);
      setExtractionData(data);
    } catch (err) {
      setExtractionError(getApiError(err) ?? t("common.error"));
    } finally {
      setExtractionLoading(false);
    }
  };

  const uploadProps: UploadProps = {
    beforeUpload: (file) => {
      setFileList(file as unknown as File);
      return false; // prevent auto-upload
    },
    onRemove: () => setFileList(null),
    fileList: fileList
      ? [
          {
            uid: "-1",
            name: fileList.name,
            size: fileList.size,
            type: fileList.type,
            originFileObj: fileList,
          } as UploadFile,
        ]
      : [],
    maxCount: 1,
  };

  const columns = [
    {
      title: t("pages.intake.colFile"),
      dataIndex: "source_type",
      width: 70,
      render: (sourceType: string, record: IntakeDocWithJob) => (
        <FileOutlined style={{ fontSize: 18, color: "#8c8c8c" }} title={record.original_filename ?? sourceType} />
      ),
    },
    {
      title: t("pages.intake.colSourceType"),
      dataIndex: "source_type",
      width: 110,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    {
      title: t("pages.intake.colCustomer"),
      key: "customer",
      width: 210,
      render: (_: unknown, r: IntakeDocWithJob) => {
        if (r.customer_id) {
          // `pickName` yields "" when both names are missing. That is a data
          // fault, not "no customer", so fall back rather than showing a blank
          // that reads as unbound.
          return pickName(lang, r.customer_name_en, r.customer_name_zh) || "—";
        }
        // A held row is a TASK, not missing data. Putting the action here is
        // what stops the operator opening the draft, clicking Confirm and
        // reading a toast telling them to go somewhere else.
        if (canMutate && isHeld(r)) {
          // The action alone is not enough. The operator is being asked to make
          // a decision about a conversation, so the row has to say who is on the
          // other end — otherwise the only identifier is a `chat_key` they
          // cannot act on. WeCom often tells us nothing, and that is worth
          // saying too rather than leaving a gap to be guessed at.
          const who = contactLabel(r);
          return (
            <Space direction="vertical" size={0}>
              <Button
                size="small"
                type="link"
                icon={<LinkOutlined />}
                style={{ paddingLeft: 0 }}
                onClick={() => openBindFor(r)}
              >
                {t("pages.intake.bind.needsCustomer")}
              </Button>
              <Typography.Text
                type={who ? "secondary" : "warning"}
                style={{ fontSize: 11 }}
              >
                {who ?? t("pages.intake.bind.unknownSender")}
              </Typography.Text>
            </Space>
          );
        }
        return "—";
      },
    },
    {
      title: t("pages.intake.colUploadedBy"),
      dataIndex: "uploaded_by",
      width: 140,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.intake.colCreatedAt"),
      dataIndex: "created_at",
      width: 150,
      render: (v: string) => formatDateTime(v),
    },
    {
      title: t("pages.intake.colJobStatus"),
      key: "job_status",
      // Wide enough for a rejection reason to read in a line or two. At 130 the
      // reason wrapped to five lines and every rejected row became a tall,
      // hard-to-scan block.
      width: 230,
      render: (_: unknown, r: IntakeDocWithJob) =>
        r.job_status ? (
          <Space direction="vertical" size={2}>
            <Space size={4}>
              <StatusTag domain="intake" value={r.job_status} />
              {r.job_status === "needs_review" && <Badge status="processing" />}
            </Space>
            {/* A rejection is a decision, and a decision with no visible reason
                is indistinguishable from a mistake. The reason is the whole
                point of having asked for one. */}
            {r.job_status === "rejected" && r.rejection ? (
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                {t(`pages.intake.review.rejectReason.${r.rejection.reason}`)}
                {r.rejection.note ? ` — ${r.rejection.note}` : ""}
              </Typography.Text>
            ) : null}
          </Space>
        ) : (
          "—"
        ),
    },
    {
      title: t("pages.intake.colDraftOrder"),
      key: "draft_order",
      width: 150,
      render: (_: unknown, r: IntakeDocWithJob) =>
        r.draft_order_id ? (
          <Link to={`/orders/${r.draft_order_id}`}>{t("pages.intake.viewDraftOrder")}</Link>
        ) : (
          "—"
        ),
    },
    {
      title: t("common.actions"),
      key: "actions",
      width: 180,
      render: (_: unknown, r: IntakeDocWithJob) => (
        <Space size="small">
          <Button
            size="small"
            icon={<DownloadOutlined />}
            onClick={() => handleDownload(r.id)}
            title={t("pages.intake.downloadOriginal")}
          />
          {r.job_id && r.job_status !== "parked" && (
            <Button
              size="small"
              icon={<EyeOutlined />}
              onClick={() => handleViewExtraction(r.job_id!)}
              title={t("pages.intake.viewExtraction")}
            />
          )}
          {canMutate && r.job_status === "needs_review" && (
            <Button
              size="small"
              type="primary"
              icon={<EyeOutlined />}
              onClick={() => openReview(r)}
            >
              {t("pages.intake.review.title")}
            </Button>
          )}
          {canMutate && r.job_status === "parked" && r.job_id && (
            <Popconfirm
              title={t("pages.intake.promoteConfirm")}
              onConfirm={() => handlePromote(r.job_id!)}
              disabled={mutateLoading}
            >
              <Button size="small" icon={<RollbackOutlined />} loading={mutateLoading}>
                {t("pages.intake.promote")}
              </Button>
            </Popconfirm>
          )}
          {canMutate && (r.job_status === "failed" || r.job_status === "completed") && (
            <Popconfirm
              title={t("pages.intake.retryConfirm")}
              onConfirm={() => handleRetry(r.job_id!)}
              disabled={mutateLoading}
            >
              <Button size="small" icon={<ReloadOutlined />} loading={mutateLoading}>
                {t("pages.intake.retry")}
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  const filterRow = (
    <Space wrap size="middle" style={{ marginBottom: 12 }}>
      <Select
        allowClear
        placeholder={t("pages.intake.allCustomers")}
        style={{ width: 220 }}
        value={customerFilter}
        onChange={(v) => {
          setCustomerFilter(v);
          list.setPage(1);
        }}
        options={customers.map((c) => ({
          value: c.id,
          label: pickName(lang, c.name_en, c.name_zh),
        }))}
      />
      <Select
        allowClear
        placeholder={t("pages.intake.allSourceTypes")}
        style={{ width: 160 }}
        value={sourceFilter}
        onChange={(v) => {
          setSourceFilter(v);
          list.setPage(1);
        }}
        options={SOURCE_TYPES.map((s) => ({ value: s, label: s }))}
      />
      <Select
        allowClear
        placeholder={t("pages.intake.allStatuses")}
        style={{ width: 160 }}
        value={statusFilter}
        onChange={(v) => {
          setStatusFilter(v);
          list.setPage(1);
        }}
        options={["queued", "processing", "completed", "failed", "needs_review", "parked", "rejected"].map((s) => ({
          value: s,
          label: t(`status.intake.${s}`),
        }))}
      />
    </Space>
  );

  return (
    <Card
      title={t("pages.intake.title")}
      extra={
        canMutate ? (
          <Button type="primary" icon={<UploadOutlined />} onClick={() => setNewOpen(true)}>
            {t("pages.intake.newIntake")}
          </Button>
        ) : null
      }
    >
      {pendingCount ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          // "17 waiting for review" is true and useless when all 17 are held:
          // it reads as a busy queue when it is a BLOCKED one, with a single
          // action behind it. Say which it is.
          message={
            unboundCount
              ? t("pages.intake.bind.pendingNeedsCustomer", {
                  pending: pendingCount,
                  unbound: unboundCount,
                })
              : t("pages.intake.pendingBanner", { count: pendingCount })
          }
          description={
            unboundCount
              ? t("pages.intake.bind.notBoundHint")
              : t("pages.intake.reviewHint")
          }
          action={
            <Space direction="vertical" size={4}>
              <Button
                size="small"
                onClick={() => {
                  setPendingOnly((v) => !v);
                  setParkedOnly(false);
                }}
              >
                {pendingOnly ? t("pages.intake.showAll") : t("pages.intake.pendingOnly")}
              </Button>
              {unboundCount ? (
                <Button
                  size="small"
                  type={unboundOnly ? "primary" : "default"}
                  onClick={() => {
                    setUnboundOnly((v) => !v);
                    setParkedOnly(false);
                    list.setPage(1);
                  }}
                >
                  {t("pages.intake.bind.unboundOnly")}
                </Button>
              ) : null}
            </Space>
          }
        />
      ) : null}
      {parkedCount ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={t("pages.intake.parkedBanner", { count: parkedCount })}
          description={t("pages.intake.parkedHint")}
          action={
            <Button
              size="small"
              onClick={() => {
                setParkedOnly((v) => !v);
                setPendingOnly(false);
              }}
            >
              {parkedOnly ? t("pages.intake.showAll") : t("pages.intake.parkedOnly")}
            </Button>
          }
        />
      ) : null}
      {filterRow}
      <Table<IntakeDocWithJob>
        rowKey="id"
        loading={list.loading}
        dataSource={list.items}
        columns={columns}
        size="middle"
        rowClassName={(r: IntakeDocWithJob) =>
          r.job_status === "needs_review" ? "intake-row-unreviewed" : ""
        }
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
        title={t("pages.intake.newIntakeTitle")}
        open={newOpen}
        onClose={() => {
          if (pollTimer.current) clearTimeout(pollTimer.current);
          setNewOpen(false);
          resetDrawer();
        }}
        width={520}
        footer={
          <Space style={{ float: "right" }}>
            <Button
              onClick={() => {
                if (pollTimer.current) clearTimeout(pollTimer.current);
                setNewOpen(false);
                resetDrawer();
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button
              type="primary"
              loading={mutateLoading || pollStatus === "processing" || pollStatus === "queued"}
              onClick={handleSubmit}
              disabled={!!draftOrderId}
            >
              {t("common.submit")}
            </Button>
          </Space>
        }
      >
        <Form form={form} layout="vertical" initialValues={{ source_type: "text" }}>
          <Form.Item name="source_type" label={t("pages.intake.sourceType")}>
            <Select
              options={SOURCE_TYPES.map((s) => ({ value: s, label: s }))}
              onChange={(v) => setSourceType(v)}
            />
          </Form.Item>
          <Form.Item name="customer_id" label={t("pages.intake.customer")}>
            <Select
              allowClear
              placeholder={t("pages.intake.selectCustomer")}
              options={customers.map((c) => ({
                value: c.id,
                label: pickName(lang, c.name_en, c.name_zh),
              }))}
            />
          </Form.Item>
          <Form.Item name="delivery_date" label={`${t("pages.intake.deliveryDate")} (${t("pages.intake.optional")})`}>
            <DatePicker style={{ width: "100%" }} />
          </Form.Item>
          {FILE_SOURCE_TYPES.has(sourceType) ? (
            <Form.Item label={t("pages.intake.file")} required>
              <Upload.Dragger {...uploadProps}>
                <p className="ant-upload-drag-icon">
                  <UploadOutlined />
                </p>
                <p className="ant-upload-text">{t("pages.intake.uploadHint")}</p>
              </Upload.Dragger>
            </Form.Item>
          ) : (
            <Form.Item
              name="raw_text"
              label={t("pages.intake.rawText")}
              rules={[{ required: true, message: t("pages.intake.rawTextPlaceholder") }]}
            >
              <Input.TextArea
                rows={6}
                placeholder={t("pages.intake.rawTextPlaceholder")}
              />
            </Form.Item>
          )}
        </Form>

        {submitMsg && (
          <Alert
            type={draftOrderId ? "success" : pollStatus === "failed" ? "error" : "info"}
            message={submitMsg}
            showIcon
            style={{ marginTop: 12 }}
          />
        )}
        {pollingJob && pollStatus && (pollStatus === "queued" || pollStatus === "processing") && (
          <Typography.Text type="secondary">{t("pages.intake.processing")}</Typography.Text>
        )}
        {draftOrderId && (
          <Button type="link" style={{ paddingLeft: 0 }}>
            <Link to={`/orders/${draftOrderId}`}>{t("pages.intake.viewDraftOrder")} →</Link>
          </Button>
        )}
      </Drawer>

      <Drawer
        title={t("pages.intake.extractionTitle")}
        open={extractionOpen}
        onClose={() => setExtractionOpen(false)}
        width={620}
      >
        {extractionLoading ? (
          <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
        ) : extractionError ? (
          <Alert type="error" message={extractionError} showIcon />
        ) : extractionData ? (
          <Space direction="vertical" style={{ width: "100%" }}>
            {typeof extractionData.confidence === "number" && (
              <div>
                <Typography.Text strong>{t("pages.intake.confidence")}: </Typography.Text>
                <ConfidenceTag value={extractionData.confidence} />
              </div>
            )}
            <pre
              style={{
                background: "#f5f5f5",
                padding: 12,
                borderRadius: 4,
                maxHeight: 480,
                overflow: "auto",
                fontSize: 12,
              }}
            >
              {JSON.stringify(extractionData, null, 2)}
            </pre>
          </Space>
        ) : (
          <Typography.Text type="secondary">{t("pages.intake.extractionEmpty")}</Typography.Text>
        )}
      </Drawer>

      <IntakeReviewDrawer
        open={reviewOpen}
        jobId={reviewJobId ?? ""}
        fileUrl={reviewFileUrl}
        canConfirm={canMutate}
        held={reviewDoc ? isHeld(reviewDoc) : false}
        customerLabel={
          reviewDoc?.customer_id
            ? pickName(lang, reviewDoc.customer_name_en, reviewDoc.customer_name_zh)
            : null
        }
        onChooseCustomer={() => {
          // Carry the job id through: the operator asked to create this order,
          // so once the conversation is bound we finish it for them.
          if (reviewDoc) openBindFor(reviewDoc, reviewJobId);
        }}
        onClose={() => setReviewOpen(false)}
        onConfirmed={() => {
          setReviewOpen(false);
          list.refresh();
          refreshPendingCount();
        }}
        // A rejection creates no order but does settle the row, so the queue and
        // the badge both have to be re-read. Without this the row the operator
        // just refused stays on screen still looking like work to do.
        onRejected={() => {
          setReviewOpen(false);
          list.refresh();
          refreshPendingCount();
        }}
      />

      <BindCustomerDrawer
        open={bindOpen}
        kind={bindTarget?.identity?.kind ?? null}
        value={bindTarget?.identity?.value ?? null}
        chatKey={bindTarget?.identity?.chat_key ?? null}
        onClose={() => setBindOpen(false)}
        onBound={async () => {
          // Reflect the release first, then finish the confirm the operator
          // actually asked for — binding is the step that unblocks it, not a
          // detour that costs them a second click.
          list.refresh();
          refreshPendingCount();
          const job = resumeJobId;
          setResumeJobId(null);
          if (!job) return;
          await run(() => api.post(`/intake/jobs/${job}/confirm-review`), {
            success: t("pages.intake.review.confirmed"),
            onSuccess: () => {
              setReviewOpen(false);
              list.refresh();
              refreshPendingCount();
            },
          });
        }}
      />
    </Card>
  );
}
