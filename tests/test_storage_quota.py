"""Storage-Quota-Tests (PR 6).

Akzeptanz-Kriterium:
  Paralleltest – 2 gleichzeitige Uploads, die zusammen die Quota sprengen:
  genau einer schlägt mit 413 fehl.

Weitere Tests:
  - reserve_storage: Einzelreservierung, Überschreitung, Release.
  - Quotaprüfung bei NULL-Quota (unbegrenzt).
"""
import asyncio
import io
import os
import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

# BigInteger → INTEGER für SQLite
@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"

from app.db import Base
from app.models.master import FireDept, OrgStorageUsage
from app.services.storage_service import (
    get_org_storage_info,
    reconcile_storage,
    release_storage,
    reserve_storage,
)


TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture()
def db():
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def orgs(db):
    org_a = FireDept(slug="quota-a", name="Quota Org A", color="#ff0000", bos="Feuerwehr",
                     storage_quota_bytes=1000)
    org_b = FireDept(slug="quota-b", name="Quota Org B", color="#00ff00", bos="Feuerwehr",
                     storage_quota_bytes=None)  # unbegrenzt
    db.add_all([org_a, org_b])
    db.flush()
    return org_a, org_b


# ── Grundlegende Reserve / Release ────────────────────────────────

def test_reserve_within_quota(db, orgs):
    org_a, _ = orgs
    reserve_storage(db, org_a.id, 400)
    info = get_org_storage_info(db, org_a.id)
    assert info["used_bytes"] == 400


def test_reserve_exact_quota(db, orgs):
    org_a, _ = orgs
    reserve_storage(db, org_a.id, 1000)
    info = get_org_storage_info(db, org_a.id)
    assert info["used_bytes"] == 1000


def test_reserve_exceeds_quota_raises_413(db, orgs):
    org_a, _ = orgs
    reserve_storage(db, org_a.id, 500)
    with pytest.raises(HTTPException) as exc_info:
        reserve_storage(db, org_a.id, 600)  # 500 + 600 > 1000
    assert exc_info.value.status_code == 413


def test_release_decrements(db, orgs):
    org_a, _ = orgs
    reserve_storage(db, org_a.id, 700)
    release_storage(db, org_a.id, 300)
    info = get_org_storage_info(db, org_a.id)
    assert info["used_bytes"] == 400


def test_release_floor_at_zero(db, orgs):
    org_a, _ = orgs
    reserve_storage(db, org_a.id, 100)
    release_storage(db, org_a.id, 9999)
    info = get_org_storage_info(db, org_a.id)
    assert info["used_bytes"] == 0


# ── Unbegrenzte Quota (NULL) ───────────────────────────────────────

def test_unlimited_quota_never_raises(db, orgs):
    _, org_b = orgs
    # Should never raise, even for large values
    reserve_storage(db, org_b.id, 10**12)
    info = get_org_storage_info(db, org_b.id)
    assert info["used_bytes"] == 10**12
    assert info["quota_bytes"] is None


# ── Org-Isolation ─────────────────────────────────────────────────

def test_quotas_are_org_isolated(db, orgs):
    org_a, org_b = orgs
    reserve_storage(db, org_a.id, 900)
    reserve_storage(db, org_b.id, 500)
    # Org A should be near limit, Org B unaffected
    info_a = get_org_storage_info(db, org_a.id)
    info_b = get_org_storage_info(db, org_b.id)
    assert info_a["used_bytes"] == 900
    assert info_b["used_bytes"] == 500
    # Org A: one more byte up to limit works
    reserve_storage(db, org_a.id, 100)
    # Org A: now full
    with pytest.raises(HTTPException):
        reserve_storage(db, org_a.id, 1)
    # Org B: still unlimited
    reserve_storage(db, org_b.id, 10**9)


# ── Paralleltest: genau einer schlägt fehl ─────────────────────────

def test_parallel_uploads_exactly_one_fails(db, orgs):
    """Zwei gleichzeitige Reservierungen die zusammen die Quota sprengen:
    genau eine muss mit 413 fehlschlagen."""
    org_a, _ = orgs
    results = []

    async def _upload(n_bytes: int):
        try:
            reserve_storage(db, org_a.id, n_bytes)
            results.append("ok")
        except HTTPException as exc:
            results.append(exc.status_code)

    async def _run():
        await asyncio.gather(
            _upload(600),
            _upload(600),
        )

    asyncio.run(_run())

    assert results.count("ok") == 1
    assert results.count(413) == 1


# ── reconcile_storage: KONS-1 Regressionstest ──────────────────────
# reconcile_storage summierte bislang lage_journal_media und cross_marker_media
# nicht mit -> nach einem Reconcile waere die Quota faelschlich niedriger als
# tatsaechlich belegt (De-facto-Bypass, da danach wieder mehr hochgeladen werden
# koennte als die konfigurierte Quota erlaubt).

def test_reconcile_storage_includes_journal_and_cross_marker_media(db, orgs):
    from app.models.major_incident import (
        CrossMarkerMedia,
        CrossSiteMarker,
        IncidentSite,
        LageJournalEntry,
        LageJournalMedia,
        MajorIncident,
        SiteMedia,
    )

    org_a, _ = orgs
    lage = MajorIncident(org_id=org_a.id, name="Reconcile-Testlage")
    db.add(lage)
    db.flush()

    site = IncidentSite(major_incident_id=lage.id, org_id=org_a.id, bezeichnung="Stelle")
    db.add(site)
    db.flush()
    db.add(SiteMedia(incident_site_id=site.id, stored_filename="a.jpg",
                      original_filename="a.jpg", media_type="image", bytes=100, org_id=org_a.id))

    entry = LageJournalEntry(major_incident_id=lage.id, text="Eintrag")
    db.add(entry)
    db.flush()
    db.add(LageJournalMedia(journal_entry_id=entry.id, stored_filename="b.jpg",
                             original_filename="b.jpg", media_type="image", bytes=200, org_id=org_a.id))

    marker = CrossSiteMarker(major_incident_id=lage.id, title="Marker", org_id=org_a.id)
    db.add(marker)
    db.flush()
    db.add(CrossMarkerMedia(marker_id=marker.id, stored_filename="c.jpg",
                             original_filename="c.jpg", media_type="image", bytes=300, org_id=org_a.id))
    db.commit()

    total = reconcile_storage(db, org_a.id)
    assert total == 600, "reconcile_storage zaehlt journal_media/cross_marker_media nicht mit (KONS-1-Regression)"


# ── Retry bei transienten MariaDB-Sperrkonflikten (Vorfall 2026-07-06) ─────────
# INSERT/UPDATE auf das gemeinsame org_storage_usage-Row warf unter paralleler
# Dokumentverarbeitung MySQL-Error 1020 ("Record has changed since last read;
# try restarting transaction") -> Dokument-Verarbeitung schlug fehl. _execute
# muss genau diese transienten Sperrfehler ein paar Mal wiederholen.

def _fake_operational_error(errno: int):
    from sqlalchemy.exc import OperationalError

    class _Orig(Exception):
        pass

    orig = _Orig("boom")
    orig.args = (errno, "transient")
    return OperationalError("UPDATE ...", {}, orig)


def test_execute_retries_transient_lock_error_then_succeeds():
    from sqlalchemy import text

    from app.services import storage_service

    calls = {"n": 0}

    class _FakeDB:
        def execute(self, stmt, params):
            calls["n"] += 1
            if calls["n"] < 3:
                raise _fake_operational_error(1020)
            return "ok"

    result = storage_service._execute(_FakeDB(), text("UPDATE x"), {})
    assert result == "ok"
    assert calls["n"] == 3  # zweimal 1020, dritter Versuch erfolgreich


def test_execute_gives_up_after_max_retries():
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from app.services import storage_service

    class _FakeDB:
        def execute(self, stmt, params):
            raise _fake_operational_error(1020)

    with pytest.raises(OperationalError):
        storage_service._execute(_FakeDB(), text("UPDATE x"), {})


def test_execute_does_not_retry_non_lock_errors():
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from app.services import storage_service

    calls = {"n": 0}

    class _FakeDB:
        def execute(self, stmt, params):
            calls["n"] += 1
            raise _fake_operational_error(1064)  # Syntax error – nicht retrybar

    with pytest.raises(OperationalError):
        storage_service._execute(_FakeDB(), text("UPDATE x"), {})
    assert calls["n"] == 1  # genau ein Versuch, kein Retry
