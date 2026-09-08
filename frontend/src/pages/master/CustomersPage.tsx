import { useEffect, useMemo, useState } from "react";
import {
  Button,
  Card,
  Drawer,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Typography,
  type TableProps,
} from "antd";
import { EditOutlined, PlusOutlined } from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { api, type Page } from "../../api/client";
import { useList, useMutate } from "../../api/hooks";
import { pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";

interface Customer {
  id: string;
  code: string;
  name_en: string;
  name_zh: string;
  type: string;
  contact_name: string | null;
  contact_phone: string | null;
  address: string | null;
  delivery_zone: string | null;
  notes: string | null;
  status: string;
}
interface CustomerContact {
  id: string;
  customer_id: string;
  name: string;
  phone: string | null;
  role: string | null;
}
interface CustomerAlias {
  id: string;
  customer_id: string;
  alias: string;
  product_id: string | null;
  product_name_en?: string | null;
  product_name_zh?: string | null;
}
interface Product {
  id: string;
  sku: string;
  name_en: string;
  name_zh: string;
}

const CUSTOMER_TYPES = ["school", "restaurant", "canteen", "other"];
const STATUS_VALUES = ["active", "inactive"];

export default function CustomersPage() {
  const { t, lang } = useLanguage();
  const [q, setQ] = useState<string>("");
  const [typeFilter, setTypeFilter] = useState<string | undefined>();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const params = useMemo(
    () => ({
      ...(q ? { q } : {}),
      ...(typeFilter ? { type: typeFilter } : {}),
      ...(statusFilter ? { status: statusFilter } : {}),
    }),
    [q, typeFilter, statusFilter],
  );
  const list = useList<Customer>("/customers", params);

  // Edit/Create drawer
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<Customer | null>(null);
  const [form] = Form.useForm();
  const { loading: mutateLoading, run } = useMutate();

  // Detail drawer (contacts + aliases)
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [tab, setTab] = useState("contacts");
  const [contacts, setContacts] = useState<CustomerContact[]>([]);
  const [aliases, setAliases] = useState<CustomerAlias[]>([]);
  const [products, setProducts] = useState<Product[]>([]);

  // Contact/alias edit modal
  const [subOpen, setSubOpen] = useState(false);
  const [subKind, setSubKind] = useState<"contact" | "alias">("contact");
  const [subEditing, setSubEditing] = useState<CustomerContact | CustomerAlias | null>(null);
  const [subForm] = Form.useForm();

  useEffect(() => {
    api
      .get<Page<Product>>("/products", { page: 1, page_size: 200 })
      .then((r) => setProducts(r.items))
      .catch(() => setProducts([]));
  }, []);

  const openNew = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ type: "other", status: "active" });
    setEditOpen(true);
  };

  const openEdit = (c: Customer) => {
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
      const ok = await run(() => api.patch(`/customers/${editing.id}`, values), {
        success: t("pages.master.customers.saved"),
        onSuccess: () => {
          setEditOpen(false);
          list.refresh();
        },
      });
      void ok;
    } else {
      const ok = await run(() => api.post("/customers", values), {
        success: t("pages.master.customers.saved"),
        onSuccess: () => {
          setEditOpen(false);
          list.refresh();
        },
      });
      void ok;
    }
  };

  const toggleStatus = async (c: Customer) => {
    const newStatus = c.status === "active" ? "inactive" : "active";
    const ok = await run(() => api.patch(`/customers/${c.id}`, { status: newStatus }), {
      success:
        newStatus === "active"
          ? t("pages.master.customers.activated")
          : t("pages.master.customers.deactivated"),
      onSuccess: list.refresh,
    });
    void ok;
  };

  const openDetail = (c: Customer) => {
    setDetailId(c.id);
    setDetailOpen(true);
    setTab("contacts");
    setContacts([]);
    setAliases([]);
    api
      .get<CustomerContact[]>(`/customers/${c.id}/contacts`)
      .then(setContacts)
      .catch(() => setContacts([]));
    api
      .get<CustomerAlias[]>(`/customers/${c.id}/aliases`)
      .then(setAliases)
      .catch(() => setAliases([]));
  };

  const openContactNew = () => {
    setSubKind("contact");
    setSubEditing(null);
    subForm.resetFields();
    setSubOpen(true);
  };

  const openContactEdit = (c: CustomerContact) => {
    setSubKind("contact");
    setSubEditing(c);
    subForm.resetFields();
    subForm.setFieldsValue(c);
    setSubOpen(true);
  };

  const openAliasNew = () => {
    setSubKind("alias");
    setSubEditing(null);
    subForm.resetFields();
    setSubOpen(true);
  };

  const openAliasEdit = (a: CustomerAlias) => {
    setSubKind("alias");
    setSubEditing(a);
    subForm.resetFields();
    subForm.setFieldsValue(a);
    setSubOpen(true);
  };

  const handleSubSave = async () => {
    if (!detailId) return;
    let values: Record<string, unknown>;
    try {
      values = await subForm.validateFields();
    } catch {
      return;
    }
    if (subKind === "contact") {
      const editingContact = subEditing as CustomerContact | null;
      if (editingContact) {
        // No PATCH endpoint per contract; delete + recreate would be needed.
        // Workaround: use POST after DELETE.
        await run(
          () =>
            api.delete(`/customers/contacts/${editingContact.id}`).then(() =>
              api.post(`/customers/${detailId}/contacts`, values),
            ),
          { success: t("pages.master.customers.contactSaved") },
        );
      } else {
        await run(() => api.post(`/customers/${detailId}/contacts`, values), {
          success: t("pages.master.customers.contactSaved"),
        });
      }
      api.get<CustomerContact[]>(`/customers/${detailId}/contacts`).then(setContacts).catch(() => {});
    } else {
      const editingAlias = subEditing as CustomerAlias | null;
      if (editingAlias) {
        await run(
          () =>
            api.delete(`/customers/aliases/${editingAlias.id}`).then(() =>
              api.post(`/customers/${detailId}/aliases`, values),
            ),
          { success: t("pages.master.customers.aliasSaved") },
        );
      } else {
        await run(() => api.post(`/customers/${detailId}/aliases`, values), {
          success: t("pages.master.customers.aliasSaved"),
        });
      }
      api.get<CustomerAlias[]>(`/customers/${detailId}/aliases`).then(setAliases).catch(() => {});
    }
    setSubOpen(false);
  };

  const deleteContact = async (id: string) => {
    if (!detailId) return;
    await run(() => api.delete(`/customers/contacts/${id}`), {
      success: t("pages.master.customers.contactDeleted"),
      onSuccess: () =>
        api.get<CustomerContact[]>(`/customers/${detailId}/contacts`).then(setContacts).catch(() => {}),
    });
  };

  const deleteAlias = async (id: string) => {
    if (!detailId) return;
    await run(() => api.delete(`/customers/aliases/${id}`), {
      success: t("pages.master.customers.aliasDeleted"),
      onSuccess: () =>
        api.get<CustomerAlias[]>(`/customers/${detailId}/aliases`).then(setAliases).catch(() => {}),
    });
  };

  const columns: TableProps<Customer>["columns"] = [
    {
      title: t("pages.master.customers.code"),
      dataIndex: "code",
      width: 130,
      render: (v: string, r: Customer) => <a onClick={() => openDetail(r)}>{v}</a>,
    },
    {
      title: t("pages.master.customers.nameEn"),
      key: "name_en",
      dataIndex: "name_en",
    },
    {
      title: t("pages.master.customers.nameZh"),
      dataIndex: "name_zh",
    },
    {
      title: t("pages.master.customers.type"),
      dataIndex: "type",
      width: 110,
      render: (v: string) => <StatusTag domain="customerType" value={v} />,
    },
    {
      title: t("pages.master.customers.contactName"),
      dataIndex: "contact_name",
      width: 140,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.master.customers.deliveryZone"),
      dataIndex: "delivery_zone",
      width: 140,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.master.customers.status"),
      dataIndex: "status",
      width: 110,
      render: (v: string) => <StatusTag domain="master" value={v} />,
    },
    {
      title: t("common.actions"),
      key: "actions",
      width: 200,
      render: (_: unknown, r: Customer) => (
        <Space size="small">
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>
            {t("common.edit")}
          </Button>
          <Popconfirm
            title={
              r.status === "active"
                ? t("pages.master.customers.deactivateConfirm")
                : t("pages.master.customers.activateConfirm")
            }
            onConfirm={() => toggleStatus(r)}
          >
            <Button size="small" danger={r.status === "active"}>
              {r.status === "active"
                ? t("pages.master.customers.deactivate")
                : t("pages.master.customers.activate")}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const contactCols: TableProps<CustomerContact>["columns"] = [
    { title: t("pages.master.customers.contactName"), dataIndex: "name" },
    {
      title: t("pages.master.customers.contactRole"),
      dataIndex: "role",
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.master.customers.contactPhone"),
      dataIndex: "phone",
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("common.actions"),
      key: "actions",
      width: 160,
      render: (_: unknown, r: CustomerContact) => (
        <Space size="small">
          <Button size="small" onClick={() => openContactEdit(r)}>
            {t("common.edit")}
          </Button>
          <Popconfirm title={t("common.delete")} onConfirm={() => deleteContact(r.id)}>
            <Button size="small" danger>
              {t("common.delete")}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const aliasCols: TableProps<CustomerAlias>["columns"] = [
    {
      title: t("pages.master.customers.aliasText"),
      dataIndex: "alias",
    },
    {
      title: t("pages.master.customers.aliasProduct"),
      key: "product",
      render: (_: unknown, r: CustomerAlias) => pickName(lang, r.product_name_en, r.product_name_zh),
    },
    {
      title: t("common.actions"),
      key: "actions",
      width: 160,
      render: (_: unknown, r: CustomerAlias) => (
        <Space size="small">
          <Button size="small" onClick={() => openAliasEdit(r)}>
            {t("common.edit")}
          </Button>
          <Popconfirm title={t("common.delete")} onConfirm={() => deleteAlias(r.id)}>
            <Button size="small" danger>
              {t("common.delete")}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={t("pages.master.customers.title")}
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={openNew}>
          {t("pages.master.customers.new")}
        </Button>
      }
    >
      <Space wrap size="middle" style={{ marginBottom: 12 }}>
        <Input.Search
          allowClear
          placeholder={t("pages.master.customers.filterKeyword")}
          style={{ width: 240 }}
          onSearch={(v) => {
            setQ(v);
            list.setPage(1);
          }}
        />
        <Select
          allowClear
          placeholder={t("pages.master.customers.allTypes")}
          style={{ width: 160 }}
          value={typeFilter}
          onChange={(v) => {
            setTypeFilter(v);
            list.setPage(1);
          }}
          options={CUSTOMER_TYPES.map((s) => ({ value: s, label: t(`status.customerType.${s}`) }))}
        />
        <Select
          allowClear
          placeholder={t("pages.master.customers.allStatuses")}
          style={{ width: 140 }}
          value={statusFilter}
          onChange={(v) => {
            setStatusFilter(v);
            list.setPage(1);
          }}
          options={STATUS_VALUES.map((s) => ({ value: s, label: t(`status.master.${s}`) }))}
        />
      </Space>
      <Table<Customer>
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
        title={editing ? t("pages.master.customers.edit") : t("pages.master.customers.new")}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        width={560}
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
            name="code"
            label={t("pages.master.customers.code")}
            rules={[{ required: true, message: t("pages.master.customers.codeRequired") }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="name_en"
            label={t("pages.master.customers.nameEn")}
            rules={[{ required: true, message: t("pages.master.customers.nameEnRequired") }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="name_zh" label={t("pages.master.customers.nameZh")}>
            <Input />
          </Form.Item>
          <Form.Item name="type" label={t("pages.master.customers.type")}>
            <Select options={CUSTOMER_TYPES.map((s) => ({ value: s, label: t(`status.customerType.${s}`) }))} />
          </Form.Item>
          <Form.Item name="contact_name" label={t("pages.master.customers.contactName")}>
            <Input />
          </Form.Item>
          <Form.Item name="contact_phone" label={t("pages.master.customers.contactPhone")}>
            <Input />
          </Form.Item>
          <Form.Item name="address" label={t("pages.master.customers.address")}>
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="delivery_zone" label={t("pages.master.customers.deliveryZone")}>
            <Input />
          </Form.Item>
          <Form.Item name="notes" label={t("pages.master.customers.notes")}>
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="status" label={t("pages.master.customers.status")}>
            <Select options={STATUS_VALUES.map((s) => ({ value: s, label: t(`status.master.${s}`) }))} />
          </Form.Item>
        </Form>
      </Drawer>

      <Drawer
        title={t("pages.master.customers.manageContacts")}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={860}
      >
        <Tabs
          activeKey={tab}
          onChange={setTab}
          items={[
            {
              key: "contacts",
              label: t("pages.master.customers.contacts"),
              children: (
                <>
                  <Button
                    type="primary"
                    size="small"
                    icon={<PlusOutlined />}
                    onClick={openContactNew}
                    style={{ marginBottom: 8 }}
                  >
                    {t("pages.master.customers.addContact")}
                  </Button>
                  {contacts.length > 0 ? (
                    <Table<CustomerContact>
                      rowKey="id"
                      size="small"
                      pagination={false}
                      dataSource={contacts}
                      columns={contactCols}
                    />
                  ) : (
                    <Typography.Text type="secondary">
                      {t("pages.master.customers.noContacts")}
                    </Typography.Text>
                  )}
                </>
              ),
            },
            {
              key: "aliases",
              label: t("pages.master.customers.aliases"),
              children: (
                <>
                  <Button
                    type="primary"
                    size="small"
                    icon={<PlusOutlined />}
                    onClick={openAliasNew}
                    style={{ marginBottom: 8 }}
                  >
                    {t("pages.master.customers.addAlias")}
                  </Button>
                  {aliases.length > 0 ? (
                    <Table<CustomerAlias>
                      rowKey="id"
                      size="small"
                      pagination={false}
                      dataSource={aliases}
                      columns={aliasCols}
                    />
                  ) : (
                    <Typography.Text type="secondary">
                      {t("pages.master.customers.noAliases")}
                    </Typography.Text>
                  )}
                </>
              ),
            },
          ]}
        />
      </Drawer>

      <Drawer
        title={
          subKind === "contact"
            ? subEditing
              ? t("pages.master.customers.editContact")
              : t("pages.master.customers.addContact")
            : t("pages.master.customers.addAlias")
        }
        open={subOpen}
        onClose={() => setSubOpen(false)}
        width={460}
        footer={
          <Space style={{ float: "right" }}>
            <Button onClick={() => setSubOpen(false)}>{t("common.cancel")}</Button>
            <Button type="primary" loading={mutateLoading} onClick={handleSubSave}>
              {t("common.save")}
            </Button>
          </Space>
        }
      >        <Form form={subForm} layout="vertical">
          {subKind === "contact" ? (
            <>
              <Form.Item
                name="name"
                label={t("pages.master.customers.contactName")}
                rules={[{ required: true, message: t("pages.master.customers.contactNameRequired") }]}
              >
                <Input />
              </Form.Item>
              <Form.Item name="role" label={t("pages.master.customers.contactRole")}>
                <Input />
              </Form.Item>
              <Form.Item name="phone" label={t("pages.master.customers.contactPhone")}>
                <Input />
              </Form.Item>
            </>
          ) : (
            <>
              <Form.Item
                name="alias"
                label={t("pages.master.customers.aliasText")}
                rules={[{ required: true, message: t("pages.master.customers.aliasTextRequired") }]}
              >
                <Input />
              </Form.Item>
              <Form.Item
                name="product_id"
                label={t("pages.master.customers.aliasProduct")}
                rules={[{ required: true, message: t("pages.master.customers.aliasProductRequired") }]}
              >
                <Select
                  showSearch
                  placeholder={t("pages.master.customers.aliasProduct")}
                  options={products.map((p) => ({
                    value: p.id,
                    label: `${p.sku} · ${pickName(lang, p.name_en, p.name_zh)}`,
                  }))}
                  optionFilterProp="label"
                />
              </Form.Item>
            </>
          )}
        </Form>
      </Drawer>
    </Card>
  );
}
