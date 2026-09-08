"""Sequential business document numbers: PREFIX-YYYYMMDD-0001."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import Base


def next_number(db: Session, model: type[Base], field_name: str, prefix: str, on: date | None = None) -> str:
    on = on or date.today()
    stamp = on.strftime("%Y%m%d")
    like = f"{prefix}-{stamp}-%"
    column = getattr(model, field_name)
    count = db.query(func.count(getattr(model, "id"))).filter(column.like(like)).scalar() or 0
    return f"{prefix}-{stamp}-{count + 1:04d}"
