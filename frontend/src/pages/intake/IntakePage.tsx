import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Alert,
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
  ReloadOutlined,
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
interface IntakeDocWithJob extends IntakeDocument {
  job?: IntakeJob;
  customer_name_en?: string | null;
  customer_name_zh?: string | null;
}
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

  const params = useMemo(
    () => ({
      ...(customerFilter ? { customer_id: customerFilter } : {}),
      ...(sourceFilter ? { source_type: sourceFilter } : {}),
      ...(statusFilter ? { status: statusFilter } : {}),
    }),
    [customerFilter, sourceFilter, statusFilter],
  );

  const list = useList<IntakeDocWithJob>("/intake/documents", params);

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

  const openReview = (jobId: string, fileUrl?: string | null) => {
    setReviewJobId(jobId);
    setReviewFileUrl(fileUrl ?? null);
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
      render: (_: unknown, r: IntakeDocWithJob) =>
        r.customer_id ? pickName(lang, r.customer_name_en, r.customer_name_zh) : "—",
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
      width: 130,
      render: (_: unknown, r: IntakeDocWithJob) =>
        r.job ? <StatusTag domain="intake" value={r.job.status} /> : "—",
    },
    {
      title: t("pages.intake.colDraftOrder"),
      key: "draft_order",
      width: 150,
      render: (_: unknown, r: IntakeDocWithJob) =>
        r.job?.draft_order_id ? (
          <Link to={`/orders/${r.job.draft_order_id}`}>{t("pages.intake.viewDraftOrder")}</Link>
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
          {r.job && (
            <Button
              size="small"
              icon={<EyeOutlined />}
              onClick={() => handleViewExtraction(r.job!.id)}
              title={t("pages.intake.viewExtraction")}
            />
          )}
          {canMutate && r.job?.status === "needs_review" && (
            <Button
              size="small"
              type="primary"
              icon={<EyeOutlined />}
              onClick={() => openReview(r.job!.id, r.file_url)}
            >
              {t("pages.intake.review.title")}
            </Button>
          )}
          {canMutate && r.job && (r.job.status === "failed" || r.job.status === "completed") && (
            <Popconfirm
              title={t("pages.intake.retryConfirm")}
              onConfirm={() => handleRetry(r.job!.id)}
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
        options={["queued", "processing", "completed", "failed", "needs_review"].map((s) => ({
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
      {filterRow}
      <Table<IntakeDocWithJob>
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
        onClose={() => setReviewOpen(false)}
        onConfirmed={() => list.refresh()}
      />
    </Card>
  );
}
