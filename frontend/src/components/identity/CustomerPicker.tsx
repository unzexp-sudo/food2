import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Descriptions,
  Empty,
  Input,
  List,
  Space,
  Table,
  Tag,
  Typography,
  theme,
} from "antd";
import { api, getApiError, type Page } from "../../api/client";
import { useLanguage } from "../../i18n";
import { formatDate, pickName } from "../../utils/format";
import StatusTag from "../StatusTag";
import type { Customer } from "./types";

interface PickerProps {
  /** The chosen customer, or null. This component never picks one itself. */
  value: Customer | null;
  onChange: (customer: Customer | null) => void;
  /**
   * A name taken from the conversation (WeCom alias / company / contact).
   *
   * Used ONLY to pre-fill the search box, so the operator starts from the name
   * they can already see instead of retyping it. It never selects anything:
   * `value` stays null until a click, and the box is clearable, because a
   * pre-filled *selection* would be indistinguishable from a verified one.
   */
  hint?: string | null;
}

const PAGE_SIZE = 100;
const SEARCH_LIMIT = 50;

/**
 * RIGHT COLUMN — the customer picker.
 *
 * Deliberately built as a *list you click*, not a combobox that already has an
 * answer in it: a pre-filled select is indistinguishable from a verified one,
 * and the owner's rule is that an unverified value must never look verified.
 * The search box filters on code, either name, phone and delivery zone; the
 * backend's `q` only covers code and names, so the extra fields are filtered
 * here (and a server search runs alongside for customers past the first page).
 *
 * `hint` pre-fills that box from the conversation. Each result then says which
 * field it matched, so a suggestion is *explained* rather than merely offered —
 * when the WeCom alias is "陈记饭店" and the account is "Chen Restaurant", the
 * operator needs to see that the link is a phone number, not a name.
 */
export default function CustomerPicker({ value, onChange, hint }: PickerProps) {
  const { t, lang } = useLanguage();
  const { token } = theme.useToken();
  const [loaded, setLoaded] = useState<Customer[]>([]);
  const [total, setTotal] = useState(0);
  const [serverHits, setServerHits] = useState<Customer[]>([]);
  const [query, setQuery] = useState(hint ?? "");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Re-fill when the conversation changes — the drawer is reused from one held
  // row to the next, so the box would otherwise keep the previous chat's name.
  // Keyed on `hint` alone: re-running on anything else would fight the typing.
  useEffect(() => {
    setQuery(hint ?? "");
  }, [hint]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .get<Page<Customer>>("/customers", { page: 1, page_size: PAGE_SIZE })
      .then((res) => {
        if (cancelled) return;
        setLoaded(res.items);
        setTotal(res.total);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) setError(getApiError(err) ?? t("common.error"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [t]);

  // Server-side search, debounced. Runs only when there is a query, so the
  // common case (a short customer list) costs nothing.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setServerHits([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      api
        .get<Page<Customer>>("/customers", { q, page: 1, page_size: SEARCH_LIMIT })
        .then((res) => {
          if (!cancelled) setServerHits(res.items);
        })
        .catch(() => {
          if (!cancelled) setServerHits([]);
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const pool = new Map<string, Customer>();
    for (const c of loaded) pool.set(c.id, c);
    for (const c of serverHits) pool.set(c.id, c);
    const all = [...pool.values()];
    if (!q) return all;
    return all.filter((c) =>
      [c.code, c.name_en, c.name_zh, c.contact_name, c.contact_phone, c.delivery_zone]
        .filter(Boolean)
        .some((field) => String(field).toLowerCase().includes(q)),
    );
  }, [loaded, serverHits, query]);

  // Which field a row matched on, for the "matched on …" tag. Null when nothing
  // matched (the unfiltered list), so no tag is shown for it.
  const matchedField = (c: Customer): string | null => {
    const q = query.trim().toLowerCase();
    if (!q) return null;
    const fields: [string, string | null | undefined][] = [
      [t("pages.identity.bind.code"), c.code],
      [t("pages.identity.bind.name"), c.name_zh],
      [t("pages.identity.bind.name"), c.name_en],
      [t("pages.identity.bind.contact"), c.contact_name],
      [t("pages.identity.bind.phone"), c.contact_phone],
      [t("pages.identity.bind.zone"), c.delivery_zone],
    ];
    const hit = fields.find(([, v]) => v && String(v).toLowerCase().includes(q));
    return hit ? hit[0] : null;
  };

  return (
    <Space direction="vertical" size="small" style={{ width: "100%" }}>
      <Typography.Text strong>{t("pages.identity.bind.searchLabel")}</Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {t("pages.identity.bind.searchHint")}
      </Typography.Text>
      {/* Say where the pre-fill came from. Otherwise a box that already has a
          name in it looks like the system made the choice for the operator. */}
      {hint ? (
        <Alert
          type="info"
          showIcon
          message={t("pages.identity.bind.searchFromChat", { name: hint })}
          description={t("pages.identity.bind.searchFromChatHint")}
        />
      ) : null}
      <Input.Search
        allowClear
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={t("pages.identity.bind.searchPlaceholder")}
      />

      {error ? <Alert type="error" showIcon message={error} /> : null}

      <div>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t("pages.identity.bind.resultsCount", { count: matches.length })}
          {total > loaded.length && !query.trim()
            ? ` · ${t("pages.identity.bind.showingFirst", { count: loaded.length })}`
            : ""}
        </Typography.Text>
      </div>

      <div
        style={{
          maxHeight: 320,
          overflow: "auto",
          border: `1px solid ${token.colorBorder}`,
          borderRadius: token.borderRadius,
        }}
      >
        {loading ? (
          <div style={{ padding: 12 }}>
            <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
          </div>
        ) : matches.length === 0 ? (
          <div style={{ padding: 12 }}>
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {t("pages.identity.bind.noResults")}
                </Typography.Text>
              }
            />
          </div>
        ) : (
          <List
            size="small"
            dataSource={matches}
            renderItem={(c) => {
              const selected = value?.id === c.id;
              const reason = matchedField(c);
              return (
                <List.Item
                  onClick={() => onChange(selected ? null : c)}
                  style={{
                    cursor: "pointer",
                    paddingInline: 12,
                    background: selected ? token.controlItemBgActive : undefined,
                    borderInlineStart: selected
                      ? `3px solid ${token.colorPrimary}`
                      : "3px solid transparent",
                  }}
                >
                  <Space direction="vertical" size={0} style={{ width: "100%" }}>
                    <Space wrap size="small">
                      <Typography.Text style={{ fontFamily: "monospace", fontSize: 12 }} type="secondary">
                        {c.code}
                      </Typography.Text>
                      <Typography.Text strong>
                        {pickName(lang, c.name_en, c.name_zh)}
                      </Typography.Text>
                      <StatusTag domain="master" value={c.status} />
                    </Space>
                    <Space wrap size="small">
                      {c.delivery_zone ? (
                        <Tag color="blue">{c.delivery_zone}</Tag>
                      ) : (
                        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                          {t("pages.identity.bind.zone")}: —
                        </Typography.Text>
                      )}
                      {c.contact_phone ? (
                        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                          {c.contact_phone}
                        </Typography.Text>
                      ) : null}
                      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                        {lang === "zh" ? c.name_en : c.name_zh}
                      </Typography.Text>
                    </Space>
                    {/* Why this row is in front of you. A filtered list without a
                        reason is just a shorter list; the operator has to be able
                        to judge the match, not just accept it. */}
                    {reason ? (
                      <Tag color="green" style={{ fontSize: 11, marginInlineEnd: 0 }}>
                        {t("pages.identity.bind.matchedOn", { field: reason })}
                      </Tag>
                    ) : null}
                  </Space>
                </List.Item>
              );
            }}
          />
        )}
      </div>
    </Space>
  );
}

/** One row of the "last 3 orders" sanity check. */
interface RecentOrder {
  id: string;
  order_number: string;
  status: string;
  delivery_date: string | null;
  line_count: number;
  products?: string[];
}

interface SummaryProps {
  customer: Customer;
}

/**
 * The selected customer, checked from the other direction.
 *
 * Showing code, zone and status is table stakes; showing the **last 3 orders
 * and what was in them** is what actually catches a wrong pick — if this
 * conversation is about vegetables for a school canteen and the account's
 * recent orders are cleaning supplies for an office, the human can see it here.
 */
export function CustomerSummary({ customer }: SummaryProps) {
  const { t } = useLanguage();
  const { token } = theme.useToken();
  const [orders, setOrders] = useState<RecentOrder[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setOrders(null);
    setError(null);
    api
      .get<Page<RecentOrder>>("/orders", {
        customer_id: customer.id,
        page: 1,
        page_size: 3,
      })
      .then(async (res) => {
        // The list endpoint returns line *counts* only, so fetch each of the
        // three orders to show what was actually ordered.
        const detailed = await Promise.all(
          res.items.map((o) =>
            api
              .get<{ lines?: { product_display: string | null; raw_text: string | null }[] }>(
                `/orders/${o.id}`,
              )
              .then((full) => ({
                ...o,
                products: (full.lines ?? [])
                  .map((l) => l.product_display || l.raw_text || "")
                  .filter((s) => s.length > 0),
              }))
              .catch(() => o),
          ),
        );
        if (!cancelled) setOrders(detailed);
      })
      .catch((err) => {
        if (!cancelled) setError(getApiError(err) ?? t("common.error"));
      });
    return () => {
      cancelled = true;
    };
  }, [customer.id, t]);

  const columns = [
    {
      title: t("pages.orders.colOrderNumber"),
      dataIndex: "order_number",
      width: 140,
    },
    {
      title: t("pages.orders.colDeliveryDate"),
      dataIndex: "delivery_date",
      width: 110,
      render: (v: string | null) => formatDate(v),
    },
    {
      title: t("common.status"),
      dataIndex: "status",
      width: 150,
      render: (v: string) => <StatusTag domain="order" value={v} />,
    },
    {
      title: t("pages.identity.bind.orderLines"),
      key: "products",
      render: (_: unknown, r: RecentOrder) =>
        r.products && r.products.length ? (
          <Space wrap size={[4, 4]}>
            {r.products.map((p, i) => (
              <Tag key={i}>{p}</Tag>
            ))}
          </Space>
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        ),
    },
  ];

  return (
    <div
      style={{
        border: `1px solid ${token.colorPrimaryBorder}`,
        borderRadius: token.borderRadius,
        padding: 12,
      }}
    >
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        <Space wrap size="small">
          <Typography.Text strong>{t("pages.identity.bind.selectedTitle")}</Typography.Text>
          <Tag color="blue">{t("pages.identity.bind.selectedHint")}</Tag>
        </Space>

        <Descriptions size="small" column={1} bordered>
          <Descriptions.Item label={t("pages.identity.bind.code")}>
            <Typography.Text style={{ fontFamily: "monospace" }}>{customer.code}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.identity.bind.name")}>
            {customer.name_zh} / {customer.name_en}
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.identity.bind.zone")}>
            {customer.delivery_zone ?? "—"}
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.identity.bind.status")}>
            <StatusTag domain="master" value={customer.status} />
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.identity.bind.contact")}>
            {customer.contact_name ?? "—"}
            {customer.contact_phone ? ` · ${customer.contact_phone}` : ""}
          </Descriptions.Item>
          <Descriptions.Item label={t("pages.identity.bind.address")}>
            {customer.address ?? "—"}
            {!customer.address_confirmed_at ? (
              <div>
                <Typography.Text type="warning" style={{ fontSize: 11 }}>
                  {t("pages.identity.bind.addressUnverified")}
                </Typography.Text>
              </div>
            ) : null}
          </Descriptions.Item>
        </Descriptions>

        <div>
          <Typography.Text strong style={{ fontSize: 12 }}>
            {t("pages.identity.bind.recentOrders")}
          </Typography.Text>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {t("pages.identity.bind.recentOrdersHint")}
            </Typography.Text>
          </div>
        </div>

        {error ? <Alert type="warning" showIcon message={error} /> : null}
        {orders === null ? (
          <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
        ) : orders.length === 0 ? (
          <Alert
            type="warning"
            showIcon
            message={t("pages.identity.bind.recentOrdersEmpty")}
          />
        ) : (
          <Table<RecentOrder>
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={orders}
            columns={columns}
            scroll={{ x: "max-content" }}
          />
        )}
      </Space>
    </div>
  );
}
