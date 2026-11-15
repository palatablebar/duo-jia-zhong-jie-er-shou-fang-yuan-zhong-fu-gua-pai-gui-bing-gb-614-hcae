"""来源链路｜转载记录对齐原发布中介，并挑选「一中介一份」的报价记录。

规则（业务方口径）：
- 直达记录（``record_kind=direct``）：中介自己发布，是簇内唯一可以计入报价的记录；
- 转载记录（``record_kind=repost``）：平台 / 数据服务渠道转载中介挂牌，必须保留
  ``origin_agency_*``（原发布中介）与 ``origin_lid``（原挂牌编号）；
  能按 (原发布中介, 原挂牌编号) 找到那条直达记录时 ``provenance_status=linked``，
  ``source_listing_id`` 指向直达记录，只作佐证、不计报价；
  对不上时 ``provenance_status=unresolved``，留在来源台账、不进估价清单，
  补齐原发布中介/编号并重算后才参与；
- 同一中介在同一簇里可能采到多条直达记录（重复发布），只保留一份报价：
  优先在售，其次入库更晚的一条（``id`` 更大），结果确定性。

纯函数（``resolve_records`` / ``select_quotes``）不访问数据库，便于单测。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.constants.enums import ProvenanceStatus, RecordKind

_STATUS_PRIORITY = {"active": 0, "sold": 1, "off_market": 2}


def _field(record: object, name: str, default=None):
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _agency_fields(agency: object) -> tuple[str, str]:
    return str(_field(agency, "code", "") or ""), str(_field(agency, "name", "") or "")


def _normalize_text(value: object) -> str:
    return str(value or "").strip().lower()


def _resolve_origin_agency(
    record: object,
    agency_by_code: Mapping[str, int],
    agency_by_name: Mapping[str, int],
) -> tuple[int | None, str | None]:
    """返回 (原发布中介 id, 失败原因)。"""
    code = _normalize_text(_field(record, "origin_agency_code"))
    raw = _field(record, "origin_agency_raw")
    if code:
        agency_id = agency_by_code.get(code)
        return (agency_id, None) if agency_id is not None else (None, "origin_agency_unknown")
    name = _normalize_text(raw)
    if name:
        if name in agency_by_code:
            return agency_by_code[name], None
        if name in agency_by_name:
            return agency_by_name[name], None
        return None, "origin_agency_unknown"
    return None, "origin_agency_missing"


def resolve_records(records: Sequence[object], agencies: Mapping[int, object]) -> dict[int, dict]:
    """为每条记录计算来源状态（不写库）。

    ``records`` 需含 ``id`` / ``agency_id`` / ``record_kind`` / ``origin_agency_code`` /
    ``origin_agency_raw`` / ``origin_lid``（ORM 对象或 dict 均可）。
    ``agencies``：``{agency_id: {"code", "name"}}``。
    """
    agency_by_code: dict[str, int] = {}
    agency_by_name: dict[str, int] = {}
    for agency_id, agency in agencies.items():
        code, name = _agency_fields(agency)
        if code:
            agency_by_code[code.lower()] = agency_id
        if name:
            agency_by_name[name.lower()] = agency_id

    direct_by_key: dict[tuple[int, str], object] = {}
    for record in records:
        if _field(record, "record_kind", RecordKind.DIRECT.value) != RecordKind.DIRECT.value:
            continue
        lid = str(_field(record, "lid", "") or "").strip()
        if not lid:
            continue
        direct_by_key[(int(_field(record, "agency_id")), lid)] = record

    results: dict[int, dict] = {}
    for record in records:
        record_id = int(_field(record, "id"))
        kind = _field(record, "record_kind", RecordKind.DIRECT.value)
        if kind == RecordKind.DIRECT.value:
            results[record_id] = {
                "record_kind": RecordKind.DIRECT.value,
                "provenance_status": RecordKind.DIRECT.value,
                "provenance_reason": None,
                "source_listing_id": None,
                "origin_agency_id": None,
                "origin_lid": None,
                "is_quote": True,
            }
            continue

        origin_agency_id, reason = _resolve_origin_agency(record, agency_by_code, agency_by_name)
        origin_lid = str(_field(record, "origin_lid", "") or "").strip()
        source_listing_id = None
        if reason is None and not origin_lid:
            reason = "origin_lid_missing"
        if reason is None and origin_agency_id is not None:
            target = direct_by_key.get((origin_agency_id, origin_lid))
            if target is None:
                reason = "origin_listing_not_found"
            else:
                source_listing_id = int(_field(target, "id"))
        linked = source_listing_id is not None
        results[record_id] = {
            "record_kind": RecordKind.REPOST.value,
            "provenance_status": ProvenanceStatus.LINKED.value if linked else ProvenanceStatus.UNRESOLVED.value,
            "provenance_reason": None if linked else (reason or "origin_listing_not_found"),
            "source_listing_id": source_listing_id,
            "origin_agency_id": origin_agency_id,
            "origin_lid": origin_lid or None,
            "is_quote": False,
        }
    return results


def quote_sort_key(record: object) -> tuple[int, int]:
    """报价记录排序：在售优先，其次入库更晚（id 更大）优先；确定性。"""
    status = str(_field(record, "status", "active") or "active")
    return _STATUS_PRIORITY.get(status, 3), -int(_field(record, "id", 0) or 0)


def select_quotes(records: Sequence[object]) -> list[object]:
    """同一簇内按中介挑一份报价：每家只留一条直达记录。

    转载记录永远不参与报价；同一中介的多条直达记录按 ``quote_sort_key`` 取第一条。
    返回顺序按中介 id 升序、再按报价记录 id 升序，便于确定性输出。
    """
    chosen: dict[int, object] = {}
    for record in records:
        if _field(record, "record_kind", RecordKind.DIRECT.value) != RecordKind.DIRECT.value:
            continue
        agency_id = int(_field(record, "agency_id"))
        current = chosen.get(agency_id)
        if current is None or quote_sort_key(record) < quote_sort_key(current):
            chosen[agency_id] = record
    return sorted(chosen.values(), key=lambda item: (int(_field(item, "agency_id")), int(_field(item, "id"))))


def build_ledger(records: Sequence[object], resolved: Mapping[int, dict]) -> list[dict]:
    """把转载记录整理成来源台账行（纯函数，供测试与核对）。"""
    ledger: list[dict] = []
    for record in records:
        if _field(record, "record_kind", RecordKind.DIRECT.value) != RecordKind.REPOST.value:
            continue
        info = resolved.get(int(_field(record, "id"))) or {}
        ledger.append(
            {
                "listing_id": int(_field(record, "id")),
                "agency_id": int(_field(record, "agency_id")),
                "lid": _field(record, "lid"),
                "record_kind": RecordKind.REPOST.value,
                "origin_agency_id": info.get("origin_agency_id"),
                "origin_lid": info.get("origin_lid"),
                "provenance_status": info.get("provenance_status"),
                "provenance_reason": info.get("provenance_reason"),
                "source_listing_id": info.get("source_listing_id"),
                "property_id": None,
            }
        )
    return ledger


def sync_source_ledger(db, listings: Sequence[object] | None = None, resolved: Mapping[int, dict] | None = None) -> int:
    """把转载记录的原发布信息 upsert 进来源台账；从不删除条目。

    已对齐的记录也留在台账（状态 ``linked``），便于从房源详情反查「采用谁家的哪条发布、
    对应哪些转载」；对不上的记录状态 ``unresolved``，不进估价清单，待补齐后重算再参与。
    返回台账中转载记录总数。
    """
    from sqlalchemy import select

    from src.constants.enums import ProvenanceStatus
    from src.models.listing import Listing
    from src.models.source_record import SourceRecord

    if listings is None:
        listings = list(db.scalars(select(Listing).order_by(Listing.id)).all())
    if resolved is None:
        from src.models.agency import Agency

        agencies = {agency.id: agency for agency in db.scalars(select(Agency)).all()}
        resolved = resolve_records(listings, agencies)

    existing = {
        row.listing_id: row for row in db.scalars(select(SourceRecord)).all()
    }
    for listing in listings:
        if _field(listing, "record_kind", RecordKind.DIRECT.value) != RecordKind.REPOST.value:
            continue
        info = resolved.get(int(listing.id)) or {}
        row = existing.get(int(listing.id))
        if row is None:
            row = SourceRecord(listing_id=listing.id)
            db.add(row)
            existing[int(listing.id)] = row
        row.agency_id = listing.agency_id
        row.lid = listing.lid
        row.record_kind = RecordKind.REPOST.value
        row.origin_agency_id = info.get("origin_agency_id")
        row.origin_agency_code = _field(listing, "origin_agency_code") or None
        row.origin_agency_raw = _field(listing, "origin_agency_raw") or None
        row.origin_lid = info.get("origin_lid")
        row.provenance_status = info.get("provenance_status") or ProvenanceStatus.UNRESOLVED.value
        row.provenance_reason = info.get("provenance_reason")
        row.source_listing_id = info.get("source_listing_id")
        row.property_id = listing.property_id
    db.flush()
    return sum(1 for row in db.scalars(select(SourceRecord)).all() if row.record_kind == RecordKind.REPOST.value)
