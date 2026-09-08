"""End-to-end demo: WeCom order → confirm → consolidate → receive → pick →
deliver → invoice, with made-up numbers, in one command.

Runs both services with fresh temp databases, stages a WeCom-style text order,
waits for the AI parser to create a draft order, then fast-forwards the full
lifecycle. At each transition it prints what happened and where to look in the
frontend.

Usage:
    python scripts/demo_order_flow.py

No credentials, no network, no WeCom required — everything is mock mode.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta

import httpx

PY = sys.executable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ERP_PORT, GW_PORT = 8000, 8100
ERP = f"http://127.0.0.1:{ERP_PORT}"
GW = f"http://127.0.0.1:{GW_PORT}"
ADMIN = {"email": "admin@erp.local", "password": "erp123"}
TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)

SEP = "=" * 72
OK = "  [ok]     "
INFO = "  [info]   "
WARN = "  [warn]   "
ERR = "  [err]    "


def banner(title: str) -> None:
    print(f"\n{SEP}\n{title}\n{SEP}")


def wait_for_services(client: httpx.Client, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    for name, url in (("gateway", f"{GW}/wecom/health"), ("erp", f"{ERP}/docs")):
        while time.time() < deadline:
            try:
                if client.get(url).status_code == 200:
                    print(f"{OK}{name}: up")
                    break
            except Exception:
                time.sleep(0.5)
        else:
            print(f"{ERR}{name}: failed to start")
            return False
    return True


def login(client: httpx.Client) -> str:
    r = client.post(f"{ERP}/api/v1/auth/login", json=ADMIN)
    r.raise_for_status()
    return r.json()["token"]


def api(client: httpx.Client, method: str, path: str, **kwargs) -> dict:
    """Call an ERP endpoint with the admin token."""
    headers = kwargs.pop("headers", {})
    headers.setdefault("Authorization", f"Bearer {client.headers.get('Authorization', '').split()[-1]}")
    r = client.request(method, f"{ERP}{path}", headers=headers, **kwargs)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        print(f"{ERR}HTTP {r.status_code} on {method} {path}")
        try:
            print(f"     {json.dumps(r.json(), ensure_ascii=False)[:300]}")
        except Exception:
            print(f"     {r.text[:300]}")
        raise
    return r.json()


def gw_post(client: httpx.Client, path: str, **kwargs) -> dict:
    """Call a Gateway endpoint (no auth required for mock ingest)."""
    r = client.post(f"{GW}{path}", **kwargs)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError:
        print(f"{ERR}HTTP {r.status_code} on POST {path}")
        try:
            print(f"     {json.dumps(r.json(), ensure_ascii=False)[:300]}")
        except Exception:
            print(f"     {r.text[:300]}")
        raise
    return r.json()


def find_order(client: httpx.Client, customer_id: str) -> dict | None:
    """Poll the order list until one appears (pending_confirmation or confirmed)."""
    for _ in range(120):
        for status in ("pending_confirmation", "confirmed"):
            data = api(client, "GET", "/api/v1/orders", params={"customer_id": customer_id, "status": status, "page_size": 20})
            items = data.get("items", [])
            if items:
                return items[0]
        time.sleep(0.5)
    return None


def poll_job(client: httpx.Client, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = api(client, "GET", f"/api/v1/intake/jobs/{job_id}")
        st = job.get("status", "")
        if st in ("completed", "failed"):
            return job
        time.sleep(0.5)
    return api(client, "GET", f"/api/v1/intake/jobs/{job_id}")


def pretty_dict(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, indent=2)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="demo-")
    erp_env = dict(
        os.environ,
        ERP_SERVICE_KEY="dev-service-key",
        ERP_WECOM_GATEWAY_URL=GW,
        ERP_WECOM_GATEWAY_KEY="dev-gateway-key",
        ERP_DATABASE_URL=f"sqlite:///{tmp}/erp.db",
    )
    gw_env = dict(
        os.environ,
        WECOM_MODE="mock",
        WECOM_ERP_API_KEY="dev-service-key",
        WECOM_GATEWAY_SERVICE_KEY="dev-gateway-key",
        WECOM_ERP_BASE_URL=ERP,
        WECOM_DATABASE_URL=f"sqlite:///{tmp}/wecom.db",
        WECOM_OUTBOX_DIR=f"{tmp}/outbox",
        WECOM_MOCK_ARCHIVE_DIR=f"{tmp}/archive",
        WECOM_MOCK_MEDIA_DIR=f"{tmp}/media",
    )

    banner("Food Delivery ERP — end-to-end demo")
    print(f"{INFO}Temp dir: {tmp}")
    print(f"{INFO}ERP: {ERP}   Gateway: {GW}")

    procs: list[subprocess.Popen] = []
    try:
        # ------------------------------------------------------------------
        # 0. Start services
        # ------------------------------------------------------------------
        banner("0. Starting services")
        procs = [
            subprocess.Popen(
                [PY, "-m", "uvicorn", "app.main:app", "--port", str(ERP_PORT)],
                cwd=f"{ROOT}/backend", env=erp_env,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            ),
            subprocess.Popen(
                [PY, "-m", "uvicorn", "app.main:app", "--port", str(GW_PORT)],
                cwd=f"{ROOT}/wecom-gateway", env=gw_env,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            ),
        ]
        client = httpx.Client(trust_env=False, timeout=30)
        if not wait_for_services(client):
            return 1

        # ------------------------------------------------------------------
        # 1. Login
        # ------------------------------------------------------------------
        banner("1. Login")
        token = login(client)
        client.headers["Authorization"] = f"Bearer {token}"
        me = api(client, "GET", "/api/v1/auth/me")
        print(f"{OK}Logged in as {me['name']} ({me['role']})")

        # ------------------------------------------------------------------
        # 2. Stage WeCom traffic (simulator + archive pull)
        # ------------------------------------------------------------------
        banner("2. Injecting a WeCom order")
        # Run the simulator against our temp dirs.
        subprocess.run(
            [PY, "-m", "simulator.producer", "--scenario", "all"],
            cwd=f"{ROOT}/wecom-gateway", env=gw_env,
            capture_output=True, text=True,
        )
        # Trigger archive pull so messages land in the gateway DB.
        gw_post(client, "/wecom/archive/callback", json={"type": "msgaudit_notify"})
        time.sleep(3)

        # Pull again (simulator may have written more than one batch).
        gw_post(client, "/wecom/archive/callback", json={"type": "msgaudit_notify"})
        time.sleep(2)

        # Show what the gateway saw.
        msgs = client.get(f"{GW}/wecom/messages", params={"page_size": 50}).json()
        print(f"{INFO}Gateway messages: {msgs['total']}")
        for m in msgs.get("items", [])[:5]:
            cid = m.get("customer_id") or "—"
            print(f"     {m['msgid'][:22]:<22} {m['status']:<10} cust={cid[:8]} src={m.get('source_type')}")

        # ------------------------------------------------------------------
        # 3. Find the draft order created by AI intake
        # ------------------------------------------------------------------
        banner("3. Waiting for AI parser to create a draft order")
        # Seed customers get random UUIDs in temp DBs — query them first.
        customers = api(client, "GET", "/api/v1/customers", params={"page_size": 10})
        cust = customers.get("items", [{}])[0] if customers.get("items") else None
        if not cust:
            print(f"{ERR}No customers seeded. Aborting.")
            return 1
        cust_id = cust["id"]
        print(f"{INFO}Using customer: {cust.get('name_zh','')} ({cust_id[:8]})")

        order = find_order(client, cust_id)
        if order is None:
            # Fallback: any order at all (auto-confirm may have used a different customer).
            for _ in range(60):
                data = api(client, "GET", "/api/v1/orders", params={"page_size": 20})
                items = data.get("items", [])
                if items:
                    order = items[0]
                    break
                time.sleep(0.5)
        if order is None:
            print(f"{ERR}No order found. Aborting.")
            print(f"{INFO}Try re-running; the AI parser may need more time.")
            return 1

        print(f"{OK}Order found: #{order['order_number']}  status={order['status']}")
        for ln in order.get("lines", []):
            print(f"     Line {ln['line_no']}: {ln.get('product_display','(unmatched)')} x {ln['quantity']} {ln.get('unit_code','')}")
        print(f"\n{INFO}Frontend → Orders → #{order['order_number']}")

        # ------------------------------------------------------------------
        # 4. Confirm the order
        # ------------------------------------------------------------------
        banner("4. Confirming order")
        if order["status"] == "pending_confirmation":
            api(client, "POST", f"/api/v1/orders/{order['id']}/confirm", json={"notes": "Demo confirmation"})
            order = api(client, "GET", f"/api/v1/orders/{order['id']}")
            print(f"{OK}Confirmed → status={order['status']}")
        else:
            print(f"{INFO}Already {order['status']} (auto-confirm may have fired)")
        print(f"{INFO}Frontend → Orders → #{order['order_number']} → status badge turns green")

        # ------------------------------------------------------------------
        # 5. Consolidation → PO
        # ------------------------------------------------------------------
        banner("5. Running consolidation (creates purchase orders)")
        try:
            cons = api(client, "POST", "/api/v1/consolidation/run", json={"delivery_date": TOMORROW.isoformat()})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                print(f"{WARN}Consolidation already run for {TOMORROW}")
                cons = None
            else:
                raise

        if cons:
            pos = cons.get("purchase_orders", [])
            print(f"{OK}Created {len(pos)} purchase order(s)")
            for po in pos:
                print(f"     PO {po['po_number']}  wholesaler={po.get('wholesaler_name_zh','—')}  total={po.get('total_cost')}")
            po_id = pos[0]["id"] if pos else None
        else:
            # List existing POs.
            po_list = api(client, "GET", "/api/v1/purchase-orders", params={"page_size": 10})
            pos = po_list.get("items", [])
            print(f"{INFO}Existing POs: {len(pos)}")
            po_id = pos[0]["id"] if pos else None

        if not po_id:
            print(f"{ERR}No purchase order found. Aborting.")
            return 1

        print(f"{INFO}Frontend → Purchase Orders → PO list")

        # ------------------------------------------------------------------
        # 6. Mark PO sent
        # ------------------------------------------------------------------
        banner("6. Sending PO to wholesaler")
        api(client, "POST", f"/api/v1/purchase-orders/{po_id}/send")
        po = api(client, "GET", f"/api/v1/purchase-orders/{po_id}")
        print(f"{OK}PO status={po['status']}")
        print(f"{INFO}Frontend → Purchase Orders → {po['po_number']}")

        # ------------------------------------------------------------------
        # 7. Receive PO (inbound receipt)
        # ------------------------------------------------------------------
        banner("7. Receiving goods (inbound receipt)")
        po_detail = api(client, "GET", f"/api/v1/purchase-orders/{po_id}")
        lines = po_detail.get("lines", [])
        receipt_lines = [
            {
                "po_line_id": ln["id"],
                "quantity_received": ln["quantity_ordered"],
                "quantity_damaged": 0.0,
                "notes": "Demo receipt",
            }
            for ln in lines
        ]
        api(client, "POST", "/api/v1/inbound-receipts", json={"po_id": po_id, "lines": receipt_lines})
        print(f"{OK}Received {len(receipt_lines)} line(s)")
        print(f"{INFO}Frontend → Warehouse → Inbound Receipts")

        # ------------------------------------------------------------------
        # 8. Generate pick list
        # ------------------------------------------------------------------
        banner("8. Generating pick list")
        pick_result = api(client, "POST", "/api/v1/pick-lists/generate", json={"delivery_date": TOMORROW.isoformat()})
        if "pick_list" in pick_result:
            pick_lists = [pick_result["pick_list"]]
        elif isinstance(pick_result, list):
            pick_lists = pick_result
        else:
            pick_lists = pick_result.get("items", [])
        if not pick_lists:
            print(f"{WARN}No pick lists generated — listing existing ones")
            plist = api(client, "GET", "/api/v1/pick-lists", params={"page_size": 5})
            pick_lists = plist.get("items", [])
        if not pick_lists:
            print(f"{ERR}No pick lists. Aborting.")
            return 1

        pick = pick_lists[0]
        print(f"{OK}Pick list {pick['pick_number']}  status={pick['status']}")
        for pl in pick.get("lines", []):
            print(f"     {pl.get('product_name_zh','—'):<10}  req={pl.get('quantity')}  pick={pl.get('picked_quantity')}")
        print(f"{INFO}Frontend → Warehouse → Pick Lists")

        # ------------------------------------------------------------------
        # 9. Pick items
        # ------------------------------------------------------------------
        banner("9. Picking items")
        for pl in pick.get("lines", []):
            if pl.get("status") == "open":
                qty = pl.get("quantity", 0)
                api(client, "POST", f"/api/v1/pick-lists/{pick['id']}/lines/{pl['id']}/pick",
                    json={"picked_quantity": qty})
                print(f"{OK}Picked {pl['product_name_zh']} x {qty}")
        pick = api(client, "GET", f"/api/v1/pick-lists/{pick['id']}")
        print(f"{INFO}Pick list status={pick['status']}")

        # ------------------------------------------------------------------
        # 10. Generate delivery
        # ------------------------------------------------------------------
        banner("10. Generating delivery")
        del_result = api(client, "POST", "/api/v1/deliveries/generate", json={"delivery_date": TOMORROW.isoformat()})
        deliveries = del_result.get("created", []) if isinstance(del_result, dict) else del_result
        if not deliveries:
            dlist = api(client, "GET", "/api/v1/deliveries", params={"page_size": 5})
            deliveries = dlist.get("items", [])
        if not deliveries:
            print(f"{ERR}No deliveries. Aborting.")
            return 1

        # The generator creates one delivery per order with picked lines.
        # Match to the order we're tracking, or follow the delivery's order.
        delivery = next((d for d in deliveries if d.get("order_id") == order["id"]), deliveries[0])
        if delivery.get("order_id") != order["id"]:
            print(f"{INFO}Delivery belongs to a different order — switching track to #{delivery.get('order_number')}")
            order = api(client, "GET", f"/api/v1/orders/{delivery['order_id']}")

        print(f"{OK}Delivery {delivery['delivery_number']}  status={delivery['status']}")
        for dl in delivery.get("lines", []):
            print(f"     {dl.get('product_name_zh','—'):<10}  qty={dl.get('quantity')}")
        print(f"{INFO}Frontend → Delivery Board")

        # ------------------------------------------------------------------
        # 11. Mark out for delivery
        # ------------------------------------------------------------------
        banner("11. Dispatching delivery")
        api(client, "POST", f"/api/v1/deliveries/{delivery['id']}/status",
            json={"status": "out_for_delivery"})
        delivery = api(client, "GET", f"/api/v1/deliveries/{delivery['id']}")
        print(f"{OK}Status → {delivery['status']}")
        print(f"{INFO}At this point the ERP would send a 'out_for_delivery' WeCom message to the customer.")

        # ------------------------------------------------------------------
        # 12. Complete delivery
        # ------------------------------------------------------------------
        banner("12. Completing delivery")
        complete_lines = [
            {"delivery_line_id": dl["id"], "delivered_quantity": dl["quantity"]}
            for dl in delivery.get("lines", [])
        ]
        api(client, "POST", f"/api/v1/deliveries/{delivery['id']}/complete",
            json={"lines": complete_lines})
        delivery = api(client, "GET", f"/api/v1/deliveries/{delivery['id']}")
        print(f"{OK}Status → {delivery['status']}  delivered_at={delivery.get('delivered_at')}")
        print(f"{INFO}ERP would send a 'delivered' WeCom confirmation to the customer.")

        # ------------------------------------------------------------------
        # 13. Generate invoice
        # ------------------------------------------------------------------
        banner("13. Generating invoice")
        try:
            inv = api(client, "POST", "/api/v1/invoices/generate", json={"order_id": order['id']})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                detail = exc.response.json().get("detail", {})
                inv = detail.get("invoice", {})
                print(f"{INFO}Auto-invoice already created by the delivery.completed event handler")
            else:
                raise
        print(f"{OK}Invoice #{inv.get('invoice_number')}  total={inv.get('total_amount')}  status={inv.get('status')}")
        print(f"{INFO}Frontend → Finance → Invoices")

        # ------------------------------------------------------------------
        # 14. WeCom outbox (mock notifications)
        # ------------------------------------------------------------------
        banner("14. Mock WeCom notifications (outbox)")
        outbox_files = sorted(glob.glob(os.path.join(tmp, "outbox", "*.txt")))
        if outbox_files:
            print(f"{INFO}{len(outbox_files)} notification(s) written to mock outbox:")
            for fpath in outbox_files[-6:]:
                name = os.path.basename(fpath)
                text = open(fpath).read().splitlines()
                preview = text[0][:80] if text else ""
                print(f"     {name:<40} {preview}")
        else:
            print(f"{INFO}No outbox files (notifications may be queued in the ERP event bus)")

        # ------------------------------------------------------------------
        # Summary
        # ------------------------------------------------------------------
        banner("Summary — where to look in the frontend")
        print(f"  Order:        http://localhost:5173/orders/{order['id']}")
        print(f"  PO:           http://localhost:5173/purchase-orders")
        print(f"  Pick list:    http://localhost:5173/warehouse/pick-lists")
        print(f"  Delivery:     http://localhost:5173/deliveries")
        print(f"  Invoice:      http://localhost:5173/finance/invoices")
        print(f"  WeCom msgs:   http://localhost:5173/wecom/messages")
        print(f"  WeCom outbox: http://localhost:5173/wecom/outbound")
        print(f"\n  Or open the ERP Swagger UI: {ERP}/docs")

        return 0

    finally:
        for p in procs:
            p.terminate()
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"\n{INFO}Cleaned up temp dir")


if __name__ == "__main__":
    sys.exit(main())
