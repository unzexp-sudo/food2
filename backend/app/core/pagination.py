"""Shared pagination envelope."""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


def page_response(items: list, total: int, page: int, page_size: int) -> dict:
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def clamp_page(page: int = 1, page_size: int = 20) -> tuple[int, int]:
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    return page, page_size
