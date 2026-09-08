import { useEffect, useMemo, useState } from "react";
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
  type TableProps,
} from "antd";
import { EditOutlined, PlusOutlined } from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { api, type Page } from "../../api/client";
import { useList, useMutate } from "../../api/hooks";
import { pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";

interface Product {
  id: string;
  sku: string;
  name_en: string;
  name_zh: string;
  category_id: string | null;
  category_name_en: string | null;
  category_name_zh: string | null;
  default_unit_id: string | null;
  default_unit_code: string | null;
  shelf_life_days: number | null;
  is_active: boolean;
}
interface Category {
  id: string;
  name_en: string;
  name_zh: string;
  is_active: boolean;
}
interface Unit {
  id: string;
  code: string;
  name_en: string;
  name_zh: string;
}

function ProductsTab() {
  const { t, lang } = useLanguage();
  const [q, setQ] = useState<string>("");
  const [categoryFilter, setCategoryFilter] = useState<string | undefined>();
  const [activeFilter, setActiveFilter] = useState<string | undefined>();
  const params = useMemo(
    () => ({
      ...(q ? { q } : {}),
      ...(categoryFilter ? { category_id: categoryFilter } : {}),
      ...(activeFilter ? { is_active: activeFilter } : {}),
    }),
    [q, categoryFilter, activeFilter],
  );
  const list = useList<Product>("/products", params);

  const [categories, setCategories] = useState<Category[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  useEffect(() => {
    api.get<Page<Category>>("/product-categories", { page: 1, page_size: 100 }).then((r) => setCategories(r.items)).catch(() => setCategories([]));
    api.get<Page<Unit>>("/units", { page: 1, page_size: 50 }).then((r) => setUnits(r.items)).catch(() => setUnits([]));
  }, []);

  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<Product | null>(null);
  const [form] = Form.useForm();
  const { loading: mutateLoading, run } = useMutate();

  const openNew = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ is_active: true, shelf_life_days: 0 });
    setEditOpen(true);
  };

  const openEdit = (p: Product) => {
    setEditing(p);
    form.resetFields();
    form.setFieldsValue(p);
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
      await run(() => api.patch(`/products/${editing.id}`, values), {
        success: t("pages.master.products.saved"),
        onSuccess: () => {
          setEditOpen(false);
          list.refresh();
        },
      });
    } else {
      await run(() => api.post("/products", values), {
        success: t("pages.master.products.saved"),
        onSuccess: () => {
          setEditOpen(false);
          list.refresh();
        },
      });
    }
  };

  const toggleActive = async (p: Product) => {
    const next = !p.is_active;
    await run(() => api.patch(`/products/${p.id}`, { is_active: next }), {
      success: next
        ? t("pages.master.products.activated")
        : t("pages.master.products.deactivated"),
      onSuccess: list.refresh,
    });
  };

  const columns: TableProps<Product>["columns"] = [
    {
      title: t("pages.master.products.colSku"),
      dataIndex: "sku",
      width: 140,
    },
    {
      title: t("pages.master.products.colName"),
      key: "name",
      render: (_: unknown, r: Product) => pickName(lang, r.name_en, r.name_zh),
    },
    {
      title: t("pages.master.products.colCategory"),
      key: "category",
      render: (_: unknown, r: Product) =>
        pickName(lang, r.category_name_en, r.category_name_zh),
    },
    {
      title: t("pages.master.products.colDefaultUnit"),
      dataIndex: "default_unit_code",
      width: 110,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.master.products.colShelfLife"),
      dataIndex: "shelf_life_days",
      width: 130,
      align: "right" as const,
      render: (v: number | null) => v ?? "—",
    },
    {
      title: t("pages.master.products.colActive"),
      dataIndex: "is_active",
      width: 100,
      render: (v: boolean) => <StatusTag domain="master" value={v ? "active" : "inactive"} />,
    },
    {
      title: t("pages.master.products.colActions"),
      key: "actions",
      width: 200,
      render: (_: unknown, r: Product) => (
        <Space size="small">
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>
            {t("common.edit")}
          </Button>
          <Popconfirm
            title={
              r.is_active
                ? t("pages.master.products.deactivateConfirm")
                : t("pages.master.products.activateConfirm")
            }
            onConfirm={() => toggleActive(r)}
          >
            <Button size="small" danger={r.is_active}>
              {r.is_active
                ? t("pages.master.products.deactivate")
                : t("pages.master.products.activate")}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Space wrap size="middle" style={{ marginBottom: 12 }}>
        <Input.Search
          allowClear
          placeholder={t("pages.master.products.filterKeyword")}
          style={{ width: 240 }}
          onSearch={(v) => {
            setQ(v);
            list.setPage(1);
          }}
        />
        <Select
          allowClear
          showSearch
          placeholder={t("pages.master.products.allCategories")}
          style={{ width: 200 }}
          value={categoryFilter}
          onChange={(v) => {
            setCategoryFilter(v);
            list.setPage(1);
          }}
          options={categories.map((c) => ({
            value: c.id,
            label: pickName(lang, c.name_en, c.name_zh),
          }))}
          optionFilterProp="label"
        />
        <Select
          allowClear
          placeholder={t("pages.master.products.allActive")}
          style={{ width: 140 }}
          value={activeFilter}
          onChange={(v) => {
            setActiveFilter(v);
            list.setPage(1);
          }}
          options={[
            { value: "true", label: t("pages.master.products.activeYes") },
            { value: "false", label: t("pages.master.products.activeNo") },
          ]}
        />
      </Space>
      <Table<Product>
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
        title={editing ? t("pages.master.products.edit") : t("pages.master.products.new")}
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
          <Form.Item
            name="sku"
            label={t("pages.master.products.sku")}
            rules={[{ required: true, message: t("pages.master.products.skuRequired") }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="name_en"
            label={t("pages.master.products.nameEn")}
            rules={[{ required: true, message: t("pages.master.products.nameEnRequired") }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="name_zh" label={t("pages.master.products.nameZh")}>
            <Input />
          </Form.Item>
          <Form.Item name="category_id" label={t("pages.master.products.category")}>
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
          <Form.Item name="default_unit_id" label={t("pages.master.products.defaultUnit")}>
            <Select
              allowClear
              showSearch
              options={units.map((u) => ({
                value: u.id,
                label: `${u.code} · ${pickName(lang, u.name_en, u.name_zh)}`,
              }))}
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item name="shelf_life_days" label={t("pages.master.products.shelfLifeDays")}>
            <InputNumber min={0} step={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="is_active" label={t("pages.master.products.isActive")}>
            <Select
              options={[
                { value: true, label: t("pages.master.products.activeYes") },
                { value: false, label: t("pages.master.products.activeNo") },
              ]}
            />
          </Form.Item>
        </Form>
      </Drawer>

      <Button
        type="primary"
        icon={<PlusOutlined />}
        onClick={openNew}
        style={{ marginTop: 12 }}
      >
        {t("pages.master.products.new")}
      </Button>
    </>
  );
}

function CategoriesTab() {
  const { t } = useLanguage();
  const [items, setItems] = useState<Category[]>([]);
  const [loading, setLoading] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<Category | null>(null);
  const [form] = Form.useForm();
  const { loading: mutateLoading, run } = useMutate();

  const refresh = () => {
    setLoading(true);
    api
      .get<Page<Category>>("/product-categories", { page: 1, page_size: 100 })
      .then((r) => setItems(r.items))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
  }, []);

  const openNew = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ is_active: true });
    setEditOpen(true);
  };

  const openEdit = (c: Category) => {
    setEditing(c);
    form.resetFields();
    form.setFieldsValue(c);
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
      await run(() => api.patch(`/product-categories/${editing.id}`, values), {
        success: t("pages.master.products.categorySaved"),
        onSuccess: () => {
          setEditOpen(false);
          refresh();
        },
      });
    } else {
      await run(() => api.post("/product-categories", values), {
        success: t("pages.master.products.categorySaved"),
        onSuccess: () => {
          setEditOpen(false);
          refresh();
        },
      });
    }
  };

  const handleDelete = async (id: string) => {
    await run(() => api.delete(`/product-categories/${id}`), {
      success: t("pages.master.products.categoryDeleted"),
      onSuccess: refresh,
    });
  };

  const columns: TableProps<Category>["columns"] = [
    {
      title: t("pages.master.products.catColNameEn"),
      dataIndex: "name_en",
    },
    {
      title: t("pages.master.products.catColNameZh"),
      dataIndex: "name_zh",
    },
    {
      title: t("pages.master.products.catColActive"),
      dataIndex: "is_active",
      width: 100,
      render: (v: boolean) => <StatusTag domain="master" value={v ? "active" : "inactive"} />,
    },
    {
      title: t("pages.master.products.catColActions"),
      key: "actions",
      width: 160,
      render: (_: unknown, r: Category) => (
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
        {t("pages.master.products.newCategory")}
      </Button>
      <Table<Category>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={items}
        columns={columns}
        pagination={false}
      />

      <Drawer
        title={editing ? t("pages.master.products.editCategory") : t("pages.master.products.newCategory")}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        width={420}
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
          <Form.Item name="name_en" label={t("pages.master.products.catColNameEn")} rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="name_zh" label={t("pages.master.products.catColNameZh")}>
            <Input />
          </Form.Item>
          <Form.Item name="is_active" label={t("pages.master.products.catColActive")}>
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

function UnitsTab() {
  const { t } = useLanguage();
  const [items, setItems] = useState<Unit[]>([]);
  const [loading, setLoading] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<Unit | null>(null);
  const [form] = Form.useForm();
  const { loading: mutateLoading, run } = useMutate();

  const refresh = () => {
    setLoading(true);
    api
      .get<Page<Unit>>("/units", { page: 1, page_size: 50 })
      .then((r) => setItems(r.items))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
  }, []);

  const openNew = () => {
    setEditing(null);
    form.resetFields();
    setEditOpen(true);
  };

  const openEdit = (u: Unit) => {
    setEditing(u);
    form.resetFields();
    form.setFieldsValue(u);
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
      await run(() => api.patch(`/units/${editing.id}`, values), {
        success: t("pages.master.products.unitSaved"),
        onSuccess: () => {
          setEditOpen(false);
          refresh();
        },
      });
    } else {
      await run(() => api.post("/units", values), {
        success: t("pages.master.products.unitSaved"),
        onSuccess: () => {
          setEditOpen(false);
          refresh();
        },
      });
    }
  };

  const handleDelete = async (id: string) => {
    await run(() => api.delete(`/units/${id}`), {
      success: t("pages.master.products.unitDeleted"),
      onSuccess: refresh,
    });
  };

  const columns: TableProps<Unit>["columns"] = [
    {
      title: t("pages.master.products.unitColCode"),
      dataIndex: "code",
      width: 120,
    },
    {
      title: t("pages.master.products.unitColNameEn"),
      dataIndex: "name_en",
    },
    {
      title: t("pages.master.products.unitColNameZh"),
      dataIndex: "name_zh",
    },
    {
      title: t("pages.master.products.unitColActions"),
      key: "actions",
      width: 160,
      render: (_: unknown, r: Unit) => (
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
        {t("pages.master.products.newUnit")}
      </Button>
      <Table<Unit>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={items}
        columns={columns}
        pagination={false}
      />
      <Drawer
        title={editing ? t("pages.master.products.editUnit") : t("pages.master.products.newUnit")}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        width={420}
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
          <Form.Item name="code" label={t("pages.master.products.unitColCode")} rules={[{ required: true, message: t("pages.master.products.codeRequired") }]}>
            <Input />
          </Form.Item>
          <Form.Item name="name_en" label={t("pages.master.products.unitColNameEn")} rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="name_zh" label={t("pages.master.products.unitColNameZh")}>
            <Input />
          </Form.Item>
        </Form>
      </Drawer>
    </>
  );
}

export default function ProductsPage() {
  const { t } = useLanguage();
  return (
    <Card title={t("pages.master.products.title")}>
      <Tabs
        defaultActiveKey="products"
        items={[
          { key: "products", label: t("pages.master.products.tabProducts"), children: <ProductsTab /> },
          { key: "categories", label: t("pages.master.products.tabCategories"), children: <CategoriesTab /> },
          { key: "units", label: t("pages.master.products.tabUnits"), children: <UnitsTab /> },
        ]}
      />
    </Card>
  );
}
