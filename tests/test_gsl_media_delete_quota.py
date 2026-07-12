"""Regressionstests (Session 2026-07-12): GSL-Medien-Loeschung (Site/CrossMarker/
LageJournal) gab die beim Upload reservierte Quota bisher NIE frei -- used_bytes
stand nach jedem geloeschten Foto dauerhaft zu hoch. Zusaetzlich: annotierte
Bilder wurden beim Loeschen der zugrundeliegenden Medienzeile weder von der
Platte entfernt noch ihre Quota freigegeben.
"""
import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.db import Base
from app.models.major_incident import (
    CrossMarkerMedia,
    CrossSiteMarker,
    IncidentSite,
    LageJournalEntry,
    LageJournalMedia,
    MajorIncident,
    SiteMedia,
)
from app.models.master import FireDept
from app.services import annotation_service as ann_svc
from app.services import lage_media_service
from app.services.storage_service import get_org_storage_info, reserve_storage

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
def org(db):
    o = FireDept(slug="gsl-quota", name="GSL Quota Org", color="#ff00ff", bos="Feuerwehr")
    db.add(o)
    db.flush()
    return o


@pytest.fixture()
def lage(db, org):
    lg = MajorIncident(org_id=org.id, name="Quota-Testlage")
    db.add(lg)
    db.flush()
    return lg


def _echtes_png_data_url() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color=(1, 2, 3)).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ── Site-Medien ────────────────────────────────────────────────────────────

def test_delete_site_media_gibt_quota_frei(db, org, lage):
    site = IncidentSite(major_incident_id=lage.id, org_id=org.id, bezeichnung="Stelle")
    db.add(site)
    db.flush()
    reserve_storage(db, org.id, 500)
    media = SiteMedia(incident_site_id=site.id, stored_filename="a.jpg",
                      original_filename="a.jpg", media_type="image", bytes=500, org_id=org.id)
    db.add(media)
    db.flush()

    lage_media_service.delete_site_media(media, db)
    db.commit()

    assert get_org_storage_info(db, org.id)["used_bytes"] == 0


# ── Cross-Marker-Medien ──────────────────────────────────────────────────────

def test_delete_cross_marker_media_gibt_quota_frei(db, org, lage):
    marker = CrossSiteMarker(major_incident_id=lage.id, title="Marker", org_id=org.id)
    db.add(marker)
    db.flush()
    reserve_storage(db, org.id, 300)
    media = CrossMarkerMedia(marker_id=marker.id, stored_filename="b.jpg",
                             original_filename="b.jpg", media_type="image", bytes=300, org_id=org.id)
    db.add(media)
    db.flush()

    lage_media_service.delete_cross_marker_media(media, db)
    db.commit()

    assert get_org_storage_info(db, org.id)["used_bytes"] == 0


# ── Lage-Journal-Medien ──────────────────────────────────────────────────────

def test_delete_journal_media_gibt_quota_frei(db, org, lage):
    entry = LageJournalEntry(major_incident_id=lage.id, text="Eintrag")
    db.add(entry)
    db.flush()
    reserve_storage(db, org.id, 200)
    media = LageJournalMedia(journal_entry_id=entry.id, stored_filename="c.jpg",
                             original_filename="c.jpg", media_type="image", bytes=200, org_id=org.id)
    db.add(media)
    db.flush()

    lage_media_service.delete_journal_media(media, db)
    db.commit()

    assert get_org_storage_info(db, org.id)["used_bytes"] == 0


# ── Annotation-Cleanup beim Loeschen ──────────────────────────────────────────

def test_delete_site_media_raeumt_annotierte_datei_und_deren_quota_auf(db, org, lage, monkeypatch, tmp_path):
    monkeypatch.setattr(lage_media_service, "_LAGE_MEDIA_DIR", str(tmp_path / "lage_media"))
    site = IncidentSite(major_incident_id=lage.id, org_id=org.id, bezeichnung="Stelle")
    db.add(site)
    db.flush()
    media = SiteMedia(incident_site_id=site.id, stored_filename="orig.jpg",
                      original_filename="orig.jpg", media_type="image", bytes=100, org_id=org.id)
    db.add(media)
    db.flush()
    reserve_storage(db, org.id, 100)

    ann_svc.save_annotation(db, SimpleNamespace(id=1), "site", media, '{"a":1}', _echtes_png_data_url())
    db.commit()
    abs_png = ann_svc._annotated_abs_path("site", media)
    assert abs_png.exists()
    used_after_annotate = get_org_storage_info(db, org.id)["used_bytes"]
    assert used_after_annotate == 100 + abs_png.stat().st_size

    lage_media_service.delete_site_media(media, db)
    db.commit()

    assert not abs_png.exists(), "annotierte PNG-Datei bleibt als Leiche liegen"
    assert get_org_storage_info(db, org.id)["used_bytes"] == 0
    assert ann_svc.get_annotation(db, "site", media.id) is None


# ── Ganzer Journal-Eintrag (HTTP-Route, Kaskade) ─────────────────────────────

def test_journal_entry_delete_route_gibt_quota_fuer_alle_fotos_frei(client, setup_db):
    from app.core.security import hash_password
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.user import Role, User, UserRole

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug="gsl-quota-http", name="GSL Quota HTTP Org", color="#00ffff", bos="Feuerwehr")
        db.add(org)
        db.flush()
        user = User(username="gsl_quota_admin", password_hash=hash_password("Test1234!"),
                    display_name="Admin", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == "incident_leader").first()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        lg = MajorIncident(org_id=org.id, name="HTTP-Quota-Testlage")
        db.add(lg)
        db.flush()
        entry = LageJournalEntry(major_incident_id=lg.id, text="Eintrag")
        db.add(entry)
        db.flush()
        reserve_storage(db, org.id, 150)
        media = LageJournalMedia(journal_entry_id=entry.id, stored_filename="d.jpg",
                                 original_filename="d.jpg", media_type="image", bytes=150, org_id=org.id)
        db.add(media)
        db.commit()
        org_id, lage_id, entry_id = org.id, lg.id, entry.id
    finally:
        db.close()

    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    r = client.post("/login", data={"username": "gsl_quota_admin", "password": "Test1234!", "_csrf": csrf},
                    follow_redirects=False)
    assert r.status_code == 302

    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/lage/{lage_id}/journal/{entry_id}/loeschen", data={"_csrf": csrf})
    assert r.status_code == 204

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert get_org_storage_info(db, org_id)["used_bytes"] == 0
    finally:
        db.close()
