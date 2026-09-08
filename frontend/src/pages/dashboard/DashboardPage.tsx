import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { Card, Col, Row, Statistic, Table, Typography, Button, Space, Spin } from "antd";
import {
  InboxOutlined,
  ShoppingCartOutlined,
  DatabaseOutlined,
  CarOutlined,
} from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import { api, type Page } from "../../api/client";
import StatusTag from "../../components/StatusTag";
import { pickName } from "../../utils/format";

/** Minimal row shape — only the fields the dashboard table renders. */
interface OrderRow {
  id: string;
  code: string;
  customer_name_en: string | null;
  customer_name_zh: string | null;
  status: string;
  created_at: string;
}

/** Returns the total count for a list endpoint, or 0 if it fails (e.g. offline). */
async function safeTotal(url: string): Promise<number> {
  try {
    const res = await api.get<{ total: number }>(url, { page: 1, page_size: 1 });
    return res.total ?? 0;
  } catch {
    return 0;
  }
}

interface Kpi {
  key: string;
  label: string;
  value: number;
  icon: ReactNode;
  color: string;
}

/** Guanmai-style admin overview: live KPIs, recent orders, quick navigation. */
export default function DashboardPage() {
  const { t, lang } = useLanguage();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [counts, setCounts] = useState({ orders: 0, pos: 0, inventory: 0, deliveries: 0 });
  const [recent, setRecent] = useState<OrderRow[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [orders, pos, inventory, deliveries] = await Promise.all([
        safeTotal("/orders"),
        safeTotal("/purchase-orders"),
        safeTotal("/warehouse/inventory"),
        safeTotal("/delivery"),
      ]);
      if (!cancelled) setCounts({ orders, pos, inventory, deliveries });

      let rows: OrderRow[] = [];
      try {
        const res = await api.get<Page<OrderRow>>("/orders", { page: 1, page_size: 5 });
        rows = res.items;
      } catch {
        rows = [];
      }
      if (!cancelled) {
        setRecent(rows);
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  const kpis: Kpi[] = [
    { key: "orders", label: t("nav.orders"), value: counts.orders, icon: <InboxOutlined />, color: "#5B5BD6" },
    { key: "pos", label: t("nav.purchaseOrders"), value: counts.pos, icon: <ShoppingCartOutlined />, color: "#0EA5E9" },
    { key: "inventory", label: t("nav.inventory"), value: counts.inventory, icon: <DatabaseOutlined />, color: "#10B981" },
    { key: "deliveries", label: t("nav.delivery"), value: counts.deliveries, icon: <CarOutlined />, color: "#F59E0B" },
  ];

  const quickLinks = [
    { key: "/intake", label: t("nav.intake") },
    { key: "/orders", label: t("nav.orders") },
    { key: "/sales/quotations", label: t("nav.quotations") },
    { key: "/master/customers", label: t("nav.customers") },
    { key: "/warehouse/inventory", label: t("nav.inventory") },
    { key: "/delivery", label: t("nav.delivery") },
    { key: "/finance/invoices", label: t("nav.invoices") },
  ];

  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        {t("nav.dashboard")}
      </Typography.Title>

      <Spin spinning={loading}>
        <Row gutter={[16, 16]}>
          {kpis.map((k) => (
            <Col key={k.key} xs={24} sm={12} md={6}>
              <Card variant="borderless" style={{ height: "100%" }}>
                <Statistic
                  title={k.label}
                  value={k.value}
                  prefix={<span style={{ color: k.color, marginRight: 6 }}>{k.icon}</span>}
                  valueStyle={{ color: k.color }}
                />
              </Card>
            </Col>
          ))}
        </Row>

        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col xs={24} lg={14}>
            <Card title={t("common.recentOrders")} variant="borderless">
              <Table<OrderRow>
                rowKey="id"
                size="middle"
                dataSource={recent}
                pagination={false}
                locale={{ emptyText: t("common.noData") }}
                columns={[
                  { title: t("common.code"), dataIndex: "code", key: "code" },
                  {
                    title: t("nav.customers"),
                    key: "customer",
                    render: (_, r) => pickName(lang, r.customer_name_en, r.customer_name_zh),
                  },
                  {
                    title: t("common.status"),
                    key: "status",
                    render: (_, r) => <StatusTag domain="order" value={r.status} />,
                  },
                  { title: t("common.createdAt"), dataIndex: "created_at", key: "created_at" },
                ]}
              />
            </Card>
          </Col>
          <Col xs={24} lg={10}>
            <Card title={t("common.quickActions")} variant="borderless">
              <Space wrap size="middle">
                {quickLinks.map((q) => (
                  <Button key={q.key} onClick={() => navigate(q.key)}>
                    {q.label}
                  </Button>
                ))}
              </Space>
            </Card>
          </Col>
        </Row>
      </Spin>
    </div>
  );
}
