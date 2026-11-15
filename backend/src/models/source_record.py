"""来源台账：每条转载记录的原发布中介 / 原挂牌编号与对齐状态。

台账与 ``listings`` 一一对应（``listing_id`` 唯一），重算时只做 upsert、不删除，
保证「转载归并时不能丢失记录」；对不上原发布的记录 ``provenance_status=unresolved``，
在这里保留待补齐，补齐后下一次重算自行转为 ``linked`` 并参与归并。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class SourceRecord(Base):
    __tablename__ = "source_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), unique=True, index=True
    )
    agency_id: Mapped[int] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    lid: Mapped[str] = mapped_column(String(40), index=True)
    record_kind: Mapped[str] = mapped_column(String(10), default="repost", index=True)
    origin_agency_id: Mapped[int | None] = mapped_column(
        ForeignKey("agencies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    origin_agency_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    origin_agency_raw: Mapped[str | None] = mapped_column(String(160), nullable=True)
    origin_lid: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    provenance_status: Mapped[str] = mapped_column(String(20), default="unresolved", index=True)
    provenance_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL"), nullable=True, index=True
    )
    property_id: Mapped[int | None] = mapped_column(
        ForeignKey("properties.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
