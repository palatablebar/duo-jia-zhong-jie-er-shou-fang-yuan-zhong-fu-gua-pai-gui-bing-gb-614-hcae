"""来源台账路由：只读查询转载记录与原发布的对齐状态。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.controllers import source_controller
from src.middlewares.rbac import READ_ROLES, require_roles
from src.utils.pagination import page_params

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("")
def list_sources(
    q: str | None = None,
    provenance_status: str | None = None,
    agency_id: int | None = None,
    reason: str | None = None,
    limit: int = Query(20),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    page = page_params(limit, offset)
    return source_controller.list_sources(
        db,
        q=q,
        provenance_status=provenance_status,
        agency_id=agency_id,
        reason=reason,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{source_id}")
def get_source(
    source_id: int, db: Session = Depends(get_db), principal: dict = Depends(require_roles(*READ_ROLES))
) -> dict:
    return source_controller.get_source(db, source_id)
