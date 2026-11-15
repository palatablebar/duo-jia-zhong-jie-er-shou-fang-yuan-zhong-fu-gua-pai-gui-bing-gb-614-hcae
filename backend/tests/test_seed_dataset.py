"""业务验收：seed 数据集经来源链路 + 算法 A/B 之后的口径必须与 batch-plan 一致。

口径变化（相比纯挂牌归并）：
- 572 条直达发布（含 32 条同店重复发布）+ 72 条转载 = 644 条采集记录；
- 只有直达记录参与实体解析 → 386 个簇、41 个多成员簇；
- 同一中介一簇只算一份报价 → 540 份报价 = 540 = 归并中介数；
- 48 条转载对齐到直达记录（只作佐证），24 条待补齐（留来源台账、不参与）。
"""
from __future__ import annotations

from collections import Counter

from scripts.demo_config import AGENCIES, build_listings
from src.services import entity_resolution, normalizer, provenance_service, risk_rules


def _resolve():
    clusters, records = build_listings()
    agencies = {index + 1: {"code": spec["code"], "name": spec["name"]} for index, spec in enumerate(AGENCIES)}
    code_to_id = {spec["code"]: index + 1 for index, spec in enumerate(AGENCIES)}
    for offset, row in enumerate(records, start=1):
        row["id"] = offset
        row["agency_id"] = code_to_id[row["agency_code"]]
    provenance = provenance_service.resolve_records(records, agencies)
    direct = [row for row in records if row["record_kind"] == "direct"]
    norms = [normalizer.normalize_listing(row) for row in direct]
    meta = [
        {"id": row["id"], "agency_id": row["agency_id"], "status": row["status"], "record_kind": "direct"}
        for row in direct
    ]
    result = entity_resolution.resolve(norms, [row["agency_id"] for row in direct], record_meta=meta)
    truth = {row["id"]: cluster["index"] for cluster in clusters for row in cluster["records"]}
    return clusters, records, direct, norms, result, truth, provenance


def test_dataset_shape():
    clusters, records, direct, _norms, _result, _truth, provenance = _resolve()
    assert len(clusters) == 386
    assert len(records) == 644
    assert len(direct) == 572  # 540 条基线 + 32 条同店重复发布
    assert Counter(row["record_kind"] for row in records) == {"direct": 572, "repost": 72}
    assert Counter(row["agency_code"] for row in direct) == {
        "lianjia": 116, "wawj": 115, "zhongyuan": 114, "zhijia": 114, "hongkan": 113
    }
    # 转载渠道只转载、不自己发布
    assert {row["agency_code"] for row in records if row["record_kind"] == "repost"} == {
        "fangdd", "anjuke", "ke", "taofang"
    }
    # 72 条转载：48 对齐 + 24 待补齐，且三种待补齐原因各 8 条
    statuses = Counter(
        provenance[row["id"]]["provenance_status"] for row in records if row["record_kind"] == "repost"
    )
    assert statuses == {"linked": 48, "unresolved": 24}
    reasons = Counter(
        provenance[row["id"]]["provenance_reason"]
        for row in records
        if row["record_kind"] == "repost" and provenance[row["id"]]["provenance_status"] == "unresolved"
    )
    assert reasons == {
        "origin_agency_unknown": 8,
        "origin_listing_not_found": 8,
        "origin_lid_missing": 8,
    }


def test_seed_dataset_clusters():
    _clusters, records, direct, _norms, result, truth, _prov = _resolve()
    assert result["metrics"]["listings"] == 572
    assert result["metrics"]["clusters"] == 386
    assert result["metrics"]["multi_clusters"] == 41
    # 每个预测簇都只含同一套真值房源（没有过度合并）
    for cluster in result["clusters"]:
        assert len({truth[direct[index]["id"]] for index in cluster["members"]}) == 1
    # 真值簇数量与预测一致（没有漏并）
    assert len({truth[row["id"]] for row in direct}) == 386


def test_one_quote_per_agency():
    _clusters, _records, direct, _norms, result, _truth, _prov = _resolve()
    for cluster in result["clusters"]:
        agencies = {direct[index]["agency_id"] for index in cluster["quotes"]}
        assert len(agencies) == len(cluster["quotes"]), "同一中介一个簇只能有一份报价"
        assert set(cluster["quotes"]).issubset(set(cluster["members"]))
    total_quotes = sum(cluster["golden"]["quote_count"] for cluster in result["clusters"])
    assert total_quotes == 540
    assert sum(cluster["golden"]["agency_count"] for cluster in result["clusters"]) == 540


def test_special_cluster_cross_agency():
    _clusters, _records, direct, _norms, result, _truth, _prov = _resolve()
    matched = [
        cluster
        for cluster in result["clusters"]
        if cluster["golden"]["layout"] == "3-2-1" and abs(cluster["golden"]["area_mode"] - 89.5) < 1e-9
    ]
    assert matched, "应有 89.5㎡ 的 3-2-1 簇"
    golden = matched[0]["golden"]
    assert golden["community"] == "翠湖名邸"
    # 4 家门店各一条直达 + 1 条同店重复发布；报价仍 4 份
    assert golden["member_count"] == 5
    assert golden["agency_count"] == 4
    assert golden["quote_count"] == 4
    assert golden["spread"] == 120_000
    assert golden["price_median"] == 6_040_000


def test_seed_dataset_risk_profile():
    _clusters, _records, direct, norms, result, _truth, _prov = _resolve()
    risky = []
    baits = stales = 0
    for cluster in result["clusters"]:
        members = [
            {
                "id": direct[index]["id"],
                "lid": direct[index]["lid"],
                "price_num": norms[index]["price_num"],
                "listed_days": direct[index]["listed_days"],
                "views": direct[index]["views"],
                "status": direct[index]["status"],
            }
            for index in cluster["quotes"]
        ]
        risk = risk_rules.scan_cluster(members, cluster["golden"])
        if risk["risk_level"] in {"high", "medium"}:
            risky.append(risk)
        baits += len(risk["risk_detail"]["bait_lids"])
        stales += len(risk["risk_detail"]["stale_lids"])
    assert len(risky) == 6, len(risky)
    assert sum(1 for risk in risky if "bait" in risk["risk_tags"]) == 1
    assert baits == 2
    assert stales == 5
    assert max(risk["risk_score"] for risk in risky) == 65


def test_source_ledger_keeps_every_repost():
    """转载归并不能丢记录：72 条转载在台账里都在，对齐的带原发布指针。"""
    clusters, records, _direct, _norms, _result, _truth, provenance = _resolve()
    ledger = provenance_service.build_ledger(records, provenance)
    assert len(ledger) == 72
    assert sum(1 for row in ledger if row["provenance_status"] == "linked") == 48
    assert sum(1 for row in ledger if row["provenance_status"] == "unresolved") == 24
    for row in ledger:
        if row["provenance_status"] == "linked":
            assert row["source_listing_id"], "对齐的转载必须指向直达记录"
        else:
            assert row["property_id"] is None, "待补齐的转载不挂簇、不进估价清单"
