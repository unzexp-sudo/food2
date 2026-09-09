import { useEffect, useState } from "react";
import { Modal, Table, Typography, Tag, Space, Skeleton } from "antd";
import { useLanguage } from "../../i18n";
import { api } from "../../api/client";
import { pickName } from "../../utils/format";
import type { Quotation, QuotationLine } from "./types";

interface Props {
  open: boolean;
  /** When provided, the modal fetches GET /quotations/{id}. */
  quotationId?: string;
  /** Optional preloaded quotation (skips the fetch). */
  quotation?: Quotation;
  onClose: () => void;
}

/** Read-only printable preview of a quotation. */
export default function QuotationPreviewModal({ open, quotationId, quotation: preloaded, onClose }: Props) {
  const { lang, t } = useLanguage();
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<Quotation | null>(preloaded ?? null);

  useEffect(() => {
    if (!open) return;
    if (preloaded) {
      setData(preloaded);
      return;
    }
    if (!quotationId) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setData(null);
    api
      .get<Quotation>(`/quotations/${quotationId}`)
      .then((q) => {
        if (!cancelled) setData(q);
      })
      .catch(() => {
        if (!cancelled) setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, quotationId, preloaded]);

  const lines: QuotationLine[] = data?.lines ?? [];
  const total = lines.reduce(
    (sum, l) => sum + (Number(l.quantity) || 0) * (Number(l.unit_price) || 0),
    0,
  );

  const columns = [
    {
      title: t("pages.sales.quotationPreview.product"),
      dataIndex: "product_display",
      key: "product_display",
      render: (v: string | null) => v ?? "—",
    },
    { title: t("pages.sales.quotationPreview.qty"), dataIndex: "quantity", key: "quantity" },
    {
      title: t("pages.sales.quotationPreview.unit"),
      dataIndex: "unit_code",
      key: "unit_code",
      render: (v: string | null) => v ?? "—",
    },
    {
      title: t("pages.sales.quotationPreview.unitPrice"),
      dataIndex: "unit_price",
      key: "unit_price",
      align: "right" as const,
      render: (v: number | null) => (v != null ? v.toFixed(2) : "—"),
    },
    {
      title: t("pages.sales.quotationPreview.amount"),
      key: "amount",
      align: "right" as const,
      render: (_: unknown, r: QuotationLine) =>
        ((Number(r.quantity) || 0) * (Number(r.unit_price) || 0)).toFixed(2),
    },
  ];

  return (
    <Modal
      open={open}
      title={t("common.preview")}
      footer={null}
      width={720}
      onCancel={onClose}
      destroyOnClose
    >
      {loading ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : !data ? (
        <Typography.Text type="secondary">—</Typography.Text>
      ) : (
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Space direction="vertical" size={4} style={{ width: "100%" }}>
            <Typography.Title level={5} style={{ margin: 0 }}>
              {pickName(lang, data.customer_name_en, data.customer_name_zh)}
            </Typography.Title>
            <Space wrap size={[8, 4]}>
              <Typography.Text type="secondary">{t("common.code")}: {data.code}</Typography.Text>
              <Typography.Text type="secondary">{t("pages.sales.quotationPreview.serviceTime")}: {data.service_time}</Typography.Text>
              <Typography.Text type="secondary">
                {t("pages.sales.quotationPreview.pricingCycle")}: {data.pricing_cycle ?? "—"}
              </Typography.Text>
              {data.tags?.map((tag) => (
                <Tag key={tag}>{tag}</Tag>
              ))}
            </Space>
          </Space>

          <Table<QuotationLine>
            size="small"
            rowKey={(r) => r.id}
            pagination={false}
            columns={columns}
            dataSource={lines}
            summary={() => (
              <Table.Summary.Row>
                <Table.Summary.Cell index={0} colSpan={4}>
                  <strong>{t("common.total")}</strong>
                </Table.Summary.Cell>
                <Table.Summary.Cell index={1} align="right">
                  <strong>{total.toFixed(2)}</strong>
                </Table.Summary.Cell>
              </Table.Summary.Row>
            )}
          />
        </Space>
      )}
    </Modal>
  );
}
