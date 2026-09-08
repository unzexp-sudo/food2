import { useState } from "react";
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
  Tag,
  type TableProps,
} from "antd";
import { EditOutlined, PlusOutlined } from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { useList, useMutate } from "../../api/hooks";
import { api } from "../../api/client";
import { parseStoredUser, type Role } from "../../types";

interface User {
  id: string;
  email: string;
  name: string;
  role: string;
  is_active: boolean;
  created_at: string;
}

const ROLE_VALUES: Role[] = ["admin", "ops", "warehouse", "finance", "driver"];
const ROLE_TAG_COLORS: Record<Role, string> = {
  admin: "gold",
  ops: "blue",
  warehouse: "green",
  finance: "purple",
  driver: "orange",
};

export default function UsersPage() {
  const { t } = useLanguage();
  const user = parseStoredUser();
  const list = useList<User>("/users", {});

  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [form] = Form.useForm();
  const { loading: mutateLoading, run } = useMutate();

  const openNew = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ role: "ops", is_active: true });
    setEditOpen(true);
  };

  const openEdit = (u: User) => {
    setEditing(u);
    form.resetFields();
    form.setFieldsValue({
      ...u,
      password: "",
    });
    setEditOpen(true);
  };

  const handleSave = async () => {
    let values: { email: string; name: string; role: string; password?: string; is_active: boolean };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const body: Record<string, unknown> = {
      email: values.email,
      name: values.name,
      role: values.role,
      is_active: values.is_active,
    };
    if (values.password) body.password = values.password;
    if (editing) {
      await run(() => api.patch(`/users/${editing.id}`, body), {
        success: t("pages.system.users.saved"),
        onSuccess: () => {
          setEditOpen(false);
          list.refresh();
        },
      });
    } else {
      await run(() => api.post("/users", body), {
        success: t("pages.system.users.saved"),
        onSuccess: () => {
          setEditOpen(false);
          list.refresh();
        },
      });
    }
  };

  const toggleActive = async (u: User) => {
    const next = !u.is_active;
    await run(() => api.patch(`/users/${u.id}`, { is_active: next }), {
      success: next
        ? t("pages.system.users.activated")
        : t("pages.system.users.deactivated"),
      onSuccess: list.refresh,
    });
  };

  const columns: TableProps<User>["columns"] = [
    {
      title: t("pages.system.users.colEmail"),
      dataIndex: "email",
    },
    {
      title: t("pages.system.users.colName"),
      dataIndex: "name",
    },
    {
      title: t("pages.system.users.colRole"),
      dataIndex: "role",
      width: 140,
      render: (v: string) => (
        <Tag color={ROLE_TAG_COLORS[v as Role] ?? "default"}>
          {t(`roles.${v}`)}
        </Tag>
      ),
    },
    {
      title: t("pages.system.users.colActive"),
      dataIndex: "is_active",
      width: 100,
      render: (v: boolean) => (
        <Tag color={v ? "green" : "default"}>{v ? t("pages.master.products.activeYes") : t("pages.master.products.activeNo")}</Tag>
      ),
    },
    {
      title: t("pages.system.users.colActions"),
      key: "actions",
      width: 200,
      render: (_: unknown, r: User) => (
        <Space size="small">
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>
            {t("common.edit")}
          </Button>
          {r.id !== user?.id && (
            <Popconfirm
              title={
                r.is_active
                  ? t("pages.system.users.deactivateConfirm")
                  : t("pages.system.users.activateConfirm")
              }
              onConfirm={() => toggleActive(r)}
            >
              <Button size="small" danger={r.is_active}>
                {r.is_active
                  ? t("pages.system.users.deactivate")
                  : t("pages.system.users.activate")}
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={t("pages.system.users.title")}
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={openNew}>
          {t("pages.system.users.new")}
        </Button>
      }
    >
      <Table<User>
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
        title={editing ? t("pages.system.users.edit") : t("pages.system.users.new")}
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
          <Form.Item name="email" label={t("pages.system.users.email")} rules={[{ required: true, message: t("pages.system.users.emailRequired") }]}>
            <Input />
          </Form.Item>
          <Form.Item name="name" label={t("pages.system.users.name")} rules={[{ required: true, message: t("pages.system.users.nameRequired") }]}>
            <Input />
          </Form.Item>
          <Form.Item name="role" label={t("pages.system.users.role")} rules={[{ required: true, message: t("pages.system.users.roleRequired") }]}>
            <Select options={ROLE_VALUES.map((r) => ({ value: r, label: t(`roles.${r}`) }))} />
          </Form.Item>
          <Form.Item
            name="password"
            label={t("pages.system.users.password")}
            rules={editing ? [] : [{ required: true, message: t("pages.system.users.passwordRequired") }]}
            extra={editing ? t("pages.system.users.passwordEditHint") : undefined}
          >
            <Input.Password />
          </Form.Item>
          <Form.Item name="is_active" label={t("pages.system.users.isActive")}>
            <Select
              options={[
                { value: true, label: t("pages.master.products.activeYes") },
                { value: false, label: t("pages.master.products.activeNo") },
              ]}
            />
          </Form.Item>
        </Form>
      </Drawer>
    </Card>
  );
}
