"""挂牌明细服务：原文与归一结果同时返回，供「挂牌明细」页面对照。"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.constants.enums import (
    LISTING_STATUS_LABELS,
    PROVENANCE_REASON_LABELS,
    PROVENANCE_STATUS_LABELS,
    RECORD_KIND_LABELS,
    RISK_TAG_LABELS,
)
from src.models.agency import Agency
from src.models.listing import Listing
from src.models.property import Property
from src.utils.errors import not_found
from src.utils.timeutil import iso_utc


def serialize(
    listing: Listing,
    agency: Agency | None = None,
    prop: Property | None = None,
    origin_agency: Agency | None = None,
    source_listing: Listing | None = None,
    source_agency: Agency | None = None,
) -> dict:
    return {
        "id": listing.id,
        "lid": listing.lid,
        "agency_id": listing.agency_id,
        "agency_name": agency.name if agency else "",
        "agency_code": agency.code if agency else "",
        "community_raw": listing.community_raw,
        "layout_raw": listing.layout_raw,
        "area_raw": listing.area_raw,
        "price_raw": listing.price_raw,
        "floor_raw": listing.floor_raw,
        "orientation_raw": listing.orientation_raw,
        "title": listing.title,
        "status": listing.status,
        "status_label": LISTING_STATUS_LABELS.get(listing.status, listing.status),
        "listed_days": listing.listed_days,
        "views": listing.views,
        "captured_at": iso_utc(listing.captured_at),
        "norm": {
            "community": listing.community_norm,
            "layout": listing.layout_norm,
            "area": listing.area_num,
            "price": listing.price_num,
            "floor": listing.floor_num,
            "floor_total": listing.floor_total,
            "floor_band": listing.floor_band,
            "orientation": listing.orientation_norm,
            "title_tokens": listing.title_tokens or [],
        },
        "parse_flags": listing.parse_flags or [],
        "risk_tags": listing.risk_tags or [],
        "risk_labels": [RISK_TAG_LABELS.get(tag, tag) for tag in (listing.risk_tags or [])],
        "property_id": listing.property_id,
        "property_community": prop.community if prop else None,
        "property_layout": prop.layout if prop else None,
        "property_risk_level": prop.risk_level if prop else None,
        "provenance": {
            "record_kind": listing.record_kind or "direct",
            "record_kind_label": RECORD_KIND_LABELS.get(listing.record_kind or "direct", listing.record_kind or "direct"),
            "status": listing.provenance_status,
            "status_label": PROVENANCE_STATUS_LABELS.get(listing.provenance_status, listing.provenance_status),
            "reason": listing.provenance_reason,
            "reason_label": PROVENANCE_REASON_LABELS.get(listing.provenance_reason or "", ""),
            "is_quote": bool(listing.is_quote),
            "origin_agency_id": listing.origin_agency_id,
            "origin_agency_code": listing.origin_agency_code,
            "origin_agency_name": origin_agency.name if origin_agency else None,
            "origin_agency_raw": listing.origin_agency_raw,
            "origin_lid": listing.origin_lid,
            "source_listing_id": listing.source_listing_id,
            "source_agency_name": source_agency.name if source_agency else None,
            "source_lid": source_listing.lid if source_listing else None,
        },
    }


class _Context:
    """一次查询内的关联缓存：中介、房源簇、转载指向的直达记录。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.agencies = {agency.id: agency for agency in db.scalars(select(Agency)).all()}
        self.props = {prop.id: prop for prop in db.scalars(select(Property)).all()}
        self._listings: dict[int, Listing] | None = None

    def listing(self, listing_id: int | None) -> Listing | None:
        if not listing_id:
            return None
        if self._listings is None:
            self._listings = {row.id: row for row in self.db.scalars(select(Listing)).all()}
        return self._listings.get(listing_id)

    def serialize(self, listing: Listing) -> dict:
        source_listing = self.listing(listing.source_listing_id)
        source_agency = self.agencies.get(source_listing.agency_id) if source_listing else None
        return serialize(
            listing,
            self.agencies.get(listing.agency_id),
            self.props.get(listing.property_id),
            self.agencies.get(listing.origin_agency_id) if listing.origin_agency_id else None,
            source_listing,
            source_agency,
        )


def _context(db: Session) -> _Context:
    return _Context(db)


def list_listings(
    db: Session,
    *,
    q: str | None = None,
    agency_id: int | None = None,
    status: str | None = None,
    risk: str | None = None,
    property_id: int | None = None,
    record_kind: str | None = None,
    provenance_status: str | None = None,
    quote_only: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    stmt = select(Listing)
    count_stmt = select(func.count()).select_from(Listing)
    if q:
        like = f"%{q.strip()}%"
        condition = or_(Listing.community_raw.like(like), Listing.title.like(like), Listing.lid.like(like))
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    if agency_id:
        stmt = stmt.where(Listing.agency_id == agency_id)
        count_stmt = count_stmt.where(Listing.agency_id == agency_id)
    if status:
        stmt = stmt.where(Listing.status == status)
        count_stmt = count_stmt.where(Listing.status == status)
    if risk == "bait":
        stmt = stmt.where(Listing.risk_tags.like('%"bait"%'))
        count_stmt = count_stmt.where(Listing.risk_tags.like('%"bait"%'))
    elif risk == "stale":
        stmt = stmt.where(Listing.risk_tags.like('%"stale"%'))
        count_stmt = count_stmt.where(Listing.risk_tags.like('%"stale"%'))
    elif risk == "none":
        stmt = stmt.where(Listing.risk_tags == "[]")
        count_stmt = count_stmt.where(Listing.risk_tags == "[]")
    if property_id:
        stmt = stmt.where(Listing.property_id == property_id)
        count_stmt = count_stmt.where(Listing.property_id == property_id)
    if record_kind:
        stmt = stmt.where(Listing.record_kind == record_kind)
        count_stmt = count_stmt.where(Listing.record_kind == record_kind)
    if provenance_status:
        stmt = stmt.where(Listing.provenance_status == provenance_status)
        count_stmt = count_stmt.where(Listing.provenance_status == provenance_status)
    if quote_only:
        stmt = stmt.where(Listing.is_quote.is_(True))
        count_stmt = count_stmt.where(Listing.is_quote.is_(True))
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.order_by(Listing.id).limit(limit).offset(offset)).all()
    context = _context(db)
    return {
        "items": [context.serialize(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_listing(db: Session, listing_id: int) -> dict:
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise not_found("listing", listing_id)
    context = _context(db)
    payload = context.serialize(listing)
    prop = context.props.get(listing.property_id)
    payload["cluster"] = (
        {
            "id": prop.id,
            "community": prop.community,
            "layout": prop.layout,
            "member_count": prop.member_count,
            "agency_count": prop.agency_count,
            "price_median": prop.price_median,
            "risk_score": prop.risk_score,
            "risk_level": prop.risk_level,
            "risk_tags": prop.risk_tags or [],
        }
        if prop
        else None
    )
    return payload
