#!/usr/bin/env python3
"""Audit: every frontend API call path vs every backend route (per-file prefixes)."""
import re
from pathlib import Path

ROOT = Path("/Users/harshjani/Documents/Foshan1")
FE = ROOT / "frontend" / "src"
BE = ROOT / "backend" / "app" / "api" / "v1"


def norm(path: str) -> str:
    p = path.split("?")[0]
    if p.endswith("/") and len(p) > 1:
        p = p[:-1]
    p = re.sub(r"\$\{[^}]*\}", "{}", p)
    p = re.sub(r":[^/]+", "{}", p)
    p = re.sub(r"\{[^}]+\}", "{}", p)
    return p


def to_regex(normalized: str) -> str:
    parts = normalized.split("/")
    out = [re.escape(x) if x != "{}" else "[^/]+" for x in parts]
    return "^" + "/".join(out) + "$"


# ---- frontend paths -------------------------------------------------------
fe_paths = set()
api_re = re.compile(r"api\.(get|post|put|patch|delete)\(\s*([\"'`])(.*?)\2", re.S)
concat_re = re.compile(r"api\.(get|post|put|patch|delete)\(\s*([\"'`])(.*?)\2\s*\+")
list_re = re.compile(r"use(List|Detail)\(\s*([\"'`])(.*?)\2", re.S)

for f in list(FE.rglob("*.ts")) + list(FE.rglob("*.tsx")):
    txt = f.read_text(encoding="utf-8", errors="ignore")
    for m in api_re.finditer(txt):
        lit = m.group(3).strip().lstrip("`")
        if lit.startswith("/"):
            fe_paths.add(norm(lit))
    for m in concat_re.finditer(txt):
        lit = m.group(3).strip().rstrip("`")
        if lit.startswith("/"):
            fe_paths.add(norm(lit + "/{}"))
    for m in list_re.finditer(txt):
        lit = m.group(3).strip().rstrip("`")
        if lit.startswith("/"):
            fe_paths.add(norm(lit))

fe_paths = {p for p in fe_paths if not p.startswith("/wecom")}

# ---- backend routes (prefixes per file) -----------------------------------
route_re = re.compile(r"(\w+_router|router)\s*=\s*APIRouter\((.*?)\)", re.S)
dec_re = re.compile(r"@(\w+_router|router)\.(get|post|put|patch|delete)\(\s*[\"']([^\"']*)[\"']")
be_routes = set()
for f in BE.glob("*.py"):
    txt = f.read_text(encoding="utf-8", errors="ignore")
    f_prefix = {}
    for m in route_re.finditer(txt):
        body = m.group(2)
        pm = re.search(r"prefix\s*=\s*[\"']([^\"']+)[\"']", body)
        f_prefix[m.group(1)] = pm.group(1) if pm else ""
    for m in dec_re.finditer(txt):
        prefix = f_prefix.get(m.group(1), "")
        sub = m.group(3)
        full = (prefix.rstrip("/") + "/" + sub.lstrip("/")).replace("//", "/")
        full = re.sub(r"/+", "/", full)
        if full.startswith("/api/v1"):
            full = full[7:]
        be_routes.add(norm(full))

# ---- compare --------------------------------------------------------------
missing = []
for p in sorted(fe_paths):
    rx = to_regex(p)
    if not any(re.match(rx, b) for b in be_routes):
        missing.append(p)

print("FRONTEND_PATHS:", len(fe_paths))
print("BACKEND_ROUTES:", len(be_routes))
print("\n=== FRONTEND CALLS WITH NO MATCHING BACKEND ROUTE (true 404 risk) ===")
if missing:
    for m in missing:
        print("  MISSING:", m)
else:
    print("  (none — full frontend/backend coverage)")
