import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Modal,
  Select,
  Space,
  Statistic,
  Steps,
  Table,
  Tag,
  Typography,
  Upload,
  type TableProps,
  type UploadProps,
} from "antd";
import { InboxOutlined, ReloadOutlined } from "@ant-design/icons";
import { api, getApiError } from "../../api/client";
import { useLanguage } from "../../i18n";

/**
 * Batch master-data import: upload → confirm the column mapping → review → commit.
 *
 * The flow is two calls to the same endpoint, and the wizard exists to make the
 * first one visible. `dry_run=true` validates every row and writes nothing; the
 * operator then confirms, and the same bytes are sent again with `dry_run=false`
 * plus the hash the preview returned.
 *
 * The hash matters more than it looks. Without it, a file edited between the
 * preview and the commit would be applied unreviewed — the operator would have
 * approved a report describing a different file. The server refuses that, and
 * this component surfaces the refusal rather than hiding it.
 */

/** One row's verdict, as the engine reports it. */
interface ImportRow {
  row_number: number;
  action: "create" | "update" | "error" | string;
  key: string;
  values: Record<string, unknown>;
  errors: string[];
  warnings: string[];
  creates: string[];
}

interface ImportField {
  name: string;
  required: boolean;
  kind: string;
  help: string;
}

interface ImportReport {
  kind: string;
  shape: string;
  headers: string[];
  mapping: Record<string, string | null>;
  unmapped_headers: string[];
  rows: ImportRow[];
  counts: Record<string, number>;
  ok: boolean;
  applied: boolean;
  content_sha256: string;
  encoding: string | null;
  sheet_name: string | null;
  header_row_number: number;
  notes: string[];
  spec: { kind: string; label: string; key_fields: string[]; fields: ImportField[] };
}

export interface ImportWizardProps {
  /** Endpoint that accepts the multipart upload, e.g. `/customers/import`. */
  endpoint: string;
  /** Shown in the dialog title, e.g. "customers". */
  entityLabel: string;
  /** Called after a successful commit so the page can refresh its list. */
  onImported?: () => void;
}

export default function ImportWizard({ endpoint, entityLabel, onImported }: ImportWizardProps) {
  const { t } = useLanguage();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [applied, setApplied] = useState<{ create: number; update: number } | null>(null);

  const reset = () => {
    setBusy(false);
    setFile(null);
    setReport(null);
    setMapping({});
    setFailure(null);
    setApplied(null);
  };

  const openWizard = () => {
    reset();
    setOpen(true);
  };

  /**
   * Send the file once. `dryRun` decides whether this is the preview or the
   * write; the mapping and the reviewed hash travel with it.
   */
  const send = async (
    target: File,
    options: { dryRun: boolean; mapping?: Record<string, string | null>; sha?: string },
  ): Promise<ImportReport | null> => {
    const form = new FormData();
    form.append("file", target);
    form.append("dry_run", options.dryRun ? "true" : "false");
    if (options.mapping) {
      form.append("mapping", JSON.stringify(options.mapping));
    }
    if (options.sha) {
      form.append("options", JSON.stringify({ sha256: options.sha }));
    }
    try {
      return await api.postForm<ImportReport>(endpoint, form);
    } catch (error) {
      setFailure(getApiError(error) ?? t("common.error"));
      return null;
    }
  };

  const preview = async (target: File, nextMapping?: Record<string, string | null>) => {
    setBusy(true);
    setFailure(null);
    const result = await send(target, { dryRun: true, mapping: nextMapping });
    if (result) {
      setReport(result);
      setMapping(result.mapping);
    }
    setBusy(false);
  };

  /**
   * Download the row report as CSV.
   *
   * A thousand-row file cannot be reviewed in a paginated table, and the rows
   * that matter are the failures. Handing the operator the whole report as a
   * file lets them sort and filter it in Excel, fix the source file, and
   * re-upload — which is the loop this feature is for.
   */
  const downloadReport = () => {
    if (!report) return;
    const escape = (value: unknown) => {
      const text = value === null || value === undefined ? "" : String(value);
      return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    };
    const header = [
      t("importWizard.rowNumber"),
      t("importWizard.action"),
      t("importWizard.record"),
      t("importWizard.details"),
    ];
    const lines = [header, ...report.rows.map((r) => [
      r.row_number,
      r.action,
      r.key,
      [...r.errors, ...r.warnings, ...r.creates].join(" | "),
    ])];
    // The BOM is what makes Excel open a UTF-8 CSV with Chinese text correctly
    // instead of showing mojibake — the same reason the importer reads one.
    const csv = "\ufeff" + lines.map((row) => row.map(escape).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `import-report-${report.kind}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const commit = async () => {
    if (!file || !report) return;
    setBusy(true);
    setFailure(null);
    const result = await send(file, {
      dryRun: false,
      mapping,
      sha: report.content_sha256,
    });
    if (result?.applied) {
      setApplied({
        create: result.counts.create ?? 0,
        update: result.counts.update ?? 0,
      });
      setReport(result);
      onImported?.();
    } else if (result) {
      // The commit refused: show why, and leave the operator on the report.
      setReport(result);
      setFailure(result.notes.join(" "));
    }
    setBusy(false);
  };

  // Not memoised on purpose. `beforeUpload` closes over `preview`, which closes
  // over `t`; a `useMemo(..., [])` would freeze the closure at first render and
  // every later error message would come back in the language the operator
  // started the session in. antd does not need a stable identity here.
  const uploadProps: UploadProps = {
    accept: ".csv,.xlsx,.xlsm,.xls",
    maxCount: 1,
    showUploadList: false,
    beforeUpload: (selected) => {
      setFile(selected as File);
      void preview(selected as File);
      // Never let antd upload on its own — we control both calls.
      return false;
    },
  };

  const step = applied ? 3 : report ? 2 : file ? 1 : 0;

  const counts = report?.counts ?? {};
  const errorCount = counts.error ?? 0;
  const createCount = counts.create ?? 0;
  const updateCount = counts.update ?? 0;
  const totalRows = createCount + updateCount;

  const rowColumns: TableProps<ImportRow>["columns"] = [
    { title: t("importWizard.rowNumber"), dataIndex: "row_number", width: 70 },
    {
      title: t("importWizard.action"),
      dataIndex: "action",
      width: 110,
      render: (v: string) =>
        v === "error" ? (
          <Tag color="red">{t("importWizard.countError")}</Tag>
        ) : v === "create" ? (
          <Tag color="green">{t("importWizard.countCreate")}</Tag>
        ) : (
          <Tag color="blue">{t("importWizard.countUpdate")}</Tag>
        ),
    },
    { title: t("importWizard.record"), dataIndex: "key", width: 200 },
    {
      title: t("importWizard.details"),
      key: "details",
      render: (_: unknown, r: ImportRow) => (
        <>
          {r.errors.map((e) => (
            <div key={e}>
              <Typography.Text type="danger">{e}</Typography.Text>
            </div>
          ))}
          {r.warnings.map((w) => (
            <div key={w}>
              <Typography.Text type="warning">{w}</Typography.Text>
            </div>
          ))}
          {r.creates.length > 0 && (
            <Typography.Text type="secondary">
              {t("importWizard.willCreate")}: {r.creates.join(", ")}
            </Typography.Text>
          )}
          {r.errors.length === 0 && r.warnings.length === 0 && r.creates.length === 0 && "—"}
        </>
      ),
    },
  ];

  const mappingColumns: TableProps<string>["columns"] = [
    {
      title: t("importWizard.columnInFile"),
      dataIndex: "header",
      key: "header",
      render: (header: string) => <strong>{header}</strong>,
    },
    {
      title: t("importWizard.mapsTo"),
      key: "field",
      render: (_: unknown, header: string) => (
        <Select
          style={{ width: 240 }}
          value={mapping[header] ?? null}
          onChange={(value) => setMapping((prev) => ({ ...prev, [header]: value }))}
          options={[
            { value: null, label: t("importWizard.notImported") },
            ...(report?.spec.fields ?? []).map((f) => ({
              value: f.name,
              label: f.required ? `${f.name} *` : f.name,
            })),
          ]}
        />
      ),
    },
  ];

  return (
    <>
      <Button onClick={openWizard}>{t("importWizard.button")}</Button>
      <Modal
        open={open}
        onCancel={() => setOpen(false)}
        title={t("importWizard.title", { entity: entityLabel })}
        width={900}
        footer={null}
        destroyOnClose
      >
        <Steps
          size="small"
          current={step}
          style={{ marginBottom: 16 }}
          items={[
            { title: t("importWizard.stepUpload") },
            { title: t("importWizard.stepMapping") },
            { title: t("importWizard.stepReview") },
          ]}
        />

        {failure && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 12 }}
            message={t("importWizard.failedTitle")}
            description={failure}
          />
        )}

        {applied && (
          <Alert
            type="success"
            showIcon
            style={{ marginBottom: 12 }}
            message={t("importWizard.done", {
              create: applied.create,
              update: applied.update,
            })}
          />
        )}

        {!report && (
          <>
            <Typography.Paragraph type="secondary">
              {t("importWizard.intro")}
            </Typography.Paragraph>
            <Upload.Dragger {...uploadProps} disabled={busy}>
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">
                {busy ? t("importWizard.reading") : t("importWizard.dropHint")}
              </p>
              <p className="ant-upload-hint">{t("importWizard.dropNote")}</p>
            </Upload.Dragger>
          </>
        )}

        {report && (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Descriptions size="small" column={3}>
              <Descriptions.Item label={t("importWizard.encoding")}>
                {report.encoding ?? "—"}
              </Descriptions.Item>
              <Descriptions.Item label={t("importWizard.stepMapping")}>
                {report.shape === "matrix"
                  ? t("importWizard.shapeMatrix")
                  : t("importWizard.shapeLong")}
              </Descriptions.Item>
              <Descriptions.Item label=" ">
                {t("importWizard.headerRow", { n: report.header_row_number })}
              </Descriptions.Item>
            </Descriptions>

            {report.notes.map((note) => (
              <Alert key={note} type="warning" showIcon message={note} />
            ))}

            <Card size="small" title={t("importWizard.mappingTitle")}>
              <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
                {t("importWizard.mappingHelp")}
              </Typography.Paragraph>
              <Table<string>
                rowKey={(h) => h}
                size="small"
                pagination={false}
                dataSource={report.headers}
                columns={mappingColumns}
              />
              {report.unmapped_headers.length > 0 && (
                <Alert
                  type="info"
                  showIcon
                  style={{ marginTop: 8 }}
                  message={t("importWizard.unmappedWarning", {
                    count: report.unmapped_headers.length,
                  })}
                />
              )}
              <Button
                style={{ marginTop: 8 }}
                icon={<ReloadOutlined />}
                loading={busy}
                onClick={() => file && preview(file, mapping)}
              >
                {t("importWizard.recheck")}
              </Button>
            </Card>

            <Space size="large">
              <Statistic title={t("importWizard.countCreate")} value={createCount} />
              <Statistic title={t("importWizard.countUpdate")} value={updateCount} />
              <Statistic
                title={t("importWizard.countError")}
                value={errorCount}
                valueStyle={errorCount > 0 ? { color: "#cf1322" } : undefined}
              />
            </Space>

            <Card size="small" title={t("importWizard.rowsTitle")}>
              <Space style={{ marginBottom: 8 }}>
                <Button size="small" onClick={downloadReport}>
                  {t("importWizard.downloadReport")}
                </Button>
              </Space>
              <Table<ImportRow>
                rowKey={(r) => `${r.row_number}-${r.key}`}
                size="small"
                dataSource={report.rows}
                columns={rowColumns}
                pagination={{ pageSize: 20, showSizeChanger: false }}
              />
            </Card>

            {errorCount > 0 ? (
              <Alert
                type="error"
                showIcon
                message={t("importWizard.blockedTitle")}
                description={t("importWizard.blockedBody", { count: errorCount })}
              />
            ) : (
              <Alert
                type="info"
                showIcon
                message={t("importWizard.confirmTitle", { count: totalRows })}
                description={t("importWizard.confirmBody", {
                  create: createCount,
                  update: updateCount,
                })}
              />
            )}

            <Space style={{ float: "right" }}>
              <Button onClick={reset}>{t("common.back")}</Button>
              <Button
                type="primary"
                disabled={errorCount > 0 || applied !== null || totalRows === 0}
                loading={busy}
                onClick={commit}
              >
                {t("importWizard.confirm")}
              </Button>
            </Space>
          </Space>
        )}
      </Modal>
    </>
  );
}
