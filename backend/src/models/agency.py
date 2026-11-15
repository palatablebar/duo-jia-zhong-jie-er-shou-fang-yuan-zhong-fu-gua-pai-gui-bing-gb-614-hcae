"""中介 / 采集渠道：9 家挂牌来源的登记与采集口径。

``kind`` 决定该渠道采集到的记录默认是「直达发布」还是「转载」：
``platform`` / ``feed`` 是转载渠道（平台、数据服务商），其余（``broker``）是中介自发布。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow

REPOST_AGENCY_KINDS = ("platform", "feed")


class Agency(Base):
    __tablename__ = "agencies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="broker")
    endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner: Mapped[str] = mapped_column(String(80), default="安家数据 · 房源中台")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
