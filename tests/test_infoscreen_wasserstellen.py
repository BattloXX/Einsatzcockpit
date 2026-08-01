"""Wasserstellen am oeffentlichen Alarm-Infoscreen."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings
from app.models.objekt import AlarmInfoscreenToken
from app.models.wasserstelle import Wasserstelle
from app.routers.ui_infoscreen_alarm import infoscreen_hydranten


@pytest.fixture()
def wasser_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)

    org = FireDept(slug="wasser-is", name="Wasser Infoscreen")
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, hydrant_layer_enabled=True))
    db.add(AlarmInfoscreenToken(
        org_id=org.id, token_hash=hash_api_key("wasser-token"), name="Monitor", aktiv=True,
    ))
    db.add(Incident(
        primary_org_id=org.id, alarm_type_code="B3", status="active",
        started_at=datetime.now(UTC).replace(tzinfo=None), lat=47.465, lng=9.750,
    ))
    db.commit()

    yield db, org

    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.mark.asyncio
async def test_infoscreen_liefert_eigene_wasserstelle_ohne_osm(wasser_db, monkeypatch):
    db, org = wasser_db
    monkeypatch.setattr(settings, "HYDRANT_ENABLED", False)
    db.add(Wasserstelle(
        org_id=org.id, bezeichnung="Teich Nord", typ="loeschteich",
        lat=47.4655, lng=9.7505, aktiv=True,
    ))
    db.commit()

    daten = await infoscreen_hydranten("wasser-token", db)

    assert daten["aktiv"] is False
    assert daten["zentrum"] == {"lat": 47.465, "lng": 9.750}
    assert len(daten["hydranten"]) == 1
    eintrag = daten["hydranten"][0]
    assert eintrag["quelle"] == "stammdaten"
    assert eintrag["icon_kat"] == "loeschwasser"
    assert eintrag["entfernung_m"] > 0


@pytest.mark.asyncio
async def test_deaktivierter_org_osm_layer_laesst_stammdaten_sichtbar(wasser_db, monkeypatch):
    db, org = wasser_db
    monkeypatch.setattr(settings, "HYDRANT_ENABLED", True)
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org.id).one()
    org_settings.hydrant_layer_enabled = False
    db.add(Wasserstelle(
        org_id=org.id, bezeichnung="Saugstelle", typ="saugstelle",
        lat=47.4654, lng=9.7504, aktiv=True,
    ))
    db.commit()

    daten = await infoscreen_hydranten("wasser-token", db)

    assert daten["aktiv"] is False
    assert [h["ref"] for h in daten["hydranten"]] == ["Saugstelle"]


@pytest.mark.asyncio
async def test_inaktive_und_zu_weit_entfernte_wasserstellen_fehlen(wasser_db, monkeypatch):
    db, org = wasser_db
    monkeypatch.setattr(settings, "HYDRANT_ENABLED", False)
    db.add_all([
        Wasserstelle(
            org_id=org.id, bezeichnung="Defekt", typ="ueberflur",
            lat=47.4652, lng=9.7502, aktiv=False,
        ),
        Wasserstelle(
            org_id=org.id, bezeichnung="Zu weit", typ="brunnen",
            lat=47.5000, lng=9.7500, aktiv=True,
        ),
    ])
    db.commit()

    daten = await infoscreen_hydranten("wasser-token", db)

    assert daten["hydranten"] == []
