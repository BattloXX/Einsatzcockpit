"""Objektverwaltung PR 6: Alarm-Infoscreen (Token, Daten-Payload, DSGVO)."""
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

# BigInteger → INTEGER für SQLite-Testumgebung
@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings
from app.models.objekt import (
    OBJEKT_STATUS_FREIGEGEBEN,
    AlarmInfoscreenToken,
    Objekt,
    ObjektBMA,
    ObjektEinsatz,
    ObjektWohnanlage,
)
from app.routers.ui_infoscreen_alarm import IDLE_MODI, _token_org, infoscreen_daten


@pytest.fixture()
def is_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)

    org = FireDept(slug="is-org", name="Infoscreen Org", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, alarm_infoscreen_idle_modus="einsatzliste",
                       alarm_infoscreen_alarm_dauer_min=60))
    db.add(AlarmInfoscreenToken(org_id=org.id, token_hash=hash_api_key("test-token-123"),
                                name="Halle", aktiv=True))
    db.add(AlarmInfoscreenToken(org_id=org.id, token_hash=hash_api_key("alter-token"),
                                name="Alt", aktiv=False))
    db.commit()

    yield db, org

    db.close()
    Base.metadata.drop_all(bind=engine)


def test_token_gueltig(is_db):
    db, org = is_db
    eintrag, gefunden_org = _token_org(db, "test-token-123")
    assert gefunden_org.id == org.id
    assert eintrag.name == "Halle"


def test_token_ungueltig_und_inaktiv(is_db):
    db, _ = is_db
    with pytest.raises(HTTPException) as exc:
        _token_org(db, "falscher-token")
    assert exc.value.status_code == 401
    with pytest.raises(HTTPException) as exc2:
        _token_org(db, "alter-token")
    assert exc2.value.status_code == 401


def test_daten_idle_einsatzliste(is_db):
    db, org = is_db
    daten = infoscreen_daten("test-token-123", request=None, db=db)  # type: ignore[arg-type]
    assert daten["modus"] == "idle"
    assert daten["idle_modus"] == "einsatzliste"
    assert daten["org_name"] == "Infoscreen Org"
    assert "einsaetze" in daten


def test_daten_alarm_mit_objekt_ohne_wohnanlagen_hinweise(is_db):
    db, org = is_db
    objekt = Objekt(org_id=org.id, nummer=1, name="Rattpack Werk 2",
                    status=OBJEKT_STATUS_FREIGEGEBEN, lat=47.4652, lng=9.7503)
    db.add(objekt)
    db.flush()
    db.add(ObjektBMA(org_id=org.id, objekt_id=objekt.id, bma_nummer="1044",
                     bmz_standort="EG Büro", fbf_standort="Haupteingang",
                     schluesselsafe_vorhanden=True,
                     schluesselsafe_standort="beim Haupteingang"))
    # DSGVO-Testfall: Wohnanlagen-Hinweise duerfen NIE im Payload landen
    db.add(ObjektWohnanlage(org_id=org.id, objekt_id=objekt.id,
                            hinweise="3. OG: Bewohner mit Gehhilfe"))
    incident = Incident(
        primary_org_id=org.id, alarm_type_code="F14", status="active",
        report_text="bmz 1044 rattpack werk2 hat ausgelöst",
        address_street="Dammstraße", address_no="64", address_city="Wolfurt",
        started_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(incident)
    db.flush()
    db.add(ObjektEinsatz(org_id=org.id, objekt_id=objekt.id, incident_id=incident.id,
                         quelle="bma", status="bestaetigt"))
    db.commit()

    daten = infoscreen_daten("test-token-123", request=None, db=db)  # type: ignore[arg-type]
    assert daten["modus"] == "alarm"
    assert daten["incident"]["stichwort"] == "F14"
    assert "Dammstraße 64" in daten["incident"]["adresse"]
    assert daten["objekt"]["name"] == "Rattpack Werk 2"
    assert daten["objekt"]["bestaetigt"] is True
    assert daten["objekt"]["bma"]["bmz_standort"] == "EG Büro"
    assert daten["objekt"]["bma"]["fsd_standort"] == "beim Haupteingang"

    import json
    payload_text = json.dumps(daten, ensure_ascii=False, default=str)
    assert "Gehhilfe" not in payload_text  # DSGVO: nie am Wandmonitor
    assert "hinweise" not in daten["objekt"]


def test_alter_alarm_faellt_auf_idle_zurueck(is_db):
    db, org = is_db
    from datetime import timedelta
    incident = Incident(
        primary_org_id=org.id, alarm_type_code="T1", status="active",
        started_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=3),
    )
    db.add(incident)
    db.commit()
    daten = infoscreen_daten("test-token-123", request=None, db=db)  # type: ignore[arg-type]
    assert daten["modus"] == "idle"


def test_idle_modi_konfiguration():
    assert set(IDLE_MODI) == {"uhr", "wetter", "einsatzliste"}


def test_pr6_registrierung():
    from app.core.tenant import _TENANT_TABLE_NAMES
    assert "alarm_infoscreen_token" in _TENANT_TABLE_NAMES
    from app.routers.ui_infoscreen_alarm import router
    pfade = {getattr(r, "path", "") for r in router.routes}
    assert "/infoscreen/alarm/{token}" in pfade
    assert "/infoscreen/alarm/{token}/daten" in pfade
    assert "/ws/infoscreen/{token}" in pfade
    assert "/infoscreen-alarm/verwaltung" in pfade
