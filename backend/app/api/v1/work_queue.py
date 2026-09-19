"""WORK QUEUE — one endpoint for every nav badge.

Implements:
  GET /api/v1/work-queue   → {section: count} for the sections this role can act
                             on. Polled by the nav shell every 15s. R: any user

Why one endpoint and not one per section: the shell needs all of them at the
same moment, on every tick. Per-section endpoints meant N requests per tick and
a 403 for each section the role cannot open, which is invisible in the UI and
noise in the network log. See `app/services/workqueue.py` for what each number
means and the rule they all obey.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import User
from app.services import workqueue

router = APIRouter(prefix="/api/v1/work-queue", tags=["work-queue"])


@router.get("", response_model=None)
def work_queue_endpoint(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Counts of work waiting on a person, for the sections this role can act on.

    Any authenticated role may call it — the response is already scoped to what
    that role can see, so there is nothing here to withhold.
    """
    return workqueue.collect(db, user=user)
