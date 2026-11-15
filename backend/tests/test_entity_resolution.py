"""业务模块 ③④ 算法 A：blocking / 相似度 / 并查集 / golden record。"""
from __future__ import annotations

from src.constants.limits import SIMILARITY_THRESHOLD
from src.services import entity_resolution as er
from src.services import normalizer


def _row(community="翠湖名邸", layout="3-2-1", area="89.5", price="598万", floor="2/18", orientation="南北", title="3-2-1 89.5平方米 南北 2层 满五唯一"):
    return normalizer.normalize_listing(
        {
            "community_raw": community,
            "layout_raw": layout,
            "area_raw": area,
            "price_raw": price,
            "floor_raw": floor,
            "orientation_raw": orientation,
            "title": title,
        }
    )


def test_duplicate_pair_scores_above_threshold():
    left = _row(area="89.5㎡", price="598万")
    right = _row(area="建面89.5", price="602万")
    detail = er.similarity(left, right)
    assert detail["score"] >= SIMILARITY_THRESHOLD
    assert detail["area"] == 1.0
    assert detail["floor"] == 1.0
    assert detail["orientation"] == 1.0
    assert detail["score"] == round(
        0.35 * detail["area"] + 0.25 * detail["price"] + 0.20 * detail["floor"]
        + 0.10 * detail["orientation"] + 0.10 * detail["title"],
        4,
    )


def test_distinct_property_scores_below_threshold():
    left = _row(area="89.5", price="598万")
    right = _row(area="89.5", price="598万", floor="12/18", orientation="北", title="2-1-1 62平方米 北 12层 采光好")
    detail = er.similarity(left, right)
    assert detail["score"] < SIMILARITY_THRESHOLD


def test_blocking_excludes_far_area_and_other_community():
    rows = [
        _row(area="89.5"),
        _row(area="91.5"),  # 同小区同户型，面积差 2㎡ -> 候选
        _row(area="118"),  # 面积差过大 -> 不入候选
        _row(community="仁恒河滨城", area="89.5"),  # 换小区 -> 不入候选
    ]
    pairs = er.candidate_pairs(rows)
    assert (0, 1) in pairs
    assert all(2 not in pair and 3 not in pair for pair in pairs)


def test_union_find_transitive_closure():
    """A~B、B~C 都过阈值，则 A/B/C 归为同一簇（传递闭包）。"""
    rows = [
        _row(area="89.5", price="598万"),
        _row(area="89.5", price="602万"),
        _row(area="89.5", price="606万"),
    ]
    result = er.resolve(rows, [1, 2, 3])
    assert result["metrics"]["clusters"] == 1
    assert result["clusters"][0]["members"] == [0, 1, 2]
    assert 0.8 <= result["clusters"][0]["confidence"] <= 1.0


def test_golden_record_median_mode_and_agency_count():
    rows = [_row(area="89.5", price="598万"), _row(area="89.5", price="602万"), _row(area="89.5", price="610万")]
    result = er.resolve(rows, [11, 22, 33])
    golden = result["clusters"][0]["golden"]
    assert golden["price_median"] == 6_020_000
    assert golden["area_mode"] == 89.5
    assert golden["price_min"] == 5_980_000
    assert golden["price_max"] == 6_100_000
    assert golden["spread"] == 120_000
    assert golden["agency_count"] == 3
    assert golden["member_count"] == 3


def test_golden_record_prices_only_selected_quotes():
    """同一中介两条直达、外加一条转载时：价格中位与中介数只按 3 份报价算。"""
    rows = [
        _row(area="89.5", price="598万"),
        _row(area="89.5", price="602万"),
        _row(area="89.5", price="606万"),
        _row(area="89.5", price="598万"),  # 同中介重复发布
        _row(area="89.5", price="598万"),  # 转载
    ]
    meta = [
        {"id": 1, "agency_id": 11, "status": "active", "record_kind": "direct"},
        {"id": 2, "agency_id": 22, "status": "active", "record_kind": "direct"},
        {"id": 3, "agency_id": 33, "status": "active", "record_kind": "direct"},
        {"id": 4, "agency_id": 11, "status": "active", "record_kind": "direct"},
        {"id": 5, "agency_id": 44, "status": "active", "record_kind": "repost"},
    ]
    result = er.resolve(rows, [11, 22, 33, 11, 44], record_meta=meta)
    golden = result["clusters"][0]["golden"]
    assert golden["member_count"] == 5
    assert golden["quote_count"] == 3
    assert golden["agency_count"] == 3
    assert golden["price_min"] == 5_980_000
    assert golden["price_max"] == 6_060_000
    # 报价是每家入库更晚的那条：中介 11 的 id=4
    assert result["clusters"][0]["quotes"] == [3, 1, 2]


def test_quote_prefers_active_over_sold():
    rows = [_row(area="89.5", price="598万"), _row(area="89.5", price="620万")]
    meta = [
        {"id": 1, "agency_id": 11, "status": "sold", "record_kind": "direct"},
        {"id": 2, "agency_id": 11, "status": "active", "record_kind": "direct"},
    ]
    result = er.resolve(rows, [11, 11], record_meta=meta)
    assert result["clusters"][0]["quotes"] == [1]
    assert result["clusters"][0]["golden"]["price_median"] == 6_200_000


def test_deterministic_resolution():
    rows = [_row(area="89.5", price="598万"), _row(area="89.5", price="602万"), _row(area="118", price="780万")]
    first = er.resolve(rows, [1, 2, 3])
    second = er.resolve(rows, [1, 2, 3])
    assert first == second


def test_threshold_controls_linking():
    rows = [_row(area="89.5", price="598万"), _row(area="89.5", price="700万")]
    loose = er.resolve(rows, [1, 2], threshold=0.80)
    strict = er.resolve(rows, [1, 2], threshold=0.99)
    assert loose["metrics"]["clusters"] == 1
    assert strict["metrics"]["clusters"] == 2
