import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import AdminLayout from "./layouts/AdminLayout";
import LoginPage from "./pages/auth/LoginPage";
import IntakePage from "./pages/intake/IntakePage";
import OrdersPage from "./pages/orders/OrdersPage";
import OrderDetailPage from "./pages/orders/OrderDetailPage";
import ConsolidationPage from "./pages/consolidation/ConsolidationPage";
import PurchaseOrdersPage from "./pages/purchase-orders/PurchaseOrdersPage";
import PurchaseOrderDetailPage from "./pages/purchase-orders/PurchaseOrderDetailPage";
import InboundPage from "./pages/warehouse/InboundPage";
import PickListsPage from "./pages/warehouse/PickListsPage";
import InventoryPage from "./pages/warehouse/InventoryPage";
import DeliveryPage from "./pages/delivery/DeliveryPage";
import InvoicesPage from "./pages/finance/InvoicesPage";
import StatementsPage from "./pages/finance/StatementsPage";
import MarginPage from "./pages/finance/MarginPage";
import CustomersPage from "./pages/master/CustomersPage";
import ProductsPage from "./pages/master/ProductsPage";
import WholesalersPage from "./pages/master/WholesalersPage";
import ContractsPage from "./pages/master/ContractsPage";
import StandingOrdersPage from "./pages/master/StandingOrdersPage";
import UsersPage from "./pages/system/UsersPage";
import AuditPage from "./pages/system/AuditPage";
import SettingsPage from "./pages/system/SettingsPage";
import DashboardPage from "./pages/dashboard/DashboardPage";
import WeComMessagesPage from "./pages/wecom/WeComMessagesPage";
import WeComContactsPage from "./pages/wecom/WeComContactsPage";
import WeComOutboundPage from "./pages/wecom/WeComOutboundPage";

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
