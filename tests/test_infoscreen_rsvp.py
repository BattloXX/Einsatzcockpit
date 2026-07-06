"""Infoscreen-Alarmansicht: RSVP-Zähler im Payload (Phase 5)."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings
from app.models.objekt import AlarmInfoscreenToken
from app.models.teilnahme import Teilnahme
from app.routers.ui_infoscreen_alarm import infoscreen_daten


@pytest.fixture()
def is_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)
    org = FireDept(slug="rsvp-org", name="RSVP Org", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, alarm_infoscreen_idle_modus="uhr",
                       alarm_infoscreen_alarm_dauer_min=60))
    db.add(AlarmInfoscreenToken(org_id=org.id, token_hash=hash_api_key("rsvp-token"),
                                name="Monitor", aktiv=True))
    inc = Incident(primary_org_id=org.id, alarm_type_code="F14", status="active",
                   report_text="Test", started_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(inc)
    db.flush()
    for name, status in [("A", "zugesagt"), ("B", "zugesagt"), ("C", "abgesagt")]:
        db.add(Teilnahme(org_id=org.id, bezug_typ="einsatz", bezug_id=inc.id,
                         freitext_name=name, rsvp_status=status))
    db.commit()
    yield db, org, inc
    db.close()
    Base.metadata.drop_all(bind=engine)


def test_infoscreen_payload_enthaelt_rsvp(is_db):
    db, org, inc = is_db
    daten = infoscreen_daten("rsvp-token", request=None, db=db)  # type: ignore[arg-type]
    assert daten["modus"] == "alarm"
    assert daten["incident"]["rsvp"] == {"zusagen": 2, "absagen": 1}
