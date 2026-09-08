import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Descriptions,
  Drawer,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { PlayCircleOutlined } from "@ant-design/icons";
import { type Dayjs } from "dayjs";
import { useLanguage } from "../../i18n";
import { api } from "../../api/client";
import { useList, useMutate } from "../../api/hooks";
import { formatDate, formatDateTime, pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";

/** Entity shapes per AGENT_CONTRACTS §4 + §5 consolidation run result. */
interface ConsolidationBatch {
  id: string;
  batch_number: string;
  delivery_date: string;
  cutoff_at: string | null;
  status: string;
  order_count: number;
  exception_count: number;
  created_by: string | null;
  created_at: string;
}
interface ResultPO {
  po_number: string;
  wholesaler: string;
  category: string;
  total_amount: number;
  line_count: number;
}
interface ResultException {
  order_number: string;
  order_id?: string;
  product: string;
  reason: string;
}
interface RunResult {
  batch: ConsolidationBatch;
  purchase_orders: ResultPO[];
  exceptions: ResultException[];
}

/** Batch detail shape from GET /consolidation/batches/{id}. */
interface BatchDetail extends ConsolidationBatch {
  purchase_orders?: Array<{
    id: string;
    po_number: string;
    wholesaler_name_en: string;
    wholesaler_name_zh: string;
    total_amount: number;
  }>;
  exceptions?: Array<{
    order_id: string | null;
    order_number: string | null;
    product_name_en: string | null;
    product_name_zh: string | null;
    reason: string;
  }>;
}

type DetailPO = NonNullable<BatchDetail["purchase_orders"]>[number];
type DetailException = NonNullable<BatchDetail["exceptions"]>[number];

export default function ConsolidationPage() {
  const { t, lang } = useLanguage();

  const [deliveryFilter, setDeliveryFilter] = useState<string | undefined>();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const params = useMemo(
    () => ({
      ...(deliveryFilter ? { delivery_date: deliveryFilter } : {}),
      ...(statusFilter ? { status: statusFilter } : {}),
    }),
    [deliveryFilter, statusFilter],
  );
  const list = useList<ConsolidationBatch>("/consolidation/batches", params);

  // Run form.
  const [runDate, setRunDate] = useState<Dayjs | null>(null);
  const { loading, run } = useMutate();
  const [result, setResult] = useState<RunResult | null>(null);
  const [resultOpen, setResultOpen] = useState(false);

  const handleRun = async () => {
    if (!runDate) return;
    let res: RunResult | null = null;
    const ok = await run(
      async () => {
        res = await api.post<RunResult>("/consolidation/run", {
          delivery_date: runDate.format("YYYY-MM-DD"),
        });
      },
      {
        success: t("pages.consolidation.runSuccess"),
        onSuccess: list.refresh,
      },
    );
    if (ok && res) {
      setResult(res);
      setResultOpen(true);
    }
  };

  // Batch detail drawer.
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<BatchDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const openDetail = (id: string) => {
    setDetail(null);
    setDetailLoading(true);
    setDetailOpen(true);
    api
      .get<BatchDetail>(`/consolidation/batches/${id}`)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setDetailLoading(false));
  };

  const batchColumns = [
    {
      title: t("pages.consolidation.colBatchNumber"),
      dataIndex: "batch_number",
      width: 170,
      render: (v: string, r: ConsolidationBatch) => (
        <a onClick={() => openDetail(r.id)}>{v}</a>
      ),
    },
    {
      title: t("pages.consolidation.colDeliveryDate"),
      dataIndex: "delivery_date",
      width: 130,
      render: (v: string) => formatDate(v),
    },
    {
      title: t("pages.consolidation.colStatus"),
      dataIndex: "status",
      width: 110,
      render: (v: string) => <StatusTag domain="consolidation" value={v} />,
    },
    {
      title: t("pages.consolidation.colOrderCount"),
      dataIndex: "order_count",
      width: 90,
      align: "right" as const,
    },
    {
      title: t("pages.consolidation.colExceptionCount"),
      dataIndex: "exception_count",
      width: 110,
      align: "right" as const,
      render: (v: number) => (v > 0 ? <Tag color="warning">{v}</Tag> : v),
    },
    {
      title: t("pages.consolidation.colCreatedAt"),
      dataIndex: "created_at",
      width: 150,
      render: (v: string) => formatDateTime(v),
    },
  ];

  const resultPOColumns = [
    { title: t("pages.consolidation.poNumber"), dataIndex: "po_number", width: 170 },
    { title: t("pages.consolidation.wholesaler"), dataIndex: "wholesaler" },
    { title: t("pages.consolidation.category"), dataIndex: "category" },
    {
      title: t("pages.consolidation.totalAmount"),
      dataIndex: "total_amount",
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: t("pages.consolidation.lineCount"),
      dataIndex: "line_count",
      align: "right" as const,
    },
  ];

  const resultExceptionColumns = [
    {
      title: t("pages.consolidation.exceptionOrder"),
      dataIndex: "order_number",
      render: (v: string, r: ResultException) =>
        r.order_id ? <Link to={`/orders/${r.order_id}`}>{v}</Link> : v,
    },
    { title: t("pages.consolidation.exceptionProduct"), dataIndex: "product" },
    { title: t("pages.consolidation.exceptionReason"), dataIndex: "reason" },
  ];

  const detailPOColumns = [
    {
      title: t("pages.consolidation.poNumber"),
      dataIndex: "po_number",
      render: (v: string) => v,
    },
    {
      title: t("pages.consolidation.wholesaler"),
      key: "wholesaler",
      render: (_: unknown, r: DetailPO) =>
        pickName(lang, r.wholesaler_name_en, r.wholesaler_name_zh),
    },
    {
      title: t("pages.consolidation.totalAmount"),
      dataIndex: "total_amount",
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
  ];

  const detailExceptionColumns = [
    {
      title: t("pages.consolidation.exceptionOrder"),
      dataIndex: "order_number",
      render: (v: string | null, r: DetailException) =>
        r.order_id && v ? <Link to={`/orders/${r.order_id}`}>{v}</Link> : v ?? "—",
    },
    {
      title: t("pages.consolidation.exceptionProduct"),
      key: "product",
      render: (_: unknown, r: DetailException) =>
        pickName(lang, r.product_name_en, r.product_name_zh),
    },
    { title: t("pages.consolidation.exceptionReason"), dataIndex: "reason" },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card title={t("pages.consolidation.runCardTitle")}>
        <Space direction="vertical" size="small" style={{ width: "100%" }}>
          <Typography.Text type="secondary">{t("pages.consolidation.runCardHint")}</Typography.Text>
          <Space>
            <DatePicker
              value={runDate}
              onChange={setRunDate}
              placeholder={t("pages.consolidation.deliveryDate")}
            />
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              loading={loading}
              onClick={handleRun}
              disabled={!runDate}
            >
              {t("pages.consolidation.run")}
            </Button>
          </Space>
        </Space>
      </Card>

      <Card title={t("pages.consolidation.title")}>
        <Space wrap size="middle" style={{ marginBottom: 12 }}>
          <DatePicker
            placeholder={t("pages.consolidation.deliveryDate")}
            onChange={(d) => {
              setDeliveryFilter(d ? d.format("YYYY-MM-DD") : undefined);
              list.setPage(1);
            }}
          />
          <Select
            allowClear
            placeholder={t("pages.intake.allStatuses")}
            style={{ width: 160 }}
            onChange={(v) => {
              setStatusFilter(v);
              list.setPage(1);
            }}
            options={["open", "closed"].map((s) => ({ value: s, label: t(`status.consolidation.${s}`) }))}
          />
        </Space>
        <Table<ConsolidationBatch>
          rowKey="id"
          loading={list.loading}
          dataSource={list.items}
          columns={batchColumns}
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
      </Card>

      {/* Run result drawer */}
      <Drawer
        title={t("pages.consolidation.runResultTitle")}
        open={resultOpen}
        onClose={() => setResultOpen(false)}
        width={760}
      >
        {result ? (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label={t("pages.consolidation.resultBatch")}>
                {result.batch.batch_number}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.consolidation.colDeliveryDate")}>
                {formatDate(result.batch.delivery_date)}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.consolidation.colOrderCount")}>
                {result.batch.order_count}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.consolidation.colExceptionCount")}>
                {result.batch.exception_count}
              </Descriptions.Item>
            </Descriptions>

            <Card size="small" title={t("pages.consolidation.resultPOs")}>
              {result.purchase_orders.length > 0 ? (
                <Table<ResultPO>
                  rowKey="po_number"
                  size="small"
                  dataSource={result.purchase_orders}
                  columns={resultPOColumns}
                  pagination={false}
                />
              ) : (
                <Typography.Text type="secondary">{t("pages.consolidation.resultNoPOs")}</Typography.Text>
              )}
            </Card>

            <Card size="small" title={t("pages.consolidation.resultExceptions")}>
              {result.exceptions.length > 0 ? (
                <Table<ResultException>
                  rowKey={(r) => `${r.order_number}-${r.product}`}
                  size="small"
                  dataSource={result.exceptions}
                  columns={resultExceptionColumns}
                  pagination={false}
                />
              ) : (
                <Typography.Text type="secondary">{t("pages.consolidation.resultNoExceptions")}</Typography.Text>
              )}
            </Card>
          </Space>
        ) : (
          <Alert type="info" message={t("common.noData")} />
        )}
      </Drawer>

      {/* Batch detail drawer */}
      <Drawer
        title={t("pages.consolidation.batchDetailTitle")}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={760}
      >
        {detailLoading ? (
          <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
        ) : detail ? (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label={t("pages.consolidation.colBatchNumber")}>
                {detail.batch_number}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.consolidation.colStatus")}>
                <StatusTag domain="consolidation" value={detail.status} />
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.consolidation.colDeliveryDate")}>
                {formatDate(detail.delivery_date)}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.consolidation.colOrderCount")}>
                {detail.order_count}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.consolidation.colExceptionCount")}>
                {detail.exception_count}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.consolidation.colCreatedAt")}>
                {formatDateTime(detail.created_at)}
              </Descriptions.Item>
            </Descriptions>

            <Card size="small" title={t("pages.consolidation.resultPOs")}>
              {detail.purchase_orders && detail.purchase_orders.length > 0 ? (
                <Table<DetailPO>
                  rowKey="id"
                  size="small"
                  dataSource={detail.purchase_orders}
                  columns={detailPOColumns}
                  pagination={false}
                />
              ) : (
                <Typography.Text type="secondary">{t("pages.consolidation.resultNoPOs")}</Typography.Text>
              )}
            </Card>

            <Card size="small" title={t("pages.consolidation.exceptions")}>
              {detail.exceptions && detail.exceptions.length > 0 ? (
                <Table<DetailException>
                  rowKey={(r) => `${r.order_number ?? ""}-${r.product_name_en ?? r.product_name_zh ?? ""}-${r.reason}`}
                  size="small"
                  dataSource={detail.exceptions}
                  columns={detailExceptionColumns}
                  pagination={false}
                />
              ) : (
                <Typography.Text type="secondary">{t("pages.consolidation.resultNoExceptions")}</Typography.Text>
              )}
            </Card>
          </Space>
        ) : (
          <Typography.Text type="secondary">{t("common.noData")}</Typography.Text>
        )}
      </Drawer>
    </Space>
  );
}
