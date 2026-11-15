"""来源台账控制器。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from src.services import source_service


def list_sources(db: Session, **filters) -> dict:
    return source_service.list_sources(db, **filters)


def get_source(db: Session, source_id: int) -> dict:
    return source_service.get_source(db, source_id)


def summary(db: Session) -> dict:
    return source_service.summary(db)
