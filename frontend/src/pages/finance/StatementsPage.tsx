import { useEffect, useState } from "react";
import {
  Button,
  Card,
  DatePicker,
  Form,
  Select,
  Table,
  Tabs,
  Typography,
  type TableProps,
} from "antd";
import { useLanguage } from "../../i18n";
import { api, type Page } from "../../api/client";
import { useList } from "../../api/hooks";
import { pickName } from "../../utils/format";
import { formatDate } from "../../utils/format";
import StatementGenerateModal from "./StatementGenerateModal";
interface Customer {
  id: string;
  name_en: string;
  name_zh: string;
  status: string;
}
interface Wholesaler {
  id: string;
  name_en: string;
  name_zh: string;
  is_active: boolean;
}
interface StatementRow {
  order_id: string;
  order_number: string;
  delivery_date: string;
  description: string;
  quantity: number;
  unit_price: number;
  amount: number;
}
interface StatementResult {
  rows: StatementRow[];
  total: number;
}

function StatementForm({
  entityType,
  entities,
  onQuery,
  loading,
}: {
  entityType: "customer" | "wholesaler";
  entities: { id: string; name_en: string; name_zh: string }[];
  onQuery: (id: string, from: string, to: string) => void;
  loading: boolean;
}) {
  const { t, lang } = useLanguage();
  const [form] = Form.useForm();

  const handleSubmit = async () => {
    let values: { entity: string; range: [unknown, unknown] };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const entityIdVal = values.entity as string;
    const [fromD, toD] = values.range as [
      { format: (f: string) => string },
      { format: (f: string) => string },
    ];
    const fromStr = fromD?.format("YYYY-MM-DD") ?? "";
    const toStr = toD?.format("YYYY-MM-DD") ?? "";
    if (!entityIdVal || !fromStr || !toStr) return;
    onQuery(entityIdVal, fromStr, toStr);
  };

  const placeholder =
    entityType === "customer" ? t("pages.finance.statements.selectCustomer") : t("pages.finance.statements.selectWholesaler");

  return (
    <Form form={form} layout="inline" style={{ marginBottom: 12 }}>
      <Form.Item
        name="entity"
        label={entityType === "customer" ? t("pages.finance.statements.selectCustomer") : t("pages.finance.statements.selectWholesaler")}
        rules={[{ required: true }]}
      >
        <Select
          showSearch
          placeholder={placeholder}
          style={{ width: 240 }}
          options={entities.map((e) => ({
            value: e.id,
            label: pickName(lang, e.name_en, e.name_zh),
          }))}
          optionFilterProp="label"
        />
      </Form.Item>
      <Form.Item label={t("pages.finance.statements.from")} name="range" rules={[{ required: true }]}>
        <DatePicker.RangePicker />
      </Form.Item>
      <Form.Item>
        <Button type="primary" onClick={handleSubmit} loading={loading}>
          {t("pages.finance.statements.query")}
        </Button>
      </Form.Item>
    </Form>
  );
}

function CustomerAR() {
  const { t } = useLanguage();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [data, setData] = useState<StatementResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api
      .get<Page<Customer>>("/customers", { page: 1, page_size: 100 })
      .then((r) => setCustomers(r.items))
      .catch(() => setCustomers([]));
  }, []);

  const query = (id: string, from: string, to: string) => {
    setLoading(true);
    api
      .get<StatementResult>(`/statements/customer/${id}`, { from, to })
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  };

  const columns: TableProps<StatementRow>["columns"] = [
    { title: t("pages.finance.statements.colOrder"), dataIndex: "order_number", width: 170 },
    {
      title: t("pages.finance.statements.colDeliveryDate"),
      dataIndex: "delivery_date",
      width: 130,
      render: (v: string) => formatDate(v),
    },
    { title: t("pages.finance.statements.colDescription"), dataIndex: "description" },
    {
      title: t("pages.finance.statements.colQty"),
      dataIndex: "quantity",
      width: 90,
      align: "right" as const,
    },
    {
      title: t("pages.finance.statements.colUnitPrice"),
      dataIndex: "unit_price",
      width: 110,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: t("pages.finance.statements.colAmount"),
      dataIndex: "amount",
      width: 120,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
  ];

  return (
    <>
      <StatementForm
        entityType="customer"
        entities={customers}
        onQuery={query}
        loading={loading}
      />
      <Table<StatementRow>
        rowKey={(r) => `${r.order_id}-${r.description}`}
        size="small"
        loading={loading}
        dataSource={data?.rows ?? []}
        columns={columns}
        pagination={false}
        locale={{ emptyText: t("pages.finance.statements.noData") }}
        summary={() =>
          data ? (
            <Table.Summary.Row>
              <Table.Summary.Cell index={0} colSpan={5}>
                <Typography.Text strong>{t("pages.finance.statements.total")}</Typography.Text>
              </Table.Summary.Cell>
              <Table.Summary.Cell index={5} align="right">
                <Typography.Text strong>{data.total.toFixed(2)}</Typography.Text>
              </Table.Summary.Cell>
            </Table.Summary.Row>
          ) : null
        }
      />
    </>
  );
}

function WholesalerAP() {
  const { t } = useLanguage();
  const [wholesalers, setWholesalers] = useState<Wholesaler[]>([]);
  const [data, setData] = useState<StatementResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api
      .get<Page<Wholesaler>>("/wholesalers", { page: 1, page_size: 100 })
      .then((r) => setWholesalers(r.items))
      .catch(() => setWholesalers([]));
  }, []);

  const query = (id: string, from: string, to: string) => {
    setLoading(true);
    api
      .get<StatementResult>(`/statements/wholesaler/${id}`, { from, to })
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  };

  const columns: TableProps<StatementRow>["columns"] = [
    { title: t("pages.finance.statements.colOrder"), dataIndex: "order_number", width: 170 },
    {
      title: t("pages.finance.statements.colDeliveryDate"),
      dataIndex: "delivery_date",
      width: 130,
      render: (v: string) => formatDate(v),
    },
    { title: t("pages.finance.statements.colDescription"), dataIndex: "description" },
    {
      title: t("pages.finance.statements.colQty"),
      dataIndex: "quantity",
      width: 90,
      align: "right" as const,
    },
    {
      title: t("pages.finance.statements.colUnitPrice"),
      dataIndex: "unit_price",
      width: 110,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: t("pages.finance.statements.colAmount"),
      dataIndex: "amount",
      width: 120,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
  ];

  // Map wholesalers to the shared form shape.
  const entities = wholesalers.map((w) => ({
    id: w.id,
    name_en: w.name_en,
    name_zh: w.name_zh,
  }));

  return (
    <>
      <StatementForm
        entityType="wholesaler"
        entities={entities}
        onQuery={query}
        loading={loading}
      />
      <Table<StatementRow>
        rowKey={(r) => `${r.order_id}-${r.description}`}
        size="small"
        loading={loading}
        dataSource={data?.rows ?? []}
        columns={columns}
        pagination={false}
        locale={{ emptyText: t("pages.finance.statements.noData") }}
        summary={() =>
          data ? (
            <Table.Summary.Row>
              <Table.Summary.Cell index={0} colSpan={5}>
                <Typography.Text strong>{t("pages.finance.statements.total")}</Typography.Text>
              </Table.Summary.Cell>
              <Table.Summary.Cell index={5} align="right">
                <Typography.Text strong>{data.total.toFixed(2)}</Typography.Text>
              </Table.Summary.Cell>
            </Table.Summary.Row>
          ) : null
        }
      />
    </>
  );
}

export default function StatementsPage() {
  const { t } = useLanguage();
  const customerList = useList<Customer>("/customers", { page: 1, page_size: 100 });
  const [genOpen, setGenOpen] = useState(false);

  return (
    <Card
      title={t("pages.finance.statements.title")}
      extra={
        <Button type="primary" onClick={() => setGenOpen(true)}>
          Generate
        </Button>
      }
    >
      <Tabs
        defaultActiveKey="customer"
        items={[
          {
            key: "customer",
            label: t("pages.finance.statements.tabCustomer"),
            children: <CustomerAR />,
          },
          {
            key: "wholesaler",
            label: t("pages.finance.statements.tabWholesaler"),
            children: <WholesalerAP />,
          },
        ]}
      />
      <StatementGenerateModal
        open={genOpen}
        customers={customerList.items}
        loadingCustomers={customerList.loading}
        onClose={() => setGenOpen(false)}
        onGenerated={customerList.refresh}
      />
    </Card>
  );
}
