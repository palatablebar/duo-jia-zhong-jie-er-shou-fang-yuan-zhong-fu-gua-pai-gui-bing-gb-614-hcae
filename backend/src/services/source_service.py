"""来源台账服务：转载记录追溯到原发布中介与原挂牌编号。

台账条目只增不删；对不齐的记录状态 ``unresolved``，不进入估价清单，
补齐原发布中介 / 原挂牌编号并重算后才转为 ``linked`` 并参与归并。
"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.constants.enums import (
    PROVENANCE_REASON_LABELS,
    PROVENANCE_STATUS_LABELS,
    RECORD_KIND_LABELS,
    RISK_TAG_LABELS,
)
from src.models.agency import Agency
from src.models.listing import Listing
from src.models.property import Property
from src.models.source_record import SourceRecord
from src.utils.errors import not_found
from src.utils.timeutil import iso_utc


def serialize(
    row: SourceRecord,
    listing: Listing | None = None,
    agency: Agency | None = None,
    origin_agency: Agency | None = None,
    source_listing: Listing | None = None,
    prop: Property | None = None,
) -> dict:
    return {
        "id": row.id,
        "listing_id": row.listing_id,
        "lid": row.lid,
        "agency_id": row.agency_id,
        "agency_name": agency.name if agency else "",
        "agency_code": agency.code if agency else "",
        "record_kind": row.record_kind,
        "record_kind_label": RECORD_KIND_LABELS.get(row.record_kind, row.record_kind),
        "origin_agency_id": row.origin_agency_id,
        "origin_agency_code": row.origin_agency_code,
        "origin_agency_name": origin_agency.name if origin_agency else (row.origin_agency_raw or ""),
        "origin_lid": row.origin_lid,
        "provenance_status": row.provenance_status,
        "provenance_status_label": PROVENANCE_STATUS_LABELS.get(row.provenance_status, row.provenance_status),
        "provenance_reason": row.provenance_reason,
        "provenance_reason_label": PROVENANCE_REASON_LABELS.get(row.provenance_reason or "", ""),
        "source_listing_id": row.source_listing_id,
        "source_lid": source_listing.lid if source_listing else None,
        "property_id": row.property_id,
        "property_community": prop.community if prop else None,
        "property_layout": prop.layout if prop else None,
        "price_num": listing.price_num if listing else None,
        "community_raw": listing.community_raw if listing else "",
        "community_norm": listing.community_norm if listing else "",
        "risk_tags": (listing.risk_tags or []) if listing else [],
        "risk_tag_labels": [RISK_TAG_LABELS.get(tag, tag) for tag in ((listing.risk_tags or []) if listing else [])],
        "updated_at": iso_utc(row.updated_at),
    }


def list_sources(
    db: Session,
    *,
    q: str | None = None,
    provenance_status: str | None = None,
    agency_id: int | None = None,
    reason: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    stmt = select(SourceRecord)
    count_stmt = select(func.count()).select_from(SourceRecord)
    conditions = []
    if q:
        like = f"%{q.strip()}%"
        conditions.append(
            or_(
                SourceRecord.lid.like(like),
                SourceRecord.origin_lid.like(like),
                SourceRecord.origin_agency_code.like(like),
                SourceRecord.origin_agency_raw.like(like),
            )
        )
    if provenance_status:
        conditions.append(SourceRecord.provenance_status == provenance_status)
    if agency_id:
        conditions.append(SourceRecord.agency_id == agency_id)
    if reason:
        conditions.append(SourceRecord.provenance_reason == reason)
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(
        stmt.order_by(SourceRecord.provenance_status.desc(), SourceRecord.id).limit(limit).offset(offset)
    ).all()
    listings = {row.id: row for row in db.scalars(select(Listing)).all()}
    agencies = {agency.id: agency for agency in db.scalars(select(Agency)).all()}
    props = {prop.id: prop for prop in db.scalars(select(Property)).all()}
    return {
        "items": [
            serialize(
                row,
                listings.get(row.listing_id),
                agencies.get(row.agency_id),
                agencies.get(row.origin_agency_id) if row.origin_agency_id else None,
                listings.get(row.source_listing_id) if row.source_listing_id else None,
                props.get(row.property_id) if row.property_id else None,
            )
            for row in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_source(db: Session, source_id: int) -> dict:
    row = db.get(SourceRecord, source_id)
    if row is None:
        raise not_found("source_record", source_id)
    listings = {item.id: item for item in db.scalars(select(Listing)).all()}
    agencies = {agency.id: agency for agency in db.scalars(select(Agency)).all()}
    props = {prop.id: prop for prop in db.scalars(select(Property)).all()}
    return serialize(
        row,
        listings.get(row.listing_id),
        agencies.get(row.agency_id),
        agencies.get(row.origin_agency_id) if row.origin_agency_id else None,
        listings.get(row.source_listing_id) if row.source_listing_id else None,
        props.get(row.property_id) if row.property_id else None,
    )


def summary(db: Session) -> dict:
    rows = db.execute(
        select(SourceRecord.provenance_status, func.count()).group_by(SourceRecord.provenance_status)
    ).all()
    counts = {status: count for status, count in rows}
    return {
        "total": sum(counts.values()),
        "linked": counts.get("linked", 0),
        "unresolved": counts.get("unresolved", 0),
    }
