import type { ReactNode } from "react";
import { Card, Dropdown, Tag, Typography, Space } from "antd";
import { MoreOutlined } from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { pickName, formatDate } from "../../utils/format";
import StatusTag from "../../components/StatusTag";
import type { Quotation } from "./types";

interface Props {
  q: Quotation;
  onEdit: (q: Quotation) => void;
  onDelete: (q: Quotation) => void;
  onToggleStatus: (q: Quotation) => void;
}

/** A single quotation card in the 4-column grid (Guanmai "in-sale" card). */
export default function QuotationCard({ q, onEdit, onDelete, onToggleStatus }: Props) {
  const { t, lang } = useLanguage();
  const isActive = q.status === "active";

  const menu = {
    items: [
      { key: "edit", label: t("common.edit") },
      {
        key: "toggle",
        label: isActive ? t("pages.quotations.deactivate") : t("pages.quotations.activate"),
      },
      { key: "delete", label: t("common.delete"), danger: true },
    ],
    onClick: ({ key }: { key: string }) => {
      if (key === "edit") onEdit(q);
      else if (key === "toggle") onToggleStatus(q);
      else if (key === "delete") onDelete(q);
    },
  };

  const field = (label: string, value: ReactNode) => (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "2px 0" }}>
      <Typography.Text type="secondary" style={{ fontSize: 13, flexShrink: 0 }}>
        {label}
      </Typography.Text>
      <Typography.Text style={{ fontSize: 13, textAlign: "right" }}>{value ?? "—"}</Typography.Text>
    </div>
  );

  return (
    <Card
      size="small"
      styles={{ body: { padding: 16 } }}
      title={
        <Space style={{ width: "100%", justifyContent: "space-between" }}>
          <StatusTag domain="quotation" value={q.status} />
          <Dropdown menu={menu} trigger={["click"]}>
            <MoreOutlined style={{ fontSize: 18, cursor: "pointer" }} />
          </Dropdown>
        </Space>
      }
    >
      <Typography.Title level={5} style={{ marginTop: 0, marginBottom: 8 }} ellipsis>
        {pickName(lang, q.customer_name_en, q.customer_name_zh)}
      </Typography.Title>
      <div style={{ borderTop: "1px solid #f0f0f0", marginBottom: 8 }} />
      {field(t("pages.quotations.salesInvoiceId"), q.code)}
      {field(t("pages.quotations.externalName"), q.external_name)}
      {field(t("pages.quotations.itemsSold"), q.line_count)}
      {field(
        t("pages.quotations.tags"),
        q.tags?.length ? q.tags.map((tag) => <Tag key={tag}>{tag}</Tag>) : "—",
      )}
      {field(t("pages.quotations.serviceTime"), q.service_time)}
      {field(t("pages.quotations.pricingCycle"), q.pricing_cycle ?? "—")}
      {field(t("pages.quotations.description"), q.description)}
      {field(t("pages.quotations.createdAt"), formatDate(q.created_at))}
    </Card>
  );
}
