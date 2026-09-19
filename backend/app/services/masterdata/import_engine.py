"""MASTER DATA IMPORT — owner: master data agent.

Batch-load customers, products and prices from whatever the customer's current
system already exports. The whole design follows from one fact: **we do not know
their format**, and we never will — every ERP, every accountant and every
hand-kept spreadsheet has its own headers, its own column order and its own
language.

So the importer does not try to guess a format. It does three things instead:

1. **Parse anything** — CSV (with the encoding cascade real Chinese Excel
   exports need) or XLSX.
2. **Propose a column mapping and let a human confirm it.** Synonyms cover the
   common headers; anything unrecognised is shown as unmapped rather than
   silently dropped. The mapping is the interface, so an unknown format is a
   five-second decision rather than a code change.
3. **Show every row's fate before writing anything.** A dry run returns a
   per-row verdict — create / update / skip / error — with the reason. Nothing
   touches the database until the same file is submitted with `dry_run=False`.

Two shapes of price list are supported, because both are common:

- **Long**: one row per customer × product, with `customer`, `product`, `price`
  columns. Maps straight onto `contract_prices`.
- **Matrix**: customers down the rows, products across the columns. Detected by
  the absence of a product column — see `detect_shape`.

Everything writes through the existing master-data services
(`create_customer`, `update_product`, `create_contract_price`, …). That is
deliberate: those already write an `audit_logs` row per change with the before
and after values, so an import is traceable and reversible through the audit
trail without a single new table.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models import (
    ContractPrice,
    Customer,
    Product,
    ProductCategory,
    Unit,
    User,
)
from app.services.masterdata import catalog as catalog_svc
from app.services.masterdata import contracts as contracts_svc
from app.services.masterdata import customers as customers_svc

# --- Limits -------------------------------------------------------------------
# A cap rather than a promise: a 200k-row upload is a mistake or a hostile file,
# and the honest thing is to refuse it with a reason instead of timing out
# halfway through and leaving the operator unsure what was written.
MAX_ROWS = 50_000
# How far down the sheet to look for the header row. Real exports from Chinese
# ERP systems routinely put a title, a date and a blank line above the real
# header, so row 1 is often not the header.
HEADER_SCAN_ROWS = 10

# Encodings to try, in order. `utf-8-sig` first because Excel writes a BOM on
# every "Save as CSV UTF-8"; without it the first header keeps an invisible
# \ufeff and no synonym ever matches. `gbk`/`gb18030` because that is what a
# Chinese Excel writes by default. Same cascade as the intake parser
# (`app/ai/adapters.py`), which learned this the hard way.
_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030")


# =============================================================================
# Table parsing
# =============================================================================
@dataclass
class ParsedTable:
    """A parsed sheet: a header row plus the data rows beneath it."""

    headers: list[str]
    rows: list[list[str]]
    sheet_name: str | None = None
    encoding: str | None = None
    header_row_number: int = 1


class ImportError_(ValueError):
    """A file-level problem the operator has to fix (not a row problem)."""


def parse_form_json(raw: str | None, *, what: str) -> dict[str, Any]:
    """Parse a JSON blob sent alongside a multipart upload.

    Multipart has no nested types, so a column mapping or an options object has
    to travel as a JSON string in a form field. An unparseable one raises rather
    than being ignored: silently dropping a mapping would import every row
    through whatever columns the *proposal* happened to pick, which is the
    wrong-answer-right-column failure this whole module exists to avoid.
    """
    if raw is None or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImportError_(f"{what} is not valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ImportError_(f"{what} must be a JSON object.")
    return value


def parse_table(filename: str, content: bytes) -> ParsedTable:
    """Parse CSV/XLSX bytes into a header row + data rows.

    Raises `ImportError_` for anything that makes the file unusable as a whole —
    empty, unsupported extension, unreadable. Per-row problems are collected
    later by `validate_rows` and never raise.
    """
    if not content:
        raise ImportError_("The uploaded file is empty.")

    name = (filename or "").lower().strip()
    if name.endswith((".xlsx", ".xlsm")):
        headers, rows, sheet, header_row = _parse_xlsx(content)
        table = ParsedTable(
            headers=headers, rows=rows, sheet_name=sheet, header_row_number=header_row
        )
    elif name.endswith(".xls"):
        # openpyxl cannot read the legacy binary .xls format, and pretending
        # otherwise would fail deep inside a zip error the operator cannot act
        # on. Say what to do instead.
        raise ImportError_(
            "The old .xls format is not supported. Open the file in Excel and "
            "save it as .xlsx or .csv, then upload again."
        )
    else:
        headers, rows, encoding, header_row = _parse_csv(content)
        table = ParsedTable(
            headers=headers, rows=rows, encoding=encoding, header_row_number=header_row
        )

    if not table.headers:
        raise ImportError_(
            "No header row found. The file must have a row of column names."
        )
    if len(table.rows) > MAX_ROWS:
        raise ImportError_(
            f"The file has {len(table.rows)} data rows; the limit is {MAX_ROWS}. "
            "Split it into smaller files."
        )
    return table


def _parse_csv(content: bytes) -> tuple[list[str], list[list[str]], str]:
    text: str | None = None
    used = ""
    for enc in _ENCODINGS:
        try:
            text = content.decode(enc)
            used = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        # Last resort: never lose the file, but the replacement characters will
        # show up in the preview so the operator can see the encoding is wrong.
        text = content.decode("utf-8", errors="replace")
        used = "utf-8 (with replacements)"

    reader = list(csv.reader(io.StringIO(text)))
    headers, rows, header_row = _split_header([[c for c in row] for row in reader])
    return headers, rows, used, header_row


def _parse_xlsx(content: bytes) -> tuple[list[str], list[list[str]], str | None]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - openpyxl is pinned
        raise ImportError_("XLSX support is unavailable on this server.") from exc

    try:
        wb = load_workbook(filename=io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - explain it rather than leak a zip error
        raise ImportError_(
            "That file could not be read as an .xlsx workbook. If it was exported "
            "from another system, re-save it as .xlsx or .csv."
        ) from exc

    try:
        sheet = wb.active
        sheet_name = sheet.title if sheet is not None else None
        raw: list[list[str]] = []
        if sheet is not None:
            for row in sheet.iter_rows(values_only=True):
                raw.append([_cell_to_str(c) for c in row])
    finally:
        wb.close()

    headers, rows, header_row = _split_header(raw)
    return headers, rows, sheet_name, header_row


def _cell_to_str(value: Any) -> str:
    """Render a cell without inventing precision Excel did not have.

    A date cell arrives as a `datetime`; `str()` gives "2026-09-19 00:00:00",
    which the date parser would then have to unpick. Format it as a date
    directly. A float that is integral is rendered without the ".0" so that a
    code column holding 1001 does not become "1001.0" and miss its match.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _split_header(raw: list[list[str]]) -> tuple[list[str], list[list[str]], int]:
    """Choose the header row and return it, everything below it, and its number.

    The header is the row among the first `HEADER_SCAN_ROWS` that names the most
    *fields* — not simply the first non-empty row. An export that opens with a
    merged title ("2026年客户资料表") and a blank line would otherwise treat the
    title as the header and map nothing.

    The row number is 1-based and travels with the result because every row
    number the operator is shown is derived from it. Reporting "row 2" for the
    first data row of a file whose header sits on row 3 sends them to the wrong
    line of their own spreadsheet — the file is right, the pointer is wrong.
    """
    candidates = [
        i for i, row in enumerate(raw[:HEADER_SCAN_ROWS]) if _non_empty(row) >= 2
    ]
    if not candidates:
        # Fall back to the first row with anything in it, so a single-column
        # file still produces a readable error rather than "no header".
        candidates = [i for i, row in enumerate(raw[:HEADER_SCAN_ROWS]) if _non_empty(row)]
    if not candidates:
        return [], [], 1

    best = max(candidates, key=lambda i: (_header_score(raw[i]), _non_empty(raw[i])))
    header = [h.strip() for h in raw[best]]
    rows = [r for r in raw[best + 1 :] if _non_empty(r)]
    # Trim trailing empty header cells so a stray formatted column does not
    # become an unnamed column in the mapping UI.
    while header and not header[-1]:
        header.pop()
    return header, rows, best + 1


def _header_score(row: list[str]) -> int:
    """How many of this row's cells look like a field name we know."""
    return sum(1 for c in row if _looks_like_a_header(c))


def _looks_like_a_header(cell: str) -> bool:
    """Whether a cell is plausibly a column *name*, not a value.

    The synonym match is deliberately permissive (a real header is often
    "客户名称(必填)" or "单价 元"), which means a long *value* that happens to
    embed a short field name — "自定义表头客户" contains "客户" — also matches.
    Left alone, that false positive makes a data row outscore the real header
    row, and the header detector then picks the data row. The consequence is
    not cosmetic: the operator's column mapping is rejected with "these columns
    are not in the file", naming columns that are plainly on screen.

    So require the matched synonym to account for most of the cell. A genuine
    header is mostly field name; a value that merely contains one is mostly
    something else.
    """
    text = normalise_header(cell)
    if not text:
        return False
    found = _match_synonym(cell, _ALL_SYNONYMS)
    if found is None:
        return False
    synonym, _target = found
    return len(synonym) * 2 >= len(text)


def _non_empty(row: Iterable[str]) -> int:
    return sum(1 for c in row if c and c.strip())


# =============================================================================
# Header normalisation
# =============================================================================
def normalise_header(value: str) -> str:
    """Fold a header down to something comparable.

    NFKC first, so full-width characters ("客户名称" typed with full-width
    parentheses, or "ＳＫＵ") fold to their ASCII/full-width-normal equivalents —
    a Chinese Excel export mixes the two freely and an exact match then fails
    for reasons invisible on screen.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    for ch in " \t\r\n_-–—/\\()（）[]【】.:：,，*　":
        text = text.replace(ch, "")
    return text


# =============================================================================
# Field specs
# =============================================================================
@dataclass(frozen=True)
class FieldSpec:
    name: str
    synonyms: tuple[str, ...]
    required: bool = False
    # "text" | "number" | "int" | "date" | "bool"
    kind: str = "text"
    help: str = ""


@dataclass(frozen=True)
class EntitySpec:
    kind: str
    label: str
    fields: tuple[FieldSpec, ...]
    # The field(s) that identify an existing row for upsert purposes.
    key_fields: tuple[str, ...]

    def field(self, name: str) -> FieldSpec | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]


CUSTOMER_SPEC = EntitySpec(
    kind="customers",
    label="Customers",
    key_fields=("code",),
    fields=(
        FieldSpec(
            "code", required=True,
            synonyms=(
                "code", "customer code", "cust code", "customer id", "cust id", "id",
                "account", "account code", "客户编码", "客户编号", "客户代码",
                "客户档案编号", "编码", "编号", "档案编号", "代码",
            ),
            help="The customer's unique code. Used to match an existing customer.",
        ),
        FieldSpec(
            "name_zh",
            synonyms=(
                "客户名称", "客户姓名", "客户名", "客户", "中文名称", "中文名", "名称",
                "name zh", "chinese name", "name chinese", "name",
            ),
            help="Chinese name.",
        ),
        FieldSpec(
            "name_en",
            synonyms=("english name", "name en", "english", "英文名称", "英文名", "英文"),
            help="English name.",
        ),
        FieldSpec(
            "type",
            synonyms=("customer type", "type", "客户类型", "类型", "业态", "客户业态"),
            help="school | restaurant | canteen | other",
        ),
        FieldSpec(
            "contact_name",
            synonyms=("contact", "contact name", "联系人", "联络人", "联系人姓名", "负责人"),
        ),
        FieldSpec(
            "contact_phone",
            synonyms=(
                "phone", "tel", "telephone", "mobile", "contact phone", "电话",
                "联系电话", "手机", "手机号", "电话号码", "联系方式",
            ),
        ),
        FieldSpec("address", synonyms=("address", "地址", "送货地址", "详细地址", "收货地址")),
        FieldSpec(
            "delivery_zone",
            synonyms=(
                "delivery zone", "zone", "route", "area", "区域", "配送区域", "片区",
                "路线", "配送路线", "送货区域", "线路",
            ),
        ),
        FieldSpec("notes", synonyms=("notes", "note", "remark", "备注", "说明", "备注说明")),
    ),
)

PRODUCT_SPEC = EntitySpec(
    kind="products",
    label="Products",
    key_fields=("sku",),
    fields=(
        FieldSpec(
            "sku", required=True,
            synonyms=(
                "sku", "code", "product code", "item code", "barcode", "ean", "id",
                "商品编码", "商品编号", "商品代码", "货号", "条码", "条形码", "编码", "编号",
            ),
            help="The product's unique code. Used to match an existing product.",
        ),
        FieldSpec(
            "name_zh",
            synonyms=(
                "商品名称", "品名", "名称", "商品", "产品名称", "中文名称", "中文名",
                "name zh", "chinese name", "product name", "name",
            ),
            help="Chinese name.",
        ),
        FieldSpec(
            "name_en",
            synonyms=("english name", "name en", "english", "英文名称", "英文名", "英文"),
            help="English name.",
        ),
        FieldSpec(
            "category",
            synonyms=("category", "product category", "分类", "类别", "商品分类", "品类", "商品类别"),
            help="Matched by name; created if it does not exist.",
        ),
        FieldSpec(
            "unit",
            synonyms=(
                "unit", "uom", "unit of measure", "单位", "计量单位", "规格单位", "基本单位",
            ),
            help="Matched by code or name; created if it does not exist.",
        ),
        FieldSpec(
            "shelf_life_days",
            synonyms=("shelf life", "shelf life days", "保质期", "保质期天数", "保存期限"),
            kind="int",
        ),
        FieldSpec("is_active", synonyms=("is active", "active", "enabled", "状态", "是否启用", "启用"), kind="bool"),
    ),
)

PRICE_SPEC = EntitySpec(
    kind="prices",
    label="Customer prices",
    key_fields=("customer", "product", "unit"),
    fields=(
        FieldSpec(
            "customer", required=True,
            synonyms=(
                "customer", "customer code", "customer name", "cust code", "account",
                "客户", "客户编码", "客户编号", "客户名称", "客户代码", "客户简称",
            ),
            help="Customer code or name.",
        ),
        FieldSpec(
            "product", required=True,
            synonyms=(
                "product", "product code", "product name", "sku", "item", "item code",
                "商品", "商品编码", "商品编号", "商品名称", "品名", "货号", "产品",
            ),
            help="Product SKU or name.",
        ),
        FieldSpec(
            "unit",
            synonyms=("unit", "uom", "单位", "计量单位", "规格单位", "基本单位"),
            help="Prices are per unit. Blank matches any unit.",
        ),
        FieldSpec(
            "price", required=True,
            synonyms=(
                "price", "unit price", "contract price", "rate", "amount",
                "价格", "单价", "报价", "合同价", "供货价", "售价", "协议价", "结算价",
            ),
            kind="number",
        ),
        FieldSpec(
            "valid_from",
            synonyms=("valid from", "start date", "from", "effective date", "生效日期", "开始日期", "起始日期", "生效时间"),
            kind="date",
        ),
        FieldSpec(
            "valid_until",
            synonyms=("valid until", "end date", "to", "expiry date", "失效日期", "结束日期", "到期日期", "截止日期"),
            kind="date",
        ),
    ),
)

SPECS: dict[str, EntitySpec] = {
    "customers": CUSTOMER_SPEC,
    "products": PRODUCT_SPEC,
    "prices": PRICE_SPEC,
}

# Fields a wide matrix supplies from its own structure rather than from a mapped
# column: the product is the heading and the price is the cell under it. The
# required-column check has to know this or every valid matrix looks broken.
_MATRIX_SUPPLIED_FIELDS: dict[str, frozenset[str]] = {
    "prices": frozenset({"product", "price"}),
}

# Every synonym across every spec, for header-row scoring.
_ALL_SYNONYMS: dict[str, str] = {}
for _spec in SPECS.values():
    for _f in _spec.fields:
        for _syn in _f.synonyms:
            _ALL_SYNONYMS.setdefault(normalise_header(_syn), f"{_spec.kind}.{_f.name}")


# =============================================================================
# Header mapping
# =============================================================================
def _match_field(header: str, synonyms: dict[str, str]) -> str | None:
    """Resolve one header to a field name, exact match first then containment."""
    found = _match_synonym(header, synonyms)
    return found[1] if found else None


def _match_synonym(header: str, synonyms: dict[str, str]) -> tuple[str, str] | None:
    """Resolve a header to `(matched synonym, field name)`.

    One implementation, because the header scorer needs the synonym that
    matched and the mapper needs the field: two copies of this logic would
    drift and the scorer would start disagreeing with the mapper about what a
    header is.
    """
    norm = normalise_header(header)
    if not norm:
        return None
    if norm in synonyms:
        return norm, synonyms[norm]
    # Containment, longest synonym first, so "客户名称" wins over "客户" for a
    # header like "客户名称（全称）" and "商品编码" is not stolen by "商品".
    best: tuple[int, str, str] | None = None
    for syn, target in synonyms.items():
        if len(syn) >= 2 and syn in norm:
            if best is None or len(syn) > best[0]:
                best = (len(syn), syn, target)
    return (best[1], best[2]) if best else None


def propose_mapping(headers: list[str], spec: EntitySpec) -> dict[str, str | None]:
    """Best guess at header → field. Unmapped headers map to None.

    Deliberately one-to-one in the other direction too: a field already taken by
    a better match is not offered twice, so "编码" cannot claim both `code` and
    `sku` on a file that has both columns.
    """
    synonyms: dict[str, str] = {}
    for f in spec.fields:
        for syn in f.synonyms:
            synonyms[normalise_header(syn)] = f.name

    mapping: dict[str, str | None] = {}
    claimed: set[str] = set()
    for header in headers:
        target = _match_field(header, synonyms)
        if target is not None and target in claimed:
            target = None
        if target is not None:
            claimed.add(target)
        mapping[header] = target
    return mapping


def resolve_mapping(
    headers: list[str], spec: EntitySpec, mapping: dict[str, str] | None
) -> dict[str, str | None]:
    """Use the operator's mapping when given, else the proposal.

    Validated rather than trusted: an unknown field name or two headers claiming
    one field is refused here, because silently dropping half a mapping is how
    an import writes the wrong thing to the right column.
    """
    if mapping is None:
        return propose_mapping(headers, spec)

    unknown = {h: f for h, f in mapping.items() if h not in headers}
    if unknown:
        raise ImportError_(
            "The mapping refers to columns that are not in the file: "
            + ", ".join(sorted(unknown))
        )
    bad_fields = {
        h: f
        for h, f in mapping.items()
        if f is not None and spec.field(f) is None
    }
    if bad_fields:
        raise ImportError_(
            f"Unknown {spec.label.lower()} field(s): "
            + ", ".join(sorted({str(f) for f in bad_fields.values()}))
        )
    seen: dict[str, str] = {}
    for header, target in mapping.items():
        if target is None:
            continue
        if target in seen:
            raise ImportError_(
                f"Both '{seen[target]}' and '{header}' are mapped to '{target}'. "
                "Each field can only come from one column."
            )
        seen[target] = header
    return mapping


# =============================================================================
# Shape detection (long vs wide matrix)
# =============================================================================
def detect_shape(headers: list[str], mapping: dict[str, str | None]) -> str:
    """`long` or `matrix`.

    The tell is a missing product column: a long price list names the product in
    a column, a matrix spreads products across the columns. If `customer` is
    mapped and `product` is not, the unmapped columns *are* the products.
    """
    mapped = set(mapping.values())
    if "customer" in mapped and "product" not in mapped:
        return "matrix"
    return "long"


# =============================================================================
# Coercion + reference resolution
# =============================================================================
def _coerce(raw: str, spec: FieldSpec) -> Any:
    """Turn a cell into the type the field wants, or raise with a reason."""
    text = (raw or "").strip()
    if not text:
        return None
    if spec.kind == "text":
        return text
    if spec.kind == "number":
        # Tolerate the things a price column really contains: a currency symbol,
        # thousands separators, and a full-width comma from a Chinese keyboard.
        cleaned = (
            text.replace(",", "").replace("，", "").replace("¥", "")
            .replace("￥", "").replace("$", "").replace("元", "").strip()
        )
        try:
            value = float(cleaned)
        except ValueError:
            raise ValueError(f"{spec.name}: '{text}' is not a number")
        if value < 0:
            raise ValueError(f"{spec.name}: '{text}' is negative")
        return value
    if spec.kind == "int":
        cleaned = text.replace(",", "").replace("，", "").strip()
        # "12个月" / "12 days" — keep the number, ignore the unit the operator
        # typed next to it.
        digits = ""
        for ch in cleaned:
            if ch.isdigit():
                digits += ch
            elif digits:
                break
        if not digits:
            raise ValueError(f"{spec.name}: '{text}' is not a whole number")
        return int(digits)
    if spec.kind == "date":
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        raise ValueError(
            f"{spec.name}: '{text}' is not a date (use YYYY-MM-DD)"
        )
    if spec.kind == "bool":
        low = text.lower()
        if low in ("1", "true", "yes", "y", "是", "启用", "active", "enabled"):
            return True
        if low in ("0", "false", "no", "n", "否", "停用", "inactive", "disabled"):
            return False
        raise ValueError(f"{spec.name}: '{text}' is not yes/no")
    return text


def _customer_index(db: Session) -> tuple[dict[str, Customer], dict[str, Customer]]:
    """(by code, by lowercased name in either language)."""
    by_code: dict[str, Customer] = {}
    by_name: dict[str, Customer] = {}
    for c in db.query(Customer).all():
        by_code[c.code.strip().lower()] = c
        for name in (c.name_en, c.name_zh):
            if name:
                by_name.setdefault(name.strip().lower(), c)
    return by_code, by_name


def _product_index(db: Session) -> tuple[dict[str, Product], dict[str, Product]]:
    by_sku: dict[str, Product] = {}
    by_name: dict[str, Product] = {}
    for p in db.query(Product).all():
        by_sku[p.sku.strip().lower()] = p
        for name in (p.name_en, p.name_zh):
            if name:
                by_name.setdefault(name.strip().lower(), p)
    return by_sku, by_name


def _unit_index(db: Session) -> dict[str, Unit]:
    """Units by code and by either name, all lowercased."""
    index: dict[str, Unit] = {}
    for u in db.query(Unit).all():
        index.setdefault(u.code.strip().lower(), u)
        for name in (u.name_en, u.name_zh):
            if name:
                index.setdefault(name.strip().lower(), u)
    return index


def _category_index(db: Session) -> dict[str, ProductCategory]:
    index: dict[str, ProductCategory] = {}
    for c in db.query(ProductCategory).all():
        for name in (c.name_en, c.name_zh):
            if name:
                index.setdefault(name.strip().lower(), c)
    return index


# =============================================================================
# Row results
# =============================================================================
@dataclass
class RowResult:
    row_number: int
    action: str  # create | update | error
    key: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    creates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "action": self.action,
            "key": self.key,
            "values": _jsonable(self.values),
            "errors": self.errors,
            "warnings": self.warnings,
            "creates": self.creates,
        }


def _jsonable(values: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in values.items():
        out[k] = v.isoformat() if isinstance(v, date) else v
    return out


@dataclass
class ImportReport:
    kind: str
    shape: str
    headers: list[str]
    mapping: dict[str, str | None]
    rows: list[RowResult]
    counts: dict[str, int]
    content_sha256: str
    # The field list travels with the report so the UI can render the mapping
    # step without a second round-trip. It is a required field, not an optional
    # one: `to_dict` projects it, and a projection that reads an attribute the
    # dataclass never stores is an AttributeError on every single request.
    spec: EntitySpec
    applied: bool = False
    encoding: str | None = None
    sheet_name: str | None = None
    header_row_number: int = 1
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing failed. Warnings do not make a file bad."""
        return self.counts.get("error", 0) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "shape": self.shape,
            "headers": self.headers,
            "mapping": self.mapping,
            "unmapped_headers": [h for h, f in self.mapping.items() if f is None],
            "rows": [r.to_dict() for r in self.rows],
            "counts": self.counts,
            "ok": self.ok,
            "applied": self.applied,
            "content_sha256": self.content_sha256,
            "encoding": self.encoding,
            "sheet_name": self.sheet_name,
            "header_row_number": self.header_row_number,
            "notes": self.notes,
            "spec": _spec_dict(self.spec),
        }


def _spec_dict(spec: EntitySpec) -> dict[str, Any]:
    return {
        "kind": spec.kind,
        "label": spec.label,
        "key_fields": list(spec.key_fields),
        "fields": [
            {
                "name": f.name,
                "required": f.required,
                "kind": f.kind,
                "help": f.help,
            }
            for f in spec.fields
        ],
    }


def run_import_request(
    db: Session,
    *,
    kind: str,
    filename: str,
    content: bytes,
    mapping_raw: str | None,
    options_raw: str | None,
    dry_run: bool,
    actor: User | None,
) -> dict[str, Any]:
    """Endpoint-facing wrapper: parse the form JSON, run, serialise.

    Exists so the three routers stay thin and cannot drift from one another —
    each one is the same six lines with a different `kind`.
    """
    mapping = parse_form_json(mapping_raw, what="mapping") or None
    options = parse_form_json(options_raw, what="options")
    report = run_import(
        db,
        kind=kind,
        filename=filename,
        content=content,
        mapping=mapping,
        dry_run=dry_run,
        actor=actor,
        options=options,
    )
    return report.to_dict()


# =============================================================================
# The import itself
# =============================================================================
def run_import(
    db: Session,
    *,
    kind: str,
    filename: str,
    content: bytes,
    mapping: dict[str, str | None] | None = None,
    dry_run: bool = True,
    actor: User | None = None,
    options: dict[str, Any] | None = None,
) -> ImportReport:
    """Validate (and optionally apply) a batch import.

    `dry_run=True` writes nothing — not a row, not an audit entry. That is the
    contract the UI relies on to show the operator what will happen, and it is
    asserted in the tests.
    """
    spec = SPECS.get(kind)
    if spec is None:
        raise ImportError_(f"Unknown import kind: {kind}")

    options = options or {}
    table = parse_table(filename, content)
    resolved = resolve_mapping(table.headers, spec, mapping)
    shape = detect_shape(table.headers, resolved) if kind == "prices" else "long"
    sha = hashlib.sha256(content).hexdigest()

    # The reviewed file must be the written file. A commit carries the hash the
    # preview returned; if the bytes differ, the operator is about to apply a
    # report that described a different file. Refuse rather than apply.
    expected_sha = str(options.get("sha256") or "").strip()
    if expected_sha and not dry_run and expected_sha != sha:
        raise ImportError_(
            "This file is not the one that was reviewed — its contents changed "
            "since the preview. Upload it again and check the preview before "
            "importing."
        )

    if shape == "matrix":
        results = _validate_matrix(db, spec, table, resolved, options)
    else:
        results = _validate_long(db, spec, table, resolved, options)

    counts: dict[str, int] = {"create": 0, "update": 0, "error": 0}
    for r in results:
        counts[r.action] = counts.get(r.action, 0) + 1

    report = ImportReport(
        kind=kind,
        shape=shape,
        headers=table.headers,
        mapping=resolved,
        rows=results,
        counts=counts,
        content_sha256=sha,
        spec=spec,
        encoding=table.encoding,
        sheet_name=table.sheet_name,
        header_row_number=table.header_row_number,
    )

    # A file that cannot be imported at all must not be applied half-way, and a
    # file whose shape we guessed wrong must not be applied at all.
    #
    # In `matrix` shape the product and the price are not mapped cells at all —
    # they *are* the column headings and the cells beneath them. Demanding them
    # as mapped columns would flag every valid matrix as an error, and because
    # `ok` gates the apply step, a matrix could never be committed at all.
    supplied_by_shape = _MATRIX_SUPPLIED_FIELDS.get(kind, frozenset()) if shape == "matrix" else frozenset()
    missing = [
        f.name
        for f in spec.fields
        if f.required
        and f.name not in set(resolved.values())
        and f.name not in supplied_by_shape
    ]
    if missing:
        report.notes.append(
            "These required columns are not mapped: "
            + ", ".join(missing)
            + ". Map them and try again."
        )
        report.counts["error"] = max(report.counts.get("error", 0), 1)
        return report

    if not dry_run and report.ok:
        _apply(db, spec, report, shape, options, actor)
        report.applied = True
        _write_batch_audit(db, spec, report, filename, actor)
    elif not dry_run and not report.ok:
        # Refuse rather than apply the good rows: a partial import leaves the
        # operator reconciling a half-loaded file by hand.
        report.notes.append(
            "Nothing was written — fix the errors above and upload again. "
            "A partial import is harder to reconcile than none."
        )
    return report


def _validate_long(
    db: Session,
    spec: EntitySpec,
    table: ParsedTable,
    mapping: dict[str, str | None],
    options: dict[str, Any],
) -> list[RowResult]:
    by_header = {h: i for i, h in enumerate(table.headers)}
    results: list[RowResult] = []
    seen_keys: dict[str, int] = {}
    # One shared index per run: resolved as we go so that two rows naming the
    # same new category create it once.
    ctx = _Context(db, options)

    for offset, row in enumerate(table.rows):
        row_number = table.header_row_number + offset + 1
        values: dict[str, Any] = {}
        errors: list[str] = []
        warnings: list[str] = []
        creates: list[str] = []

        for header, target in mapping.items():
            if target is None:
                continue
            idx = by_header.get(header)
            raw = row[idx] if idx is not None and idx < len(row) else ""
            fs = spec.field(target)
            if fs is None:
                continue
            try:
                coerced = _coerce(raw, fs)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if coerced is not None:
                values[target] = coerced

        key = str(values.get(spec.key_fields[0], "") or "").strip()
        if not key:
            errors.append(f"{spec.key_fields[0]} is required")

        if errors:
            results.append(RowResult(row_number, "error", key, values, errors, warnings, creates))
            continue

        # A code repeated inside one file: the second one would silently
        # overwrite the first, and the operator would never know which won.
        if key.lower() in seen_keys:
            results.append(RowResult(
                row_number, "error", key, values,
                [f"{key} also appears on row {seen_keys[key.lower()]} — "
                 "each code may only appear once per file"],
                warnings, creates,
            ))
            continue
        seen_keys[key.lower()] = row_number

        action, extra_warnings, extra_creates = _prepare(db, spec, ctx, values)
        warnings.extend(extra_warnings)
        creates.extend(extra_creates)
        results.append(RowResult(row_number, action, key, values, errors, warnings, creates))

    return results


def _validate_matrix(
    db: Session,
    spec: EntitySpec,
    table: ParsedTable,
    mapping: dict[str, str | None],
    options: dict[str, Any],
) -> list[RowResult]:
    """Unpivot a customers × products price grid into one row per cell.

    Every non-empty cell becomes a `(customer, product, price)` record. The unit
    comes from a unit column if one is mapped, otherwise from a unit written
    into the product header ("土豆(斤)"), otherwise from `default_unit`.
    """
    by_header = {h: i for i, h in enumerate(table.headers)}
    customer_header = next((h for h, f in mapping.items() if f == "customer"), None)
    if customer_header is None:
        raise ImportError_("A price matrix needs a customer column.")

    unit_header = next((h for h, f in mapping.items() if f == "unit"), None)
    # The product columns are the ones the mapping did not claim.
    product_headers = [
        h for h in table.headers if mapping.get(h) is None and h.strip()
    ]
    if not product_headers:
        raise ImportError_(
            "No product columns found. A price matrix needs product names as "
            "column headings, or a product column for the long format."
        )

    unit_index = _unit_index(db)
    default_unit = (options.get("default_unit") or "").strip()
    ctx = _Context(db, options)
    results: list[RowResult] = []

    for offset, row in enumerate(table.rows):
        row_number = table.header_row_number + offset + 1
        cidx = by_header.get(customer_header)
        customer_raw = row[cidx] if cidx is not None and cidx < len(row) else ""
        customer_text = (customer_raw or "").strip()
        if not customer_text:
            continue  # a blank customer row is a spacer, not an error

        for pheader in product_headers:
            pidx = by_header.get(pheader)
            raw = row[pidx] if pidx is not None and pidx < len(row) else ""
            if not (raw or "").strip():
                continue  # an empty cell means "no price agreed", not zero

            errors: list[str] = []
            warnings: list[str] = []
            creates: list[str] = []
            values: dict[str, Any] = {"customer": customer_text}

            product_name, unit_hint = _split_unit_hint(pheader, unit_index)
            values["product"] = product_name

            if unit_header is not None:
                uidx = by_header.get(unit_header)
                unit_text = (row[uidx] if uidx is not None and uidx < len(row) else "").strip()
            else:
                unit_text = unit_hint or default_unit
            if unit_text:
                values["unit"] = unit_text

            try:
                values["price"] = _coerce(raw, spec.field("price"))  # type: ignore[arg-type]
            except ValueError as exc:
                errors.append(str(exc))

            if "price" not in values or values["price"] is None:
                errors.append("price is required")

            if errors:
                results.append(RowResult(
                    row_number, "error", f"{customer_text} × {product_name}",
                    values, errors, warnings, creates,
                ))
                continue

            action, extra_warnings, extra_creates = _prepare(db, spec, ctx, values)
            warnings.extend(extra_warnings)
            creates.extend(extra_creates)
            results.append(RowResult(
                row_number, action, f"{customer_text} × {product_name}",
                values, warnings=warnings, creates=creates,
            ))

    return results


def _split_unit_hint(
    header: str, unit_index: dict[str, Unit]
) -> tuple[str, str | None]:
    """Pull a unit out of a product column heading.

    "土豆(斤)" / "土豆（斤）" / "土豆 斤" all mean the same product priced per jin.
    The unit is only accepted if it matches a unit we already know — otherwise
    the parentheses are part of the product's real name and stripping them would
    look for a product that does not exist.
    """
    text = header.strip()
    for open_ch, close_ch in (("(", ")"), ("（", "）")):
        if open_ch in text and text.rstrip().endswith(close_ch):
            head, _, tail = text.rpartition(open_ch)
            candidate = tail.rstrip(close_ch).strip()
            if candidate and candidate.lower() in unit_index:
                return head.strip(), candidate
    parts = text.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].lower() in unit_index:
        return parts[0].strip(), parts[1]
    return text, None


class _Context:
    """Per-run resolution cache.

    Two rows naming the same new category must create it once. The cache also
    means a 1000-row file does not re-query the whole table per row.
    """

    def __init__(self, db: Session, options: dict[str, Any]) -> None:
        self.db = db
        self.options = options
        self.customer_by_code, self.customer_by_name = _customer_index(db)
        self.product_by_sku, self.product_by_name = _product_index(db)
        self.units = _unit_index(db)
        self.categories = _category_index(db)
        self.pending_units: dict[str, Unit] = {}
        self.pending_categories: dict[str, ProductCategory] = {}

    def find_customer(self, text: str) -> Customer | None:
        low = text.strip().lower()
        return self.customer_by_code.get(low) or self.customer_by_name.get(low)

    def find_product(self, text: str) -> Product | None:
        low = text.strip().lower()
        return self.product_by_sku.get(low) or self.product_by_name.get(low)

    def find_unit(self, text: str) -> Unit | None:
        low = text.strip().lower()
        return self.units.get(low) or self.pending_units.get(low)

    def find_category(self, text: str) -> ProductCategory | None:
        low = text.strip().lower()
        return self.categories.get(low) or self.pending_categories.get(low)


def _prepare(
    db: Session, spec: EntitySpec, ctx: _Context, values: dict[str, Any]
) -> tuple[str, list[str], list[str]]:
    """Decide create vs update for one row and flag anything odd.

    Never writes. References that will have to be created are reported in
    `creates` so the preview can say so before anything happens.
    """
    warnings: list[str] = []
    creates: list[str] = []
    auto_create = bool(ctx.options.get("create_missing_refs", True))

    if spec.kind == "customers":
        existing = ctx.customer_by_code.get(str(values.get("code", "")).lower())
        _mirror_names(values, warnings)
        return ("update" if existing else "create"), warnings, creates

    if spec.kind == "products":
        existing = ctx.product_by_sku.get(str(values.get("sku", "")).lower())
        _mirror_names(values, warnings)
        if values.get("category"):
            cat = ctx.find_category(str(values["category"]))
            if cat is None:
                if auto_create:
                    creates.append(f"category: {values['category']}")
                else:
                    warnings.append(
                        f"category '{values['category']}' does not exist and "
                        "auto-create is off — the product will be created without one"
                    )
        if values.get("unit"):
            unit = ctx.find_unit(str(values["unit"]))
            if unit is None:
                if auto_create:
                    creates.append(f"unit: {values['unit']}")
                else:
                    warnings.append(
                        f"unit '{values['unit']}' does not exist and auto-create "
                        "is off — the product will be created without one"
                    )
        return ("update" if existing else "create"), warnings, creates

    # prices
    #
    # A price file never invents the customer or the product it prices — a
    # typo'd code must not become a real trading partner, and a customer cannot
    # be created from a code alone anyway (name_en and name_zh are NOT NULL).
    # `_apply_price` skips such a row, so it has to be an *error* here too: the
    # preview drives `ok`, `ok` gates the apply step, and a row reported as an
    # "update" that is then silently skipped is a preview that lies about what
    # the commit will do. The operator would see "1 update, 0 errors", confirm,
    # and get a green result that wrote nothing.
    missing_refs: list[str] = []
    if ctx.find_customer(str(values.get("customer", ""))) is None:
        missing_refs.append(f"customer '{values.get('customer')}'")
    if ctx.find_product(str(values.get("product", ""))) is None:
        missing_refs.append(f"product '{values.get('product')}'")
    if missing_refs:
        warnings.append(
            " and ".join(missing_refs)
            + " not found — no price will be imported for this row. Import the "
            "customer/product first, or correct the code."
        )
        return "error", warnings, creates
    if values.get("unit") and ctx.find_unit(str(values["unit"])) is None:
        if auto_create:
            creates.append(f"unit: {values['unit']}")
        else:
            warnings.append(
                f"unit '{values['unit']}' does not exist — the price will be "
                "stored without a unit and will only match unit-less order lines"
            )
    # A price is always an update in effect: `create_contract_price` supersedes
    # the open row for the same (customer, product, unit). Saying "create" would
    # be technically true and practically misleading, so report what it means.
    return "update", warnings, creates


def _mirror_names(values: dict[str, Any], warnings: list[str]) -> None:
    """Fill the missing language from the one that is present.

    `name_en` and `name_zh` are both NOT NULL on Customer and Product, so a file
    with a single name column cannot be loaded as-is. Copying is better than
    refusing — but it is flagged on every affected row, because a Chinese name
    sitting in the English field is something a human should eventually fix and
    an unflagged copy is indistinguishable from real data.
    """
    zh = (values.get("name_zh") or "").strip()
    en = (values.get("name_en") or "").strip()
    if zh and not en:
        values["name_en"] = zh
        warnings.append(f"name_en filled from the Chinese name ('{zh}')")
    elif en and not zh:
        values["name_zh"] = en
        warnings.append(f"name_zh filled from the English name ('{en}')")


# =============================================================================
# Applying
# =============================================================================
def _apply(
    db: Session,
    spec: EntitySpec,
    report: ImportReport,
    shape: str,
    options: dict[str, Any],
    actor: User | None,
) -> None:
    """Write every non-error row through the existing master-data services.

    Reuses `create_customer` / `update_product` / `create_contract_price` rather
    than inserting directly, so each change gets its `audit_logs` row with the
    before and after values, and every existing validation still applies.
    """
    ctx = _Context(db, options)
    auto_create = bool(options.get("create_missing_refs", True))

    for result in report.rows:
        if result.action == "error":
            continue
        try:
            if spec.kind == "customers":
                _apply_customer(db, ctx, result, actor)
            elif spec.kind == "products":
                _apply_product(db, ctx, result, actor, auto_create)
            else:
                _apply_price(db, ctx, result, actor, auto_create, options)
        except ValueError as exc:
            # A row that validated but failed on write (a race with another
            # import, a unique constraint) must be reported, not swallowed —
            # and the batch keeps going so one bad row cannot lose the other 999.
            result.action = "error"
            result.errors.append(f"write failed: {exc}")
            report.counts["create"] = max(0, report.counts.get("create", 0) - 1)
            report.counts["error"] = report.counts.get("error", 0) + 1

    db.flush()


def _apply_customer(db: Session, ctx: _Context, result: RowResult, actor: User | None) -> None:
    v = result.values
    existing = ctx.customer_by_code.get(str(v.get("code", "")).lower())
    if existing is None:
        created = customers_svc.create_customer(
            db,
            code=str(v["code"]),
            name_en=str(v.get("name_en") or v.get("name_zh") or ""),
            name_zh=str(v.get("name_zh") or v.get("name_en") or ""),
            type=str(v.get("type") or "other"),
            contact_name=v.get("contact_name"),
            contact_phone=v.get("contact_phone"),
            address=v.get("address"),
            delivery_zone=v.get("delivery_zone"),
            notes=v.get("notes"),
            actor=actor,
        )
        ctx.customer_by_code[str(v["code"]).lower()] = created
        result.action = "create"
        return
    customers_svc.update_customer(
        db,
        existing,
        name_en=v.get("name_en"),
        name_zh=v.get("name_zh"),
        type=v.get("type"),
        contact_name=v.get("contact_name"),
        contact_phone=v.get("contact_phone"),
        address=v.get("address"),
        delivery_zone=v.get("delivery_zone"),
        notes=v.get("notes"),
        actor=actor,
    )
    result.action = "update"


def _apply_product(
    db: Session,
    ctx: _Context,
    result: RowResult,
    actor: User | None,
    auto_create: bool,
) -> None:
    v = result.values
    category_id = None
    if v.get("category"):
        category_id = _resolve_category(db, ctx, str(v["category"]), actor, auto_create)
    unit_id = None
    if v.get("unit"):
        unit_id = _resolve_unit(db, ctx, str(v["unit"]), actor, auto_create)

    existing = ctx.product_by_sku.get(str(v.get("sku", "")).lower())
    if existing is None:
        created = catalog_svc.create_product(
            db,
            sku=str(v["sku"]),
            name_en=str(v.get("name_en") or v.get("name_zh") or ""),
            name_zh=str(v.get("name_zh") or v.get("name_en") or ""),
            category_id=category_id,
            default_unit_id=unit_id,
            shelf_life_days=v.get("shelf_life_days"),
            is_active=bool(v.get("is_active", True)),
            actor=actor,
        )
        ctx.product_by_sku[str(v["sku"]).lower()] = created
        result.action = "create"
        return
    catalog_svc.update_product(
        db,
        existing,
        name_en=v.get("name_en"),
        name_zh=v.get("name_zh"),
        category_id=category_id,
        default_unit_id=unit_id,
        shelf_life_days=v.get("shelf_life_days"),
        is_active=v.get("is_active"),
        actor=actor,
    )
    result.action = "update"


def _apply_price(
    db: Session,
    ctx: _Context,
    result: RowResult,
    actor: User | None,
    auto_create: bool,
    options: dict[str, Any],
) -> None:
    v = result.values
    customer = ctx.find_customer(str(v.get("customer", "")))
    product = ctx.find_product(str(v.get("product", "")))
    if customer is None or product is None:
        # Validation warned about this and the preview showed it; the write is
        # where it becomes a skip.
        result.action = "error"
        result.errors.append("customer or product not found")
        return

    unit_id: str | None = None
    if v.get("unit"):
        unit_id = _resolve_unit(db, ctx, str(v["unit"]), actor, auto_create)
    if unit_id is None:
        # ContractPrice.unit_id is NOT NULL. There is no "any unit" row, so a
        # price with no unit has to borrow the product's own default unit —
        # otherwise the import would have to invent a unit, which is worse.
        unit_id = product.default_unit_id
    if unit_id is None:
        result.action = "error"
        result.errors.append(
            f"no unit for '{product.name_zh}' — add a unit column, name the unit "
            "in the product heading, or set the product's default unit"
        )
        return

    valid_from = v.get("valid_from")
    if valid_from is None:
        default_from = options.get("valid_from")
        valid_from = (
            date.fromisoformat(default_from) if isinstance(default_from, str)
            else default_from or date.today()
        )

    contracts_svc.create_contract_price(
        db,
        customer_id=customer.id,
        product_id=product.id,
        unit_id=unit_id,
        price=float(v["price"]),
        valid_from=valid_from,
        valid_until=v.get("valid_until"),
        actor=actor,
    )
    result.action = "update"


def _resolve_category(
    db: Session,
    ctx: _Context,
    text: str,
    actor: User | None,
    auto_create: bool,
) -> str | None:
    found = ctx.find_category(text)
    if found is not None:
        return found.id
    if not auto_create:
        return None
    # Categories carry both languages and one of them has to be a guess: we only
    # know the text the operator wrote. Mirror it, exactly as `_mirror_names`
    # does for products, rather than inventing a translation.
    created = catalog_svc.create_category(
        db, name_en=text, name_zh=text, actor=actor
    )
    ctx.categories[text.lower()] = created
    ctx.pending_categories[text.lower()] = created
    return created.id


def _resolve_unit(
    db: Session,
    ctx: _Context,
    text: str,
    actor: User | None,
    auto_create: bool,
) -> str | None:
    found = ctx.find_unit(text)
    if found is not None:
        return found.id
    if not auto_create:
        return None
    code = _unit_code_for(text)
    # A code collision means the unit exists under a name we did not index.
    # Reuse it rather than failing the row.
    clash = db.query(Unit).filter(Unit.code == code).first()
    if clash is not None:
        ctx.units[text.lower()] = clash
        return clash.id
    created = catalog_svc.create_unit(
        db, code=code, name_en=text, name_zh=text, actor=actor
    )
    ctx.units[text.lower()] = created
    ctx.units[code.lower()] = created
    ctx.pending_units[text.lower()] = created
    return created.id


def _unit_code_for(text: str) -> str:
    """A stable ASCII code for a unit we are inventing.

    `Unit.code` is the only unique key and it is used in order lines and
    payloads, so it must be ASCII and stable. The Chinese text itself is not
    usable as a code, so map the units we can recognise and otherwise fall back
    to a normalised ASCII slug — deterministic, so importing the same file twice
    does not create a second unit.
    """
    known = {
        "斤": "jin", "公斤": "kg", "千克": "kg", "kg": "kg",
        "箱": "box", "件": "piece", "个": "piece", "只": "piece",
        "袋": "bag", "包": "bag", "桶": "bucket", "瓶": "bottle",
        "盒": "carton", "打": "dozen", "筐": "basket", "把": "bunch",
        "吨": "ton", "克": "g", "g": "g", "升": "l", "l": "l",
        "份": "portion", "板": "tray", "扎": "bundle",
    }
    low = text.strip().lower()
    if low in known:
        return known[low]
    ascii_slug = "".join(
        ch for ch in unicodedata.normalize("NFKC", text).lower() if ch.isalnum()
    )
    if ascii_slug:
        return ascii_slug[:20]
    # Pure non-ASCII text we do not recognise. Keep the code stable and
    # traceable rather than hashing it into something nobody can read.
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"u{digest}"


def _write_batch_audit(
    db: Session, spec: EntitySpec, report: ImportReport, filename: str, actor: User | None
) -> None:
    """One audit row per import, so a batch is findable as a batch.

    The per-row changes already have their own audit entries (the services write
    them). This is the summary that ties them together: without it there is no
    record that 1000 rows arrived together in one file.
    """
    from app.core.audit import log_audit

    log_audit(
        db,
        actor,
        "ImportBatch",
        report.content_sha256[:36],
        "import",
        before=None,
        after={
            "kind": spec.kind,
            "filename": filename,
            "shape": report.shape,
            "counts": report.counts,
            "sha256": report.content_sha256,
        },
        summary=(
            f"Imported {spec.label.lower()} from {filename}: "
            f"{report.counts.get('create', 0)} created, "
            f"{report.counts.get('update', 0)} updated, "
            f"{report.counts.get('error', 0)} failed"
        ),
    )
    db.commit()
