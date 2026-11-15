"""房源簇服务：列表、成员对照、golden record 与风险详情。

详情页的两条口径：
- ``members``：簇内全部直达记录（含同中介重复发布），标明哪一条被采用为报价；
- ``quotes``：按中介聚合的「采用发布」，每家一条，并挂上对齐到它的转载记录
  （``reposts``），可以从房源详情一路追到每个中介采用的发布和相应转载。
"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.constants.enums import RISK_LEVEL_LABELS, RISK_TAG_LABELS
from src.models.agency import Agency
from src.models.listing import Listing
from src.models.property import Property
from src.services import listing_service
from src.utils.errors import not_found
from src.utils.timeutil import iso_utc


def serialize(prop: Property, agency_names: list[str] | None = None) -> dict:
    return {
        "id": prop.id,
        "cluster_key": prop.cluster_key,
        "community": prop.community,
        "layout": prop.layout,
        "area_mode": prop.area_mode,
        "area_min": prop.area_min,
        "area_max": prop.area_max,
        "price_median": prop.price_median,
        "price_min": prop.price_min,
        "price_max": prop.price_max,
        "price_p10": prop.price_p10,
        "price_p90": prop.price_p90,
        "spread": prop.spread,
        "spread_wan": round(prop.spread / 10000, 1),
        "dispersion": prop.dispersion,
        "agency_count": prop.agency_count,
        "quote_count": prop.quote_count,
        "direct_count": prop.direct_count,
        "linked_repost_count": prop.linked_repost_count,
        "record_count": prop.record_count,
        "member_count": prop.member_count,
        "confidence": prop.confidence,
        "risk_score": prop.risk_score,
        "risk_level": prop.risk_level,
        "risk_level_label": RISK_LEVEL_LABELS.get(prop.risk_level, prop.risk_level),
        "risk_tags": prop.risk_tags or [],
        "risk_tag_labels": [RISK_TAG_LABELS.get(tag, tag) for tag in (prop.risk_tags or [])],
        "risk_detail": prop.risk_detail or {},
        "agency_names": agency_names or [],
        "run_id": prop.run_id,
        "updated_at": iso_utc(prop.updated_at),
    }


def _agency_names_by_property(db: Session) -> dict[int, list[str]]:
    """簇的主档中介名：只算被采用为报价的直达记录（转载渠道不冒充中介）。"""
    rows = db.execute(
        select(Listing.property_id, Agency.name)
        .join(Agency, Agency.id == Listing.agency_id)
        .where(Listing.property_id.is_not(None), Listing.is_quote.is_(True))
        .distinct()
    ).all()
    grouped: dict[int, list[str]] = {}
    for property_id, name in rows:
        if property_id is None:
            continue
        grouped.setdefault(property_id, []).append(name)
    return {key: sorted(value) for key, value in grouped.items()}


def list_properties(
    db: Session,
    *,
    q: str | None = None,
    risk_level: str | None = None,
    tag: str | None = None,
    multi: bool | None = None,
    agency_id: int | None = None,
    sort: str = "risk",
    limit: int = 20,
    offset: int = 0,
) -> dict:
    stmt = select(Property)
    count_stmt = select(func.count()).select_from(Property)
    conditions = []
    if q:
        like = f"%{q.strip()}%"
        conditions.append(or_(Property.community.like(like), Property.layout.like(like)))
    if risk_level:
        conditions.append(Property.risk_level == risk_level)
    if tag:
        conditions.append(Property.risk_tags.like(f'%"{tag}"%'))
    if multi:
        conditions.append(Property.quote_count > 1)
    if agency_id:
        subquery = select(Listing.property_id).where(
            Listing.agency_id == agency_id, Listing.property_id.is_not(None)
        )
        conditions.append(Property.id.in_(subquery))
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    order = {
        "risk": (Property.risk_score.desc(), Property.id.asc()),
        "spread": (Property.spread.desc(), Property.id.asc()),
        "members": (Property.quote_count.desc(), Property.id.asc()),
        "recent": (Property.updated_at.desc(), Property.id.asc()),
    }.get(sort, (Property.risk_score.desc(), Property.id.asc()))
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.order_by(*order).limit(limit).offset(offset)).all()
    names = _agency_names_by_property(db)
    return {
        "items": [serialize(row, names.get(row.id, [])) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_property(db: Session, property_id: int) -> dict:
    prop = db.get(Property, property_id)
    if prop is None:
        raise not_found("property", property_id)
    names = _agency_names_by_property(db)
    payload = serialize(prop, names.get(prop.id, []))
    listings = db.scalars(select(Listing).where(Listing.property_id == prop.id).order_by(Listing.id)).all()
    context = listing_service._context(db)
    agencies = context.agencies
    median = prop.price_median or 0

    members = []
    quotes: dict[int, dict] = {}
    for listing in listings:
        if listing.record_kind != "direct":
            continue
        item = context.serialize(listing)
        price = listing.price_num or 0
        item["price_deviation"] = round((price - median) / median, 4) if median and price else 0.0
        item["is_bait"] = "bait" in (listing.risk_tags or [])
        item["is_stale"] = "stale" in (listing.risk_tags or [])
        members.append(item)
        if listing.is_quote:
            quotes[listing.id] = {
                "listing_id": listing.id,
                "agency_id": listing.agency_id,
                "agency_name": agencies[listing.agency_id].name if listing.agency_id in agencies else "",
                "agency_code": agencies[listing.agency_id].code if listing.agency_id in agencies else "",
                "lid": listing.lid,
                "price_num": listing.price_num,
                "price_raw": listing.price_raw,
                "status": listing.status,
                "reposts": [],
            }

    reposts = []
    for listing in listings:
        if listing.record_kind != "repost":
            continue
        item = context.serialize(listing)
        reposts.append(item)
        prov = item["provenance"]
        source_listing = context.listing(listing.source_listing_id)
        # 转载挂到「它所指向的原发布中介」在本簇被采用的那份报价上；
        # 若指向的是该中介更早的一条重复发布，也归到同一中介名下并标明原编号。
        target_agency_id = source_listing.agency_id if source_listing else listing.origin_agency_id
        target = next(
            (quote for quote in quotes.values() if quote["agency_id"] == target_agency_id),
            None,
        )
        if target is not None and prov["status"] == "linked":
            target["reposts"].append(
                {
                    "listing_id": listing.id,
                    "agency_name": agencies[listing.agency_id].name if listing.agency_id in agencies else "",
                    "lid": listing.lid,
                    "status_label": prov["status_label"],
                    "source_lid": source_listing.lid if source_listing else None,
                    "is_adopted_source": bool(source_listing and source_listing.is_quote),
                    "price_num": listing.price_num,
                }
            )

    payload["members"] = members
    payload["quotes"] = [quotes[key] for key in sorted(quotes)]
    payload["reposts"] = reposts
    payload["unresolved_reposts"] = [
        item for item in reposts if item["provenance"]["status"] == "unresolved"
    ]
    return payload


def agency_coverage(db: Session) -> list[dict]:
    rows = db.execute(
        select(Agency.id, Agency.name, func.count(Listing.id))
        .join(Listing, Listing.agency_id == Agency.id, isouter=True)
        .group_by(Agency.id, Agency.name)
        .order_by(Agency.id)
    ).all()
    return [{"agency_id": row[0], "name": row[1], "listing_count": row[2]} for row in rows]
