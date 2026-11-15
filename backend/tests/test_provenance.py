"""来源链路（转载对齐）与「一中介一份报价」的纯函数测试。"""
from __future__ import annotations

from src.constants.enums import ProvenanceStatus, RecordKind
from src.services import provenance_service


AGENCIES = {
    1: {"code": "lianjia", "name": "链家 · 浦东花木店"},
    2: {"code": "anjuke", "name": "安居客 · 浦东频道"},
    3: {"code": "fangdd", "name": "房多多 · 线上撮合"},
}


def _record(rid, agency_id, *, kind=RecordKind.DIRECT.value, lid="L-1", origin_code=None, origin_raw=None, origin_lid=None, status="active"):
    return {
        "id": rid,
        "agency_id": agency_id,
        "record_kind": kind,
        "lid": lid,
        "status": status,
        "origin_agency_code": origin_code,
        "origin_agency_raw": origin_raw,
        "origin_lid": origin_lid,
    }


def test_direct_record_is_its_own_quote():
    records = [_record(1, 1)]
    info = provenance_service.resolve_records(records, AGENCIES)
    assert info[1]["provenance_status"] == "direct"
    assert info[1]["is_quote"] is True


def test_repost_linked_when_origin_agency_and_lid_match_direct():
    records = [
        _record(1, 1, lid="LIANJIA-0001"),
        _record(2, 2, kind=RecordKind.REPOST.value, lid="ANJUKE-0001", origin_code="lianjia", origin_lid="LIANJIA-0001"),
    ]
    info = provenance_service.resolve_records(records, AGENCIES)
    repost = info[2]
    assert repost["provenance_status"] == ProvenanceStatus.LINKED.value
    assert repost["source_listing_id"] == 1
    assert repost["origin_agency_id"] == 1
    assert repost["is_quote"] is False  # 转载只作佐证


def test_repost_origin_agency_can_be_resolved_by_name():
    records = [
        _record(1, 1, lid="LIANJIA-0001"),
        _record(2, 3, kind=RecordKind.REPOST.value, lid="FANGDD-0001", origin_raw="链家 · 浦东花木店", origin_lid="LIANJIA-0001"),
    ]
    info = provenance_service.resolve_records(records, AGENCIES)
    assert info[2]["provenance_status"] == ProvenanceStatus.LINKED.value
    assert info[2]["source_listing_id"] == 1


def test_unresolved_reasons():
    records = [
        _record(1, 1, lid="LIANJIA-0001"),
        _record(2, 2, kind=RecordKind.REPOST.value, lid="A-1", origin_code="unknown-broker", origin_lid="X-1"),
        _record(3, 2, kind=RecordKind.REPOST.value, lid="A-2", origin_code="lianjia", origin_lid="LIANJIA-9999"),
        _record(4, 2, kind=RecordKind.REPOST.value, lid="A-3", origin_code="lianjia"),
        _record(5, 2, kind=RecordKind.REPOST.value, lid="A-4"),
    ]
    info = provenance_service.resolve_records(records, AGENCIES)
    assert info[2]["provenance_reason"] == "origin_agency_unknown"
    assert info[3]["provenance_reason"] == "origin_listing_not_found"
    assert info[4]["provenance_reason"] == "origin_lid_missing"
    assert info[5]["provenance_reason"] == "origin_agency_missing"
    for rid in (2, 3, 4, 5):
        assert info[rid]["provenance_status"] == ProvenanceStatus.UNRESOLVED.value
        assert info[rid]["source_listing_id"] is None


def test_original_lid_kept_verbatim():
    """对齐不上时原挂牌编号必须原样保留在台账，供后续补齐核对。"""
    records = [
        _record(2, 2, kind=RecordKind.REPOST.value, lid="A-2", origin_code="lianjia", origin_lid="LIANJIA-9999"),
    ]
    info = provenance_service.resolve_records(records, AGENCIES)
    assert info[2]["origin_lid"] == "LIANJIA-9999"
    assert info[2]["origin_agency_id"] == 1


def test_select_quotes_one_per_agency_and_skip_reposts():
    records = [
        _record(1, 1, lid="L-1", status="sold"),
        _record(2, 1, lid="L-2", status="active"),   # 同中介更晚的在售发布，胜出
        _record(3, 2, kind=RecordKind.REPOST.value, lid="R-1"),
        _record(4, 3, lid="F-1"),
    ]
    quotes = provenance_service.select_quotes(records)
    assert [row["id"] for row in quotes] == [2, 4]


def test_ledger_never_drops_unresolved_reposts():
    records = [
        _record(1, 1, lid="L-1"),
        _record(2, 2, kind=RecordKind.REPOST.value, lid="R-1"),
        _record(3, 3, kind=RecordKind.REPOST.value, lid="R-2", origin_code="lianjia", origin_lid="L-1"),
    ]
    info = provenance_service.resolve_records(records, AGENCIES)
    ledger = provenance_service.build_ledger(records, info)
    assert {row["listing_id"] for row in ledger} == {2, 3}
    assert sum(1 for row in ledger if row["provenance_status"] == "unresolved") == 1
    assert sum(1 for row in ledger if row["provenance_status"] == "linked") == 1
