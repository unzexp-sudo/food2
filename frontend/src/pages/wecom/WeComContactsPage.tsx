import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { LinkOutlined, ReloadOutlined } from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { api, gatewayApi, WECOM_GATEWAY_URL, type Page } from "../../api/client";
import { useList, useMutate } from "../../api/hooks";
import { formatDateTime, pickName } from "../../utils/format";
import ConfidenceTag from "../../components/ConfidenceTag";
import { parseStoredUser } from "../../types";
import type { ErpCustomer, WeComContact } from "./types";

/** WeCom contacts (GET {gateway}/wecom/contacts) + manual ERP binding. */
export default function WeComContactsPage() {
  const { t, lang } = useLanguage();
  const user = parseStoredUser();
  const canMutate = user?.role === "admin" || user?.role === "ops";

  const [customerFilter, setCustomerFilter] = useState<string | undefined>();
  const [keyword, setKeyword] = useState<string | undefined>();

  const params = useMemo(
    () => ({
      ...(customerFilter ? { customer_id: customerFilter } : {}),
      ...(keyword ? { q: keyword } : {}),
    }),
    [customerFilter, keyword],
  );

  const list = useList<WeComContact>("/wecom/contacts", params, gatewayApi);
  const { loading: mutateLoading, run } = useMutate();

  const [customers, setCustomers] = useState<ErpCustomer[]>([]);
  useEffect(() => {
    api
      .get<Page<ErpCustomer>>("/customers", { page: 1, page_size: 200 })
      .then((res) => setCustomers(res.items))
      .catch(() => setCustomers([]));
  }, []);

  const customerName = (id: string | null) => {
    if (!id) return null;
    const c = customers.find((x) => x.id === id);
    return c ? pickName(lang, c.name_en, c.name_zh) : id;
  };

  // Bind modal state.
  const [bindOpen, setBindOpen] = useState(false);
  const [bindTarget, setBindTarget] = useState<WeComContact | null>(null);
  const [form] = Form.useForm<{ customer_id: string }>();

  const openBind = (row: WeComContact) => {
    setBindTarget(row);
    form.setFieldsValue({ customer_id: row.customer_id ?? undefined });
    setBindOpen(true);
  };

  const submitBind = async () => {
    if (!bindTarget) return;
    let values: { customer_id: string };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const ok = await run(
      () =>
        gatewayApi.post(`/wecom/contacts/${bindTarget.external_userid}/bind`, {
          customer_id: values.customer_id,
          bind_method: "manual",
        }),
      { success: t("pages.wecom.contacts.bindSuccess") },
    );
    if (ok) {
      setBindOpen(false);
      setBindTarget(null);
      list.refresh();
    }
  };

  const columns = [
    {
      title: t("pages.wecom.contacts.colName"),
      dataIndex: "name",
      render: (v: string | null, r: WeComContact) => (
        <Space direction="vertical" size={0}>
          <span>{v ?? r.alias ?? "—"}</span>
          {r.corp_name && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {r.corp_name}
            </Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: t("pages.wecom.contacts.colExternalId"),
      dataIndex: "external_userid",
      width: 200,
      render: (v: string) => <Typography.Text code>{v}</Typography.Text>,
    },
    {
      title: t("pages.wecom.contacts.colCustomer"),
      dataIndex: "customer_id",
      width: 200,
      render: (v: string | null) =>
        v ? (
          <Tag color="blue">{customerName(v)}</Tag>
        ) : (
          <Tag>{t("pages.wecom.contacts.unbound")}</Tag>
        ),
    },
    {
      title: t("pages.wecom.contacts.colBindMethod"),
      dataIndex: "bind_method",
      width: 140,
      render: (v: string | null) =>
        v ? <Tag>{t(`status.wecomBind.${v}`)}</Tag> : "—",
    },
    {
      title: t("pages.wecom.contacts.colConfidence"),
      dataIndex: "bind_confidence",
      width: 110,
      render: (v: number | null) => (v === null ? "—" : <ConfidenceTag value={v} />),
    },
    {
      title: t("pages.wecom.contacts.colStaff"),
      dataIndex: "is_staff",
      width: 100,
      render: (v: boolean) => (v ? <Tag color="gold">{t("pages.wecom.contacts.staff")}</Tag> : "—"),
    },
    {
      title: t("pages.wecom.contacts.colUpdatedAt"),
      dataIndex: "updated_at",
      width: 150,
      render: (v: string) => formatDateTime(v),
    },
    {
      title: t("common.actions"),
      key: "actions",
      width: 120,
      render: (_: unknown, r: WeComContact) =>
        canMutate ? (
          <Button size="small" icon={<LinkOutlined />} onClick={() => openBind(r)}>
            {t("pages.wecom.contacts.bind")}
          </Button>
        ) : (
          "—"
        ),
    },
  ];

  return (
    <Card
      title={t("pages.wecom.contacts.title")}
      extra={
        <Button icon={<ReloadOutlined />} onClick={list.refresh} loading={list.loading}>
          {t("common.refresh")}
        </Button>
      }
    >
      {list.data === null && !list.loading && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={t("pages.wecom.gatewayUnreachable", { url: WECOM_GATEWAY_URL })}
        />
      )}

      <Space wrap size="middle" style={{ marginBottom: 12 }}>
        <Input.Search
          allowClear
          placeholder={t("pages.wecom.contacts.filterKeyword")}
          style={{ width: 240 }}
          onSearch={(v) => {
            setKeyword(v || undefined);
            list.setPage(1);
          }}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder={t("pages.wecom.contacts.allCustomers")}
          style={{ width: 240 }}
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
      </Space>

      <Table<WeComContact>
        rowKey="id"
        loading={list.loading}
        dataSource={list.items}
        columns={columns}
        size="middle"
        scroll={{ x: 1100 }}
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

      <Modal
        title={t("pages.wecom.contacts.bindTitle")}
        open={bindOpen}
        onCancel={() => {
          setBindOpen(false);
          setBindTarget(null);
        }}
        onOk={submitBind}
        confirmLoading={mutateLoading}
        okText={t("common.save")}
        cancelText={t("common.cancel")}
        destroyOnClose
      >
        {bindTarget && (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Typography.Text>
              {bindTarget.name ?? bindTarget.alias ?? "—"}{" "}
              <Typography.Text code>{bindTarget.external_userid}</Typography.Text>
            </Typography.Text>
            <Form form={form} layout="vertical">
              <Form.Item
                name="customer_id"
                label={t("pages.wecom.contacts.bindCustomer")}
                rules={[
                  { required: true, message: t("pages.wecom.contacts.bindCustomerRequired") },
                ]}
              >
                <Select
                  showSearch
                  optionFilterProp="label"
                  placeholder={t("pages.wecom.contacts.bindCustomer")}
                  options={customers.map((c) => ({
                    value: c.id,
                    label: `${c.code} — ${pickName(lang, c.name_en, c.name_zh)}`,
                  }))}
                />
              </Form.Item>
            </Form>
          </Space>
        )}
      </Modal>
    </Card>
  );
}
