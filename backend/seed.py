"""Idempotent demo seed: users, catalog, customers, wholesalers, contracts, settings.

Run:  cd backend && python seed.py
(The app also seeds automatically on startup when the DB is empty.)
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import (
    ContractPrice,
    Customer,
    CustomerProductAlias,
    Product,
    ProductCategory,
    StandingOrderTemplate,
    StandingOrderTemplateLine,
    SupplierRule,
    SystemSetting,
    Unit,
    User,
    Wholesaler,
    ProductWholesalerMapping,
)

PASSWORD = "erp123"
VALID_FROM = date(2026, 1, 1)


def seed(db: Session) -> bool:
    """Returns True if data was created, False if DB already seeded."""
    if db.query(User).count() > 0:
        return False

    # --- Users -------------------------------------------------------------
    users = [
        User(email="admin@erp.local", name="Admin", role="admin", password_hash=hash_password(PASSWORD)),
        User(email="ops@erp.local", name="Ops User", role="ops", password_hash=hash_password(PASSWORD)),
        User(email="warehouse@erp.local", name="Warehouse User", role="warehouse", password_hash=hash_password(PASSWORD)),
        User(email="finance@erp.local", name="Finance User", role="finance", password_hash=hash_password(PASSWORD)),
        User(email="driver@erp.local", name="Driver User", role="driver", password_hash=hash_password(PASSWORD)),
    ]
    db.add_all(users)

    # --- Units ---------------------------------------------------------------
    units = {
        "jin": Unit(code="jin", name_en="Jin (500g)", name_zh="斤"),
        "kg": Unit(code="kg", name_en="Kilogram", name_zh="公斤"),
        "box": Unit(code="box", name_en="Box", name_zh="箱"),
        "bag": Unit(code="bag", name_en="Bag", name_zh="袋"),
        "piece": Unit(code="piece", name_en="Piece", name_zh="个"),
    }
    db.add_all(units.values())

    # --- Categories ------------------------------------------------------------
    cats = {
        "veg": ProductCategory(name_en="Vegetables", name_zh="蔬菜"),
        "meat": ProductCategory(name_en="Meat", name_zh="肉类"),
        "grain": ProductCategory(name_en="Rice & Grain", name_zh="米面粮油"),
        "cond": ProductCategory(name_en="Condiments", name_zh="调味品"),
    }
    db.add_all(cats.values())
    db.flush()  # populate ids for constructions below

    # --- Products --------------------------------------------------------------
    def product(sku, en, zh, cat, unit, shelf):
        return Product(
            sku=sku, name_en=en, name_zh=zh,
            category_id=cats[cat].id, default_unit_id=units[unit].id,
            shelf_life_days=shelf, is_active=True,
        )

    products = [
        product("VG001", "Potato", "土豆", "veg", "jin", 30),
        product("VG002", "Chinese Cabbage", "大白菜", "veg", "jin", 10),
        product("VG003", "Tomato", "番茄", "veg", "jin", 7),
        product("VG004", "Cucumber", "黄瓜", "veg", "jin", 7),
        product("VG005", "Carrot", "胡萝卜", "veg", "jin", 20),
        product("VG006", "Lettuce", "生菜", "veg", "jin", 5),
        product("MT001", "Pork Belly", "五花肉", "meat", "jin", 3),
        product("MT002", "Pork Leg", "猪腿肉", "meat", "jin", 3),
        product("MT003", "Whole Chicken", "白条鸡", "meat", "jin", 3),
        product("MT004", "Beef", "牛肉", "meat", "jin", 4),
        product("RG001", "Rice", "大米", "grain", "bag", 180),
        product("RG002", "Flour", "面粉", "grain", "bag", 180),
        product("RG003", "Noodles", "面条", "grain", "bag", 120),
        product("RG004", "Eggs", "鸡蛋", "grain", "jin", 25),
        product("CD001", "Soy Sauce", "生抽", "cond", "box", 365),
        product("CD002", "Cooking Oil", "食用油", "cond", "box", 365),
        product("CD003", "Salt", "食盐", "cond", "bag", 730),
        product("CD004", "Sugar", "白糖", "cond", "bag", 730),
    ]
    db.add_all(products)
    db.flush()  # product ids for mappings/aliases/prices below

    # --- Customers ---------------------------------------------------------------
    cust1 = Customer(
        code="C001", name_en="Foshan No.1 Primary School", name_zh="佛山第一小学",
        type="school", contact_name="Ms. Chen 陈老师", contact_phone="13800000001",
        address="88 Lingnan Avenue, Chancheng, Foshan 佛山市禅城区岭南大道88号",
        delivery_zone="Chancheng 禅城", status="active",
    )
    cust2 = Customer(
        code="C002", name_en="Golden Dragon Restaurant", name_zh="金龙酒家",
        type="restaurant", contact_name="Mr. Li 李经理", contact_phone="13800000002",
        address="12 Guilan Road, Nanhai, Foshan 佛山市南海区桂澜路12号",
        delivery_zone="Nanhai 南海", status="active",
    )
    cust3 = Customer(
        code="C003", name_en="Nanhai District Government Canteen", name_zh="南海区政府食堂",
        type="canteen", contact_name="Mr. Huang 黄主任", contact_phone="13800000003",
        address="1 Nanhai Avenue, Nanhai, Foshan 佛山市南海区南海大道1号",
        delivery_zone="Nanhai 南海", status="active",
    )
    db.add_all([cust1, cust2, cust3])

    # --- Wholesalers -----------------------------------------------------------
    w1 = Wholesaler(
        code="W001", name_en="Foshan Agri-Wholesale Market", name_zh="佛山农批市场",
        contact_name="Mr. Zhou 周老板", contact_phone="13900000001", is_active=True,
    )
    w2 = Wholesaler(
        code="W002", name_en="Guangzhou Fresh Direct", name_zh="广州鲜蔬直供",
        contact_name="Ms. Wu 吴女士", contact_phone="13900000002", is_active=True,
    )
    db.add_all([w1, w2])
    db.flush()  # wholesaler/customer ids for mappings/rules below

    # --- Product -> wholesaler mappings (cost prices) -----------------------------
    costs = {
        "VG001": 1.8, "VG002": 1.0, "VG003": 2.5, "VG004": 2.0, "VG005": 1.5, "VG006": 2.2,
        "MT001": 15.5, "MT002": 13.0, "MT003": 9.5, "MT004": 28.0,
        "RG001": 2.8, "RG002": 3.0, "RG003": 3.5, "RG004": 4.5,
        "CD001": 12.0, "CD002": 45.0, "CD003": 2.0, "CD004": 4.0,
    }
    by_sku = {p.sku: p for p in products}
    for sku, cost in costs.items():
        db.add(ProductWholesalerMapping(
            product_id=by_sku[sku].id, wholesaler_id=w1.id,
            supplier_sku=f"{sku}-W1", cost_price=cost,
        ))
    # Secondary supplier for vegetables
    for sku in ("VG001", "VG002", "VG005"):
        db.add(ProductWholesalerMapping(
            product_id=by_sku[sku].id, wholesaler_id=w2.id,
            supplier_sku=f"{sku}-W2", cost_price=costs[sku] + 0.1,
        ))

    # --- Supplier rules -----------------------------------------------------------
    for cat in cats.values():
        db.add(SupplierRule(
            category_id=cat.id, wholesaler_id=w1.id,
            priority=10, is_default=True, lead_time_days=1,
        ))
    db.add(SupplierRule(category_id=cats["veg"].id, wholesaler_id=w2.id, priority=5, lead_time_days=1))

    # --- Customer aliases ------------------------------------------------------------
    alias_map = [
        (cust1, [("土豆", "VG001"), ("potato", "VG001"), ("洋芋", "VG001"), ("大白菜", "VG002"), ("五花肉", "MT001"), ("大米", "RG001")]),
        (cust2, [("菜心", "VG006"), ("五花肉", "MT001"), ("牛肉", "MT004"), ("大米", "RG001")]),
        (cust3, [("大米", "RG001"), ("鸡蛋", "RG004"), ("生抽", "CD001"), ("食用油", "CD002")]),
    ]
    for cust, pairs in alias_map:
        for text, sku in pairs:
            db.add(CustomerProductAlias(customer_id=cust.id, alias=text, product_id=by_sku[sku].id))

    # --- Contract prices ---------------------------------------------------------------
    def price(cust, sku, unit, amount):
        db.add(ContractPrice(
            customer_id=cust.id, product_id=by_sku[sku].id, unit_id=units[unit].id,
            price=amount, valid_from=VALID_FROM,
        ))

    price(cust1, "VG001", "jin", 2.2)
    price(cust1, "VG002", "jin", 1.5)
    price(cust1, "MT001", "jin", 18.0)
    price(cust1, "RG001", "bag", 58.0)
    price(cust1, "RG004", "jin", 5.5)
    price(cust2, "VG006", "jin", 3.0)
    price(cust2, "MT001", "jin", 19.0)
    price(cust2, "MT004", "jin", 33.0)
    price(cust2, "VG003", "jin", 3.2)
    price(cust3, "RG001", "bag", 57.0)
    price(cust3, "RG004", "jin", 5.4)
    price(cust3, "CD001", "box", 15.0)
    price(cust3, "CD002", "box", 52.0)

    # --- Standing order template --------------------------------------------------------
    tpl = StandingOrderTemplate(
        customer_id=cust3.id, name="Weekly staples 每周主食",
        delivery_days=["mon", "thu"], is_active=True,
    )
    db.add(tpl)
    db.flush()
    tpl_lines = [
        (by_sku["RG001"], 2.0, units["bag"].id),
        (by_sku["CD001"], 1.0, units["box"].id),
        (by_sku["RG004"], 10.0, units["jin"].id),
    ]
    for prod, qty, unit_id in tpl_lines:
        db.add(StandingOrderTemplateLine(
            template_id=tpl.id, product_id=prod.id, quantity=qty, unit_id=unit_id,
        ))

    # --- Settings --------------------------------------------------------------------------
    db.add_all([
        SystemSetting(key="auto_confirm", value={"enabled": True, "min_confidence": 0.95},
                      description="Auto-confirm orders when confidence and rules pass"),
        SystemSetting(key="cutoff_time", value="18:00",
                      description="Daily consolidation cutoff (local time HH:MM)"),
        SystemSetting(key="auto_invoice", value={"enabled": True},
                      description="Auto-generate invoice on delivery confirmation"),
    ])

    db.commit()
    return True


if __name__ == "__main__":
    from app.core.database import SessionLocal, init_db

    init_db()
    with SessionLocal() as session:
        created = seed(session)
    print("Seed: created demo data" if created else "Seed: DB already seeded, skipped")
