import type { ReactNode } from "react";
import { lazy } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import AdminLayout from "./layouts/AdminLayout";
import LoginPage from "./pages/auth/LoginPage";

// Login is eager so the auth screen paints immediately (no token yet).
// Every other route is split into its own chunk via React.lazy so the initial
// bundle stays small and each module loads on demand.
const IntakePage = lazy(() => import("./pages/intake/IntakePage"));
const OrdersPage = lazy(() => import("./pages/orders/OrdersPage"));
const OrderDetailPage = lazy(() => import("./pages/orders/OrderDetailPage"));
const ConsolidationPage = lazy(() => import("./pages/consolidation/ConsolidationPage"));
const PurchaseOrdersPage = lazy(() => import("./pages/purchase-orders/PurchaseOrdersPage"));
const PurchaseOrderDetailPage = lazy(() => import("./pages/purchase-orders/PurchaseOrderDetailPage"));
const InboundPage = lazy(() => import("./pages/warehouse/InboundPage"));
const PickListsPage = lazy(() => import("./pages/warehouse/PickListsPage"));
const InventoryPage = lazy(() => import("./pages/warehouse/InventoryPage"));
const DeliveryPage = lazy(() => import("./pages/delivery/DeliveryPage"));
const InvoicesPage = lazy(() => import("./pages/finance/InvoicesPage"));
const StatementsPage = lazy(() => import("./pages/finance/StatementsPage"));
const MarginPage = lazy(() => import("./pages/finance/MarginPage"));
const CustomersPage = lazy(() => import("./pages/master/CustomersPage"));
const ProductsPage = lazy(() => import("./pages/master/ProductsPage"));
const WholesalersPage = lazy(() => import("./pages/master/WholesalersPage"));
const ContractsPage = lazy(() => import("./pages/master/ContractsPage"));
const StandingOrdersPage = lazy(() => import("./pages/master/StandingOrdersPage"));
const QuotationsPage = lazy(() => import("./pages/sales/QuotationsPage"));
const UsersPage = lazy(() => import("./pages/system/UsersPage"));
const AuditPage = lazy(() => import("./pages/system/AuditPage"));
const SettingsPage = lazy(() => import("./pages/system/SettingsPage"));
const DashboardPage = lazy(() => import("./pages/dashboard/DashboardPage"));
const WeComMessagesPage = lazy(() => import("./pages/wecom/WeComMessagesPage"));
const WeComContactsPage = lazy(() => import("./pages/wecom/WeComContactsPage"));
const WeComOutboundPage = lazy(() => import("./pages/wecom/WeComOutboundPage"));

/** Redirects unauthenticated visitors to /login. Wrap around the admin layout. */
export function AuthGuard({ children }: { children: ReactNode }) {
  const location = useLocation();
  const token = localStorage.getItem("erp_token");
  if (!token) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}

/** All routes exactly per docs/AGENT_CONTRACTS.md §7. */
export function AppRoutes() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <AuthGuard>
              <Outlet />
            </AuthGuard>
          }
        >
          <Route element={<AdminLayout />}>
            <Route path="/" element={<Navigate to="/intake" replace />} />
            <Route path="/intake" element={<IntakePage />} />
            <Route path="/orders" element={<OrdersPage />} />
            <Route path="/orders/:id" element={<OrderDetailPage />} />
            <Route path="/consolidation" element={<ConsolidationPage />} />
            <Route path="/purchase-orders" element={<PurchaseOrdersPage />} />
            <Route path="/purchase-orders/:id" element={<PurchaseOrderDetailPage />} />
            <Route path="/warehouse/inbound" element={<InboundPage />} />
            <Route path="/warehouse/pick-lists" element={<PickListsPage />} />
            <Route path="/warehouse/inventory" element={<InventoryPage />} />
            <Route path="/delivery" element={<DeliveryPage />} />
            <Route path="/finance/invoices" element={<InvoicesPage />} />
            <Route path="/finance/statements" element={<StatementsPage />} />
            <Route path="/finance/margin" element={<MarginPage />} />
            <Route path="/master/customers" element={<CustomersPage />} />
            <Route path="/master/products" element={<ProductsPage />} />
            <Route path="/master/wholesalers" element={<WholesalersPage />} />
            <Route path="/master/contracts" element={<ContractsPage />} />
            <Route path="/master/standing-orders" element={<StandingOrdersPage />} />
            <Route path="/sales/quotations" element={<QuotationsPage />} />
            <Route path="/system/users" element={<UsersPage />} />
            <Route path="/system/audit" element={<AuditPage />} />
            <Route path="/system/settings" element={<SettingsPage />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/wecom/messages" element={<WeComMessagesPage />} />
            <Route path="/wecom/contacts" element={<WeComContactsPage />} />
            <Route path="/wecom/outbound" element={<WeComOutboundPage />} />
            <Route path="*" element={<Navigate to="/intake" replace />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
