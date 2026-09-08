import { useMemo, useState } from "react";
import {
  Button,
  Card,
  DatePicker,
  Drawer,
  Form,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Typography,
  type TableProps,
} from "antd";
import { useLanguage } from "../../i18n";
import { api } from "../../api/client";
import { useList, useMutate } from "../../api/hooks";
import { formatDate, pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";

/** Entity shapes per AGENT_CONTRACTS §4. */
interface PickList {
  id: string;
  pick_number: string;
  delivery_date: string;
  status: string;
  line_count: number;
}
interface PickLine {
  id: string;
  order_id: string;
  order_number: string;
  customer_name_en: string;
  customer_name_zh: string;
  product_id: string;
  product_name_en: string;
  product_name_zh: string;
  quantity: number;
  picked_quantity: number;
  status: string;
}
interface PickListDetail extends PickList {
  lines: PickLine[];
}

const PICK_STATUSES = ["open", "picking", "picked", "cancelled"];

export default function PickListsPage() {
  const { t, lang } = useLanguage();
  const [dateFilter, setDateFilter] = useState<string | undefined>();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const params = useMemo(
    () => ({
      ...(dateFilter ? { delivery_date: dateFilter } : {}),
      ...(statusFilter ? { status: statusFilter } : {}),
    }),
    [dateFilter, statusFilter],
  );
  const list = useList<PickList>("/pick-lists", params);

  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<PickListDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [pickLineId, setPickLineId] = useState<string | null>(null);
  const [pickedQty, setPickedQty] = useState<number>(0);
  const [pickOpen, setPickOpen] = useState(false);
  const { loading: mutateLoading, run } = useMutate();

  const [genOpen, setGenOpen] = useState(false);
  const [genForm] = Form.useForm();

  const handleRow = (id: string) => {
    setDetailOpen(true);
    setDetailLoading(true);
    setDetail(null);
    api
      .get<PickListDetail>(`/pick-lists/${id}`)
      .then((d) => setDetail(d))
      .catch(() => setDetail(null))
      .finally(() => setDetailLoading(false));
  };

  const openPick = (line: PickLine) => {
    setPickLineId(line.id);
    setPickedQty(line.quantity);
    setPickOpen(true);
  };

  const handlePick = async () => {
    if (!detail || !pickLineId) return;
    const ok = await run(
      () =>
        api.post(`/pick-lists/${detail.id}/lines/${pickLineId}/pick`, {
          picked_quantity: pickedQty,
        }),
      {
        success: t("pages.warehouse.pickLists.picked"),
      },
    );
    if (ok) {
      setPickOpen(false);
      // refresh detail
      api.get<PickListDetail>(`/pick-lists/${detail.id}`).then((d) => setDetail(d)).catch(() => {});
      list.refresh();
    }
  };

  const handleGenerate = async () => {
    let values: { delivery_date: unknown };
    try {
      values = await genForm.validateFields();
    } catch {
      return;
    }
    const deliveryDate = (values.delivery_date as { format: (f: string) => string }).format(
      "YYYY-MM-DD",
    );
    const ok = await run(() => api.post("/pick-lists/generate", { delivery_date: deliveryDate }), {
      success: t("pages.warehouse.pickLists.generateSuccess"),
    });
    if (ok) {
      setGenOpen(false);
      genForm.resetFields();
      list.refresh();
    }
  };

  const columns: TableProps<PickList>["columns"] = [
    {
      title: t("pages.warehouse.pickLists.colPickNumber"),
      dataIndex: "pick_number",
      width: 180,
      render: (v: string, r: PickList) => <a onClick={() => handleRow(r.id)}>{v}</a>,
    },
    {
      title: t("pages.warehouse.pickLists.colDeliveryDate"),
      dataIndex: "delivery_date",
      width: 130,
      render: (v: string) => formatDate(v),
    },
    {
      title: t("pages.warehouse.pickLists.colStatus"),
      dataIndex: "status",
      width: 130,
      render: (v: string) => <StatusTag domain="pick" value={v} />,
    },
    {
      title: t("pages.warehouse.pickLists.colLines"),
      dataIndex: "line_count",
      width: 80,
      align: "right" as const,
    },
  ];

  const lineCols: TableProps<PickLine>["columns"] = [
    {
      title: t("pages.warehouse.pickLists.colLineOrder"),
      dataIndex: "order_number",
      width: 170,
    },
    {
      title: t("pages.warehouse.pickLists.colLineCustomer"),
      key: "customer",
      render: (_: unknown, r: PickLine) =>
        pickName(lang, r.customer_name_en, r.customer_name_zh),
    },
    {
      title: t("pages.warehouse.pickLists.colLineProduct"),
      key: "product",
      render: (_: unknown, r: PickLine) =>
        pickName(lang, r.product_name_en, r.product_name_zh),
    },
    {
      title: t("pages.warehouse.pickLists.colLineQty"),
      dataIndex: "quantity",
      width: 90,
      align: "right" as const,
    },
    {
      title: t("pages.warehouse.pickLists.colLinePicked"),
      dataIndex: "picked_quantity",
      width: 100,
      align: "right" as const,
    },
    {
      title: t("pages.warehouse.pickLists.colLineStatus"),
      dataIndex: "status",
      width: 110,
      render: (v: string) => <StatusTag domain="pick" value={v} />,
    },
    {
      title: t("pages.warehouse.pickLists.colLineAction"),
      key: "action",
      width: 120,
      render: (_: unknown, r: PickLine) =>
        r.status === "open" || r.status === "picking" ? (
          <Button size="small" onClick={() => openPick(r)}>
            {t("pages.warehouse.pickLists.pick")}
          </Button>
        ) : null,
    },
  ];

  return (
    <Card
      title={t("pages.warehouse.pickLists.title")}
      extra={
        <Button type="primary" onClick={() => setGenOpen(true)}>
          {t("pages.warehouse.pickLists.generate")}
        </Button>
      }
    >
      <Space wrap size="middle" style={{ marginBottom: 12 }}>
        <DatePicker
          placeholder={t("pages.warehouse.pickLists.filterDeliveryDate")}
          onChange={(d) => {
            setDateFilter(d ? d.format("YYYY-MM-DD") : undefined);
            list.setPage(1);
          }}
        />
        <Select
          allowClear
          placeholder={t("pages.warehouse.pickLists.allStatuses")}
          style={{ width: 200 }}
          value={statusFilter}
          onChange={(v) => {
            setStatusFilter(v);
            list.setPage(1);
          }}
          options={PICK_STATUSES.map((s) => ({ value: s, label: t(`status.pick.${s}`) }))}
        />
      </Space>
      <Table<PickList>
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
        title={t("pages.warehouse.pickLists.details")}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={960}
      >
        {detailLoading ? (
          <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
        ) : detail ? (
          <Table<PickLine>
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={detail.lines}
            columns={lineCols}
          />
        ) : (
          <Typography.Text type="secondary">
            {t("pages.warehouse.pickLists.noLines")}
          </Typography.Text>
        )}
      </Drawer>

      <Modal
        title={t("pages.warehouse.pickLists.pickTitle")}
        open={pickOpen}
        onCancel={() => setPickOpen(false)}
        onOk={handlePick}
        confirmLoading={mutateLoading}
        okText={t("pages.warehouse.pickLists.pick")}
      >
        <Form layout="vertical">
          <Form.Item
            label={t("pages.warehouse.pickLists.pickedQty")}
            required
            rules={[{ required: true, message: t("pages.warehouse.pickLists.pickedQtyRequired") }]}
          >
            <InputNumber
              min={0}
              step={1}
              value={pickedQty}
              onChange={(v) => setPickedQty(v ?? 0)}
              style={{ width: "100%" }}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={t("pages.warehouse.pickLists.generateTitle")}
        open={genOpen}
        onCancel={() => setGenOpen(false)}
        onOk={handleGenerate}
        confirmLoading={mutateLoading}
        okText={t("pages.warehouse.pickLists.generate")}
      >
        <Typography.Paragraph type="secondary">
          {t("pages.warehouse.pickLists.generateHint")}
        </Typography.Paragraph>
        <Form form={genForm} layout="vertical">
          <Form.Item
            name="delivery_date"
            label={t("pages.warehouse.pickLists.deliveryDate")}
            rules={[{ required: true }]}
          >
            <DatePicker style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
