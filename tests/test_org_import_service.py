"""PR 4: Restore/Import — Roundtrip Org A -> Export -> Import in Org B (ID-Remapping)."""
import uuid
from pathlib import Path

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, Message
from app.models.master import FireDept, Member
from app.models.objekt import Objekt, ObjektKontakt, ObjektSymbol
from app.services.org_export_service import export_org
from app.services.org_import_service import import_org, read_manifest


@pytest.fixture
def quelle_und_medien():
    """Legt Org A mit verkettetem Datengraph + einer Mediendatei an. Gibt Kennungen zurueck."""
    tag = uuid.uuid4().hex[:8]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        a = FireDept(slug=f"rt-a-{tag}", name=f"RT A {tag}")
        db.add(a)
        db.flush()
        db.add(Member(org_id=a.id, lastname=f"Alpha{tag}", firstname="Ann"))

        obj = Objekt(org_id=a.id, nummer=f"O-{tag}", name=f"Haus {tag}")
        db.add(obj)
        db.flush()
        db.add(ObjektKontakt(org_id=a.id, objekt_id=obj.id, name=f"Kontakt {tag}"))

        inc = Incident(primary_org_id=a.id, status="active", alarm_type_code="B1")
        db.add(inc)
        db.flush()
        db.add(Message(incident_id=inc.id, title=f"Meldung {tag}"))

        rel = f"rt-{tag}/sym.png"
        db.add(ObjektSymbol(org_id=a.id, code=f"SYM-{tag}", name=f"Sym {tag}", bild_pfad=rel))
        db.commit()
        ids = {"org": a.id, "objekt": obj.id, "incident": inc.id, "tag": tag, "media_rel": rel}
    finally:
        db.close()

    # Mediendatei physisch anlegen (unter OBJEKT_MEDIA_DIR)
    from app.config import settings
    pfad = Path(settings.OBJEKT_MEDIA_DIR) / rel
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_bytes(b"\x89PNG-test")
    return ids


def test_roundtrip_export_import(quelle_und_medien, tmp_path):
    ids = quelle_und_medien
    tag = ids["tag"]

    # Export A
    db = SessionLocal()
    set_tenant_context(db, None)
    zip_pfad = export_org(db, ids["org"], tmp_path, include_media=True)
    db.close()

    manifest = read_manifest(zip_pfad)
    assert manifest["org_id"] == ids["org"]

    # Ziel-Org B (leer) anlegen
    db = SessionLocal()
    set_tenant_context(db, None)
    b = FireDept(slug=f"rt-b-{tag}", name=f"RT B {tag}")
    db.add(b)
    db.commit()
    b_id = b.id

    summary = import_org(db, zip_pfad, b_id)
    db.close()

    assert summary["source_org_id"] == ids["org"]
    assert summary["rows_total"] > 0
    for t in ("member", "objekt", "objekt_kontakt", "incident", "message", "objekt_symbol"):
        assert summary["tables"].get(t, 0) >= 1, f"{t} fehlt im Import"
    assert summary["media_restored"] >= 1

    # In Org B pruefen (tenant-gescoped)
    db = SessionLocal()
    set_tenant_context(db, b_id)
    try:
        members = db.query(Member).all()
        assert any(m.lastname == f"Alpha{tag}" for m in members)

        objs = db.query(Objekt).all()
        assert len(objs) == 1
        obj_b = objs[0]
        assert obj_b.name == f"Haus {tag}"
        assert obj_b.org_id == b_id                     # Org umgeschrieben

        kontakte = db.query(ObjektKontakt).all()
        assert len(kontakte) == 1
        assert kontakte[0].objekt_id == obj_b.id        # FK auf NEUES Objekt remappt

        inc_b = db.query(Incident).all()
        assert len(inc_b) == 1 and inc_b[0].primary_org_id == b_id
        msgs = db.query(Message).execution_options(include_all_tenants=True).all()
        msg_b = [m for m in msgs if m.title == f"Meldung {tag}"]
        # Die importierte Meldung zeigt auf den NEUEN Einsatz in B (FK remappt).
        assert any(m.incident_id == inc_b[0].id for m in msg_b)
    finally:
        db.close()

    # Quelle A unveraendert (kein Cross-Effekt)
    db = SessionLocal()
    set_tenant_context(db, ids["org"])
    try:
        assert db.query(Objekt).count() == 1
        assert db.query(Member).filter(Member.lastname == f"Alpha{tag}").count() == 1
    finally:
        db.close()

    # Medien-Datei wurde beim Import (re-)angelegt
    from app.config import settings
    assert (Path(settings.OBJEKT_MEDIA_DIR) / ids["media_rel"]).is_file()


def test_import_falsche_version(tmp_path):
    import json
    import zipfile
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format_version": 999, "org_id": 1}))
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        with pytest.raises(ValueError):
            import_org(db, z, 1)
    finally:
        db.close()
