"""Board-Spalte „Objektgefahren": automatische Meldungen je Objektgefahr (Phase 3)."""
from datetime import UTC, datetime

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn, Message
from app.models.master import FireDept
from app.models.objekt import (
    OBJEKT_STATUS_FREIGEGEBEN,
    GefahrenKatalog,
    Objekt,
    ObjektGefahr,
)
from app.services.incident_service import _create_fixed_columns
from app.services.objekt_matching_service import (
    entferne_gefahren_meldungen,
    erzeuge_gefahren_meldungen,
)


def _setup():
    db = SessionLocal()
    set_tenant_context(db, None)
    org = db.query(FireDept).first()
    kat1 = GefahrenKatalog(org_id=org.id, name="Board-Gasanschluss", piktogramm_typ="gas", aktiv=True)
    kat2 = GefahrenKatalog(org_id=org.id, name="Board-Photovoltaik", piktogramm_typ="pv", aktiv=True)
    db.add_all([kat1, kat2])
    db.flush()
    obj = Objekt(org_id=org.id, nummer=8001, name="Board-Objekt",
                 status=OBJEKT_STATUS_FREIGEGEBEN)
    db.add(obj)
    db.flush()
    db.add_all([
        ObjektGefahr(org_id=org.id, objekt_id=obj.id, gefahr_id=kat1.id, un_nummer="1965",
                     gefahrnummer="23", stoffname="Flüssiggas"),
        ObjektGefahr(org_id=org.id, objekt_id=obj.id, gefahr_id=kat2.id),
    ])
    inc = Incident(primary_org_id=org.id, alarm_type_code="T1", reason="Test",
                   status="active", started_at=datetime.now(UTC))
    db.add(inc)
    db.flush()
    _create_fixed_columns(db, inc)
    db.commit()
    return db, org.id, inc, obj


def test_gefahren_meldungen_idempotent_und_entfernen():
    db, org_id, inc, obj = _setup()
    try:
        db.refresh(obj)
        n = erzeuge_gefahren_meldungen(db, inc, obj)
        db.commit()
        assert n == 2

        col = db.query(IncidentColumn).filter(
            IncidentColumn.incident_id == inc.id,
            IncidentColumn.code == "objektgefahren").first()
        assert col is not None and col.column_kind == "messages"
        msgs = db.query(Message).filter(
            Message.incident_id == inc.id, Message.objekt_gefahr_id.isnot(None)).all()
        assert len(msgs) == 2
        assert all(m.status == "achtung" and m.column_id == col.id for m in msgs)
        # Anreicherung/UN in Detail
        gas = next(m for m in msgs if "Board-Gasanschluss" in m.title)
        assert "UN 1965" in (gas.detail or "") and "Flüssiggas" in (gas.detail or "")

        # Idempotenz: erneuter Aufruf legt nichts Neues an
        assert erzeuge_gefahren_meldungen(db, inc, obj) == 0
        db.commit()
        assert db.query(Message).filter(Message.incident_id == inc.id).count() == 2

        # Entfernen (beim Lösen)
        assert entferne_gefahren_meldungen(db, inc.id, obj) == 2
        db.commit()
        assert db.query(Message).filter(
            Message.incident_id == inc.id, Message.objekt_gefahr_id.isnot(None)).count() == 0
    finally:
        db.close()
