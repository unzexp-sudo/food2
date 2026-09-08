import { useEffect, useMemo, useState } from "react";
import {
  Button,
  Card,
  DatePicker,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Typography,
  Upload,
  type TableProps,
  type UploadFile,
  type UploadProps,
} from "antd";
import {
  CarOutlined,
  CheckOutlined,
  PaperClipOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { api, getApiError, type Page } from "../../api/client";
import { useList, useMutate } from "../../api/hooks";
import { formatDate, formatDateTime, pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";
import client from "../../api/client";
import { getMessage } from "../../api/message";
import { parseStoredUser } from "../../types";

/** Entity shapes per AGENT_CONTRACTS §4. */
interface Delivery {
  id: string;
  delivery_number: string;
  order_id: string;
  order_number: string;
  customer_id: string;
  customer_name_en: string;
  customer_name_zh: string;
  route: string | null;
  driver_id: string | null;
  driver_name: string | null;
  status: string;
  scheduled_date: string;
  picked_at: string | null;
  out_at: string | null;
  delivered_at: string | null;
  pod: {
    photo_url: string | null;
    signature_url: string | null;
    received_by: string | null;
    gps_lat: number | null;
    gps_lng: number | null;
    delivered_at: string | null;
  } | null;
  lines: DeliveryLine[];
}
interface DeliveryLine {
  id: string;
  order_line_id: string | null;
  product_name_en: string;
  product_name_zh: string;
  quantity: number;
  delivered_quantity: number;
}
interface User {
  id: string;
  email: string;
  name: string;
  role: string;
  is_active: boolean;
}

const DELIVERY_STATUSES = [
  "scheduled",
  "picked",
  "out_for_delivery",
  "delivered",
  "failed",
  "partial",
];

export default function DeliveryPage() {
  const { t, lang } = useLanguage();
  const user = parseStoredUser();
  // Driver sees only their own deliveries; the API enforces this too, but we
  // pre-filter so the UI is honest.
  const isDriver = user?.role === "driver";
  const canMutate =
    user?.role === "admin" || user?.role === "warehouse" || user?.role === "driver";
  const canAssign = user?.role === "admin" || user?.role === "ops";

  const [dateFilter, setDateFilter] = useState<string | undefined>();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [driverFilter, setDriverFilter] = useState<string | undefined>(
    isDriver && user ? user.id : undefined,
  );

  const params = useMemo(
    () => ({
      ...(dateFilter ? { date: dateFilter } : {}),
      ...(statusFilter ? { status: statusFilter } : {}),
      ...(driverFilter ? { driver_id: driverFilter } : {}),
    }),
    [dateFilter, statusFilter, driverFilter],
  );
  const list = useList<Delivery>("/deliveries", params);

  const [drivers, setDrivers] = useState<User[]>([]);
  useEffect(() => {
    api
      .get<Page<User>>("/users", { page: 1, page_size: 100 })
      .then((r) => setDrivers(r.items.filter((u) => u.role === "driver" && u.is_active)))
      .catch(() => setDrivers([]));
  }, []);

  // Detail drawer
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<Delivery | null>(null);

  const handleRow = (id: string) => {
    setDetailOpen(true);
    api
      .get<Delivery>(`/deliveries/${id}`)
      .then((d) => setDetail(d))
      .catch(() => setDetail(null));
  };

  // Assign-driver modal
  const [assignOpen, setAssignOpen] = useState(false);
  const [assignId, setAssignId] = useState<string | null>(null);
  const [assignDriverId, setAssignDriverId] = useState<string | undefined>();
  const { loading: mutateLoading, run } = useMutate();

  const openAssign = (id: string) => {
    setAssignId(id);
    setAssignDriverId(undefined);
    setAssignOpen(true);
  };

  const handleAssign = async () => {
    if (!assignId || !assignDriverId) return;
    const ok = await run(
      () => api.post(`/deliveries/${assignId}/assign`, { driver_id: assignDriverId }),
      { success: t("pages.delivery.assignSuccess") },
    );
    if (ok) {
      setAssignOpen(false);
      list.refresh();
      // Refresh detail if open
      if (detail?.id === assignId) {
        api.get<Delivery>(`/deliveries/${assignId}`).then((d) => setDetail(d)).catch(() => {});
      }
    }
  };

  const handleStatus = async (id: string, status: "picked" | "out_for_delivery") => {
    const ok = await run(() => api.post(`/deliveries/${id}/status`, { status }), {
      success: t("pages.delivery.statusSuccess"),
    });
    if (ok) {
      list.refresh();
      if (detail?.id === id) {
        api.get<Delivery>(`/deliveries/${id}`).then((d) => setDetail(d)).catch(() => {});
      }
    }
  };

  // Complete drawer
  const [completeOpen, setCompleteOpen] = useState(false);
  const [completeId, setCompleteId] = useState<string | null>(null);
  const [completeLines, setCompleteLines] = useState<DeliveryLine[]>([]);
  const [receivedBy, setReceivedBy] = useState<string>("");
  const [gpsLat, setGpsLat] = useState<number | undefined>();
  const [gpsLng, setGpsLng] = useState<number | undefined>();
  const [photo, setPhoto] = useState<File | null>(null);

  const openComplete = (d: Delivery) => {
    setCompleteId(d.id);
    setCompleteLines(d.lines.map((l) => ({ ...l, delivered_quantity: l.quantity })));
    setReceivedBy(d.pod?.received_by ?? "");
    setGpsLat(d.pod?.gps_lat ?? undefined);
    setGpsLng(d.pod?.gps_lng ?? undefined);
    setPhoto(null);
    setCompleteOpen(true);
  };

  const handleComplete = async () => {
    if (!completeId) return;
    const linesBody = completeLines.map((l) => ({
      delivery_line_id: l.id,
      delivered_quantity: l.delivered_quantity,
    }));
    const body: Record<string, unknown> = {
      lines: linesBody,
      ...(receivedBy ? { received_by: receivedBy } : {}),
      ...(gpsLat != null ? { gps_lat: gpsLat } : {}),
      ...(gpsLng != null ? { gps_lng: gpsLng } : {}),
    };
    try {
      if (photo) {
        const fd = new FormData();
        fd.append("lines", JSON.stringify(linesBody));
        if (receivedBy) fd.append("received_by", receivedBy);
        if (gpsLat != null) fd.append("gps_lat", String(gpsLat));
        if (gpsLng != null) fd.append("gps_lng", String(gpsLng));
        fd.append("photo", photo);
        await client.post(`/deliveries/${completeId}/complete`, fd, {
          headers: { "Content-Type": "multipart/form-data" },
        });
      } else {
        await api.post(`/deliveries/${completeId}/complete`, body);
      }
      getMessage()?.success(t("pages.delivery.completeSuccess"));
      setCompleteOpen(false);
      list.refresh();
      if (detail?.id === completeId) {
        api.get<Delivery>(`/deliveries/${completeId}`).then((d) => setDetail(d)).catch(() => {});
      }
    } catch (err) {
      getMessage()?.error(getApiError(err) ?? t("common.error"));
    }
  };

  const uploadProps: UploadProps = {
    beforeUpload: (file) => {
      setPhoto(file as unknown as File);
      return false;
    },
    onRemove: () => setPhoto(null),
    fileList: photo
      ? [
          {
            uid: "-1",
            name: photo.name,
            size: photo.size,
            type: photo.type,
            originFileObj: photo as unknown as UploadFile["originFileObj"],
          },
        ]
      : [],
    maxCount: 1,
  };

  const columns: TableProps<Delivery>["columns"] = [
    {
      title: t("pages.delivery.colDeliveryNumber"),
      dataIndex: "delivery_number",
      width: 170,
      render: (v: string, r: Delivery) => <a onClick={() => handleRow(r.id)}>{v}</a>,
    },
    {
      title: t("pages.delivery.colOrder"),
      dataIndex: "order_number",
      width: 170,
    },
    {
      title: t("pages.delivery.colCustomer"),
      key: "customer",
      render: (_: unknown, r: Delivery) => pickName(lang, r.customer_name_en, r.customer_name_zh),
    },
    {
      title: t("pages.delivery.colRoute"),
      dataIndex: "route",
      width: 120,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.delivery.colDriver"),
      dataIndex: "driver_name",
      width: 120,
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.delivery.colStatus"),
      dataIndex: "status",
      width: 130,
      render: (v: string) => <StatusTag domain="delivery" value={v} />,
    },
    {
      title: t("pages.delivery.colScheduled"),
      dataIndex: "scheduled_date",
      width: 120,
      render: (v: string) => formatDate(v),
    },
    {
      title: t("pages.delivery.colPickedAt"),
      dataIndex: "picked_at",
      width: 150,
      render: (v: string | null) => (v ? formatDateTime(v) : "—"),
    },
    {
      title: t("pages.delivery.colOutAt"),
      dataIndex: "out_at",
      width: 150,
      render: (v: string | null) => (v ? formatDateTime(v) : "—"),
    },
    {
      title: t("pages.delivery.colDeliveredAt"),
      dataIndex: "delivered_at",
      width: 150,
      render: (v: string | null) => (v ? formatDateTime(v) : "—"),
    },
    {
      title: t("pages.delivery.colActions"),
      key: "actions",
      width: 280,
      render: (_: unknown, r: Delivery) => (
        <Space size="small" wrap>
          {canAssign && (r.status === "scheduled" || r.status === "picked") && (
            <Button size="small" icon={<UserOutlined />} onClick={() => openAssign(r.id)}>
              {t("pages.delivery.assignDriver")}
            </Button>
          )}
          {canMutate && r.status === "scheduled" && (
            <Button size="small" onClick={() => handleStatus(r.id, "picked")}>
              {t("pages.delivery.markPicked")}
            </Button>
          )}
          {canMutate && r.status === "picked" && (
            <Button size="small" onClick={() => handleStatus(r.id, "out_for_delivery")}>
              {t("pages.delivery.markOut")}
            </Button>
          )}
          {canMutate &&
            (r.status === "out_for_delivery" ||
              r.status === "picked" ||
              r.status === "partial") && (
              <Button
                size="small"
                type="primary"
                icon={<CheckOutlined />}
                onClick={() => openComplete(r)}
              >
                {t("pages.delivery.complete")}
              </Button>
            )}
        </Space>
      ),
    },
  ];

  const lineCols: TableProps<DeliveryLine>["columns"] = [
    {
      title: t("pages.delivery.colLineProduct"),
      key: "product",
      render: (_: unknown, r: DeliveryLine) => pickName(lang, r.product_name_en, r.product_name_zh),
    },
    {
      title: t("pages.delivery.colLineQty"),
      dataIndex: "quantity",
      width: 100,
      align: "right" as const,
    },
    {
      title: t("pages.delivery.colLineDelivered"),
      dataIndex: "delivered_quantity",
      width: 100,
      align: "right" as const,
      render: (v: number) => v ?? 0,
    },
  ];

  const completeLineCols: TableProps<DeliveryLine>["columns"] = [
    {
      title: t("pages.delivery.colLineProduct"),
      key: "product",
      render: (_: unknown, r: DeliveryLine) => pickName(lang, r.product_name_en, r.product_name_zh),
    },
    {
      title: t("pages.delivery.colLineQty"),
      dataIndex: "quantity",
      width: 100,
      align: "right" as const,
    },
    {
      title: t("pages.delivery.colLineDelivered"),
      key: "delivered",
      width: 160,
      render: (_: unknown, r: DeliveryLine) => (
        <InputNumber
          min={0}
          step={1}
          value={completeLines.find((l) => l.id === r.id)?.delivered_quantity ?? 0}
          onChange={(v) =>
            setCompleteLines((prev) =>
              prev.map((l) =>
                l.id === r.id ? { ...l, delivered_quantity: v ?? 0 } : l,
              ),
            )
          }
        />
      ),
    },
  ];

  return (
    <Card title={t("pages.delivery.title")}>
      <Space wrap size="middle" style={{ marginBottom: 12 }}>
        <DatePicker
          placeholder={t("pages.delivery.filterDate")}
          onChange={(d) => {
            setDateFilter(d ? d.format("YYYY-MM-DD") : undefined);
            list.setPage(1);
          }}
        />
        <Select
          allowClear
          placeholder={t("pages.delivery.allStatuses")}
          style={{ width: 200 }}
          value={statusFilter}
          onChange={(v) => {
            setStatusFilter(v);
            list.setPage(1);
          }}
          options={DELIVERY_STATUSES.map((s) => ({ value: s, label: t(`status.delivery.${s}`) }))}
        />
        {!isDriver && (
          <Select
            allowClear
            showSearch
            placeholder={t("pages.delivery.allDrivers")}
            style={{ width: 200 }}
            value={driverFilter}
            onChange={(v) => {
              setDriverFilter(v);
              list.setPage(1);
            }}
            options={drivers.map((d) => ({ value: d.id, label: d.name }))}
            optionFilterProp="label"
          />
        )}
      </Space>
      <Table<Delivery>
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
        title={t("pages.delivery.details")}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={760}
      >
        {detail ? (
          <>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label={t("pages.delivery.colDeliveryNumber")}>
                {detail.delivery_number}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.delivery.colOrder")}>
                {detail.order_number}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.delivery.colCustomer")}>
                {pickName(lang, detail.customer_name_en, detail.customer_name_zh)}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.delivery.colRoute")}>
                {detail.route ?? "—"}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.delivery.colDriver")}>
                {detail.driver_name ?? "—"}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.delivery.colStatus")}>
                <StatusTag domain="delivery" value={detail.status} />
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.delivery.colScheduled")}>
                {formatDate(detail.scheduled_date)}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.delivery.colPickedAt")}>
                {detail.picked_at ? formatDateTime(detail.picked_at) : "—"}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.delivery.colOutAt")}>
                {detail.out_at ? formatDateTime(detail.out_at) : "—"}
              </Descriptions.Item>
              <Descriptions.Item label={t("pages.delivery.colDeliveredAt")}>
                {detail.delivered_at ? formatDateTime(detail.delivered_at) : "—"}
              </Descriptions.Item>
            </Descriptions>

            <Typography.Title level={5} style={{ marginTop: 16 }}>
              {t("pages.delivery.lines")}
            </Typography.Title>
            <Table<DeliveryLine>
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={detail.lines}
              columns={lineCols}
            />

            <Typography.Title level={5} style={{ marginTop: 16 }}>
              {t("pages.delivery.pod")}
            </Typography.Title>
            {detail.pod ? (
              <Descriptions size="small" column={1} bordered>
                <Descriptions.Item label={t("pages.delivery.podReceivedBy")}>
                  {detail.pod.received_by ?? "—"}
                </Descriptions.Item>
                <Descriptions.Item label={t("pages.delivery.podGps")}>
                  {detail.pod.gps_lat != null && detail.pod.gps_lng != null
                    ? `${detail.pod.gps_lat}, ${detail.pod.gps_lng}`
                    : "—"}
                </Descriptions.Item>
                <Descriptions.Item label={t("pages.delivery.podDeliveredAt")}>
                  {detail.pod.delivered_at ? formatDateTime(detail.pod.delivered_at) : "—"}
                </Descriptions.Item>
                {detail.pod.photo_url && (
                  <Descriptions.Item label={t("pages.delivery.podPhoto")}>
                    <a
                      onClick={() => {
                        // Download the POD photo with auth headers (binary).
                        client
                          .get(`/deliveries/${detail.id}/photo`, { responseType: "blob" })
                          .then((res) => {
                            const url = URL.createObjectURL(res.data as Blob);
                            window.open(url, "_blank");
                          })
                          .catch(() => {});
                      }}
                    >
                      <PaperClipOutlined /> {t("pages.delivery.viewPhoto")}
                    </a>
                  </Descriptions.Item>
                )}
              </Descriptions>
            ) : (
              <Typography.Text type="secondary">{t("pages.delivery.noPod")}</Typography.Text>
            )}
          </>
        ) : (
          <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
        )}
      </Drawer>

      <Modal
        title={t("pages.delivery.assignDriverTitle")}
        open={assignOpen}
        onCancel={() => setAssignOpen(false)}
        onOk={handleAssign}
        confirmLoading={mutateLoading}
        okText={t("common.submit")}
      >
        <Form layout="vertical">
          <Form.Item
            label={t("pages.delivery.driver")}
            required
            rules={[{ required: true, message: t("pages.delivery.driverRequired") }]}
          >
            <Select
              showSearch
              placeholder={t("pages.delivery.driver")}
              style={{ width: "100%" }}
              value={assignDriverId}
              onChange={setAssignDriverId}
              options={drivers.map((d) => ({ value: d.id, label: d.name }))}
              optionFilterProp="label"
            />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={t("pages.delivery.completeTitle")}
        open={completeOpen}
        onClose={() => setCompleteOpen(false)}
        width={760}
        footer={
          <Space style={{ float: "right" }}>
            <Button onClick={() => setCompleteOpen(false)}>{t("common.cancel")}</Button>
            <Button type="primary" loading={mutateLoading} onClick={handleComplete}>
              {t("common.submit")}
            </Button>
          </Space>
        }
      >
        <Table<DeliveryLine>
          rowKey="id"
          size="small"
          pagination={false}
          dataSource={completeLines}
          columns={completeLineCols}
        />
        <Form layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item label={t("pages.delivery.receivedBy")}>
            <Input value={receivedBy} onChange={(e) => setReceivedBy(e.target.value)} />
          </Form.Item>
          <Space.Compact block>
            <Form.Item label={t("pages.delivery.gpsLat")} style={{ flex: 1, marginRight: 8 }}>
              <InputNumber
                value={gpsLat}
                onChange={(v) => setGpsLat(v ?? undefined)}
                style={{ width: "100%" }}
                step={0.0001}
              />
            </Form.Item>
            <Form.Item label={t("pages.delivery.gpsLng")} style={{ flex: 1 }}>
              <InputNumber
                value={gpsLng}
                onChange={(v) => setGpsLng(v ?? undefined)}
                style={{ width: "100%" }}
                step={0.0001}
              />
            </Form.Item>
          </Space.Compact>
          <Form.Item label={t("pages.delivery.photo")}>
            <Upload.Dragger {...uploadProps}>
              <p className="ant-upload-drag-icon">
                <CarOutlined />
              </p>
              <p className="ant-upload-text">{t("pages.delivery.photo")}</p>
            </Upload.Dragger>
          </Form.Item>
        </Form>
      </Drawer>
    </Card>
  );
}
