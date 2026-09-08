import { useEffect, useMemo, useState } from "react";
import {
  Button,
  Card,
  Drawer,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Table,
  Tag,
  type TableProps,
} from "antd";
import { useLanguage } from "../../i18n";
import { api, type Page } from "../../api/client";
import { useList, useMutate } from "../../api/hooks";
import { formatDateTime, pickName } from "../../utils/format";
import { parseStoredUser } from "../../types";

/** Entity shapes per AGENT_CONTRACTS §4. */
interface Product {
  id: string;
  sku: string;
  name_en: string;
  name_zh: string;
  is_active: boolean;
}
interface InventoryMovement {
  id: string;
  product_id: string;
  product_name_en: string;
  product_name_zh: string;
  quantity_delta: number;
  ref_type: string | null;
  ref_id: string | null;
  note: string | null;
  created_at: string;
}

export default function InventoryPage() {
  const { t, lang } = useLanguage();
  const user = parseStoredUser();
  const canMutate = user?.role === "admin" || user?.role === "warehouse";

  const [productFilter, setProductFilter] = useState<string | undefined>();
  const params = useMemo(
    () => (productFilter ? { product_id: productFilter } : {}),
    [productFilter],
  );
  const list = useList<InventoryMovement>("/inventory", params);

  const [products, setProducts] = useState<Product[]>([]);
  useEffect(() => {
    api
      .get<Page<Product>>("/products", { page: 1, page_size: 200 })
      .then((r) => setProducts(r.items))
      .catch(() => setProducts([]));
  }, []);

  const [lossOpen, setLossOpen] = useState(false);
  const [form] = Form.useForm();
  const { loading: mutateLoading, run } = useMutate();

  const handleLoss = async () => {
    let values: { product_id: string; quantity: number; reason: string };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const ok = await run(
      () =>
        api.post("/inventory/loss", {
          product_id: values.product_id,
          quantity: values.quantity,
          reason: values.reason,
        }),
      { success: t("pages.warehouse.inventory.lossSaved") },
    );
    if (ok) {
      setLossOpen(false);
      form.resetFields();
      list.refresh();
    }
  };

  const columns: TableProps<InventoryMovement>["columns"] = [
    {
      title: t("pages.warehouse.inventory.colProduct"),
      key: "product",
      render: (_: unknown, r: InventoryMovement) =>
        pickName(lang, r.product_name_en, r.product_name_zh),
    },
    {
      title: t("pages.warehouse.inventory.colDelta"),
      dataIndex: "quantity_delta",
      width: 120,
      align: "right" as const,
      render: (v: number) => (
        <Tag color={v >= 0 ? "green" : "red"}>
          {v > 0 ? `+${v}` : v}
        </Tag>
      ),
    },
    {
      title: t("pages.warehouse.inventory.colRefType"),
      dataIndex: "ref_type",
      width: 150,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.warehouse.inventory.colRefId"),
      dataIndex: "ref_id",
      width: 180,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.warehouse.inventory.colNote"),
      dataIndex: "note",
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.warehouse.inventory.colCreatedAt"),
      dataIndex: "created_at",
      width: 160,
      render: (v: string) => formatDateTime(v),
    },
  ];

  return (
    <Card
      title={t("pages.warehouse.inventory.title")}
      extra={
        canMutate ? (
          <Button type="primary" onClick={() => setLossOpen(true)}>
            {t("pages.warehouse.inventory.recordLoss")}
          </Button>
        ) : null
      }
    >
      <Space wrap size="middle" style={{ marginBottom: 12 }}>
        <Select
          allowClear
          showSearch
          placeholder={t("pages.warehouse.inventory.allProducts")}
          style={{ width: 280 }}
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
      <Table<InventoryMovement>
        rowKey="id"
        loading={list.loading}
        dataSource={list.items}
        columns={columns}
        size="middle"
        locale={{ emptyText: t("pages.warehouse.inventory.noData") }}
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
        title={t("pages.warehouse.inventory.recordLossTitle")}
        open={lossOpen}
        onClose={() => setLossOpen(false)}
        width={460}
        footer={
          <Space style={{ float: "right" }}>
            <Button onClick={() => setLossOpen(false)}>{t("common.cancel")}</Button>
            <Button type="primary" loading={mutateLoading} onClick={handleLoss}>
              {t("common.submit")}
            </Button>
          </Space>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="product_id"
            label={t("pages.warehouse.inventory.lossProduct")}
            rules={[{ required: true, message: t("pages.warehouse.inventory.lossProductRequired") }]}
          >
            <Select
              showSearch
              placeholder={t("pages.warehouse.inventory.selectProduct")}
              options={products.map((p) => ({
                value: p.id,
                label: `${p.sku} · ${pickName(lang, p.name_en, p.name_zh)}`,
              }))}
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item
            name="quantity"
            label={t("pages.warehouse.inventory.lossQuantity")}
            rules={[{ required: true, message: t("pages.warehouse.inventory.lossQuantityRequired") }]}
          >
            <InputNumber min={0} step={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="reason"
            label={t("pages.warehouse.inventory.lossReason")}
            rules={[{ required: true, message: t("pages.warehouse.inventory.lossReasonRequired") }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Drawer>
    </Card>
  );
}

export type { InventoryMovement };
