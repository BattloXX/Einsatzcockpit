"""Druckregeln mit expliziten Alarmstufen."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.models.gateway import Gateway, PrintRule
from app.models.master import AlarmType
from app.services import print_dispatcher as dispatcher
from tests.conftest import TEST_DB_URL


@pytest.fixture
def db(setup_db):
    session = sessionmaker(bind=create_engine(TEST_DB_URL, connect_args={"check_same_thread": False}))()
    set_tenant_context(session, None)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _regel(filters=None, options=None):
    return SimpleNamespace(
        id=1, trigger="einsatz_created", filters=filters or {}, documents=["einsatzinfo"],
        objekt_elements=["bei_einsatz_drucken"], options=options or {},
    )


def test_alarmstufen_filter_vergleicht_codes_exakt():
    regel = _regel({"alarmstufen": ["F14"]})
    assert dispatcher._filter_matches(regel, {"alarm_code": "F14"})
    assert dispatcher._filter_matches(regel, {"alarm_code": "f14"})
    assert not dispatcher._filter_matches(regel, {"alarm_code": "T14"})
    assert not dispatcher._filter_matches(regel, {"alarm_code": "f3"})
    assert not dispatcher._filter_matches(regel, {})
    assert dispatcher._filter_matches(_regel({"alarmstufen": []}), {})


def test_effektive_einstellungen_und_job_optionen(db):
    regel = PrintRule(
        org_id=998801, name="Alarmstufe", trigger="einsatz_created", printer_ids=[1],
        documents=["einsatzinfo"], objekt_elements=["bei_einsatz_drucken"],
        options={
            "copies": 1,
            "je_alarmstufe": {
                "f14": {"copies": 3, "media": "A3", "documents": ["einsatzinfo"],
                         "objekt_elements": []},
            },
        },
    )
    docs, elemente, optionen = dispatcher.effektive_regel_einstellungen(regel, {"alarm_code": "F14"})
    assert (docs, elemente, optionen) == (["einsatzinfo"], [], {"copies": 3, "media": "A3"})
    assert dispatcher.effektive_regel_einstellungen(regel, {"alarm_code": "T14"})[2] == {"copies": 1}

    gateway = Gateway(org_id=998801, name="GW")
    db.add_all([gateway, regel])
    db.flush()
    jobs = dispatcher._jobs_for_rule(db, gateway, regel, {"incident_id": 1, "alarm_code": "F14"})
    assert len(jobs) == 1
    assert jobs[0].options == {"copies": 3, "media": "A3"}
    assert "je_alarmstufe" not in jobs[0].options


def test_rule_save_speichert_alarmstufen_und_abweichungen():
    from fastapi.testclient import TestClient

    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.main import app
    from tests.test_gateway_druckregeln_render import ORG_ID, _login, _setup

    gateway_id = _setup("alarmstufen_rule_save", "org_admin")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        if not db.query(AlarmType).filter(AlarmType.org_id == ORG_ID, AlarmType.code == "F14").first():
            db.add(AlarmType(org_id=ORG_ID, code="F14", label="Brand 14", category="F"))
        regel = PrintRule(org_id=ORG_ID, name="Alarmstufen speichern", trigger="einsatz_created",
                          printer_ids=[gateway_id], documents=["einsatzinfo"], options={"copies": 1})
        db.add(regel)
        db.commit()
        regel_id = regel.id
    finally:
        db.close()

    client = TestClient(app)
    _login(client, "alarmstufen_rule_save", "Test1234!")
    response = client.post(
        f"/gateway/rules/{regel_id}/save",
        data={"_csrf": client.cookies.get("ec_csrf"), "trigger": "einsatz_created",
              "documents": "einsatzinfo", "printer_ids": str(gateway_id), "copies": "1",
              "alarmstufen": "F14", "stufe_F14_copies": "3", "stufe_F14_custom_elements": "1",
              "stufe_F14_objekt_elements": ["foerderstrecke", "ungueltig"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        regel = db.get(PrintRule, regel_id)
        assert regel.filters["alarmstufen"] == ["F14"]
        assert regel.options["je_alarmstufe"]["F14"] == {
            "copies": 3, "objekt_elements": ["foerderstrecke"],
        }
    finally:
        db.close()
