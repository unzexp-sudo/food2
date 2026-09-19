"""Batch master-data import: customers, products, prices.

The importer's whole reason to exist is that we do NOT know the customer's
format, so these tests deliberately drive it with the shapes real files arrive
in: Chinese headers, English headers, a title row above the header, GBK bytes, a
BOM, and a customers×products price matrix.

Two properties matter more than any individual case and are asserted repeatedly:

- **A dry run writes nothing.** Not a row, not an audit entry. The UI shows the
  operator a report and asks them to confirm; if the preview had side effects
  that promise would be a lie.
- **A file with any error writes nothing at all.** A partial import leaves
  somebody reconciling a half-loaded file by hand, which is worse than refusing.
"""
from __future__ import annotations

import csv
import io
import time

import pytest
from sqlalchemy.orm import joinedload

from app.core.database import SessionLocal
from app.models import ContractPrice, Customer, Product, ProductCategory, Unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _csv_bytes(rows: list[list[str]], *, encoding: str = "utf-8", bom: bool = False) -> bytes:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    text = buf.getvalue()
    data = text.encode(encoding)
    return (b"\xef\xbb\xbf" + data) if bom else data


def _import(client, headers, path: str, filename: str, content: bytes, **form):
    """POST a file to an import endpoint. `form` values become string fields."""
    data = {k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in form.items()}
    return client.post(
        path,
        headers=headers,
        files={"file": (filename, content, "text/csv")},
        data=data,
    )


def _own_customer(client, headers, code: str) -> str:
    """Create a customer that belongs to this test and to nothing else.

    The test database is session-scoped and never reset, so an import test that
    writes to a *seeded* customer (C001…) silently changes the world for every
    test that runs after it — a price import that supersedes the seeded
    `C001 × 土豆` price makes an unrelated order test assert 2.35 where it
    expects 2.20. Owning the key makes each test's writes unobservable to the
    others, which is the only way a shared database stays workable.
    """
    content = _csv_bytes([["客户编码", "客户名称"], [code, f"导入测试客户{code}"]])
    r = _import(client, headers, CUSTOMERS_IMPORT, "own.csv", content, dry_run=False)
    assert r.json().get("applied") is True, r.text
    return code


def _own_product(client, headers, sku: str, name: str = "导入测试品") -> str:
    """Create a product that belongs to this test and to nothing else."""
    content = _csv_bytes([
        ["商品编码", "商品名称", "分类", "单位"],
        [sku, name, "蔬菜", "斤"],
    ])
    r = _import(client, headers, PRODUCTS_IMPORT, "own.csv", content, dry_run=False)
    assert r.json().get("applied") is True, r.text
    return sku


def _price_rows(customer_code: str, product_sku: str) -> list[ContractPrice]:
    """Every price row for one (customer, product) pair, oldest first."""
    with SessionLocal() as db:
        cust = db.query(Customer).filter(Customer.code == customer_code).one()
        prod = db.query(Product).filter(Product.sku == product_sku).one()
        return (
            db.query(ContractPrice)
            .filter(
                ContractPrice.customer_id == cust.id,
                ContractPrice.product_id == prod.id,
            )
            .order_by(ContractPrice.valid_from)
            .all()
        )


CUSTOMERS_IMPORT = "/api/v1/customers/import"
PRODUCTS_IMPORT = "/api/v1/products/import"
PRICES_IMPORT = "/api/v1/contract-prices/import"


def _count(model) -> int:
    with SessionLocal() as db:
        return db.query(model).count()


def _find_customer(code: str) -> Customer | None:
    with SessionLocal() as db:
        return db.query(Customer).filter(Customer.code == code).first()


def _find_product(sku: str) -> Product | None:
    """Return the product with its relationships already loaded.

    The session closes before the caller asserts, so an unloaded relationship
    would raise DetachedInstanceError — a test-harness artefact that reads
    exactly like a product defect. Eager-load what the assertions touch.
    """
    with SessionLocal() as db:
        return (
            db.query(Product)
            .options(joinedload(Product.category), joinedload(Product.default_unit))
            .filter(Product.sku == sku)
            .first()
        )


# ---------------------------------------------------------------------------
# Customers — the dry run
# ---------------------------------------------------------------------------
def test_customer_dry_run_reports_without_writing(client, admin_headers):
    """The preview must be free of side effects."""
    before = _count(Customer)
    content = _csv_bytes([
        ["客户编码", "客户名称", "电话"],
        ["IMP-DRY-1", "测试客户一", "13800000001"],
        ["IMP-DRY-2", "测试客户二", "13800000002"],
    ])
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=True)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["counts"]["create"] == 2
    assert body["counts"]["error"] == 0
    assert body["applied"] is False
    assert _count(Customer) == before, "a dry run must not create customers"

    # Nothing was written, so nothing was audited either.
    from app.models import AuditLog
    with SessionLocal() as db:
        assert db.query(AuditLog).filter(
            AuditLog.entity_type == "ImportBatch"
        ).filter(AuditLog.after["sha256"].as_string() == body["content_sha256"]).first() is None


def test_customer_import_commits_after_dry_run(client, admin_headers):
    content = _csv_bytes([
        ["客户编码", "客户名称", "联系人", "电话"],
        ["IMP-OK-1", "导入客户一", "张先生", "13800000011"],
    ])
    preview = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=True)
    assert preview.json()["counts"]["create"] == 1
    assert _find_customer("IMP-OK-1") is None

    commit = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=False)
    assert commit.status_code == 200, commit.text
    assert commit.json()["applied"] is True

    created = _find_customer("IMP-OK-1")
    assert created is not None
    assert created.name_zh == "导入客户一"
    assert created.contact_phone == "13800000011"


def test_customer_import_upserts_an_existing_code(client, admin_headers):
    """A known code updates rather than duplicating — this is what makes
    re-running an import safe, which is how the operator bulk-edits."""
    before = _count(Customer)
    # C001 is seeded as 佛山第一小学.
    content = _csv_bytes([
        ["客户编码", "客户名称", "电话"],
        ["C001", "佛山第一小学", "0757-0000000"],
    ])
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=False)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"]["update"] == 1
    assert body["counts"]["create"] == 0
    assert _count(Customer) == before, "an upsert must not add a row"
    assert _find_customer("C001").contact_phone == "0757-0000000"


def test_customer_import_mirrors_a_missing_name_language(client, admin_headers):
    """name_en and name_zh are both NOT NULL, so a one-language file must fill
    the other — and must say so, because a copy is not real data."""
    content = _csv_bytes([
        ["客户编码", "客户名称"],
        ["IMP-MIRROR-1", "只有中文名的客户"],
    ])
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=True)
    row = r.json()["rows"][0]
    assert row["action"] == "create"
    assert any("name_en filled from the Chinese name" in w for w in row["warnings"]), row

    _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=False)
    created = _find_customer("IMP-MIRROR-1")
    assert created.name_zh == "只有中文名的客户"
    assert created.name_en == "只有中文名的客户"


def test_duplicate_code_inside_one_file_is_an_error(client, admin_headers):
    """Two rows for one code: the second would silently win, and nobody would
    ever know which. Name the other row instead."""
    content = _csv_bytes([
        ["客户编码", "客户名称"],
        ["IMP-DUP-1", "第一次出现"],
        ["IMP-DUP-1", "第二次出现"],
    ])
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=True)
    body = r.json()
    assert body["counts"]["error"] == 1
    err = body["rows"][1]["errors"][0]
    assert "also appears on row 2" in err, err


def test_a_file_with_any_error_writes_nothing(client, admin_headers):
    """One bad row must not cost the other 999, but it must not half-apply
    either — the operator fixes the file and resends."""
    before = _count(Customer)
    content = _csv_bytes([
        ["客户编码", "客户名称"],
        ["IMP-MIX-1", "好的这一行"],
        ["", "没有编码这一行"],
    ])
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=False)
    body = r.json()
    assert body["counts"]["error"] == 1
    assert body["applied"] is False
    assert _count(Customer) == before
    assert _find_customer("IMP-MIX-1") is None
    assert any("Nothing was written" in n for n in body["notes"])


def test_missing_required_column_is_refused_with_a_reason(client, admin_headers):
    """No code column at all: every row would fail, so say it once at the top
    rather than 500 times per row."""
    content = _csv_bytes([["客户名称", "电话"], ["无编码客户", "138"]])
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=True)
    body = r.json()
    assert body["counts"]["error"] >= 1
    assert any("not mapped" in n and "code" in n for n in body["notes"]), body["notes"]


# ---------------------------------------------------------------------------
# Customers — file shapes
# ---------------------------------------------------------------------------
def test_gbk_encoded_csv_is_read(client, admin_headers):
    """A Chinese Excel writes GBK by default. Decoding it as UTF-8 would turn
    every header into replacement characters and map nothing."""
    content = _csv_bytes(
        [["客户编码", "客户名称"], ["IMP-GBK-1", "国标编码客户"]],
        encoding="gbk",
    )
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=True)
    body = r.json()
    assert body["counts"]["create"] == 1, body
    assert body["mapping"]["客户编码"] == "code"
    assert body["encoding"] in ("gbk", "gb18030")


def test_bom_prefixed_csv_is_read(client, admin_headers):
    """Excel writes a BOM on "Save as CSV UTF-8". Without utf-8-sig first the
    first header keeps an invisible \\ufeff and never matches a synonym."""
    content = _csv_bytes(
        [["客户编码", "客户名称"], ["IMP-BOM-1", "带BOM客户"]],
        bom=True,
    )
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=True)
    body = r.json()
    assert body["counts"]["create"] == 1, body
    assert body["mapping"]["客户编码"] == "code"


def test_header_below_a_title_row_is_found(client, admin_headers):
    """Real exports open with a title and a blank line. Taking row 1 as the
    header would map nothing and look like an unsupported file."""
    content = _csv_bytes([
        ["2026年客户资料表", "", ""],
        ["", "", ""],
        ["客户编码", "客户名称", "电话"],
        ["IMP-TITLE-1", "标题行客户", "13800000021"],
    ])
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=True)
    body = r.json()
    assert body["counts"]["create"] == 1, body
    assert body["header_row_number"] == 3
    assert body["mapping"]["客户名称"] == "name_zh"


def test_english_headers_work(client, admin_headers):
    content = _csv_bytes([
        ["Customer Code", "Customer Name", "Phone"],
        ["IMP-EN-1", "English Header Co", "13800000031"],
    ])
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=True)
    body = r.json()
    assert body["mapping"]["Customer Code"] == "code"
    assert body["mapping"]["Customer Name"] == "name_zh"
    assert body["counts"]["create"] == 1, body


def test_unmapped_columns_are_reported_not_dropped(client, admin_headers):
    """An unknown column must be visible, because the operator's mental model
    of "it imported my file" depends on knowing what was ignored."""
    content = _csv_bytes([
        ["客户编码", "客户名称", "我们内部的奇怪字段"],
        ["IMP-UNK-1", "有未知列的客户", "随便什么"],
    ])
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=True)
    body = r.json()
    assert "我们内部的奇怪字段" in body["unmapped_headers"]
    assert body["counts"]["create"] == 1


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
def test_product_import_creates_missing_category_and_unit(client, admin_headers):
    """A first load of a few thousand products cannot require the taxonomy to
    exist first."""
    cats_before, units_before = _count(ProductCategory), _count(Unit)
    # Names unique to this test: asserting that "筐" does not exist yet would be
    # asserting a fact about every *other* test in the session, and it stops
    # being true the moment one of them creates a 筐 unit.
    content = _csv_bytes([
        ["商品编码", "商品名称", "分类", "单位", "保质期"],
        ["IMP-P-1", "进口带鱼", "进口水产IMP", "IMP箱", "3天"],
    ])
    r = _import(client, admin_headers, PRODUCTS_IMPORT, "p.csv", content, dry_run=True)
    row = r.json()["rows"][0]
    assert any("category: 进口水产IMP" in c for c in row["creates"]), row
    assert any("unit: IMP箱" in c for c in row["creates"]), row
    assert _count(ProductCategory) == cats_before, "dry run created a category"

    r = _import(client, admin_headers, PRODUCTS_IMPORT, "p.csv", content, dry_run=False)
    assert r.json()["applied"] is True
    assert _count(ProductCategory) == cats_before + 1
    assert _count(Unit) == units_before + 1

    p = _find_product("IMP-P-1")
    assert p is not None
    assert p.shelf_life_days == 3, "a unit suffix on a number must not break it"
    assert p.category is not None and p.category.name_zh == "进口水产IMP"
    assert p.default_unit is not None and p.default_unit.name_zh == "IMP箱"


def test_product_import_reuses_an_existing_category_by_name(client, admin_headers):
    """蔬菜 already exists, so naming it must not create a second one."""
    cats_before = _count(ProductCategory)
    content = _csv_bytes([
        ["商品编码", "商品名称", "分类", "单位"],
        ["IMP-P-2", "莲藕", "蔬菜", "斤"],
    ])
    r = _import(client, admin_headers, PRODUCTS_IMPORT, "p.csv", content, dry_run=False)
    assert r.json()["applied"] is True
    assert _count(ProductCategory) == cats_before, "duplicated an existing category"
    p = _find_product("IMP-P-2")
    assert p.category is not None and p.category.name_zh == "蔬菜"
    assert p.default_unit is not None and p.default_unit.code == "jin"


def test_product_import_updates_an_existing_sku(client, admin_headers):
    """A second file for a SKU we already hold updates it rather than creating a
    duplicate. Run against a SKU this test created, so it does not quietly
    rewrite a seeded product that another test depends on."""
    _own_product(client, admin_headers, "IMP-UPD-1", "莲藕")
    products_before = _count(Product)

    content = _csv_bytes([
        ["商品编码", "商品名称", "保质期"],
        ["IMP-UPD-1", "莲藕", "45"],
    ])
    r = _import(client, admin_headers, PRODUCTS_IMPORT, "p.csv", content, dry_run=False)
    assert r.json()["applied"] is True
    assert r.json()["counts"]["update"] == 1
    assert _count(Product) == products_before, "an update must not create a second row"
    assert _find_product("IMP-UPD-1").shelf_life_days == 45


# ---------------------------------------------------------------------------
# Prices — long format
# ---------------------------------------------------------------------------
def test_price_import_long_format(client, admin_headers):
    cust = _own_customer(client, admin_headers, "IMP-LONG-1")
    content = _csv_bytes([
        ["客户编码", "商品编码", "单位", "单价"],
        [cust, "VG001", "斤", "3.75"],
    ])
    r = _import(client, admin_headers, PRICES_IMPORT, "pr.csv", content, dry_run=True)
    body = r.json()
    assert body["shape"] == "long", body
    assert body["counts"]["error"] == 0, body["rows"]

    r = _import(client, admin_headers, PRICES_IMPORT, "pr.csv", content, dry_run=False)
    assert r.json()["applied"] is True

    rows = _price_rows(cust, "VG001")
    assert len(rows) == 1
    assert rows[0].price == 3.75
    assert rows[0].valid_until is None, "a fresh price is open-ended"


def test_price_import_accepts_a_currency_symbol_and_separators(client, admin_headers):
    """A price column really does contain "¥1,234.50" and "12元"."""
    content = _csv_bytes([
        ["客户编码", "商品编码", "单位", "单价"],
        ["C003", "VG002", "斤", "¥1,234.50"],
    ])
    r = _import(client, admin_headers, PRICES_IMPORT, "pr.csv", content, dry_run=True)
    assert r.json()["counts"]["error"] == 0, r.json()["rows"]
    assert r.json()["rows"][0]["values"]["price"] == 1234.5


def test_price_reimport_supersedes_and_keeps_history(client, admin_headers):
    """Re-importing a price must close the old row, not overwrite it.

    An order already confirmed locked the old price onto its lines; erasing the
    row would make that invoice unexplainable.
    """
    cust = _own_customer(client, admin_headers, "IMP-SUP-1")
    first = _csv_bytes([["客户编码", "商品编码", "单位", "单价"], [cust, "VG003", "斤", "4.00"]])
    second = _csv_bytes([["客户编码", "商品编码", "单位", "单价"], [cust, "VG003", "斤", "4.60"]])
    assert _import(client, admin_headers, PRICES_IMPORT, "a.csv", first, dry_run=False).json()["applied"]
    assert _import(client, admin_headers, PRICES_IMPORT, "b.csv", second, dry_run=False).json()["applied"]

    rows = _price_rows(cust, "VG003")
    assert len(rows) == 2, "the superseded price must survive as history"
    open_rows = [r for r in rows if r.valid_until is None]
    assert len(open_rows) == 1, "exactly one price may be open at a time"
    assert open_rows[0].price == 4.60


def test_price_for_an_unknown_customer_is_refused_not_invented(client, admin_headers):
    """The importer must never create a customer as a side effect of a price
    file — a typo'd code would silently become a real trading partner."""
    before = _count(Customer)
    content = _csv_bytes([
        ["客户编码", "商品编码", "单位", "单价"],
        ["NO-SUCH-CUSTOMER", "VG001", "斤", "3.00"],
    ])
    r = _import(client, admin_headers, PRICES_IMPORT, "pr.csv", content, dry_run=True)
    body = r.json()
    assert body["counts"]["error"] >= 1
    assert any("not found" in w for w in body["rows"][0]["warnings"]), body["rows"][0]
    assert _count(Customer) == before


def test_price_borrows_the_product_default_unit(client, admin_headers):
    """contract_prices.unit_id is NOT NULL, so a price with no unit column has
    to land on the product's own default unit rather than an invented one."""
    cust = _own_customer(client, admin_headers, "IMP-UNIT-1")
    content = _csv_bytes([
        ["客户编码", "商品编码", "单价"],
        [cust, "MT001", "20.00"],
    ])
    r = _import(client, admin_headers, PRICES_IMPORT, "pr.csv", content, dry_run=False)
    body = r.json()
    assert body["counts"]["error"] == 0, body["rows"]
    with SessionLocal() as db:
        prod = db.query(Product).filter(Product.sku == "MT001").one()
        owner = db.query(Customer).filter(Customer.code == cust).one()
        row = db.query(ContractPrice).filter(
            ContractPrice.customer_id == owner.id,
            ContractPrice.product_id == prod.id,
        ).one()
        assert row.unit_id == prod.default_unit_id


# ---------------------------------------------------------------------------
# Prices — wide matrix
# ---------------------------------------------------------------------------
def test_price_matrix_is_detected_and_unpivoted(client, admin_headers):
    """Customers down the rows, products across the columns — the shape most
    Chinese F&B price sheets actually use."""
    c1 = _own_customer(client, admin_headers, "IMP-MX-1")
    c2 = _own_customer(client, admin_headers, "IMP-MX-2")
    content = _csv_bytes([
        ["客户", "土豆(斤)", "大白菜(斤)"],
        [c1, "2.35", "1.60"],
        [c2, "2.40", "1.65"],
    ])
    r = _import(client, admin_headers, PRICES_IMPORT, "matrix.csv", content, dry_run=True)
    body = r.json()
    assert body["shape"] == "matrix", body
    # 2 customers × 2 products = 4 prices
    assert body["counts"]["error"] == 0, body["rows"]
    assert body["counts"]["update"] == 4, body["rows"]
    keys = {row["key"] for row in body["rows"]}
    assert f"{c1} × 土豆" in keys and f"{c2} × 大白菜" in keys, keys

    r = _import(client, admin_headers, PRICES_IMPORT, "matrix.csv", content, dry_run=False)
    assert r.json()["applied"] is True, r.json()

    row = next(
        (p for p in _price_rows(c2, "VG002") if p.valid_until is None),  # 大白菜
        None,
    )
    assert row is not None and row.price == 1.65


def test_price_matrix_reads_the_unit_from_the_column_heading(client, admin_headers):
    """"土豆(斤)" means priced per jin. If the parentheses do not name a unit we
    already know, they are part of the product's real name and must be kept."""
    content = _csv_bytes([["客户", "土豆(斤)"], ["C001", "2.50"]])
    r = _import(client, admin_headers, PRICES_IMPORT, "m.csv", content, dry_run=True)
    assert r.json()["shape"] == "matrix"
    assert r.json()["counts"]["error"] == 0, r.json()["rows"]
    # "大白菜" is not a unit, so a heading like it keeps its text intact.
    content2 = _csv_bytes([["客户", "大白菜"], ["C001", "1.70"]])
    r2 = _import(client, admin_headers, PRICES_IMPORT, "m2.csv", content2, dry_run=True)
    assert r2.json()["counts"]["error"] == 0, r2.json()["rows"]
    assert r2.json()["rows"][0]["values"]["product"] == "大白菜"


def test_price_matrix_skips_empty_cells(client, admin_headers):
    """A blank cell means "no price agreed", not zero."""
    content = _csv_bytes([
        ["客户", "土豆(斤)", "大白菜(斤)"],
        ["C001", "", "1.60"],
    ])
    r = _import(client, admin_headers, PRICES_IMPORT, "m.csv", content, dry_run=True)
    body = r.json()
    assert body["counts"]["update"] == 1, body["rows"]
    assert body["rows"][0]["values"]["product"] == "大白菜"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def test_commit_refuses_a_file_that_changed_since_the_preview(client, admin_headers):
    """The reviewed file must be the written file."""
    previewed = _csv_bytes([
        ["客户编码", "客户名称"], ["IMP-SHA-1", "预览时的名字"],
    ])
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", previewed, dry_run=True)
    sha = r.json()["content_sha256"]

    changed = _csv_bytes([
        ["客户编码", "客户名称"], ["IMP-SHA-1", "偷偷换过的名字"],
    ])
    r = _import(
        client, admin_headers, CUSTOMERS_IMPORT, "c.csv", changed,
        dry_run=False, options=f'{{"sha256": "{sha}"}}',
    )
    assert r.status_code == 400, r.text
    assert "not the one that was reviewed" in r.json()["detail"]
    assert _find_customer("IMP-SHA-1") is None


def test_commit_accepts_the_same_file_with_its_hash(client, admin_headers):
    content = _csv_bytes([["客户编码", "客户名称"], ["IMP-SHA-2", "哈希一致的客户"]])
    sha = _import(
        client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=True
    ).json()["content_sha256"]
    r = _import(
        client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content,
        dry_run=False, options=f'{{"sha256": "{sha}"}}',
    )
    assert r.status_code == 200, r.text
    assert r.json()["applied"] is True
    assert _find_customer("IMP-SHA-2") is not None


def test_an_explicit_mapping_overrides_the_proposal(client, admin_headers):
    """The mapping is the interface: a file whose headers we cannot recognise
    is a decision the operator makes, not a code change."""
    content = _csv_bytes([
        ["A", "B"],
        ["IMP-MAP-1", "自定义表头客户"],
    ])
    # Without a mapping, "A"/"B" match nothing.
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=True)
    assert r.json()["counts"]["error"] >= 1

    mapping = '{"A": "code", "B": "name_zh"}'
    r = _import(
        client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content,
        dry_run=True, mapping=mapping,
    )
    body = r.json()
    assert body["counts"]["create"] == 1, body
    assert body["mapping"] == {"A": "code", "B": "name_zh"}


def test_a_mapping_naming_an_unknown_field_is_refused(client, admin_headers):
    content = _csv_bytes([["A", "B"], ["x", "y"]])
    r = _import(
        client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content,
        dry_run=True, mapping='{"A": "code", "B": "not_a_field"}',
    )
    assert r.status_code == 400
    assert "Unknown" in r.json()["detail"]


def test_two_columns_cannot_claim_one_field(client, admin_headers):
    content = _csv_bytes([["A", "B"], ["x", "y"]])
    r = _import(
        client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content,
        dry_run=True, mapping='{"A": "code", "B": "code"}',
    )
    assert r.status_code == 400
    assert "only come from one column" in r.json()["detail"]


def test_an_unparseable_file_is_reported_not_crashed(client, admin_headers):
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", b"")
    assert r.status_code == 400
    assert "empty" in r.json()["detail"].lower()


def test_the_legacy_xls_format_says_what_to_do(client, admin_headers):
    """openpyxl cannot read .xls. Failing with a zip error would be useless."""
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "old.xls", b"\xd0\xcf\x11\xe0junk")
    assert r.status_code == 400
    assert ".xlsx" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Audit + auth
# ---------------------------------------------------------------------------
def test_a_committed_import_writes_one_batch_audit_row(client, admin_headers):
    """The per-row changes each get their own audit entry from the services.
    This is the summary that makes 1000 rows findable as one event."""
    from app.models import AuditLog

    content = _csv_bytes([["客户编码", "客户名称"], ["IMP-AUDIT-1", "审计客户"]])
    sha = _import(
        client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=True
    ).json()["content_sha256"]
    _import(client, admin_headers, CUSTOMERS_IMPORT, "c.csv", content, dry_run=False)

    with SessionLocal() as db:
        row = db.query(AuditLog).filter(
            AuditLog.entity_type == "ImportBatch",
            AuditLog.entity_id == sha[:36],
        ).first()
    assert row is not None, "an import must be findable as a batch"
    assert row.action == "import"
    assert row.after["counts"]["create"] == 1
    assert "customers" in row.summary


@pytest.mark.parametrize(
    "path,filename,content",
    [
        (CUSTOMERS_IMPORT, "c.csv", _csv_bytes([["客户编码"], ["X"]])),
        (PRODUCTS_IMPORT, "p.csv", _csv_bytes([["商品编码"], ["X"]])),
        (PRICES_IMPORT, "pr.csv", _csv_bytes([["客户", "商品", "单价"], ["a", "b", "1"]])),
    ],
)
def test_import_endpoints_require_ops_or_admin(client, warehouse_headers, path, filename, content):
    """Warehouse and driver must not be able to rewrite master data."""
    r = _import(client, warehouse_headers, path, filename, content, dry_run=True)
    assert r.status_code == 403, r.text


def test_import_endpoints_are_unauthenticated_without_a_token(client):
    r = client.post(CUSTOMERS_IMPORT)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Scale — the reason this feature exists
# ---------------------------------------------------------------------------
def test_a_thousand_rows_import_correctly(client, admin_headers):
    """The whole point is loading an existing customer database in one go.

    Measured on this machine: preview 0.12s, commit 0.54s for 1000 rows. The
    bound below is deliberately ~50x that — loose enough never to flake on a
    slow machine, tight enough to catch the regression that matters, which is a
    per-row full-table scan turning this quadratic. A timing assertion is a
    measurement, not an invariant, so it is set where only a real change trips
    it.
    """
    rows = [["客户编码", "客户名称", "电话"]]
    rows += [[f"SCALE-{i:04d}", f"规模测试客户{i}", f"138{i:08d}"] for i in range(1000)]
    content = _csv_bytes(rows)

    started = time.monotonic()
    r = _import(client, admin_headers, CUSTOMERS_IMPORT, "big.csv", content, dry_run=True)
    preview_seconds = time.monotonic() - started
    body = r.json()
    assert r.status_code == 200, r.text
    assert body["counts"]["create"] == 1000, body["counts"]
    assert len(body["rows"]) == 1000
    assert body["header_row_number"] == 1
    assert preview_seconds < 30, f"preview took {preview_seconds:.1f}s for 1000 rows"

    started = time.monotonic()
    r2 = _import(
        client, admin_headers, CUSTOMERS_IMPORT, "big.csv", content,
        dry_run=False, options=f'{{"sha256": "{body["content_sha256"]}"}}',
    )
    commit_seconds = time.monotonic() - started
    assert r2.json()["applied"] is True, r2.text
    assert r2.json()["counts"]["create"] == 1000, r2.json()["counts"]
    assert commit_seconds < 60, f"commit took {commit_seconds:.1f}s for 1000 rows"

    # Spot-check both ends of the file, not just the first row.
    assert _find_customer("SCALE-0000") is not None
    assert _find_customer("SCALE-0999") is not None


def test_a_large_price_matrix_reports_every_cell(client, admin_headers):
    """50 customers x 5 products = 250 prices from a 51-line file.

    Every cell is its own row in the report, which is what makes a wide sheet
    reviewable: the operator sees the unpivoted result before it is written.
    """
    customers = [_own_customer(client, admin_headers, f"SCALEM-{i:02d}") for i in range(50)]
    # Products must already exist: a price file never invents the product it
    # prices, so a heading naming something unknown is an error, not a creation.
    products = ["土豆", "大白菜", "番茄", "黄瓜", "胡萝卜"]
    rows = [["客户"] + [f"{p}(斤)" for p in products]]
    for index, code in enumerate(customers):
        rows.append([code] + [f"{1 + index * 0.01:.2f}"] * len(products))
    content = _csv_bytes(rows)

    r = _import(client, admin_headers, PRICES_IMPORT, "matrix.csv", content, dry_run=True)
    body = r.json()
    assert body["shape"] == "matrix", body
    assert body["counts"]["error"] == 0, body["rows"][:3]
    assert body["counts"]["update"] == 250, body["counts"]
    assert len(body["rows"]) == 250
    assert body["rows"][0]["values"]["unit"] == "斤"

    r2 = _import(client, admin_headers, PRICES_IMPORT, "matrix.csv", content, dry_run=False)
    assert r2.json()["applied"] is True, r2.text
