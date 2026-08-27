"""Regressionstests für den request-freien Druck-QR-Service."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident, IncidentToken
from app.models.major_incident import LageToken, MajorIncident, MajorIncidentStatus
from app.models.master import FireDept
from app.models.user import Role, User
from app.services.incident_qr_service import (
    einsatz_qr_login_url,
    get_or_create_qr_principal,
    lage_qr_login_url,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    set_tenant_context(session, None)
    yield session
    session.close()


def _stammdaten(db):
    org = FireDept(id=981001, slug="qr-service", name="QR Service", color="#f00", bos="Feuerwehr")
    db.add_all([org, Role(code="recorder", label="Bearbeiter")])
    db.commit()
    return org


def test_qr_principal_wird_je_org_genau_einmal_angelegt(db):
    org = _stammdaten(db)
    erster = get_or_create_qr_principal(db, org.id)
    db.commit()
    zweiter = get_or_create_qr_principal(db, org.id)
    assert erster.id == zweiter.id
    assert erster.is_device is True
    assert erster.password_hash is None
    assert erster.role_codes == {"recorder"}
    assert db.query(User).filter(User.username == f"qr-druck@org{org.id}").count() == 1


def test_token_wird_wiederverwendet_und_geschlossene_objekte_haben_keinen_qr(db):
    org = _stammdaten(db)
    incident = Incident(primary_org_id=org.id, alarm_type_code="T1", status="active")
    lage = MajorIncident(org_id=org.id, name="QR-Lage", status=MajorIncidentStatus.active)
    db.add_all([incident, lage])
    db.commit()
    assert einsatz_qr_login_url(db, incident) == einsatz_qr_login_url(db, incident)
    assert lage_qr_login_url(db, lage) == lage_qr_login_url(db, lage)
    assert db.query(IncidentToken).filter_by(incident_id=incident.id).count() == 1
    assert db.query(LageToken).filter_by(lage_id=lage.id).count() == 1
    incident.status = "closed"
    lage.status = MajorIncidentStatus.closed
    db.commit()
    assert einsatz_qr_login_url(db, incident) is None
    assert lage_qr_login_url(db, lage) is None


def test_fremder_aussteller_wird_abgelehnt(db):
    org = _stammdaten(db)
    fremde_org = FireDept(id=981002, slug="qr-fremd", name="Fremd", color="#000", bos="Feuerwehr")
    fremder = User(username="qr-fremd", display_name="Fremd", org_id=fremde_org.id)
    incident = Incident(primary_org_id=org.id, alarm_type_code="T1", status="active")
    db.add_all([fremde_org, fremder, incident])
    db.commit()
    with pytest.raises(ValueError):
        einsatz_qr_login_url(db, incident, issuing_user_id=fremder.id)


def test_bestehender_einsatzinfo_qr_bleibt_oeffentlicher_alarm_link(db):
    from app.routers.ui_incident import _einsatzinfo_qr_url

    incident = SimpleNamespace(alarm_token="alarm-regression")
    request = SimpleNamespace(base_url="https://einsatz.example/")

    assert _einsatzinfo_qr_url(request, incident, None, db) == (
        "https://einsatz.example/alarm/alarm-regression"
    )


def test_org_fremder_systemadmin_verwendet_qr_principal_der_lage():
    from app.routers.ui_major_incident import _lage_qr_aussteller_id

    lage = SimpleNamespace(org_id=981001)
    systemadmin = SimpleNamespace(id=77, org_id=None)
    org_admin = SimpleNamespace(id=78, org_id=981001)

    assert _lage_qr_aussteller_id(systemadmin, lage) is None
    assert _lage_qr_aussteller_id(org_admin, lage) == 78
