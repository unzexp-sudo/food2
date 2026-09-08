import { useEffect, useMemo, useState } from "react";
import { Button, Card, Col, Input, Modal, Row, Select, Space, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { api, type Page } from "../../api/client";
import { useList, useMutate } from "../../api/hooks";
import { pickName } from "../../utils/format";
import QuotationCard from "./QuotationCard";
import QuotationModal from "./QuotationModal";
import BulkQuotationModal from "./BulkQuotationModal";
import QuotationPreviewModal from "./QuotationPreviewModal";
import type { Customer, Product, Unit, Quotation } from "./types";

/** Quotations (sales) — 4-column card grid mirroring Guanmai "in-sale" screen. */
export default function QuotationsPage() {
  const { t, lang } = useLanguage();
  const [serviceTime, setServiceTime] = useState<string | undefined>();
  const [customerId, setCustomerId] = useState<string | undefined>();
  const [q, setQ] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Quotation | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [previewId, setPreviewId] = useState<string | undefined>();
  const [previewOpen, setPreviewOpen] = useState(false);

  const params = useMemo(
    () => ({
      ...(serviceTime ? { service_time: serviceTime } : {}),
      ...(customerId ? { customer_id: customerId } : {}),
      ...(q ? { q } : {}),
    }),
    [serviceTime, customerId, q],
  );

  const list = useList<Quotation>("/quotations", params);
  const { run } = useMutate();

  useEffect(() => {
    api
      .get<Page<Customer>>("/customers", { page: 1, page_size: 100 })
      .then((r) => setCustomers(r.items))
      .catch(() => setCustomers([]));
    api
      .get<Page<Product>>("/products", { page: 1, page_size: 200 })
      .then((r) => setProducts(r.items))
      .catch(() => setProducts([]));
    api
      .get<Page<Unit>>("/units", { page: 1, page_size: 50 })
      .then((r) => setUnits(r.items))
      .catch(() => setUnits([]));
  }, []);

  const openNew = () => {
    setEditing(null);
    setModalOpen(true);
  };
  const openEdit = (qObj: Quotation) => {
    setEditing(qObj);
    setModalOpen(true);
  };
  const handleDelete = (qObj: Quotation) => {
    Modal.confirm({
      title: t("pages.quotations.deleteConfirm"),
      okText: t("common.delete"),
      okButtonProps: { danger: true },
      cancelText: t("common.cancel"),
      onOk: async () => {
        await run(() => api.delete(`/quotations/${qObj.id}`), { onSuccess: () => list.refresh() });
      },
    });
  };
  const handleToggle = async (qObj: Quotation) => {
    const next = qObj.status === "active" ? "inactive" : "active";
    await run(() => api.patch(`/quotations/${qObj.id}`, { status: next }), {
      onSuccess: () => list.refresh(),
    });
  };

  const serviceOptions = [
    { value: "default", label: t("pages.quotations.serviceTimeDefault") },
    { value: "morning", label: t("pages.quotations.serviceTimeMorning") },
    { value: "afternoon", label: t("pages.quotations.serviceTimeAfternoon") },
    { value: "evening", label: t("pages.quotations.serviceTimeEvening") },
  ];

  return (
    <Card
      title={t("pages.quotations.title")}
      extra={
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={openNew}>
            {t("pages.quotations.newQuotation")}
          </Button>
          <Button onClick={() => setBulkOpen(true)}>{t("pages.quotations.batchNew")}</Button>
          <Select
            allowClear
            placeholder="Select quotation"
            style={{ width: 240 }}
            value={previewId}
            onChange={setPreviewId}
            options={list.items.map((it) => ({
              value: it.id,
              label: `${it.code} · ${pickName(lang, it.customer_name_en, it.customer_name_zh)}`,
            }))}
          />
          <Button onClick={() => setPreviewOpen(true)} disabled={!previewId}>
            Preview
          </Button>
        </Space>
      }
    >
      <Space wrap size="middle" style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder={t("pages.quotations.filterServiceTime")}
          style={{ width: 200 }}
          value={serviceTime}
          onChange={(v) => {
            setServiceTime(v);
            list.setPage(1);
          }}
          options={serviceOptions}
        />
        <Input.Search
          allowClear
          placeholder={t("pages.quotations.filterKeyword")}
          style={{ width: 220 }}
          onSearch={(v) => {
            setQ(v);
            list.setPage(1);
          }}
        />
        <Button type="link" onClick={() => setAdvanced((a) => !a)}>
          {t("pages.quotations.advancedFilter")}
        </Button>
      </Space>

      {advanced && (
        <Space wrap size="middle" style={{ marginBottom: 12 }}>
          <Select
            allowClear
            placeholder={t("pages.quotations.filterCustomer")}
            style={{ width: 220 }}
            value={customerId}
            onChange={(v) => {
              setCustomerId(v);
              list.setPage(1);
            }}
            options={customers.map((c) => ({
              value: c.id,
              label: pickName(lang, c.name_en, c.name_zh),
            }))}
          />
        </Space>
      )}

      {list.loading ? (
        <Typography.Text>{t("common.loading")}</Typography.Text>
      ) : (
        <Row gutter={[16, 16]}>
          {list.items.map((item) => (
            <Col key={item.id} xs={24} sm={12} md={8} lg={6} xl={6}>
              <QuotationCard
                q={item}
                onEdit={openEdit}
                onDelete={handleDelete}
                onToggleStatus={handleToggle}
              />
            </Col>
          ))}
        </Row>
      )}

      <QuotationModal
        open={modalOpen}
        initial={editing}
        customers={customers}
        products={products}
        units={units}
        onClose={() => setModalOpen(false)}
        onSaved={() => list.refresh()}
      />
      <BulkQuotationModal
        open={bulkOpen}
        customers={customers}
        products={products}
        onClose={() => setBulkOpen(false)}
        onSaved={() => list.refresh()}
      />
      <QuotationPreviewModal
        open={previewOpen}
        quotationId={previewId}
        onClose={() => setPreviewOpen(false)}
      />
    </Card>
  );
}
