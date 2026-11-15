"""端到端流水线：seed → 重算 → 对账 → 房源详情追溯（SQLite，事务隔离）。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from scripts.seed_demo import ensure_agencies, ensure_listings, ensure_reviews, ensure_users
from src.models import Base
from src.models.listing import Listing
from src.models.property import Property
from src.services import (
    export_service,
    match_run_service,
    property_service,
    provenance_service,
    review_service,
    source_service,
)


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline.db'}", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def seeded(db):
    ensure_users(db)
    ensure_agencies(db)
    ensure_listings(db)
    run = match_run_service.create_and_execute(db, trigger="seed", operator_email="admin@listingmerge.io")
    ensure_reviews(db)
    db.commit()
    return db, run


def test_pipeline_reconciliation_is_balanced(seeded):
    db, run = seeded
    reconcile = export_service.reconciliation(db)
    assert reconcile["record_count"] == 644
    assert reconcile["direct_count"] == 572
    assert reconcile["linked_repost_count"] == 48
    assert reconcile["unresolved_repost_count"] == 24
    assert reconcile["agency_count"] == 540
    assert reconcile["quote_count"] == 540
    assert reconcile["quote_rows"] == 540
    assert reconcile["cluster_rows"] == 386
    assert reconcile["listing_rows"] == 644
    assert reconcile["review_rows"] == 6
    assert reconcile["balanced"] is True
    assert run.metrics["reconciled"]["balanced"] is True


def test_export_row_counts_match_reconciliation(seeded):
    db, _run = seeded
    reconcile = export_service.reconciliation(db)
    for content, expected in (
        (export_service.clusters_csv(db), reconcile["cluster_rows"]),
        (export_service.listings_csv(db), reconcile["listing_rows"]),
        (export_service.reviews_csv(db), reconcile["review_rows"]),
    ):
        lines = content.decode("utf-8-sig").splitlines()
        assert len(lines) - 1 == expected


def test_unresolved_reposts_stay_on_ledger_and_out_of_estimate(seeded):
    db, _run = seeded
    unresolved = db.scalars(
        select(Listing).where(Listing.record_kind == "repost", Listing.provenance_status == "unresolved")
    ).all()
    assert len(unresolved) == 24
    for listing in unresolved:
        assert listing.property_id is None
        assert listing.is_quote is False
    ledger = source_service.list_sources(db, provenance_status="unresolved", limit=200)
    assert ledger["total"] == 24
    assert all(item["property_id"] is None for item in ledger["items"])


def test_linked_reposts_are_evidence_only(seeded):
    db, _run = seeded
    linked = db.scalars(
        select(Listing).where(Listing.record_kind == "repost", Listing.provenance_status == "linked")
    ).all()
    assert len(linked) == 48
    for listing in linked:
        assert listing.is_quote is False
        assert listing.property_id is not None
        assert listing.source_listing_id is not None


def test_property_detail_reaches_quote_and_reposts(seeded):
    db, _run = seeded
    candidates = db.scalars(select(Property).where(Property.community == "翠湖名邸")).all()
    prop = next(item for item in candidates if abs(item.area_mode - 89.5) < 1e-9)
    assert prop is not None
    detail = property_service.get_property(db, prop.id)
    assert detail["quote_count"] == 4
    assert detail["agency_count"] == 4
    assert len(detail["quotes"]) == 4
    # 每份报价都能指向一条中介自己的直达发布
    for quote in detail["quotes"]:
        assert quote["agency_id"]
        assert quote["lid"]
        assert quote["price_num"]
    # 该簇有一条对齐转载，挂在对应中介的采用发布下
    total_reposts = sum(len(quote["reposts"]) for quote in detail["quotes"])
    assert total_reposts == detail["linked_repost_count"] == 1


def test_each_agency_appears_once_per_cluster(seeded):
    db, _run = seeded
    for prop in db.scalars(select(Property)).all():
        quotes = db.scalars(
            select(Listing).where(Listing.property_id == prop.id, Listing.is_quote.is_(True))
        ).all()
        assert len({row.agency_id for row in quotes}) == len(quotes)
        assert len(quotes) == prop.quote_count
        assert prop.agency_count == prop.quote_count


def test_fixing_origin_lets_unresolved_join_next_run(seeded):
    """补齐原发布后重算：该条转载自动转为 linked，仍不计报价。"""
    db, _run = seeded
    agency = db.scalar(
        select(Listing).where(Listing.record_kind == "repost", Listing.provenance_status == "unresolved")
    )
    target = db.scalars(select(Listing).where(Listing.record_kind == "direct")).first()
    from src.models.agency import Agency

    agency_row = db.scalar(select(Agency).where(Agency.id == target.agency_id))
    agency.origin_agency_code = agency_row.code
    agency.origin_agency_raw = None
    agency.origin_lid = target.lid
    db.commit()

    before = export_service.reconciliation(db)
    match_run_service.create_and_execute(db, trigger="manual", operator_email="analyst@listingmerge.io")
    db.commit()
    db.refresh(agency)
    after = export_service.reconciliation(db)

    assert agency.provenance_status == "linked"
    assert agency.is_quote is False
    assert after["linked_repost_count"] == before["linked_repost_count"] + 1
    assert after["unresolved_repost_count"] == before["unresolved_repost_count"] - 1
    assert after["record_count"] == before["record_count"]
    assert after["balanced"] is True


def test_reseed_is_idempotent(seeded):
    db, _run = seeded
    ensure_users(db)
    ensure_agencies(db)
    created = ensure_listings(db)
    db.commit()
    assert created == 0
    assert export_service.reconciliation(db)["record_count"] == 644
