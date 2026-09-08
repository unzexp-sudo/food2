import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Form,
  Select,
  Table,
  Tag,
  type TableProps,
} from "antd";
import { useLanguage } from "../../i18n";
import { api } from "../../api/client";

interface MarginRow {
  dimension_id: string;
  dimension_name: string;
  revenue: number;
  cost: number;
  margin: number;
  margin_pct: number;
}
interface MarginResult {
  rows: MarginRow[];
  warning?: string | null;
}

type ByMode = "customer" | "category" | "product";

export default function MarginPage() {
  const { t } = useLanguage();
  const [form] = Form.useForm();
  const [by, setBy] = useState<ByMode>("customer");
  const [data, setData] = useState<MarginResult | null>(null);
  const [loading, setLoading] = useState(false);

  const handleQuery = async () => {
    let values: { range?: [unknown, unknown] };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const range = values.range as
      | [{ format: (f: string) => string }, { format: (f: string) => string }]
      | undefined;
    const from = range?.[0]?.format("YYYY-MM-DD") ?? "";
    const to = range?.[1]?.format("YYYY-MM-DD") ?? "";
    setLoading(true);
    api
      .get<MarginResult>("/reports/margin", { by, from, to })
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  };

  const columns: TableProps<MarginRow>["columns"] = [
    {
      title: t("pages.finance.margin.colDimension"),
      dataIndex: "dimension_name",
    },
    {
      title: t("pages.finance.margin.colRevenue"),
      dataIndex: "revenue",
      width: 130,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: t("pages.finance.margin.colCost"),
      dataIndex: "cost",
      width: 130,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: t("pages.finance.margin.colMargin"),
      dataIndex: "margin",
      width: 130,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: t("pages.finance.margin.colMarginPct"),
      dataIndex: "margin_pct",
      width: 130,
      align: "right" as const,
      render: (v: number) => {
        const color = v >= 30 ? "green" : v >= 15 ? "orange" : "red";
        return <Tag color={color}>{v.toFixed(1)}%</Tag>;
      },
    },
  ];

  return (
    <Card title={t("pages.finance.margin.title")}>
      <Form form={form} layout="inline" style={{ marginBottom: 12 }}>
        <Form.Item label={t("pages.finance.margin.by")}>
          <Select
            style={{ width: 160 }}
            value={by}
            onChange={(v: ByMode) => setBy(v)}
            options={[
              { value: "customer", label: t("pages.finance.margin.byCustomer") },
              { value: "category", label: t("pages.finance.margin.byCategory") },
              { value: "product", label: t("pages.finance.margin.byProduct") },
            ]}
          />
        </Form.Item>
        <Form.Item
          label={`${t("pages.finance.margin.from")} / ${t("pages.finance.margin.to")}`}
          name="range"
        >
          <DatePicker.RangePicker />
        </Form.Item>
        <Form.Item>
          <Button type="primary" onClick={handleQuery} loading={loading}>
            {t("pages.finance.margin.query")}
          </Button>
        </Form.Item>
      </Form>

      {data?.warning && (
        <Alert
          type="warning"
          showIcon
          message={`${t("pages.finance.margin.warning")}: ${data.warning}`}
          style={{ marginBottom: 12 }}
        />
      )}

      <Table<MarginRow>
        rowKey="dimension_id"
        size="middle"
        loading={loading}
        dataSource={data?.rows ?? []}
        columns={columns}
        pagination={false}
        locale={{ emptyText: t("pages.finance.margin.noData") }}
      />
    </Card>
  );
}
