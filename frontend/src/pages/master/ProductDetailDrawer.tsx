import { useEffect, useState } from "react";
import { Descriptions, Drawer, Skeleton } from "antd";
import { useLanguage } from "../../i18n";
import { api } from "../../api/client";
import { pickName } from "../../utils/format";
import StatusTag from "../../components/StatusTag";

export interface ProductDetail {
  id: string;
  sku: string;
  name_en: string;
  name_zh: string;
  category_name_en: string | null;
  category_name_zh: string | null;
  default_unit_code: string | null;
  shelf_life_days: number | null;
  is_active: boolean;
}

interface ProductDetailDrawerProps {
  open: boolean;
  productId: string | null;
  /** Optional pre-loaded row from the table to render instantly. */
  initial?: ProductDetail | null;
  onClose: () => void;
}

export default function ProductDetailDrawer({
  open,
  productId,
  initial,
  onClose,
}: ProductDetailDrawerProps) {
  const { t, lang } = useLanguage();
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<ProductDetail | null>(initial ?? null);

  useEffect(() => {
    if (!open) return;
    if (initial) {
      setData(initial);
      return;
    }
    if (!productId) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .get<ProductDetail>(`/products/${productId}`)
      .then((res) => {
        if (!cancelled) setData(res);
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
  }, [open, productId, initial]);

  return (
    <Drawer
      title={"View Product"}
      open={open}
      onClose={onClose}
      width={480}
    >
      {loading ? (
        <Skeleton active paragraph={{ rows: 5 }} />
      ) : data ? (
        <Descriptions column={1} bordered size="middle">
          <Descriptions.Item label={t("pages.master.products.sku")}>
            {data.sku}
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.master.products.colName")}>
            {pickName(lang, data.name_en, data.name_zh)}
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.master.products.colCategory")}>
            {pickName(lang, data.category_name_en, data.category_name_zh) || "—"}
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.master.products.colDefaultUnit")}>
            {data.default_unit_code ?? "—"}
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.master.products.colShelfLife")}>
            {data.shelf_life_days ?? "—"}
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.master.products.isActive")}>
            <StatusTag domain="master" value={data.is_active ? "active" : "inactive"} />
          </Descriptions.Item>
        </Descriptions>
      ) : (
        <span>{"Product not found"}</span>
      )}
    </Drawer>
  );
}
