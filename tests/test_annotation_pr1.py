"""PR1 Bild-Annotation: Resolver + Persistenz (Vektor-JSON, flaches PNG, Versionen)."""
import base64
from types import SimpleNamespace

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.config import settings
from app.db import Base
from app.models.media_annotation import MediaAnnotation, MediaAnnotationVersion
from app.services import annotation_service as ann


@pytest.fixture()
def db():
    from app.core.tenant import set_tenant_context
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    set_tenant_context(session, None)
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def media(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    rel = "task/1/abc.jpg"
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"original-jpg-bytes")
    return SimpleNamespace(id=1, storage_path=rel, kind="image", org_id=5, thumb_path=None)


_PNG = "data:image/png;base64," + base64.b64encode(b"flaches-png-1").decode()
_PNG2 = "data:image/png;base64," + base64.b64encode(b"flaches-png-2").decode()
_USER = SimpleNamespace(id=7)


def test_registry_und_annotatable():
    assert set(ann.registry()) == {"task", "message", "person", "site", "cross_marker", "lage_journal"}
    assert ann.is_annotatable("task", SimpleNamespace(kind="image"))
    assert not ann.is_annotatable("task", SimpleNamespace(kind="pdf"))


def test_save_schreibt_flaches_png_und_vektordaten(db, media, tmp_path):
    a = ann.save_annotation(db, _USER, "task", media, '{"attrs":{},"className":"Layer"}', _PNG)
    db.commit()
    assert a.annotated_file == "abc_annotated.png"      # Dateiname-Marker neben dem Original
    abs_png = ann._annotated_abs_path("task", media)
    assert abs_png.exists() and abs_png.read_bytes() == b"flaches-png-1"
    assert a.annotated_by == _USER.id and a.annotated_at is not None
    # get_annotation findet die Zeile wieder
    assert ann.get_annotation(db, "task", 1).id == a.id
    # Original unangetastet
    assert (tmp_path / media.storage_path).read_bytes() == b"original-jpg-bytes"


def test_zweiter_save_archiviert_vorstand(db, media):
    ann.save_annotation(db, _USER, "task", media, "STAND-A", _PNG)
    db.commit()
    ann.save_annotation(db, _USER, "task", media, "STAND-B", _PNG2)
    db.commit()
    a = ann.get_annotation(db, "task", 1)
    assert a.annotation_json == "STAND-B"
    versionen = db.query(MediaAnnotationVersion).filter_by(annotation_id=a.id).all()
    assert len(versionen) == 1 and versionen[0].annotation_json == "STAND-A"


def test_display_pfad_bevorzugt_annotierte_version(db, media, tmp_path):
    # ohne Annotation -> Original
    assert ann.display_abs_path(db, "task", media) == tmp_path / media.storage_path
    ann.save_annotation(db, _USER, "task", media, "X", _PNG)
    db.commit()
    # mit Annotation -> flaches PNG
    p = ann.display_abs_path(db, "task", media)
    assert p.suffix == ".png" and p.exists()


def test_annotated_media_ids(db, media):
    assert ann.annotated_media_ids(db, "task", [1, 2]) == set()
    ann.save_annotation(db, _USER, "task", media, "X", _PNG)
    db.commit()
    assert ann.annotated_media_ids(db, "task", [1, 2]) == {1}


def test_annotated_versions_liefert_zeitstempel(db, media):
    assert ann.annotated_versions(db, "task", [1, 2]) == {}
    ann.save_annotation(db, _USER, "task", media, "X", _PNG)
    db.commit()
    versions = ann.annotated_versions(db, "task", [1, 2])
    assert list(versions) == [1]
    assert versions[1] == int(ann.get_annotation(db, "task", 1).annotated_at.timestamp())


def _echtes_png() -> str:
    """Erzeugt ein valides 2x2-PNG (fuer Tests, die Pillow tatsaechlich oeffnen)."""
    import io as _io

    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", (2, 2), color=(255, 0, 0)).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_save_regeneriert_thumbnail_aus_bearbeitetem_bild(db, media, tmp_path):
    """Bug-Fix: Nach dem Speichern einer Bearbeitung muss die Miniaturansicht
    (thumb_path) den bearbeiteten Stand zeigen, nicht mehr das Original-Thumb
    (siehe [[project-*]]/Session 2026-07-12: /medien/thumb/{id} zeigte bisher
    dauerhaft das unbearbeitete Bild)."""
    thumb_rel = "task/1/abc_thumb.jpg"
    thumb_abs = tmp_path / thumb_rel
    thumb_abs.parent.mkdir(parents=True, exist_ok=True)
    thumb_abs.write_bytes(b"altes-thumb")
    media.thumb_path = thumb_rel

    ann.save_annotation(db, _USER, "task", media, '{"a":1}', _echtes_png())
    db.commit()

    neuer_inhalt = thumb_abs.read_bytes()
    assert neuer_inhalt != b"altes-thumb"
    # Muss ein valides JPEG sein (PIL kann es wieder oeffnen)
    from PIL import Image
    with Image.open(thumb_abs) as img:
        img.verify()


def test_save_regeneriert_thumbnail_fuer_gsl_medientyp(db, tmp_path, monkeypatch):
    """Site/CrossMarker/LageJournal-Medien haben KEINE thumb_path-Spalte (Thumb-Pfad
    wird per Dateinamens-Konvention aus stored_filename abgeleitet, siehe
    lage_media_service.site_thumb_path) -- die Registry muss auch fuer diese drei
    Typen die richtige Thumb-Datei regenerieren, nicht nur fuer task/message/person."""
    monkeypatch.chdir(tmp_path)  # site_thumb_path() nutzt CWD-relative Pfade

    site_media = SimpleNamespace(id=42, incident_site_id=9, stored_filename="xyz.jpg",
                                 media_type="image", org_id=5)
    thumb_abs = ann.spec_for("site").thumb_path(site_media)
    thumb_abs.parent.mkdir(parents=True, exist_ok=True)
    thumb_abs.write_bytes(b"altes-site-thumb")

    ann.save_annotation(db, _USER, "site", site_media, '{"a":1}', _echtes_png())
    db.commit()

    assert thumb_abs.read_bytes() != b"altes-site-thumb"
    from PIL import Image
    with Image.open(thumb_abs) as img:
        img.verify()


def test_get_or_create_idempotent(db):
    a1 = ann.get_or_create(db, "task", 99, org_id=3)
    a2 = ann.get_or_create(db, "task", 99, org_id=3)
    assert a1.id == a2.id
    assert db.query(MediaAnnotation).filter_by(media_typ="task", media_id=99).count() == 1


def test_soft_lock_heartbeat_und_fremdlock(db):
    from app.models.user import User
    db.add(User(id=1, username="anna", display_name="Anna A.", password_hash="x", active=True))
    db.flush()
    userA, userB = SimpleNamespace(id=1), SimpleNamespace(id=2)

    # A nimmt den Lock -> kein Fremd-Lock
    assert ann.acquire_lock(db, "task", 500, 1, userA)["locked_by_other"] is False
    db.commit()
    # B oeffnet -> Fremd-Lock von A gemeldet (Last-write-wins: B uebernimmt trotzdem)
    info = ann.acquire_lock(db, "task", 500, 1, userB)
    db.commit()
    assert info["locked_by_other"] is True and info["name"] == "Anna A."
    # A kann den (jetzt B gehoerenden) Lock nicht freigeben, B schon
    ann.release_lock(db, "task", 500, userA)
    db.commit()
    assert ann.get_annotation(db, "task", 500).locked_by == 2
    ann.release_lock(db, "task", 500, userB)
    db.commit()
    assert ann.get_annotation(db, "task", 500).locked_by is None


# ── Endpoint-Auth: ohne Login kein Zugriff ───────────────────────────────────

def test_editor_endpoint_ohne_login(client):
    r = client.get("/annotieren/task/123456", follow_redirects=False)
    assert r.status_code in (302, 401, 403)


def test_save_endpoint_ohne_login(client):
    r = client.put("/api/annotation/task/123456",
                   json={"annotation_json": "{}", "png": None}, follow_redirects=False)
    assert r.status_code in (302, 401, 403)
