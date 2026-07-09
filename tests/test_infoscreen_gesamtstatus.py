"""Alarm-Infoscreen: Gesamtstatus im Kopf (Status nicht gesetzt / Übernommen / Am Einsatzort)."""
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
from app.models.incident import Incident, IncidentColumn, IncidentVehicle
from app.models.master import FireDept, OrgSettings, VehicleMaster
from app.models.objekt import AlarmInfoscreenToken
from app.routers.ui_infoscreen_alarm import _gesamtstatus, infoscreen_daten


@pytest.fixture()
def is_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)

    org = FireDept(slug="gs-org", name="Gesamtstatus Org", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, alarm_infoscreen_idle_modus="uhr",
                       alarm_infoscreen_alarm_dauer_min=60))
    db.add(AlarmInfoscreenToken(org_id=org.id, token_hash=hash_api_key("gs-token"),
                                name="Monitor", aktiv=True))
    incident = Incident(primary_org_id=org.id, alarm_type_code="F3", status="active",
                        started_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(incident)
    db.flush()
    db.commit()

    yield db, org, incident

    db.close()
    Base.metadata.drop_all(bind=engine)


def test_gesamtstatus_nicht_gesetzt_ohne_fahrzeuge(is_db):
    db, org, incident = is_db
    daten = infoscreen_daten("gs-token", request=None, db=db)  # type: ignore[arg-type]
    assert daten["incident"]["gesamtstatus"] == "nicht_gesetzt"
    assert daten["incident"]["gesamtstatus_label"] == "Status nicht gesetzt"


def test_gesamtstatus_uebernommen_sobald_ein_fahrzeug_disponiert_ist(is_db):
    db, org, incident = is_db
    spalte = IncidentColumn(incident_id=incident.id, code="active",
                            title="Im Einsatz", column_kind="vehicles")
    db.add(spalte)
    kdo = VehicleMaster(dept_id=org.id, code="KDO", name="KDO", type="Kommando")
    db.add(kdo)
    db.flush()
    db.add(IncidentVehicle(incident_id=incident.id, column_id=spalte.id,
                           vehicle_master_id=kdo.id))  # Default-Status "Einsatz übernommen"
    db.commit()

    daten = infoscreen_daten("gs-token", request=None, db=db)  # type: ignore[arg-type]
    assert daten["incident"]["gesamtstatus"] == "uebernommen"
    assert daten["incident"]["gesamtstatus_label"] == "Übernommen"


def test_gesamtstatus_am_einsatzort_sobald_erstes_fahrzeug_dort_ist(is_db):
    db, org, incident = is_db
    spalte = IncidentColumn(incident_id=incident.id, code="active",
                            title="Im Einsatz", column_kind="vehicles")
    db.add(spalte)
    kdo = VehicleMaster(dept_id=org.id, code="KDO", name="KDO", type="Kommando")
    tlf = VehicleMaster(dept_id=org.id, code="TLF", name="TLF 2000", type="Tanklöschfahrzeug")
    db.add_all([kdo, tlf])
    db.flush()
    # KDO noch auf Anfahrt (Default), TLF bereits am Einsatzort — genügt für Gesamtstatus
    db.add(IncidentVehicle(incident_id=incident.id, column_id=spalte.id, vehicle_master_id=kdo.id))
    db.add(IncidentVehicle(incident_id=incident.id, column_id=spalte.id,
                           vehicle_master_id=tlf.id, unit_status="Am Einsatzort"))
    db.commit()

    daten = infoscreen_daten("gs-token", request=None, db=db)  # type: ignore[arg-type]
    assert daten["incident"]["gesamtstatus"] == "am_einsatzort"
    assert daten["incident"]["gesamtstatus_label"] == "Am Einsatzort"


def test_gesamtstatus_helper_direkt():
    assert _gesamtstatus([]) == "nicht_gesetzt"

    class _Fake:
        def __init__(self, status):
            self.unit_status = status

    assert _gesamtstatus([_Fake("Einsatz übernommen")]) == "uebernommen"
    assert _gesamtstatus([_Fake("Einsatz übernommen"), _Fake("Am Einsatzort")]) == "am_einsatzort"
