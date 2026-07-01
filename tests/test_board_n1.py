"""Regressionstest PR3 (PERF-1): Board-Query darf nicht mit der Anzahl der
Einsatzstellen wachsen (N+1 auf resources/media in _lage_or_404(eager_sites=True))."""
import pytest
from sqlalchemy import BigInteger, create_engine, event
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.major_incident import IncidentSite, MajorIncident, SiteMedia, SiteResourceAssignment
from app.routers.ui_major_incident import _lage_or_404

TEST_DB_URL = "sqlite:///:memory:"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


@pytest.fixture()
def db():
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    set_tenant_context(session, None)
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


def _make_lage_with_sites(db, n_sites: int) -> int:
    lage = MajorIncident(org_id=1, name="Testlage")
    db.add(lage)
    db.flush()
    for i in range(n_sites):
        site = IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung=f"Stelle {i}")
        db.add(site)
        db.flush()
        db.add(SiteResourceAssignment(incident_site_id=site.id, resource_type="free_text", label="Fahrzeug"))
        db.add(SiteMedia(incident_site_id=site.id, stored_filename="a.jpg",
                          original_filename="a.jpg", media_type="image"))
    db.commit()
    return lage.id


def _count_queries(db, fn):
    counter = {"n": 0}

    def _cb(*args, **kwargs):
        counter["n"] += 1

    event.listen(db.bind, "before_cursor_execute", _cb)
    try:
        fn()
    finally:
        event.remove(db.bind, "before_cursor_execute", _cb)
    return counter["n"]


def test_board_eager_load_query_count_independent_of_site_count(db):
    lage_id_small = _make_lage_with_sites(db, 2)
    lage_id_big = _make_lage_with_sites(db, 12)
    db.expire_all()

    def _load_and_touch(lid):
        lage = _lage_or_404(lid, db, eager_sites=True)
        for site in lage.sites:
            list(site.resources)
            list(site.media)

    n_small = _count_queries(db, lambda: _load_and_touch(lage_id_small))
    db.expire_all()
    n_big = _count_queries(db, lambda: _load_and_touch(lage_id_big))

    assert n_small == n_big, (
        f"Query-Zahl waechst mit Einsatzstellenzahl ({n_small} vs {n_big}) — "
        "selectinload greift nicht (PERF-1-Regression)."
    )
    # 1 (MajorIncident) + 1 (sites selectinload) + 1 (resources selectinload) + 1 (media selectinload)
    assert n_small <= 4


def test_board_without_eager_load_shows_n1(db):
    """Gegenprobe: ohne eager_sites waechst die Query-Zahl mit der Site-Anzahl."""
    lage_id_small = _make_lage_with_sites(db, 2)
    lage_id_big = _make_lage_with_sites(db, 12)
    db.expire_all()

    def _load_and_touch(lid):
        lage = _lage_or_404(lid, db, eager_sites=False)
        for site in lage.sites:
            list(site.resources)
            list(site.media)

    n_small = _count_queries(db, lambda: _load_and_touch(lage_id_small))
    db.expire_all()
    n_big = _count_queries(db, lambda: _load_and_touch(lage_id_big))

    assert n_big > n_small
