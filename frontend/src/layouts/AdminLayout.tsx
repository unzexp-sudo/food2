import { Suspense, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Badge, Layout, Menu, Segmented, Tag, Typography, Button, Switch, Spin } from "antd";
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
  SafetyCertificateOutlined,
  SunOutlined,
  MoonOutlined,
} from "@ant-design/icons";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useLanguage, type Lang } from "../i18n";
import { useThemeMode } from "../theme-context";
import { parseStoredUser, type CurrentUser, type Role } from "../types";
import { api } from "../api/client";
import ConfigWarnings from "../components/ConfigWarnings";

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
      // Gate 0 — a conversation nobody has told the system which customer it
      // belongs to. It sits with the WeCom items because that is where the
      // question comes from, and it is badged because until it is answered
      // every order in that chat is held.
      { key: "/identity/chats", labelKey: "pages.identity.navLabel", icon: <SafetyCertificateOutlined />, roles: ["admin", "ops"] },
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

/** Centered spinner shown while a lazily-loaded route chunk is fetched. */
function RouteFallback() {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: 240,
      }}
    >
      <Spin size="large" />
    </div>
  );
}

export default function AdminLayout() {
  const { t, lang, setLang } = useLanguage();
  const { mode, setMode } = useThemeMode();
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [user] = useState<CurrentUser | null>(() => parseStoredUser());

  // The three human gates, shown as unread-style badges on the nav items so
  // nobody has to open a page — or wait for a push message — to know work
  // arrived. Intake = "read this order"; Orders = "confirm this order";
  // Unbound chats = "say which customer this conversation is".
  const [pendingReview, setPendingReview] = useState(0);
  const [awaitingConfirm, setAwaitingConfirm] = useState(0);
  const [unboundChats, setUnboundChats] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      api
        .get<{ pending_review: number; parked?: number }>("/intake/review-count")
        .then((r) => {
          if (!cancelled) setPendingReview(r.pending_review ?? 0);
        })
        .catch(() => {
          /* badge is cosmetic — never let it break the shell */
        });
      api
        .get<{ awaiting_confirmation: number }>("/orders/confirm-count")
        .then((r) => {
          if (!cancelled) setAwaitingConfirm(r.awaiting_confirmation ?? 0);
        })
        .catch(() => {
          /* ditto */
        });
      api
        .get<{ items: unknown[]; total: number }>("/identity/unbound")
        .then((r) => {
          if (!cancelled) setUnboundChats(r.total ?? 0);
        })
        .catch(() => {
          /* ditto */
        });
    };
    tick();
    const id = setInterval(tick, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [location.pathname]);

  const menuItems = useMemo(() => {
    if (!user) return [];
    return MENU_GROUPS.map((group) => ({
      key: group.key,
      type: "group" as const,
      label: t(group.labelKey),
      children: group.items
        .filter((item) => item.roles.includes(user.role))
        .map((item) => {
          const label = t(item.labelKey);
          // Counts are per-gate and mean exactly one thing: "work waiting".
          // Parked messages are deliberately NOT added here. A badge that
          // counts things you cannot action teaches people to ignore badges,
          // and then a real order stops getting noticed. Parked is surfaced
          // on the intake page and dashboard instead, clearly labelled.
          const badgeCount =
            item.key === "/intake"
              ? pendingReview
              : item.key === "/orders"
                ? awaitingConfirm
                : item.key === "/identity/chats"
                  ? unboundChats
                  : 0;
          return {
            key: item.key,
            icon: item.icon,
            label:
              badgeCount > 0 ? (
                <Badge count={badgeCount} size="small" offset={[10, 0]}>
                  {label}
                </Badge>
              ) : (
                label
              ),
          };
        }),
    })).filter((group) => group.children.length > 0);
  }, [t, user, pendingReview, awaitingConfirm, unboundChats]);

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
          {/* Server-configuration warnings, above the page rather than inside
              one, because they are not about the screen you are on. Shown to
              admin and ops: ops is who feels an unannounced review queue, and
              admin is who can change the variables. Not dismissible — see the
              component. */}
          {(user.role === "admin" || user.role === "ops") && <ConfigWarnings />}
          <Suspense fallback={<RouteFallback />}>
            <Outlet />
          </Suspense>
        </Content>
      </Layout>
    </Layout>
  );
}
