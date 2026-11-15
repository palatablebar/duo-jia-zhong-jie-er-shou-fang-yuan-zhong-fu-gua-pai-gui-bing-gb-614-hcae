"""归并流水线：归一（②）→ 来源链路（转载对齐）→ 实体解析（③④）→ 风险识别（⑤）→ 落库。

口径要点：
- 只有直达记录参与 blocking / 相似度 / 并查集；平台与数据服务商的转载记录不自己成簇；
- 能对齐到直达记录的转载（``provenance_status=linked``）挂到那条直达记录所属的簇，
  只作佐证、不计报价，也不参与 golden record；
- 对不上原发布中介/原挂牌编号的转载（``unresolved``）原样留在来源台账，
  ``property_id`` 置空、不进银行估价清单，补齐后下一次重算自动参与；
- 同一簇同一中介只算一份报价：只认直达记录，在售优先、入库更晚优先。

纯后端计算，确定性；同一批原始挂牌两次执行得到完全相同的簇集合与 golden record。
"""
from __future__ import annotations

import hashlib
import time

from sqlalchemy import func, select

from src.constants.enums import ProvenanceStatus, RecordKind
from src.constants.limits import SIMILARITY_THRESHOLD
from src.models.agency import Agency
from src.models.listing import Listing
from src.models.match_run import MatchRun
from src.models.property import Property
from src.models.review import Review
from src.services import entity_resolution, normalizer, provenance_service, risk_rules
from src.utils.timeutil import iso_utc, utcnow

LISTING_FIELDS = (
    "community_raw",
    "layout_raw",
    "area_raw",
    "price_raw",
    "floor_raw",
    "orientation_raw",
    "title",
)


def cluster_key_of(agency_code: str, lid: str) -> str:
    return f"{agency_code}:{lid}"


def _apply_normalized(listing: Listing, norm: dict) -> None:
    listing.community_norm = norm["community_norm"]
    listing.layout_norm = norm["layout_norm"]
    listing.area_num = norm["area_num"]
    listing.price_num = norm["price_num"]
    listing.floor_num = norm["floor_num"]
    listing.floor_total = norm["floor_total"]
    listing.floor_band = norm["floor_band"]
    listing.orientation_norm = norm["orientation_norm"]
    listing.title_tokens = norm["title_tokens"]
    listing.parse_flags = norm["parse_flags"]


def _record_kind(listing: Listing) -> str:
    return listing.record_kind or RecordKind.DIRECT.value


def _apply_provenance(listing: Listing, info: dict) -> None:
    """把来源链路结果写回记录；转载记录始终保留原始 origin 字段用于追溯。"""
    listing.provenance_status = info["provenance_status"]
    listing.provenance_reason = info["provenance_reason"]
    listing.origin_agency_id = info["origin_agency_id"]
    listing.source_listing_id = info["source_listing_id"]
    listing.is_quote = False
    listing.quote_rank = None
    listing.risk_tags = []


def _empty_result() -> dict:
    return {
        "metrics": {
            "listings": 0,
            "direct_records": 0,
            "repost_records": 0,
            "linked_reposts": 0,
            "unresolved_reposts": 0,
            "quotes": 0,
            "clusters": 0,
            "multi_clusters": 0,
            "pairs_scored": 0,
            "pairs_linked": 0,
        },
        "log": "没有可归并的挂牌：请先采集入库。",
        "status": "partial",
    }


def run_pipeline(db, run: MatchRun, threshold: float = SIMILARITY_THRESHOLD) -> dict:
    started = time.perf_counter()
    listings = list(db.scalars(select(Listing).order_by(Listing.id)).all())
    agencies = {agency.id: agency for agency in db.scalars(select(Agency)).all()}
    if not listings:
        return _empty_result()

    # ② 归一（所有记录都归一，便于挂牌明细与来源台账展示原文 vs 归一）
    raw_rows = [{field: getattr(listing, field) for field in LISTING_FIELDS} for listing in listings]
    normalized = [normalizer.normalize_listing(raw) for raw in raw_rows]
    norm_by_id: dict[int, dict] = {}
    for listing, norm in zip(listings, normalized, strict=True):
        _apply_normalized(listing, norm)
        norm_by_id[listing.id] = norm

    # 来源链路：先判每条记录是直达、已对齐转载还是待补齐转载
    provenance = provenance_service.resolve_records(listings, agencies)
    for listing in listings:
        _apply_provenance(listing, provenance[listing.id])

    direct_listings = [listing for listing in listings if _record_kind(listing) == RecordKind.DIRECT.value]
    linked_reposts = [
        listing
        for listing in listings
        if _record_kind(listing) == RecordKind.REPOST.value
        and provenance[listing.id]["provenance_status"] == ProvenanceStatus.LINKED.value
    ]
    unresolved_reposts = [
        listing
        for listing in listings
        if _record_kind(listing) == RecordKind.REPOST.value
        and provenance[listing.id]["provenance_status"] == ProvenanceStatus.UNRESOLVED.value
    ]

    # ③④ 只让直达记录参与实体解析；同中介多条的报价在簇内再挑
    direct_norms = [norm_by_id[listing.id] for listing in direct_listings]
    direct_agency_ids = [listing.agency_id for listing in direct_listings]
    record_meta = [
        {
            "id": listing.id,
            "agency_id": listing.agency_id,
            "status": listing.status,
            "record_kind": RecordKind.DIRECT.value,
        }
        for listing in direct_listings
    ]
    result = entity_resolution.resolve(
        direct_norms, direct_agency_ids, threshold=threshold, record_meta=record_meta
    )

    existing = {prop.cluster_key: prop for prop in db.scalars(select(Property)).all()}
    seen_keys: set[str] = set()
    baits = stales = risk_high = risk_medium = 0

    for cluster in result["clusters"]:
        members = cluster["members"]  # 直达记录在 direct_listings 中的下标
        quote_indexes = cluster["quotes"]
        quote_listings = [direct_listings[index] for index in quote_indexes]
        member_listings = [direct_listings[index] for index in members]
        member_lids = sorted(
            cluster_key_of(agencies[listing.agency_id].code, listing.lid) for listing in member_listings
        )
        key = hashlib.sha256("|".join(member_lids).encode("utf-8")).hexdigest()[:40]
        seen_keys.add(key)
        golden = cluster["golden"]

        # 报价记录（每个中介一份）参与风险识别；重复直达与转载只作佐证
        quote_ids = {listing.id for listing in quote_listings}
        quote_rows = [
            {
                "id": listing.id,
                "lid": listing.lid,
                "price_num": listing.price_num,
                "listed_days": listing.listed_days,
                "views": listing.views,
                "status": listing.status,
            }
            for listing in quote_listings
        ]
        risk = risk_rules.scan_cluster(quote_rows, golden)

        prop = existing.get(key)
        if prop is None:
            prop = Property(cluster_key=key)
            db.add(prop)
        member_ids = {listing.id for listing in member_listings}
        prop.community = golden["community"]
        prop.layout = golden["layout"]
        prop.area_mode = golden["area_mode"]
        prop.area_min = golden["area_min"]
        prop.area_max = golden["area_max"]
        prop.price_median = golden["price_median"]
        prop.price_min = golden["price_min"]
        prop.price_max = golden["price_max"]
        prop.price_p10 = golden["price_p10"]
        prop.price_p90 = golden["price_p90"]
        prop.spread = golden["spread"]
        prop.dispersion = golden["dispersion"]
        prop.agency_count = golden["agency_count"]
        prop.quote_count = golden["quote_count"]
        prop.direct_count = len(member_listings)
        prop.linked_repost_count = 0
        prop.confidence = cluster["confidence"]
        prop.risk_score = risk["risk_score"]
        prop.risk_level = risk["risk_level"]
        prop.risk_tags = risk["risk_tags"]
        prop.risk_detail = risk["risk_detail"]
        prop.run_id = run.id
        prop.updated_at = utcnow()
        db.flush()

        linked_here = [
            listing for listing in linked_reposts if provenance[listing.id]["source_listing_id"] in member_ids
        ]
        prop.linked_repost_count = len(linked_here)
        # member_count 只算直达成员（与算法 A 的簇成员一致）；record_count 含对齐转载
        prop.member_count = len(member_listings)
        prop.record_count = len(member_listings) + len(linked_here)

        bait_lids = set(risk["risk_detail"]["bait_lids"])
        stale_lids = set(risk["risk_detail"]["stale_lids"])
        for listing in member_listings:
            tags: list[str] = []
            if listing.lid in bait_lids:
                tags.append("bait")
            if listing.lid in stale_lids:
                tags.append("stale")
            listing.property_id = prop.id
            listing.risk_tags = tags
            listing.is_quote = listing.id in quote_ids
            listing.quote_rank = 1 if listing.is_quote else None
        for listing in linked_here:
            listing.property_id = prop.id
        baits += len(bait_lids)
        stales += len(stale_lids)
        if prop.risk_level == "high":
            risk_high += 1
        elif prop.risk_level == "medium":
            risk_medium += 1

    # 待补齐的转载：保留在来源台账，不挂簇、不进估价清单
    for listing in unresolved_reposts:
        listing.property_id = None
        listing.is_quote = False
        listing.quote_rank = None

    pruned = _prune_orphans(db, seen_keys)
    # 来源台账只 upsert 不删除：待补齐的转载必须留在台账里等补齐，不能因归并丢失
    provenance_service.sync_source_ledger(db, listings, provenance)

    metrics = dict(result["metrics"])
    metrics.update(
        {
            "listings": len(listings),
            "direct_records": len(direct_listings),
            "repost_records": len(linked_reposts) + len(unresolved_reposts),
            "linked_reposts": len(linked_reposts),
            "unresolved_reposts": len(unresolved_reposts),
            "quotes": sum(cluster["golden"]["quote_count"] for cluster in result["clusters"]),
            "baits": baits,
            "stales": stales,
            "risk_high": risk_high,
            "risk_medium": risk_medium,
            "risk_total": risk_high + risk_medium,
            "pruned_clusters": pruned,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    )
    metrics["dedup_rate"] = (
        round(1 - metrics["clusters"] / metrics["direct_records"], 4) if metrics["direct_records"] else 0.0
    )
    props = list(db.scalars(select(Property)).all())
    metrics["reconciled"] = {
        "records": metrics["direct_records"] + metrics["linked_reposts"] + metrics["unresolved_reposts"],
        "direct_records": metrics["direct_records"],
        "linked_reposts": metrics["linked_reposts"],
        "unresolved_reposts": metrics["unresolved_reposts"],
        "quotes": metrics["quotes"],
        "agency_count": sum(prop.agency_count for prop in props),
        "clusters": len(props),
        "balanced": (
            sum(prop.agency_count for prop in props) == metrics["quotes"]
            and sum(prop.quote_count for prop in props) == metrics["quotes"]
        ),
    }
    log_lines = [
        f"输入记录 {metrics['listings']} 条：直达 {metrics['direct_records']}、转载 {metrics['repost_records']}"
        f"（已对齐 {metrics['linked_reposts']} / 待补齐 {metrics['unresolved_reposts']}）",
        f"面积归一失败 {sum(1 for n in normalized if n['area_num'] is None)} 条；"
        f"blocking 候选对 {metrics['pairs_scored']} 组，阈值 {threshold:.2f} 连边 {metrics['pairs_linked']} 条",
        f"并查集 + 传递闭包得到 {metrics['clusters']} 个簇，其中多成员簇 {metrics['multi_clusters']} 个，"
        f"去重率 {metrics['dedup_rate']:.2%}（按直达记录口径）",
        f"报价 {metrics['quotes']} 份 = 去重中介数；转载只作佐证，待补齐转载 {metrics['unresolved_reposts']} 条留在来源台账",
        f"算法 B：钓鱼价 {baits} 条、僵尸挂牌 {stales} 条，高风险簇 {risk_high} 个、中风险簇 {risk_medium} 个",
        f"淘汰旧簇 {pruned} 个；耗时 {metrics['duration_ms']} ms",
    ]
    return {"metrics": metrics, "log": "\n".join(log_lines), "status": "success"}


def _prune_orphans(db, seen_keys: set[str]) -> int:
    """删掉本次重算不再产生、且没有复核单的旧簇（有复核单的保留，避免丢历史结论）。"""
    removed = 0
    for prop in db.scalars(select(Property)).all():
        if prop.cluster_key in seen_keys:
            continue
        reviews = db.scalar(select(func.count()).select_from(Review).where(Review.property_id == prop.id)) or 0
        if reviews:
            continue
        for listing in db.scalars(select(Listing).where(Listing.property_id == prop.id)).all():
            listing.property_id = None
            listing.is_quote = False
            listing.quote_rank = None
        db.delete(prop)
        removed += 1
    db.flush()
    return removed


def last_run_stamp(db) -> str | None:
    run = db.scalar(select(MatchRun).order_by(MatchRun.id.desc()).limit(1))
    return iso_utc(run.finished_at) if run else None
