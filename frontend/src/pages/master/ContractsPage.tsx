import { useEffect, useMemo, useState } from "react";
import {
  Button,
  Card,
  DatePicker,
  Drawer,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Typography,
  type TableProps,
} from "antd";
import { DeleteOutlined, EditOutlined, PlusOutlined } from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { api, type Page } from "../../api/client";
import { useList, useMutate } from "../../api/hooks";
import { formatDate, pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";

interface Customer {
  id: string;
  name_en: string;
  name_zh: string;
  status: string;
}
interface Product {
  id: string;
  sku: string;
  name_en: string;
  name_zh: string;
}
interface Unit {
  id: string;
  code: string;
  name_en: string;
  name_zh: string;
}
interface ContractPrice {
  id: string;
  customer_id: string;
  customer_name_en?: string;
  customer_name_zh?: string;
  product_id: string;
  product_name_en?: string;
  product_name_zh?: string;
  unit_id: string;
  unit_code?: string;
  price: number;
  valid_from: string | null;
  valid_until: string | null;
}
interface StandingOrderTemplate {
  id: string;
  customer_id: string;
  customer_name_en?: string;
  customer_name_zh?: string;
  name: string;
  delivery_days: string[];
  is_active: boolean;
  lines: { id: string; product_id: string; quantity: number; unit_id: string }[];
}

const WEEK_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

function PricesTab() {
  const { t, lang } = useLanguage();
  const [customerFilter, setCustomerFilter] = useState<string | undefined>();
  const [productFilter, setProductFilter] = useState<string | undefined>();
  const params = useMemo(
    () => ({
      ...(customerFilter ? { customer_id: customerFilter } : {}),
      ...(productFilter ? { product_id: productFilter } : {}),
    }),
    [customerFilter, productFilter],
  );
  const list = useList<ContractPrice>("/contract-prices", params);

  const [customers, setCustomers] = useState<Customer[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  useEffect(() => {
    api.get<Page<Customer>>("/customers", { page: 1, page_size: 100 }).then((r) => setCustomers(r.items)).catch(() => setCustomers([]));
    api.get<Page<Product>>("/products", { page: 1, page_size: 200 }).then((r) => setProducts(r.items)).catch(() => setProducts([]));
    api.get<Page<Unit>>("/units", { page: 1, page_size: 50 }).then((r) => setUnits(r.items)).catch(() => setUnits([]));
  }, []);

  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<ContractPrice | null>(null);
  const [form] = Form.useForm();
  const { loading: mutateLoading, run } = useMutate();

  const openNew = () => {
    setEditing(null);
    form.resetFields();
    setEditOpen(true);
  };

  const openEdit = (p: ContractPrice) => {
    setEditing(p);
    form.resetFields();
    form.setFieldsValue({
      ...p,
      valid_from: p.valid_from,
      valid_until: p.valid_until,
    });
    setEditOpen(true);
  };

  const handleSave = async () => {
    let values: {
      customer_id: string;
      product_id: string;
      unit_id: string;
      price: number;
      valid_from?: { format: (f: string) => string };
      valid_until?: { format: (f: string) => string };
    };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const body = {
      customer_id: values.customer_id,
      product_id: values.product_id,
      unit_id: values.unit_id,
      price: values.price,
      ...(values.valid_from ? { valid_from: values.valid_from.format("YYYY-MM-DD") } : {}),
      ...(values.valid_until ? { valid_until: values.valid_until.format("YYYY-MM-DD") } : {}),
    };
    if (editing) {
      // No PATCH per contract — delete + recreate
      await run(
        () => api.delete(`/contract-prices/${editing.id}`).then(() => api.post("/contract-prices", body)),
        {
          success: t("pages.master.contracts.priceSaved"),
          onSuccess: () => {
            setEditOpen(false);
            list.refresh();
          },
        },
      );
    } else {
      await run(() => api.post("/contract-prices", body), {
        success: t("pages.master.contracts.priceSaved"),
        onSuccess: () => {
          setEditOpen(false);
          list.refresh();
        },
      });
    }
  };

  const handleDelete = async (id: string) => {
    await run(() => api.delete(`/contract-prices/${id}`), {
      success: t("pages.master.contracts.priceDeleted"),
      onSuccess: list.refresh,
    });
  };

  const columns: TableProps<ContractPrice>["columns"] = [
    {
      title: t("pages.master.contracts.colCustomer"),
      key: "customer",
      render: (_: unknown, r: ContractPrice) =>
        r.customer_name_en
          ? pickName(lang, r.customer_name_en, r.customer_name_zh)
          : customers.find((c) => c.id === r.customer_id)
            ? pickName(lang, customers.find((c) => c.id === r.customer_id)!.name_en, customers.find((c) => c.id === r.customer_id)!.name_zh)
            : r.customer_id,
    },
    {
      title: t("pages.master.contracts.colProduct"),
      key: "product",
      render: (_: unknown, r: ContractPrice) =>
        r.product_name_en
          ? pickName(lang, r.product_name_en, r.product_name_zh)
          : products.find((p) => p.id === r.product_id)
            ? pickName(lang, products.find((p) => p.id === r.product_id)!.name_en, products.find((p) => p.id === r.product_id)!.name_zh)
            : r.product_id,
    },
    {
      title: t("pages.master.contracts.colUnit"),
      key: "unit",
      width: 100,
      render: (_: unknown, r: ContractPrice) =>
        r.unit_code ?? units.find((u) => u.id === r.unit_id)?.code ?? r.unit_id,
    },
    {
      title: t("pages.master.contracts.colPrice"),
      dataIndex: "price",
      width: 120,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: t("pages.master.contracts.colValidFrom"),
      dataIndex: "valid_from",
      width: 120,
      render: (v: string | null) => (v ? formatDate(v) : "—"),
    },
    {
      title: t("pages.master.contracts.colValidUntil"),
      dataIndex: "valid_until",
      width: 120,
      render: (v: string | null) => (v ? formatDate(v) : "—"),
    },
    {
      title: t("pages.master.contracts.colActions"),
      key: "actions",
      width: 160,
      render: (_: unknown, r: ContractPrice) => (
        <Space size="small">
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>
            {t("common.edit")}
          </Button>
          <Popconfirm title={t("common.delete")} onConfirm={() => handleDelete(r.id)}>
            <Button size="small" danger icon={<DeleteOutlined />}>
              {t("common.delete")}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Space wrap size="middle" style={{ marginBottom: 12 }}>
        <Select
          allowClear
          showSearch
          placeholder={t("pages.master.contracts.allCustomers")}
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
          optionFilterProp="label"
        />
        <Select
          allowClear
          showSearch
          placeholder={t("pages.master.contracts.allProducts")}
          style={{ width: 220 }}
          value={productFilter}
          onChange={(v) => {
            setProductFilter(v);
            list.setPage(1);
          }}
          options={products.map((p) => ({
            value: p.id,
            label: `${p.sku} · ${pickName(lang, p.name_en, p.name_zh)}`,
          }))}
          optionFilterProp="label"
        />
      </Space>
      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openNew}>
          {t("pages.master.contracts.newPrice")}
        </Button>
      </Space>
      <Table<ContractPrice>
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
        title={editing ? t("pages.master.contracts.editPrice") : t("pages.master.contracts.newPrice")}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        width={480}
        footer={
          <Space style={{ float: "right" }}>
            <Button onClick={() => setEditOpen(false)}>{t("common.cancel")}</Button>
            <Button type="primary" loading={mutateLoading} onClick={handleSave}>
              {t("common.save")}
            </Button>
          </Space>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item name="customer_id" label={t("pages.master.contracts.priceCustomer")} rules={[{ required: true, message: t("pages.master.contracts.priceCustomerRequired") }]}>
            <Select
              showSearch
              options={customers.map((c) => ({ value: c.id, label: pickName(lang, c.name_en, c.name_zh) }))}
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item name="product_id" label={t("pages.master.contracts.priceProduct")} rules={[{ required: true, message: t("pages.master.contracts.priceProductRequired") }]}>
            <Select
              showSearch
              options={products.map((p) => ({
                value: p.id,
                label: `${p.sku} · ${pickName(lang, p.name_en, p.name_zh)}`,
              }))}
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item name="unit_id" label={t("pages.master.contracts.priceUnit")} rules={[{ required: true, message: t("pages.master.contracts.priceUnitRequired") }]}>
            <Select
              showSearch
              options={units.map((u) => ({
                value: u.id,
                label: `${u.code} · ${pickName(lang, u.name_en, u.name_zh)}`,
              }))}
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item name="price" label={t("pages.master.contracts.pricePrice")} rules={[{ required: true, message: t("pages.master.contracts.pricePriceRequired") }]}>
            <InputNumber min={0} step={0.01} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="valid_from" label={t("pages.master.contracts.priceValidFrom")}>
            <DatePicker style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="valid_until" label={t("pages.master.contracts.priceValidUntil")}>
            <DatePicker style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Drawer>
    </>
  );
}

function StandingTab() {
  const { t, lang } = useLanguage();
  const list = useList<StandingOrderTemplate>("/standing-order-templates", {});

  const [customers, setCustomers] = useState<Customer[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  useEffect(() => {
    api.get<Page<Customer>>("/customers", { page: 1, page_size: 100 }).then((r) => setCustomers(r.items)).catch(() => setCustomers([]));
    api.get<Page<Product>>("/products", { page: 1, page_size: 200 }).then((r) => setProducts(r.items)).catch(() => setProducts([]));
    api.get<Page<Unit>>("/units", { page: 1, page_size: 50 }).then((r) => setUnits(r.items)).catch(() => setUnits([]));
  }, []);

  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<StandingOrderTemplate | null>(null);
  const [form] = Form.useForm();
  const { loading: mutateLoading, run } = useMutate();

  const openNew = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ is_active: true, delivery_days: ["mon"], lines: [{ quantity: 1 }] });
    setEditOpen(true);
  };

  const openEdit = (tpl: StandingOrderTemplate) => {
    setEditing(tpl);
    form.resetFields();
    form.setFieldsValue({
      ...tpl,
      lines: tpl.lines.length > 0 ? tpl.lines : [{ quantity: 1 }],
    });
    setEditOpen(true);
  };

  const handleSave = async () => {
    let values: {
      customer_id: string;
      name: string;
      delivery_days: string[];
      is_active: boolean;
      lines: { product_id?: string; quantity: number; unit_id?: string; id?: string }[];
    };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const body = {
      customer_id: values.customer_id,
      name: values.name,
      delivery_days: values.delivery_days,
      is_active: values.is_active,
      lines: values.lines.map((l) => ({
        product_id: l.product_id,
        quantity: l.quantity,
        unit_id: l.unit_id,
      })),
    };
    if (editing) {
      await run(() => api.patch(`/standing-order-templates/${editing.id}`, body), {
        success: t("pages.master.contracts.templateSaved"),
        onSuccess: () => {
          setEditOpen(false);
          list.refresh();
        },
      });
    } else {
      await run(() => api.post("/standing-order-templates", body), {
        success: t("pages.master.contracts.templateSaved"),
        onSuccess: () => {
          setEditOpen(false);
          list.refresh();
        },
      });
    }
  };

  const handleDelete = async (id: string) => {
    await run(() => api.delete(`/standing-order-templates/${id}`), {
      success: t("pages.master.contracts.templateDeleted"),
      onSuccess: list.refresh,
    });
  };

  const handleCreateOrder = async (tpl: StandingOrderTemplate) => {
    const ok = await run(() => api.post(`/standing-order-templates/${tpl.id}/create-order`), {
      success: t("pages.master.contracts.orderCreated"),
    });
    if (ok) {
      list.refresh();
    }
  };

  const columns: TableProps<StandingOrderTemplate>["columns"] = [
    {
      title: t("pages.master.contracts.colTemplateName"),
      dataIndex: "name",
    },
    {
      title: t("pages.master.contracts.colTemplateCustomer"),
      key: "customer",
      render: (_: unknown, r: StandingOrderTemplate) =>
        r.customer_name_en
          ? pickName(lang, r.customer_name_en, r.customer_name_zh)
          : customers.find((c) => c.id === r.customer_id)
            ? pickName(lang, customers.find((c) => c.id === r.customer_id)!.name_en, customers.find((c) => c.id === r.customer_id)!.name_zh)
            : r.customer_id,
    },
    {
      title: t("pages.master.contracts.colTemplateDays"),
      dataIndex: "delivery_days",
      render: (v: string[]) => (v && v.length > 0 ? v.join(", ") : "—"),
    },
    {
      title: t("pages.master.contracts.colTemplateActive"),
      dataIndex: "is_active",
      width: 100,
      render: (v: boolean) => <StatusTag domain="master" value={v ? "active" : "inactive"} />,
    },
    {
      title: t("pages.master.contracts.colTemplateActions"),
      key: "actions",
      width: 240,
      render: (_: unknown, r: StandingOrderTemplate) => (
        <Space size="small">
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>
            {t("common.edit")}
          </Button>
          <Popconfirm
            title={t("pages.master.contracts.createOrderConfirm")}
            onConfirm={() => handleCreateOrder(r)}
          >
            <Button size="small" type="primary">
              {t("pages.master.contracts.createOrder")}
            </Button>
          </Popconfirm>
          <Popconfirm title={t("common.delete")} onConfirm={() => handleDelete(r.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Button type="primary" icon={<PlusOutlined />} onClick={openNew} style={{ marginBottom: 12 }}>
        {t("pages.master.contracts.newTemplate")}
      </Button>
      <Table<StandingOrderTemplate>
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
        title={editing ? t("pages.master.contracts.editTemplate") : t("pages.master.contracts.newTemplate")}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        width={720}
        footer={
          <Space style={{ float: "right" }}>
            <Button onClick={() => setEditOpen(false)}>{t("common.cancel")}</Button>
            <Button type="primary" loading={mutateLoading} onClick={handleSave}>
              {t("common.save")}
            </Button>
          </Space>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label={t("pages.master.contracts.templateName")} rules={[{ required: true, message: t("pages.master.contracts.templateNameRequired") }]}>
            <Input />
          </Form.Item>
          <Form.Item name="customer_id" label={t("pages.master.contracts.templateCustomer")} rules={[{ required: true, message: t("pages.master.contracts.templateCustomerRequired") }]}>
            <Select
              showSearch
              options={customers.map((c) => ({ value: c.id, label: pickName(lang, c.name_en, c.name_zh) }))}
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item name="delivery_days" label={t("pages.master.contracts.templateDays")}>
            <Select mode="multiple" options={WEEK_DAYS.map((d) => ({ value: d, label: d }))} />
          </Form.Item>
          <Form.Item name="is_active" label={t("pages.master.contracts.templateActive")}>
            <Select
              options={[
                { value: true, label: t("pages.master.products.activeYes") },
                { value: false, label: t("pages.master.products.activeNo") },
              ]}
            />
          </Form.Item>

          <Typography.Text strong>{t("pages.master.contracts.templateLines")}</Typography.Text>
          <Form.List name="lines">
            {(fields, { add, remove }) => (
              <>
                {fields.map((field) => (
                  <div
                    key={field.key}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "1fr 110px 130px 32px",
                      gap: 8,
                      alignItems: "end",
                      marginBottom: 8,
                    }}
                  >
                    <Form.Item
                      {...field}
                      name={[field.name, "product_id"]}
                      label={t("pages.master.contracts.templateLineProduct")}
                      rules={[{ required: true, message: t("pages.master.contracts.templateLineProductRequired") }]}
                    >
                      <Select
                        showSearch
                        options={products.map((p) => ({
                          value: p.id,
                          label: `${p.sku} · ${pickName(lang, p.name_en, p.name_zh)}`,
                        }))}
                        optionFilterProp="label"
                      />
                    </Form.Item>
                    <Form.Item
                      {...field}
                      name={[field.name, "quantity"]}
                      label={t("pages.master.contracts.templateLineQty")}
                      rules={[{ required: true, message: t("pages.master.contracts.templateLineQtyRequired") }]}
                    >
                      <InputNumber min={0} step={1} style={{ width: "100%" }} />
                    </Form.Item>
                    <Form.Item
                      {...field}
                      name={[field.name, "unit_id"]}
                      label={t("pages.master.contracts.templateLineUnit")}
                    >
                      <Select
                        allowClear
                        options={units.map((u) => ({
                          value: u.id,
                          label: u.code,
                        }))}
                      />
                    </Form.Item>
                    <Button
                      type="text"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={() => remove(field.name)}
                      style={{ marginBottom: 24 }}
                    />
                  </div>
                ))}
                <Button
                  type="dashed"
                  icon={<PlusOutlined />}
                  onClick={() => add({ quantity: 1 })}
                  block
                >
                  {t("pages.master.contracts.addLine")}
                </Button>
              </>
            )}
          </Form.List>
        </Form>
      </Drawer>
    </>
  );
}

export default function ContractsPage() {
  const { t } = useLanguage();
  return (
    <Card title={t("pages.master.contracts.title")}>
      <Tabs
        defaultActiveKey="prices"
        items={[
          { key: "prices", label: t("pages.master.contracts.tabPrices"), children: <PricesTab /> },
          { key: "standing", label: t("pages.master.contracts.tabStanding"), children: <StandingTab /> },
        ]}
      />
    </Card>
  );
}
