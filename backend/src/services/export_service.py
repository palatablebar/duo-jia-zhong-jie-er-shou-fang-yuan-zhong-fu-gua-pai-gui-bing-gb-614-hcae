"""导出服务：归并簇 / 挂牌明细 / 复核单 CSV（含两个算法的结果列）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.constants.enums import (
    LISTING_STATUS_LABELS,
    PROVENANCE_REASON_LABELS,
    PROVENANCE_STATUS_LABELS,
    RECORD_KIND_LABELS,
    REVIEW_REASON_LABELS,
    REVIEW_STATUS_LABELS,
    RISK_LEVEL_LABELS,
    RISK_TAG_LABELS,
)
from src.models.agency import Agency
from src.models.listing import Listing
from src.models.property import Property
from src.models.review import Review
from src.utils.csv_export import to_csv
from src.utils.timeutil import local_stamp


def _tags(tags: list[str] | None) -> str:
    return " / ".join(RISK_TAG_LABELS.get(tag, tag) for tag in (tags or []))


def clusters_csv(db: Session) -> bytes:
    props = db.scalars(select(Property).order_by(Property.risk_score.desc(), Property.id)).all()
    headers = [
        "簇编号",
        "小区",
        "户型",
        "面积众数(㎡)",
        "价格中位(元)",
        "簇置信",
        "报价数(中介)",
        "采用发布数",
        "佐证转载数",
        "簇内记录数",
        "价格极差(万元)",
        "离散度",
        "风险分",
        "风险等级",
        "风险标签",
        "钓鱼价挂牌",
        "僵尸挂牌",
        "采用发布编号",
        "更新时间",
    ]
    quote_rows = db.execute(
        select(Listing.property_id, Listing.lid, Agency.code)
        .join(Agency, Agency.id == Listing.agency_id)
        .where(Listing.is_quote.is_(True))
        .order_by(Listing.property_id, Listing.id)
    ).all()
    quote_lids: dict[int, list[str]] = {}
    for property_id, lid, code in quote_rows:
        quote_lids.setdefault(property_id, []).append(f"{code}:{lid}")
    rows = []
    for prop in props:
        detail = prop.risk_detail or {}
        rows.append(
            [
                prop.id,
                prop.community,
                prop.layout,
                f"{prop.area_mode:g}",
                prop.price_median,
                f"{prop.confidence:.3f}",
                prop.quote_count,
                prop.direct_count,
                prop.linked_repost_count,
                prop.record_count,
                f"{prop.spread / 10000:.1f}",
                f"{prop.dispersion:.4f}",
                prop.risk_score,
                RISK_LEVEL_LABELS.get(prop.risk_level, prop.risk_level),
                _tags(prop.risk_tags),
                " / ".join(detail.get("bait_lids") or []) or "-",
                " / ".join(detail.get("stale_lids") or []) or "-",
                " / ".join(quote_lids.get(prop.id, [])) or "-",
                local_stamp(prop.updated_at),
            ]
        )
    return to_csv(headers, rows)


def listings_csv(db: Session, agency_id: int | None = None) -> bytes:
    """来源台账 + 报价标记：转载记录保留原发布中介/编号，待补齐的也在清单里留痕。"""
    stmt = select(Listing)
    if agency_id:
        stmt = stmt.where(Listing.agency_id == agency_id)
    listings = db.scalars(stmt.order_by(Listing.id)).all()
    agencies = {agency.id: agency for agency in db.scalars(select(Agency)).all()}
    props = {prop.id: prop for prop in db.scalars(select(Property)).all()}
    by_id = {row.id: row for row in listings}
    headers = [
        "记录编号",
        "中介/渠道",
        "来源性质",
        "报价记录",
        "来源状态",
        "原发布中介(编号)",
        "原发布中介(台账名)",
        "原挂牌编号",
        "对齐到的直达记录",
        "待补齐原因",
        "小区(原文)",
        "小区(归一)",
        "面积(原文)",
        "面积(㎡)",
        "价格(原文)",
        "价格(元)",
        "楼层(原文)",
        "楼层",
        "朝向(归一)",
        "户型(归一)",
        "状态",
        "挂牌天数",
        "带看次数",
        "所属簇",
        "簇风险分",
        "风险标签",
        "归一标记",
    ]
    rows = []
    for listing in listings:
        prop = props.get(listing.property_id)
        origin_agency = agencies.get(listing.origin_agency_id) if listing.origin_agency_id else None
        source_listing = by_id.get(listing.source_listing_id) if listing.source_listing_id else None
        rows.append(
            [
                listing.lid,
                agencies.get(listing.agency_id).name if agencies.get(listing.agency_id) else "",
                RECORD_KIND_LABELS.get(listing.record_kind or "direct", listing.record_kind or "direct"),
                "是" if listing.is_quote else "否",
                PROVENANCE_STATUS_LABELS.get(listing.provenance_status, listing.provenance_status),
                listing.origin_agency_code or "",
                origin_agency.name if origin_agency else (listing.origin_agency_raw or ""),
                listing.origin_lid or "",
                source_listing.lid if source_listing else "",
                PROVENANCE_REASON_LABELS.get(listing.provenance_reason or "", ""),
                listing.community_raw,
                listing.community_norm,
                listing.area_raw,
                f"{listing.area_num:g}" if listing.area_num is not None else "",
                listing.price_raw,
                listing.price_num or "",
                listing.floor_raw,
                listing.floor_num or "",
                listing.orientation_norm,
                listing.layout_norm,
                LISTING_STATUS_LABELS.get(listing.status, listing.status),
                listing.listed_days,
                listing.views,
                prop.id if prop else "",
                prop.risk_score if prop else "",
                _tags(listing.risk_tags),
                " / ".join(listing.parse_flags or []),
            ]
        )
    return to_csv(headers, rows)


def reconciliation(db: Session) -> dict:
    """对账口径：中介数 / 报价数 / 各导出行数必须能对上。

    - ``agency_count``：簇内去重中介数之和（= 报价数，同一中介一簇只算一份报价）；
    - ``quote_count``：被采用为报价的直达记录行数；
    - ``direct_count``：直达记录行数（含同店重复发布）；
    - ``linked_repost_count`` / ``unresolved_repost_count``：转载记录行数，两者之和 = 转载总数；
    - ``record_count``：listings 表总行数 = 直达 + 转载；
    - ``cluster_rows`` / ``listing_rows`` / ``review_rows``：三个 CSV 的数据行数（不含表头）。
    """
    listings = list(db.scalars(select(Listing)).all())
    props = list(db.scalars(select(Property)).all())
    reviews = list(db.scalars(select(Review)).all())
    direct = [row for row in listings if (row.record_kind or "direct") == "direct"]
    linked = [row for row in listings if row.record_kind == "repost" and row.provenance_status == "linked"]
    unresolved = [row for row in listings if row.record_kind == "repost" and row.provenance_status == "unresolved"]
    quotes = [row for row in listings if row.is_quote]
    return {
        "agency_count": sum(prop.agency_count for prop in props),
        "quote_count": sum(prop.quote_count for prop in props),
        "quote_rows": len(quotes),
        "direct_count": len(direct),
        "linked_repost_count": len(linked),
        "unresolved_repost_count": len(unresolved),
        "repost_count": len(linked) + len(unresolved),
        "record_count": len(listings),
        "cluster_rows": len(props),
        "listing_rows": len(listings),
        "review_rows": len(reviews),
        "balanced": (
            len(listings) == len(direct) + len(linked) + len(unresolved)
            and sum(prop.quote_count for prop in props) == len(quotes)
            and sum(prop.agency_count for prop in props) == len(quotes)
        ),
    }


def reviews_csv(db: Session, status: str | None = None) -> bytes:
    stmt = select(Review)
    if status:
        stmt = stmt.where(Review.status == status)
    reviews = db.scalars(stmt.order_by(Review.id)).all()
    props = {prop.id: prop for prop in db.scalars(select(Property)).all()}
    headers = ["复核号", "小区", "户型", "风险分", "风险等级", "复核原因", "状态", "复核人", "结论备注", "创建时间"]
    rows = []
    for review in reviews:
        prop = props.get(review.property_id)
        rows.append(
            [
                review.id,
                prop.community if prop else "",
                prop.layout if prop else "",
                review.risk_score,
                RISK_LEVEL_LABELS.get(review.severity, review.severity),
                REVIEW_REASON_LABELS.get(review.reason, review.reason),
                REVIEW_STATUS_LABELS.get(review.status, review.status),
                review.reviewer or "",
                review.note or "",
                local_stamp(review.created_at),
            ]
        )
    return to_csv(headers, rows)
