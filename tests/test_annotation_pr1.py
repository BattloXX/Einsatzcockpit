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


# ── Quota (Session 2026-07-12): annotierte Bilder wurden nie gegen die Org-
# Quota gebucht, weder beim Speichern (reserve) noch beim Loeschen (release). ─

@pytest.fixture()
def real_task_media(db, tmp_path, monkeypatch):
    """Echte Task/Incident/FireDept-Zeilen (statt SimpleNamespace), damit
    _org_of_incident() die Org ueber den Einsatz aufloesen kann."""
    from datetime import UTC, datetime

    from app.models.incident import Incident, Task, TaskMedia
    from app.models.master import FireDept

    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    org = FireDept(slug="ann-quota", name="Annotation Quota Org", color="#123456", bos="Feuerwehr")
    db.add(org)
    db.flush()
    inc = Incident(primary_org_id=org.id, alarm_type_code="T1", status="active",
                   started_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(inc)
    db.flush()
    task = Task(incident_id=inc.id, title="Testauftrag")
    db.add(task)
    db.flush()
    rel = "task/1/xyz.jpg"
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"original")
    media = TaskMedia(task_id=task.id, incident_id=inc.id, kind="image",
                      original_filename="xyz.jpg", storage_path=rel, mime_type="image/jpeg", bytes=8)
    db.add(media)
    db.flush()
    return org, media


def _echtes_png(groesse=(2, 2)) -> str:
    import io as _io

    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", groesse, color=(1, 2, 3)).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_save_annotation_reserviert_quota_fuer_task_medien(db, real_task_media):
    from app.services.storage_service import get_org_storage_info
    org, media = real_task_media

    ann.save_annotation(db, _USER, "task", media, '{"a":1}', _echtes_png())
    db.commit()

    abs_png = ann._annotated_abs_path("task", media)
    assert abs_png.exists()
    assert get_org_storage_info(db, org.id)["used_bytes"] == abs_png.stat().st_size
    # org_id der Annotation-Zeile wird beim Speichern nachgetragen (war vorher
    # immer NULL fuer Task/Message/Person, siehe get_or_create-Aufruf)
    assert ann.get_annotation(db, "task", media.id).org_id == org.id


def test_save_annotation_re_save_bucht_nur_delta(db, real_task_media):
    from app.services.storage_service import get_org_storage_info
    org, media = real_task_media

    ann.save_annotation(db, _USER, "task", media, "STAND-A", _echtes_png((2, 2)))
    db.commit()
    abs_png = ann._annotated_abs_path("task", media)
    erste_groesse = abs_png.stat().st_size

    # Zweites Speichern derselben Annotation (ueberschreibt dieselbe Datei) darf
    # nicht nochmal die volle Groesse buchen, nur die Differenz -- deutlich
    # groesseres Bild, damit die Dateigroesse garantiert unterschiedlich ist.
    ann.save_annotation(db, _USER, "task", media, "STAND-B", _echtes_png((80, 80)))
    db.commit()
    zweite_groesse = abs_png.stat().st_size

    assert zweite_groesse != erste_groesse
    assert get_org_storage_info(db, org.id)["used_bytes"] == zweite_groesse


def test_delete_annotation_and_files_gibt_quota_frei_und_loescht_datei(db, real_task_media):
    from app.services.storage_service import get_org_storage_info
    org, media = real_task_media

    ann.save_annotation(db, _USER, "task", media, '{"a":1}', _echtes_png())
    db.commit()
    abs_png = ann._annotated_abs_path("task", media)
    assert abs_png.exists()
    assert get_org_storage_info(db, org.id)["used_bytes"] > 0

    ann.delete_annotation_and_files(db, "task", media)
    db.commit()

    assert not abs_png.exists()
    assert get_org_storage_info(db, org.id)["used_bytes"] == 0
    assert ann.get_annotation(db, "task", media.id) is None


def test_delete_media_raeumt_annotation_mit_auf(db, real_task_media):
    """media_service.delete_media() (Task/Message/Person) muss beim Loeschen der
    Medienzeile auch eine vorhandene Annotation inkl. Quota-Freigabe entfernen."""
    from app.services.media_service import delete_media
    from app.services.storage_service import get_org_storage_info
    org, media = real_task_media

    ann.save_annotation(db, _USER, "task", media, '{"a":1}', _echtes_png())
    db.commit()
    abs_png = ann._annotated_abs_path("task", media)
    assert abs_png.exists()

    delete_media(media, db)
    db.commit()

    assert not abs_png.exists()
    assert get_org_storage_info(db, org.id)["used_bytes"] == 0
    assert ann.get_annotation(db, "task", 1) is None
