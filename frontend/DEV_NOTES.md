# DEV_NOTES.md — Frontend Conventions for Screen Agents

This document is the **binding convention guide** for the two screen agents:
- **FE screens A agent** owns: `src/pages/intake/`, `src/pages/orders/`, `src/pages/consolidation/`, `src/pages/purchase-orders/`
- **FE screens B agent** owns: `src/pages/warehouse/`, `src/pages/delivery/`, `src/pages/finance/`, `src/pages/master/`, `src/pages/system/`

The foundation (routing, i18n, API client, hooks, layout, login) is **read-only for screen agents**.
If you think a foundation file must change, note it in your final report instead of editing it.

---

## 1. Where you may write

| Area | Rule |
|---|---|
| `src/pages/<your dirs>/` | Full ownership. Replace placeholder page bodies, add sub-components/hooks inside your dirs. |
| `src/i18n/en.ts`, `src/i18n/zh.ts` | **Shared** — you may ADD keys (namespaced, e.g. `pages.orders.` prefix), NEVER remove or rename existing keys. Keep en/zh perfectly in sync. |
| Everything else (`src/api/`, `src/layouts/`, `src/router.tsx`, `src/App.tsx`, `src/components/`, `src/utils/`, configs) | **Read-only.** |

Do NOT add npm packages. Installed set: `antd`, `@ant-design/icons`, `react-router-dom` (v6), `axios`, `i18next`, `react-i18next`, `dayjs`.

## 2. File & component naming

- Page components: **PascalCase + `Page.tsx`** — e.g. `OrdersPage.tsx`, `OrderDetailPage.tsx`. The route already imports your default export; keep the default export name and the file path unchanged (the router imports them by path).
- Sub-components inside your page dirs: PascalCase `ComponentName.tsx`.
- One component per file; default export for pages, named exports for helpers.

## 3. API access — `src/api/client.ts`

Never use axios directly. Import the typed helpers:

```ts
import { api, getApiError, type Page } from "../api/client";

const page = await api.get<Page<Order>>("/orders", { status: "confirmed", page: 1, page_size: 20 });
const order = await api.get<Order>(`/orders/${id}`);            // detail includes lines
await api.post(`/orders/${id}/confirm`, { notes });
await api.patch(`/purchase-orders/${id}/lines`, { lines });
await api.delete(`/contract-prices/${id}`);
```

- `baseURL` is `/api/v1` — do **not** include that prefix in your URLs.
- The Bearer token (localStorage `erp_token`) and 401 → `/login` redirect are handled automatically.
- All list endpoints return `Page<T>`: `{ items, total, page, page_size }`.
- Error messages from FastAPI land in `error.response.data.detail` — use `getApiError(err)`.

## 4. Data hooks — `src/api/hooks.ts`

```ts
const { t } = useLanguage();

// Paginated list (auto-fetches on url/params/page change; call refresh() after mutations)
const list = useList<Order>("/orders", { status, q });
// list.items, list.total, list.loading, list.page, list.pageSize, list.setPage, list.setPageSize, list.refresh

// Detail (pass null to skip, e.g. while id not yet known)
const detail = useDetail<Order>(id ? `/orders/${id}` : null);
// detail.data, detail.loading, detail.refresh

// Mutations with AntD message feedback (success/error toast handled for you)
const { loading, run } = useMutate();
await run(() => api.post(`/orders/${id}/reject`, { reason }), {
  success: t("pages.orders.rejected"),   // optional custom message
  onSuccess: list.refresh,               // optional callback
});
```

Note: changing `params` does **not** reset page — call `setPage(1)` yourself when filters change.
For toasts inside components rendered under `<AntdApp>` you may also use `App.useApp()` (`const { message, modal } = App.useApp()`); `useMutate` already routes through it.

## 5. i18n — never hardcode UI strings

```tsx
import { useLanguage } from "../../i18n";
const { t, lang, setLang } = useLanguage();
<Button>{t("common.save")}</Button>
```

- `useLanguage()` (from `src/i18n`) gives `{ lang, setLang, t }` — prefer it over raw `useTranslation`.
- Language + AntD locale + dayjs locale are switched centrally; do not touch them.
- Existing key families (already populated, do not duplicate):
  - `app.title`
  - `nav.*` (every route + `nav.groups.*`)
  - `common.*` (save, cancel, edit, delete, create, search, confirm, reject, actions, status, loading, noData, total, back, submit, refresh, export, success, error, underConstruction)
  - `login.*`, `roles.*`
  - `status.*` — see mapping table below
- Add new screen-specific keys **namespaced by your module**, e.g. `pages.orders.tableTitle`, `pages.intake.uploadHint`. Mirror every key in both `en.ts` and `zh.ts`.

### Status → i18n key mapping (status values are the raw API strings from contracts §3)

| Domain | i18n key pattern | Values |
|---|---|---|
| Order | `status.order.<value>` | `draft`, `pending_confirmation`, `needs_clarification`, `confirmed`, `consolidated`, `fulfilled`, `invoiced`, `rejected` |
| Purchase order | `status.po.<value>` | `draft`, `sent`, `partially_received`, `received`, `closed`, `cancelled` |
| Intake job | `status.intake.<value>` | `queued`, `processing`, `completed`, `failed` |
| Pick list/line | `status.pick.<value>` | `open`, `picking`, `picked`, `cancelled`, `short` |
| Delivery | `status.delivery.<value>` | `scheduled`, `picked`, `out_for_delivery`, `delivered`, `failed`, `partial` |
| Invoice | `status.invoice.<value>` | `draft`, `issued`, `partial`, `paid`, `void` |
| Payment direction | `status.payment.<value>` | `inbound`, `outbound` |
| Consolidation batch | `status.consolidation.<value>` | `open`, `closed` |
| Master data | `status.master.<value>` | `active`, `inactive` |
| Customer type | `status.customerType.<value>` | `school`, `restaurant`, `canteen`, `other` |

**Always render statuses through the shared component**, never raw strings:

```tsx
import StatusTag from "../../components/StatusTag";
<StatusTag domain="order" value={order.status} />
```

## 6. Shared display rules (contracts §7 — mandatory)

- **Dates**: `YYYY-MM-DD`. **Datetimes**: `YYYY-MM-DD HH:mm`. Use helpers from `src/utils/format.ts`:
  ```ts
  import { formatDate, formatDateTime, pickName } from "../../utils/format";
  formatDate(order.delivery_date); formatDateTime(order.created_at);
  pickName(lang, product.name_en, product.name_zh);  // bilingual field display
  ```
- **Confidence** (API gives 0–1): display as percent; color ≥0.95 green, ≥0.7 orange, else red:
  ```tsx
  import ConfidenceTag from "../../components/ConfidenceTag";
  <ConfidenceTag value={line.confidence} />
  ```
- **Money/quantities**: JSON numbers — render as-is; use `toFixed(2)` for amounts where sensible.
- **Bilingual master data**: entities carry `name_en`/`name_zh`; pick with `pickName(lang, en, zh)` per current language.

## 7. Page structure conventions

- Every list page: AntD `Card` containing filter controls (top) + `Table` (pagination wired to `useList` page/pageSize/total). Detail pages and edit forms go in drawers or dedicated detail routes. Keep it dense — this is an ops tool.
- Table locale/empty text come from AntD locale automatically; use `common.noData` if you need a custom empty state.
- Use `common.actions` for the action-column header; put action buttons (`common.edit`, `common.delete`, …) in a `Space`.
- Route params: `const { id } = useParams()` in detail pages.

## 8. Role-based UI

Menu visibility is already handled by the layout. Inside screens, gate buttons/actions per role
when the API restricts them (contracts §5 "R:" column). Get the current user via:

```ts
import { parseStoredUser } from "../../types";
const user = parseStoredUser(); // { id, name, role } | null
```

Role matrix (menu filtering, already implemented in `AdminLayout`):
admin → everything · ops → intake, orders, consolidation, POs, master data ·
warehouse → warehouse + delivery · finance → finance + orders (read-only) · driver → delivery.

For finance, remember orders are **read-only** — do not render confirm/reject/edit actions for role `finance`.

## 9. Dev workflow

```bash
export PATH=/Users/harshjani/.workbuddy-ai/binaries/node/versions/22.22.2-2/bin:$PATH
cd /Users/harshjani/Documents/Foshan1/frontend
npm run dev        # dev server; /api proxied to http://localhost:8000
npm run build      # MUST pass with zero TypeScript errors before you finish
```

`npm run build` runs `tsc -b` with strict settings (`noUnusedLocals`, `noUnusedParameters`, `verbatimModuleSyntax`): no unused imports/vars, and type-only imports must use `import type { ... }`.
