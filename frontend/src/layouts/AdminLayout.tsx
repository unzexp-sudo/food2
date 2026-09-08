import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Layout, Menu, Segmented, Tag, Typography, Button, Switch } from "antd";
import {
  InboxOutlined,
  FileTextOutlined,
  MergeOutlined,
  ShoppingCartOutlined,
  DownloadOutlined,
  OrderedListOutlined,
  DatabaseOutlined,
  CarOutlined,
  FileProtectOutlined,
  AccountBookOutlined,
  RiseOutlined,
  TeamOutlined,
  AppstoreOutlined,
  ShopOutlined,
  BookOutlined,
  ScheduleOutlined,
  TagsOutlined,
  UserOutlined,
  AuditOutlined,
  SettingOutlined,
  LogoutOutlined,
  WechatOutlined,
  MessageOutlined,
  NotificationOutlined,
  SunOutlined,
  MoonOutlined,
} from "@ant-design/icons";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useLanguage, type Lang } from "../i18n";
import { useThemeMode } from "../theme-context";
import { parseStoredUser, type CurrentUser, type Role } from "../types";

const { Header, Sider, Content } = Layout;

interface MenuItemDef {
  key: string;
  labelKey: string;
  icon: ReactNode;
  roles: Role[];
}

interface MenuGroupDef {
  key: string;
  labelKey: string;
  items: MenuItemDef[];
}

/**
 * Menu structure + role matrix (AGENT_CONTRACTS §7):
 * admin: everything · ops: intake/orders/consolidation/POs/master ·
 * warehouse: warehouse+delivery · finance: finance + orders (read) ·
 * driver: delivery.
 */
const MENU_GROUPS: MenuGroupDef[] = [
  {
    key: "operations",
    labelKey: "nav.groups.operations",
    items: [
      { key: "/intake", labelKey: "nav.intake", icon: <InboxOutlined />, roles: ["admin", "ops"] },
      { key: "/orders", labelKey: "nav.orders", icon: <FileTextOutlined />, roles: ["admin", "ops", "finance"] },
      { key: "/consolidation", labelKey: "nav.consolidation", icon: <MergeOutlined />, roles: ["admin", "ops"] },
      { key: "/purchase-orders", labelKey: "nav.purchaseOrders", icon: <ShoppingCartOutlined />, roles: ["admin", "ops"] },
      { key: "/sales/quotations", labelKey: "nav.quotations", icon: <TagsOutlined />, roles: ["admin", "ops", "finance"] },
    ],
  },
  {
    key: "warehouse",
    labelKey: "nav.groups.warehouse",
    items: [
      { key: "/warehouse/inbound", labelKey: "nav.inbound", icon: <DownloadOutlined />, roles: ["admin", "warehouse"] },
      { key: "/warehouse/pick-lists", labelKey: "nav.pickLists", icon: <OrderedListOutlined />, roles: ["admin", "warehouse"] },
      { key: "/warehouse/inventory", labelKey: "nav.inventory", icon: <DatabaseOutlined />, roles: ["admin", "warehouse"] },
    ],
  },
  {
    key: "delivery",
    labelKey: "nav.groups.delivery",
    items: [
      { key: "/delivery", labelKey: "nav.delivery", icon: <CarOutlined />, roles: ["admin", "warehouse", "driver"] },
    ],
  },
  {
    key: "finance",
    labelKey: "nav.groups.finance",
    items: [
      { key: "/finance/invoices", labelKey: "nav.invoices", icon: <FileProtectOutlined />, roles: ["admin", "finance"] },
      { key: "/finance/statements", labelKey: "nav.statements", icon: <AccountBookOutlined />, roles: ["admin", "finance"] },
      { key: "/finance/margin", labelKey: "nav.margin", icon: <RiseOutlined />, roles: ["admin", "finance"] },
    ],
  },
  {
    key: "master",
    labelKey: "nav.groups.master",
    items: [
      { key: "/master/customers", labelKey: "nav.customers", icon: <TeamOutlined />, roles: ["admin", "ops"] },
      { key: "/master/products", labelKey: "nav.products", icon: <AppstoreOutlined />, roles: ["admin", "ops"] },
      { key: "/master/wholesalers", labelKey: "nav.wholesalers", icon: <ShopOutlined />, roles: ["admin", "ops"] },
      { key: "/master/contracts", labelKey: "nav.contracts", icon: <BookOutlined />, roles: ["admin", "ops"] },
      { key: "/master/standing-orders", labelKey: "nav.standingOrders", icon: <ScheduleOutlined />, roles: ["admin", "ops"] },
    ],
  },
  {
    key: "system",
    labelKey: "nav.groups.system",
    items: [
      { key: "/system/users", labelKey: "nav.users", icon: <UserOutlined />, roles: ["admin"] },
      { key: "/system/audit", labelKey: "nav.audit", icon: <AuditOutlined />, roles: ["admin"] },
      { key: "/system/settings", labelKey: "nav.settings", icon: <SettingOutlined />, roles: ["admin"] },
    ],
  },
  {
    key: "wecom",
    labelKey: "nav.groups.wecom",
    items: [
      { key: "/wecom/messages", labelKey: "nav.wecomMessages", icon: <MessageOutlined />, roles: ["admin", "ops"] },
      { key: "/wecom/contacts", labelKey: "nav.wecomContacts", icon: <WechatOutlined />, roles: ["admin", "ops"] },
      { key: "/wecom/outbound", labelKey: "nav.wecomOutbound", icon: <NotificationOutlined />, roles: ["admin", "ops"] },
    ],
  },
];

const ROLE_TAG_COLORS: Record<Role, string> = {
  admin: "gold",
  ops: "blue",
  warehouse: "green",
  finance: "purple",
  driver: "orange",
};

export default function AdminLayout() {
  const { t, lang, setLang } = useLanguage();
  const { mode, setMode } = useThemeMode();
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [user] = useState<CurrentUser | null>(() => parseStoredUser());

  const menuItems = useMemo(() => {
    if (!user) return [];
    return MENU_GROUPS.map((group) => ({
      key: group.key,
      type: "group" as const,
      label: t(group.labelKey),
      children: group.items
        .filter((item) => item.roles.includes(user.role))
        .map((item) => ({
          key: item.key,
          icon: item.icon,
          label: t(item.labelKey),
        })),
    })).filter((group) => group.children.length > 0);
  }, [t, user]);

  // Highlight the menu item whose route matches (also for detail sub-paths).
  const selectedKey = useMemo(() => {
    const matches = MENU_GROUPS.flatMap((g) => g.items)
      .map((item) => item.key)
      .filter((key) => location.pathname === key || location.pathname.startsWith(`${key}/`));
    return matches.length > 0 ? [matches[0]] : [];
  }, [location.pathname]);

  if (!user) {
    // Token present but user object missing/corrupt — force re-login.
    localStorage.removeItem("erp_token");
    localStorage.removeItem("erp_user");
    return <Navigate to="/login" replace />;
  }

  const handleLogout = () => {
    localStorage.removeItem("erp_token");
    localStorage.removeItem("erp_user");
    navigate("/login", { replace: true });
  };

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
          paddingInline: 24,
        }}
      >
        <Typography.Title level={4} style={{ margin: 0, whiteSpace: "nowrap" }}>
          {t("app.title")}
        </Typography.Title>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Switch
            checked={mode === "dark"}
            onChange={(checked) => setMode(checked ? "dark" : "light")}
            checkedChildren={<MoonOutlined />}
            unCheckedChildren={<SunOutlined />}
            title={mode === "dark" ? t("theme.light") : t("theme.dark")}
          />
          <Segmented<Lang>
            value={lang}
            onChange={(value) => setLang(value)}
            options={[
              { label: "English", value: "en" },
              { label: "中文", value: "zh" },
            ]}
          />
          <Typography.Text>{user.name}</Typography.Text>
          <Tag color={ROLE_TAG_COLORS[user.role]}>{t(`roles.${user.role}`)}</Tag>
          <Button type="text" icon={<LogoutOutlined />} onClick={handleLogout} />
        </div>
      </Header>
      <Layout>
        <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed} width={210}>
          <Menu
            mode="inline"
            selectedKeys={selectedKey}
            items={menuItems}
            onClick={({ key }) => navigate(key)}
            style={{ height: "100%", borderRight: 0, paddingTop: 8 }}
          />
        </Sider>
        <Content style={{ padding: 16, overflow: "auto" }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
