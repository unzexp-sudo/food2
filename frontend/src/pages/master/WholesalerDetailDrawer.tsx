import { useEffect, useState } from "react";
import { Descriptions, Drawer, List, Space, Spin, Typography } from "antd";
import { useLanguage } from "../../i18n";
import { api, type Page } from "../../api/client";
import { pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";

export interface WholesalerDetail {
  id: string;
  code: string;
  name_en: string;
  name_zh: string;
  contact_name: string | null;
  contact_phone: string | null;
  is_active: boolean;
  created_at?: string | null;
}

interface SuppliedProduct {
  id: string;
  product_id: string;
  product_name_en?: string;
  product_name_zh?: string;
  supplier_sku: string | null;
  cost_price: number;
}

interface Props {
  open: boolean;
  onClose: () => void;
  wholesaler: WholesalerDetail | null;
}

export default function WholesalerDetailDrawer({ open, onClose, wholesaler }: Props) {
  const { t, lang } = useLanguage();
  const [detail, setDetail] = useState<WholesalerDetail | null>(wholesaler);
  const [products, setProducts] = useState<SuppliedProduct[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !wholesaler) return;
    setLoading(true);
    setDetail(wholesaler);
    setProducts([]);

    const detailReq = api
      .get<WholesalerDetail>(`/wholesalers/${wholesaler.id}`)
      .then((d) => setDetail(d))
      .catch(() => setDetail(wholesaler));

    const productsReq = api
      .get<Page<SuppliedProduct>>("/product-wholesaler-mappings", {
        wholesaler_id: wholesaler.id,
        page: 1,
        page_size: 10,
      })
      .then((r) => setProducts(r.items))
      .catch(() => setProducts([]));

    Promise.all([detailReq, productsReq]).finally(() => setLoading(false));
  }, [open, wholesaler]);

  const name = detail ? pickName(lang, detail.name_en, detail.name_zh) : "";

  return (
    <Drawer
      title={name || t("pages.master.wholesalers.title")}
      open={open}
      onClose={onClose}
      width={480}
    >
      <Spin spinning={loading}>
        {detail && (
          <Descriptions column={1} size="middle" bordered>
            <Descriptions.Item label={t("pages.master.wholesalers.colCode")}>
              {detail.code}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.master.wholesalers.colName")}>
              {name}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.master.wholesalers.colContact")}>
              {detail.contact_name ?? "—"}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.master.wholesalers.colPhone")}>
              {detail.contact_phone ?? "—"}
            </Descriptions.Item>
            <Descriptions.Item label={t("pages.master.wholesalers.colActive")}>
              <StatusTag domain="master" value={detail.is_active ? "active" : "inactive"} />
            </Descriptions.Item>
            {detail.created_at && (
              <Descriptions.Item label="Created">
                {new Date(detail.created_at).toLocaleString()}
              </Descriptions.Item>
            )}
          </Descriptions>
        )}

        <Typography.Title level={5} style={{ marginTop: 24 }}>
          Products supplied
        </Typography.Title>
        {products.length === 0 ? (
          <Typography.Text type="secondary">No products linked.</Typography.Text>
        ) : (
          <List
            size="small"
            bordered
            dataSource={products}
            renderItem={(p) => (
              <List.Item>
                <Space direction="vertical" size={0} style={{ width: "100%" }}>
                  <Typography.Text strong>
                    {p.product_name_en
                      ? pickName(lang, p.product_name_en, p.product_name_zh)
                      : p.product_id}
                  </Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {p.supplier_sku ? `SKU: ${p.supplier_sku} · ` : ""}
                    Cost: {p.cost_price.toFixed(2)}
                  </Typography.Text>
                </Space>
              </List.Item>
            )}
          />
        )}
      </Spin>
    </Drawer>
  );
}
