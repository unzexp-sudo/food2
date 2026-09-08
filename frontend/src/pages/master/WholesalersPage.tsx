import { useEffect, useState } from "react";
import {
  Button,
  Card,
  Drawer,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  type TableProps,
} from "antd";
import { EditOutlined, PlusOutlined } from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { api, type Page } from "../../api/client";
import { useList, useMutate } from "../../api/hooks";
import { pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";
import WholesalerDetailDrawer from "./WholesalerDetailDrawer";

interface Wholesaler {
  id: string;
  code: string;
  name_en: string;
  name_zh: string;
  contact_name: string | null;
  contact_phone: string | null;
  is_active: boolean;
}
interface Product {
  id: string;
  sku: string;
  name_en: string;
  name_zh: string;
}
interface Category {
  id: string;
  name_en: string;
  name_zh: string;
  is_active: boolean;
}
interface Mapping {
  id: string;
  product_id: string;
  product_name_en?: string;
  product_name_zh?: string;
  wholesaler_id: string;
  wholesaler_name_en?: string;
  wholesaler_name_zh?: string;
  supplier_sku: string | null;
  cost_price: number;
}
interface SupplierRule {
  id: string;
  category_id: string | null;
  product_id: string | null;
  wholesaler_id: string;
  wholesaler_name_en?: string;
  wholesaler_name_zh?: string;
  priority: number;
  moq: number | null;
  lead_time_days: number | null;
  is_default: boolean;
}

function WholesalersTab() {
  const { t, lang } = useLanguage();
  const list = useList<Wholesaler>("/wholesalers", {});

  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<Wholesaler | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [viewing, setViewing] = useState<Wholesaler | null>(null);
  const [form] = Form.useForm();
  const { loading: mutateLoading, run } = useMutate();

  const openNew = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ is_active: true });
    setEditOpen(true);
  };

  const openEdit = (w: Wholesaler) => {
    setEditing(w);
    form.resetFields();
    form.setFieldsValue(w);
    setEditOpen(true);
  };

  const openView = (w: Wholesaler) => {
    setViewing(w);
    setDetailOpen(true);
  };

  const handleSave = async () => {
    let values: Record<string, unknown>;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    if (editing) {
      await run(() => api.patch(`/wholesalers/${editing.id}`, values), {
        success: t("pages.master.wholesalers.saved"),
        onSuccess: () => {
          setEditOpen(false);
          list.refresh();
        },
      });
    } else {
      await run(() => api.post("/wholesalers", values), {
        success: t("pages.master.wholesalers.saved"),
        onSuccess: () => {
          setEditOpen(false);
          list.refresh();
        },
      });
    }
  };

  const toggleActive = async (w: Wholesaler) => {
    const next = !w.is_active;
    await run(() => api.patch(`/wholesalers/${w.id}`, { is_active: next }), {
      success: next
        ? t("pages.master.wholesalers.activated")
        : t("pages.master.wholesalers.deactivated"),
      onSuccess: list.refresh,
    });
  };

  const columns: TableProps<Wholesaler>["columns"] = [
    {
      title: t("pages.master.wholesalers.colCode"),
      dataIndex: "code",
      width: 120,
    },
    {
      title: t("pages.master.wholesalers.colName"),
      key: "name",
      render: (_: unknown, r: Wholesaler) => pickName(lang, r.name_en, r.name_zh),
    },
    {
      title: t("pages.master.wholesalers.colContact"),
      dataIndex: "contact_name",
      width: 140,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.master.wholesalers.colPhone"),
      dataIndex: "contact_phone",
      width: 140,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.master.wholesalers.colActive"),
      dataIndex: "is_active",
      width: 100,
      render: (v: boolean) => <StatusTag domain="master" value={v ? "active" : "inactive"} />,
    },
    {
      title: t("pages.master.wholesalers.colActions"),
      key: "actions",
      width: 200,
      render: (_: unknown, r: Wholesaler) => (
        <Space size="small">
          <Button size="small" onClick={() => openView(r)}>
            View
          </Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>
            {t("common.edit")}
          </Button>
          <Popconfirm
            title={
              r.is_active
                ? t("pages.master.wholesalers.deactivateConfirm")
                : t("pages.master.wholesalers.activateConfirm")
            }
            onConfirm={() => toggleActive(r)}
          >
            <Button size="small" danger={r.is_active}>
              {r.is_active
                ? t("pages.master.wholesalers.deactivate")
                : t("pages.master.wholesalers.activate")}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Button
        type="primary"
        icon={<PlusOutlined />}
        onClick={openNew}
        style={{ marginBottom: 12 }}
      >
        {t("pages.master.wholesalers.new")}
      </Button>
      <Table<Wholesaler>
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
        title={editing ? t("pages.master.wholesalers.edit") : t("pages.master.wholesalers.new")}
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
          <Form.Item name="code" label={t("pages.master.wholesalers.code")} rules={[{ required: true, message: t("pages.master.wholesalers.codeRequired") }]}>
            <Input />
          </Form.Item>
          <Form.Item name="name_en" label={t("pages.master.wholesalers.nameEn")} rules={[{ required: true, message: t("pages.master.wholesalers.nameEnRequired") }]}>
            <Input />
          </Form.Item>
          <Form.Item name="name_zh" label={t("pages.master.wholesalers.nameZh")}>
            <Input />
          </Form.Item>
          <Form.Item name="contact_name" label={t("pages.master.wholesalers.contactName")}>
            <Input />
          </Form.Item>
          <Form.Item name="contact_phone" label={t("pages.master.wholesalers.contactPhone")}>
            <Input />
          </Form.Item>
          <Form.Item name="is_active" label={t("pages.master.wholesalers.isActive")}>
            <Select
              options={[
                { value: true, label: t("pages.master.products.activeYes") },
                { value: false, label: t("pages.master.products.activeNo") },
              ]}
            />
          </Form.Item>
        </Form>
      </Drawer>

      <WholesalerDetailDrawer
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        wholesaler={viewing}
      />
    </>
  );
}

function MappingsTab() {
  const { t, lang } = useLanguage();
  const [items, setItems] = useState<Mapping[]>([]);
  const [loading, setLoading] = useState(false);
  const [products, setProducts] = useState<Product[]>([]);
  const [wholesalers, setWholesalers] = useState<Wholesaler[]>([]);
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<Mapping | null>(null);
  const [form] = Form.useForm();
  const { loading: mutateLoading, run } = useMutate();

  const refresh = () => {
    setLoading(true);
    api
      .get<Page<Mapping>>("/product-wholesaler-mappings", { page: 1, page_size: 200 })
      .then((r) => setItems(r.items))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
    api.get<Page<Product>>("/products", { page: 1, page_size: 200 }).then((r) => setProducts(r.items)).catch(() => setProducts([]));
    api.get<Page<Wholesaler>>("/wholesalers", { page: 1, page_size: 100 }).then((r) => setWholesalers(r.items)).catch(() => setWholesalers([]));
  }, []);

  const openNew = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ cost_price: 0 });
    setEditOpen(true);
  };

  const openEdit = (m: Mapping) => {
    setEditing(m);
    form.resetFields();
    form.setFieldsValue(m);
    setEditOpen(true);
  };

  const handleSave = async () => {
    let values: Record<string, unknown>;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    if (editing) {
      // No PATCH endpoint per contract — delete + recreate
      await run(
        () =>
          api.delete(`/product-wholesaler-mappings/${editing.id}`).then(() =>
            api.post("/product-wholesaler-mappings", values),
          ),
        {
          success: t("pages.master.wholesalers.mappingSaved"),
          onSuccess: () => {
            setEditOpen(false);
            refresh();
          },
        },
      );
    } else {
      await run(() => api.post("/product-wholesaler-mappings", values), {
        success: t("pages.master.wholesalers.mappingSaved"),
        onSuccess: () => {
          setEditOpen(false);
          refresh();
        },
      });
    }
  };

  const handleDelete = async (id: string) => {
    await run(() => api.delete(`/product-wholesaler-mappings/${id}`), {
      success: t("pages.master.wholesalers.mappingDeleted"),
      onSuccess: refresh,
    });
  };

  const columns: TableProps<Mapping>["columns"] = [
    {
      title: t("pages.master.wholesalers.mappingColProduct"),
      key: "product",
      render: (_: unknown, r: Mapping) =>
        r.product_name_en
          ? pickName(lang, r.product_name_en, r.product_name_zh)
          : products.find((p) => p.id === r.product_id)
            ? pickName(lang, products.find((p) => p.id === r.product_id)!.name_en, products.find((p) => p.id === r.product_id)!.name_zh)
            : r.product_id,
    },
    {
      title: t("pages.master.wholesalers.mappingColWholesaler"),
      key: "wholesaler",
      render: (_: unknown, r: Mapping) =>
        r.wholesaler_name_en
          ? pickName(lang, r.wholesaler_name_en, r.wholesaler_name_zh)
          : wholesalers.find((w) => w.id === r.wholesaler_id)
            ? pickName(lang, wholesalers.find((w) => w.id === r.wholesaler_id)!.name_en, wholesalers.find((w) => w.id === r.wholesaler_id)!.name_zh)
            : r.wholesaler_id,
    },
    {
      title: t("pages.master.wholesalers.mappingColSupplierSku"),
      dataIndex: "supplier_sku",
      width: 140,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.master.wholesalers.mappingColCost"),
      dataIndex: "cost_price",
      width: 120,
      align: "right" as const,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: t("pages.master.wholesalers.mappingColActions"),
      key: "actions",
      width: 160,
      render: (_: unknown, r: Mapping) => (
        <Space size="small">
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>
            {t("common.edit")}
          </Button>
          <Popconfirm title={t("common.delete")} onConfirm={() => handleDelete(r.id)}>
            <Button size="small" danger>
              {t("common.delete")}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Button
        type="primary"
        icon={<PlusOutlined />}
        onClick={openNew}
        style={{ marginBottom: 12 }}
      >
        {t("pages.master.wholesalers.newMapping")}
      </Button>
      <Table<Mapping>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={items}
        columns={columns}
        pagination={false}
        locale={{ emptyText: t("pages.master.wholesalers.noMappings") }}
      />
      <Drawer
        title={editing ? t("pages.master.wholesalers.editMapping") : t("pages.master.wholesalers.newMapping")}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        width={460}
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
          <Form.Item name="product_id" label={t("pages.master.wholesalers.mappingProduct")} rules={[{ required: true, message: t("pages.master.wholesalers.mappingProductRequired") }]}>
            <Select
              showSearch
              options={products.map((p) => ({
                value: p.id,
                label: `${p.sku} · ${pickName(lang, p.name_en, p.name_zh)}`,
              }))}
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item name="wholesaler_id" label={t("pages.master.wholesalers.mappingWholesaler")} rules={[{ required: true, message: t("pages.master.wholesalers.mappingWholesalerRequired") }]}>
            <Select
              showSearch
              options={wholesalers.map((w) => ({
                value: w.id,
                label: pickName(lang, w.name_en, w.name_zh),
              }))}
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item name="supplier_sku" label={t("pages.master.wholesalers.mappingSupplierSku")}>
            <Input />
          </Form.Item>
          <Form.Item name="cost_price" label={t("pages.master.wholesalers.mappingCost")}>
            <InputNumber min={0} step={0.01} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Drawer>
    </>
  );
}

function RulesTab() {
  const { t, lang } = useLanguage();
  const [items, setItems] = useState<SupplierRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [wholesalers, setWholesalers] = useState<Wholesaler[]>([]);
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<SupplierRule | null>(null);
  const [form] = Form.useForm();
  const { loading: mutateLoading, run } = useMutate();

  const refresh = () => {
    setLoading(true);
    api
      .get<Page<SupplierRule>>("/supplier-rules", { page: 1, page_size: 200 })
      .then((r) => setItems(r.items))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
    api.get<Page<Product>>("/products", { page: 1, page_size: 200 }).then((r) => setProducts(r.items)).catch(() => setProducts([]));
    api.get<Page<Category>>("/product-categories", { page: 1, page_size: 100 }).then((r) => setCategories(r.items)).catch(() => setCategories([]));
    api.get<Page<Wholesaler>>("/wholesalers", { page: 1, page_size: 100 }).then((r) => setWholesalers(r.items)).catch(() => setWholesalers([]));
  }, []);

  const openNew = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ priority: 1, is_default: false });
    setEditOpen(true);
  };

  const openEdit = (r: SupplierRule) => {
    setEditing(r);
    form.resetFields();
    form.setFieldsValue(r);
    setEditOpen(true);
  };

  const handleSave = async () => {
    let values: Record<string, unknown>;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    if (editing) {
      await run(() => api.patch(`/supplier-rules/${editing.id}`, values), {
        success: t("pages.master.wholesalers.ruleSaved"),
        onSuccess: () => {
          setEditOpen(false);
          refresh();
        },
      });
    } else {
      await run(() => api.post("/supplier-rules", values), {
        success: t("pages.master.wholesalers.ruleSaved"),
        onSuccess: () => {
          setEditOpen(false);
          refresh();
        },
      });
    }
  };

  const handleDelete = async (id: string) => {
    await run(() => api.delete(`/supplier-rules/${id}`), {
      success: t("pages.master.wholesalers.ruleDeleted"),
      onSuccess: refresh,
    });
  };

  const columns: TableProps<SupplierRule>["columns"] = [
    {
      title: t("pages.master.wholesalers.ruleColScope"),
      key: "scope",
      render: (_: unknown, r: SupplierRule) => {
        if (r.product_id) {
          const p = products.find((x) => x.id === r.product_id);
          return p ? `P: ${pickName(lang, p.name_en, p.name_zh)}` : r.product_id;
        }
        if (r.category_id) {
          const c = categories.find((x) => x.id === r.category_id);
          return c ? `C: ${pickName(lang, c.name_en, c.name_zh)}` : r.category_id;
        }
        return "Catch-all";
      },
    },
    {
      title: t("pages.master.wholesalers.ruleColWholesaler"),
      key: "wholesaler",
      render: (_: unknown, r: SupplierRule) =>
        r.wholesaler_name_en
          ? pickName(lang, r.wholesaler_name_en, r.wholesaler_name_zh)
          : wholesalers.find((w) => w.id === r.wholesaler_id)
            ? pickName(lang, wholesalers.find((w) => w.id === r.wholesaler_id)!.name_en, wholesalers.find((w) => w.id === r.wholesaler_id)!.name_zh)
            : r.wholesaler_id,
    },
    {
      title: t("pages.master.wholesalers.ruleColPriority"),
      dataIndex: "priority",
      width: 100,
      align: "right" as const,
    },
    {
      title: t("pages.master.wholesalers.ruleColMoq"),
      dataIndex: "moq",
      width: 100,
      align: "right" as const,
      render: (v: number | null) => v ?? "—",
    },
    {
      title: t("pages.master.wholesalers.ruleColLead"),
      dataIndex: "lead_time_days",
      width: 110,
      align: "right" as const,
      render: (v: number | null) => v ?? "—",
    },
    {
      title: t("pages.master.wholesalers.ruleColDefault"),
      dataIndex: "is_default",
      width: 100,
      render: (v: boolean) => (v ? <Tag color="green">✓</Tag> : null),
    },
    {
      title: t("pages.master.wholesalers.ruleColActions"),
      key: "actions",
      width: 160,
      render: (_: unknown, r: SupplierRule) => (
        <Space size="small">
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>
            {t("common.edit")}
          </Button>
          <Popconfirm title={t("common.delete")} onConfirm={() => handleDelete(r.id)}>
            <Button size="small" danger>
              {t("common.delete")}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Button type="primary" icon={<PlusOutlined />} onClick={openNew} style={{ marginBottom: 12 }}>
        {t("pages.master.wholesalers.newRule")}
      </Button>
      <Table<SupplierRule>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={items}
        columns={columns}
        pagination={false}
        locale={{ emptyText: t("pages.master.wholesalers.noRules") }}
      />
      <Drawer
        title={editing ? t("pages.master.wholesalers.editRule") : t("pages.master.wholesalers.newRule")}
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
          <Typography.Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
            {t("pages.master.wholesalers.ruleScopeHint")}
          </Typography.Text>
          <Form.Item name="category_id" label={t("pages.master.wholesalers.ruleCategory")}>
            <Select
              allowClear
              showSearch
              options={categories.map((c) => ({
                value: c.id,
                label: pickName(lang, c.name_en, c.name_zh),
              }))}
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item name="product_id" label={t("pages.master.wholesalers.ruleProduct")}>
            <Select
              allowClear
              showSearch
              options={products.map((p) => ({
                value: p.id,
                label: `${p.sku} · ${pickName(lang, p.name_en, p.name_zh)}`,
              }))}
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item name="wholesaler_id" label={t("pages.master.wholesalers.ruleWholesaler")} rules={[{ required: true, message: t("pages.master.wholesalers.ruleWholesalerRequired") }]}>
            <Select
              showSearch
              options={wholesalers.map((w) => ({
                value: w.id,
                label: pickName(lang, w.name_en, w.name_zh),
              }))}
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item name="priority" label={t("pages.master.wholesalers.rulePriority")}>
            <InputNumber min={0} step={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="moq" label={t("pages.master.wholesalers.ruleMoq")}>
            <InputNumber min={0} step={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="lead_time_days" label={t("pages.master.wholesalers.ruleLeadTime")}>
            <InputNumber min={0} step={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="is_default" label={t("pages.master.wholesalers.ruleIsDefault")}>
            <Select
              options={[
                { value: true, label: t("pages.master.products.activeYes") },
                { value: false, label: t("pages.master.products.activeNo") },
              ]}
            />
          </Form.Item>
        </Form>
      </Drawer>
    </>
  );
}

export default function WholesalersPage() {
  const { t } = useLanguage();
  return (
    <Card title={t("pages.master.wholesalers.title")}>
      <Tabs
        defaultActiveKey="wholesalers"
        items={[
          { key: "wholesalers", label: t("pages.master.wholesalers.tabWholesalers"), children: <WholesalersTab /> },
          { key: "mappings", label: t("pages.master.wholesalers.tabMappings"), children: <MappingsTab /> },
          { key: "rules", label: t("pages.master.wholesalers.tabRules"), children: <RulesTab /> },
        ]}
      />
    </Card>
  );
}
