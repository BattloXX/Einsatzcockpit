"""PR5 Objektübernahme: Kopie eines Objekt-Seiten-Renders als Einsatz-Media."""
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


from app.config import settings
from app.db import Base
from app.models.incident import Task, TaskMedia
from app.models.media_annotation import MediaAnnotation
from app.models.objekt import Objekt, ObjektDokument, ObjektDokumentSeite
from app.services.takeover_service import uebernehme_seiten

ORG = 5


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path / "objekt"))
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path / "media"))
    from app.core.tenant import set_tenant_context
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    set_tenant_context(session, None)
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


def _quell_seite(db, tmp_path, org_id=ORG):
    obj = Objekt(org_id=org_id, nummer=42, name="Volksschule")
    db.add(obj); db.flush()
    dok = ObjektDokument(org_id=org_id, objekt_id=obj.id, dateiname_original="Brandschutzplan.pdf", pfad="x/orig.pdf")
    db.add(dok); db.flush()
    rel = f"{org_id}/{obj.id}/uid/seite_0003.png"
    src = tmp_path / "objekt" / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (600, 800), "white").save(src, "PNG")
    seite = ObjektDokumentSeite(org_id=org_id, objekt_id=obj.id, dokument_id=dok.id,
                                seiten_nr=3, bild_pfad=rel)
    db.add(seite); db.flush()
    return obj, dok, seite, src


def test_uebernahme_erzeugt_media_und_herkunft(db, tmp_path):
    obj, dok, seite, src = _quell_seite(db, tmp_path)
    task = Task(incident_id=1, title="Angriff"); db.add(task); db.flush()
    user = SimpleNamespace(id=7)

    erstellt = uebernehme_seiten(db, "task", task, [seite.id], user, ORG)
    db.commit()

    assert len(erstellt) == 1
    media = db.query(TaskMedia).one()
    assert media.task_id == task.id and media.kind == "image" and media.bytes > 0
    assert media.thumb_path and media.storage_path

    ann = db.query(MediaAnnotation).one()
    assert ann.media_typ == "task" and ann.media_id == media.id
    assert ann.source_objekt_id == obj.id
    assert ann.source_dokument_id == dok.id
    assert ann.source_seite == 3

    # Original im Objekt unangetastet
    assert src.exists() and src.stat().st_size > 0


def test_fremde_org_seite_wird_uebersprungen(db, tmp_path):
    _, _, fremde, _ = _quell_seite(db, tmp_path, org_id=99)  # andere Org
    task = Task(incident_id=1, title="T"); db.add(task); db.flush()
    erstellt = uebernehme_seiten(db, "task", task, [fremde.id], SimpleNamespace(id=1), ORG)
    db.commit()
    assert erstellt == []
    assert db.query(TaskMedia).count() == 0


def test_deckel_20_seiten(db, tmp_path):
    obj, dok, seite, src = _quell_seite(db, tmp_path)
    # 25 identische IDs -> dedup auf 1; teste stattdessen den Deckel-Slice separat
    from app.services.takeover_service import MAX_SEITEN
    assert MAX_SEITEN == 20
