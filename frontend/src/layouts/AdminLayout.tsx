import { Suspense, useEffect, useMemo, useRef, useState } from "react";
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
import { BADGE_POLL_MS } from "../api/hooks";
import { getMessage } from "../api/message";
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
      // Inbound receipts live here, not under Warehouse (moved 2026-09-19).
      // Posting a receipt is what creates stock and what the wholesaler's
      // invoice is reconciled against — finance's gate. The badge moved with
      // it, and `workqueue.ROLE_SECTIONS` must agree: `tests/test_work_queue.py`
      // parses this file and fails if the two lists drift.
      { key: "/finance/inbound", labelKey: "nav.inbound", icon: <DownloadOutlined />, roles: ["admin", "finance"] },
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

/**
 * Every section that waits on a human, and the nav item its badge hangs on.
 *
 * `nav` is the nav key (the route path); `queue` is the key `GET /work-queue`
 * returns. `titleKey` is the hover text explaining the number, `toastKey` is the
 * message shown when the number RISES while the app is open.
 *
 * **Add an entry here whenever you add a nav item that waits on a person, and
 * add the matching count to `app/services/workqueue.py`.** A section that has no
 * queue — a report, a ledger, a settings page — has no entry and no badge, which
 * is the point: a badge that counts something you cannot act on teaches people
 * to ignore badges. A typo in `queue` does not error, it just goes quiet, so
 * `tests/test_work_queue.py` pins this list against the server's.
 */
const WORK_BADGES: {
  nav: string;
  queue: string;
  titleKey: string;
  toastKey: string;
}[] = [
  {
    nav: "/intake",
    queue: "intake",
    titleKey: "nav.newWork.intakeTitle",
    toastKey: "nav.newWork.intake",
  },
  {
    nav: "/orders",
    queue: "orders",
    titleKey: "nav.newWork.ordersTitle",
    toastKey: "nav.newWork.orders",
  },
  {
    nav: "/consolidation",
    queue: "consolidation",
    titleKey: "nav.newWork.consolidationTitle",
    toastKey: "nav.newWork.consolidation",
  },
  {
    nav: "/purchase-orders",
    queue: "purchase_orders",
    titleKey: "nav.newWork.purchaseOrdersTitle",
    toastKey: "nav.newWork.purchaseOrders",
  },
  {
    nav: "/finance/inbound",
    queue: "inbound",
    titleKey: "nav.newWork.inboundTitle",
    toastKey: "nav.newWork.inbound",
  },
  {
    nav: "/warehouse/pick-lists",
    queue: "pick_lists",
    titleKey: "nav.newWork.pickListsTitle",
    toastKey: "nav.newWork.pickLists",
  },
  {
    nav: "/delivery",
    queue: "delivery",
    titleKey: "nav.newWork.deliveryTitle",
    toastKey: "nav.newWork.delivery",
  },
  {
    nav: "/finance/invoices",
    queue: "invoices",
    titleKey: "nav.newWork.invoicesTitle",
    toastKey: "nav.newWork.invoices",
  },
  {
    nav: "/identity/chats",
    queue: "identity_chats",
    titleKey: "nav.newWork.identityChatsTitle",
    toastKey: "nav.newWork.identityChats",
  },
];

const BADGE_BY_NAV = new Map(WORK_BADGES.map((b) => [b.nav, b]));
const BADGE_BY_QUEUE = new Map(WORK_BADGES.map((b) => [b.queue, b]));

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

  // Work waiting on a person, keyed by section, straight from `/work-queue`.
  //
  // One request per tick for every gate in the app, not one per section: the
  // shell wants all of them at the same moment, and the server already scopes
  // the response to what this role can act on. Polled so nobody has to reload —
  // orders, WeCom messages and receipts all arrive from outside this tab.
  const [work, setWork] = useState<Record<string, number>>({});

  // Previous reading per section, so a count going UP can be told apart from a
  // count being read for the first time. A ref, not state: it must not itself
  // re-render, and it must survive the effect re-running on every navigation.
  const lastCounts = useRef<Record<string, number | null>>({});
  // Latest translator, read at toast time. Deliberately not an effect
  // dependency: `t` is not guaranteed to be referentially stable, and an
  // unstable one would re-run the effect, which re-ticks, which re-renders — a
  // poll loop that looks like a request flood.
  const tRef = useRef(t);
  tRef.current = t;

  useEffect(() => {
    if (!user) return;
    let cancelled = false;

    const tick = () => {
      api
        .get<Record<string, number>>("/work-queue")
        .then((counts) => {
          if (cancelled) return;
          setWork(counts);
          for (const [queue, n] of Object.entries(counts)) {
            const copy = BADGE_BY_QUEUE.get(queue);
            if (!copy) continue;
            const before = lastCounts.current[queue];
            lastCounts.current[queue] = n;
            // The first reading never announces: with no baseline, "went up"
            // would mean "this app has a queue" rather than "work just arrived",
            // and a toast on every page load is how people learn to dismiss
            // toasts without reading them.
            if (before === null || before === undefined || n <= before) continue;
            getMessage()?.info(tRef.current(copy.toastKey, { count: n - before }));
          }
        })
        .catch(() => {
          /* badge is cosmetic — never let it break the shell */
        });
    };

    tick();
    // A hidden tab does not poll; returning to it ticks immediately, so coming
    // back is never up to 15s stale.
    const id = setInterval(() => {
      if (!document.hidden) tick();
    }, BADGE_POLL_MS);
    const onVisible = () => {
      if (!document.hidden) tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [location.pathname, user]);

  const menuItems = useMemo(() => {
    if (!user) return [];
    // Each badge counts ONE thing: work waiting on that gate, for this role.
    // Parked messages are deliberately not folded in anywhere. A badge that
    // counts things you cannot action teaches people to ignore badges, and then
    // a real order stops getting noticed — parked is surfaced on the intake page
    // and the dashboard instead, clearly labelled.
    //
    // The title matters as much as the number: a bare "4" on a nav item is a
    // puzzle, and "4 what?" is the question that made a missing badge read as a
    // broken feature rather than a missing one.
    return MENU_GROUPS.map((group) => ({
      key: group.key,
      type: "group" as const,
      label: t(group.labelKey),
      children: group.items
        .filter((item) => item.roles.includes(user.role))
        .map((item) => {
          const label = t(item.labelKey);
          const badge = BADGE_BY_NAV.get(item.key);
          const count = badge ? (work[badge.queue] ?? 0) : 0;
          return {
            key: item.key,
            icon: item.icon,
            label:
              badge && count > 0 ? (
                <Badge
                  count={count}
                  size="small"
                  offset={[10, 0]}
                  title={t(badge.titleKey, { count })}
                >
                  {label}
                </Badge>
              ) : (
                label
              ),
          };
        }),
    })).filter((group) => group.children.length > 0);
  }, [t, user, work]);

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
