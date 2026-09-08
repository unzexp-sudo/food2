import ContractsPage from "./ContractsPage";

/**
 * The /master/standing-orders route renders the same ContractsPage with the
 * standing-orders tab focused. Per DEV_NOTES the route must work — we delegate
 * to ContractsPage (which contains the standing orders tab).
 */
export default function StandingOrdersPage() {
  return <ContractsPage />;
}
