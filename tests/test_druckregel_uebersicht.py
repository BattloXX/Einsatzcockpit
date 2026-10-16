"""Übersicht und Simulation der Einsatz-Druckregeln."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.models.gateway import Printer, PrintRule
from app.models.master import AlarmType
from app.services.druckregel_uebersicht import (
    alarmstufen_matrix,
    regel_beschreibung,
    simuliere,
)
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


def _regel(**werte):
    standard = {
        "id": 1, "name": "Hauptregel", "trigger": "einsatz_created", "aktiv": True,
        "sort_order": 1, "filters": {}, "documents": ["einsatzinfo"],
        "objekt_elements": [], "options": {"copies": 1}, "printer_ids": [7],
    }
    standard.update(werte)
    return SimpleNamespace(**standard)


def test_regel_beschreibung_enthält_filter_inhalte_und_status():
    regel = _regel(
        filters={"alarmstufen": ["F3", "F14"], "uebung": "nur_echt", "nur_bma": True},
        objekt_elements=["plan", "lage"], options={"copies": 2, "media": "A3"},
    )
    text = regel_beschreibung(
        regel, drucker_namen={7: "Florian", 8: "Büro"},
        objekt_labels={"plan": "🗺️ Brandschutzplan", "lage": "Lageplan"},
    )
    assert "Stufen F3, F14" in text
    assert "nur Echteinsätze" in text
    assert "2× Einsatzinfo, Objekt: Brandschutzplan, Lageplan, A3" in text
    assert "Florian" in text
    assert "kein Drucker gewählt" in regel_beschreibung(
        _regel(printer_ids=[]), drucker_namen={}, objekt_labels={},
    )
    assert regel_beschreibung(
        _regel(aktiv=False), drucker_namen={7: "Florian"}, objekt_labels={},
    ).startswith("(inaktiv) ")


def test_alarmstufen_matrix_zeigt_abweichungen_altlast_und_nur_einsatz(db):
    org_id = 991201
    db.add_all([
        AlarmType(org_id=org_id, code="F1", label="Kleinbrand", category="F"),
        AlarmType(org_id=org_id, code="F3", label="Brand", category="F"),
        AlarmType(org_id=org_id, code="F14", label="Großbrand", category="F"),
        AlarmType(org_id=org_id, code="T1", label="Technik", category="T"),
    ])
    db.flush()
    regel = _regel(filters={"alarmstufen": ["F1", "F14"]}, options={
        "copies": 1, "je_alarmstufe": {"F14": {"copies": 3}},
    })
    alle = _regel(id=2, name="Alle", filters={})
    alt = _regel(id=3, name="Alt", filters={"min_alarmstufe": 3})
    fremd = _regel(id=4, trigger="verleih_created")
    matrix = alarmstufen_matrix(db, org_id, [regel, alle, alt, fremd], objekt_labels={})
    assert len(matrix["spalten"]) == 3
    zeilen = {zeile["code"]: zeile for zeile in matrix["zeilen"]}
    assert zeilen["F14"]["zellen"][0]["greift"]
    assert zeilen["F14"]["zellen"][0]["abweichung"]
    assert zeilen["F14"]["zellen"][0]["kurz"].startswith("3×")
    assert not zeilen["T1"]["zellen"][0]["greift"]
    assert zeilen["T1"]["zellen"][1]["greift"]
    assert zeilen["F3"]["zellen"][2]["greift"]
    assert not zeilen["F1"]["zellen"][2]["greift"]


@pytest.mark.parametrize(("regel", "kwargs", "grund"), [
    (_regel(filters={"alarmstufen": ["F1"]}), {"alarm_code": "T1"}, "Alarmstufe T1 nicht ausgewählt"),
    (_regel(filters={"uebung": "nur_echt"}), {"is_exercise": True}, "nur Echteinsätze"),
    (_regel(filters={"zeitfenster": {"von": "22:00", "bis": "06:00"}}), {"uhrzeit": "12:00"}, "außerhalb Zeitfenster 22:00–06:00"),
    (_regel(printer_ids=[]), {}, "kein Drucker gewählt"),
    (_regel(aktiv=False), {}, "Regel ist deaktiviert"),
])
def test_simuliere_erklaert_nicht_greifende_regeln(regel, kwargs, grund):
    args = {"trigger": "einsatz_created", "alarm_code": "F1", "is_exercise": False,
            "nur_bma": False, "uhrzeit": "10:00", "stichwort": "", "drucker_namen": {7: "Florian"},
            "objekt_labels": {}}
    args.update(kwargs)
    ergebnis = simuliere(None, 1, [regel], **args)[0]
    assert not ergebnis["greift"]
    assert ergebnis["grund"] == grund


def test_simuliere_verwendet_alarmstufen_abweichung():
    regel = _regel(options={"copies": 1, "je_alarmstufe": {"F14": {"copies": 3, "media": "A3"}}})
    ergebnis = simuliere(
        None, 1, [regel], trigger="einsatz_created", alarm_code="F14", is_exercise=False,
        nur_bma=False, uhrzeit="10:00", stichwort="", drucker_namen={7: "Florian"}, objekt_labels={},
    )[0]
    assert ergebnis["greift"]
    assert ergebnis["druckt"] == [{"dokument": "Einsatzinfo", "kopien": 3, "format": "A3", "drucker": ["Florian"]}]


def test_druckregel_uebersicht_und_simulation_route_sind_org_getrennt():
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.test_gateway_druckregeln_render import ORG_ID, _login, _setup

    gateway_id = _setup("uebersicht_route_admin", "org_admin")
    session = sessionmaker(bind=create_engine(TEST_DB_URL, connect_args={"check_same_thread": False}))()
    set_tenant_context(session, None)
    try:
        if not session.query(AlarmType).filter(
            AlarmType.org_id == ORG_ID, AlarmType.code == "F7"
        ).first():
            session.add(AlarmType(org_id=ORG_ID, code="F7", label="Übersicht", category="F"))
        printer = Printer(gateway_id=gateway_id, name="Übersichtsdrucker", uri="ipp://overview")
        session.add(printer)
        session.flush()
        session.add_all([
            PrintRule(org_id=ORG_ID, name="Übersichtsregel", trigger="einsatz_created",
                      printer_ids=[printer.id], documents=["einsatzinfo"],
                      filters={"alarmstufen": ["F7"]}, options={"copies": 2}),
            PrintRule(org_id=991299, name="Fremde Regel", trigger="einsatz_created",
                      printer_ids=[printer.id], documents=["einsatzinfo"]),
        ])
        session.commit()
    finally:
        session.close()

    client = TestClient(app)
    _login(client, "uebersicht_route_admin", "Test1234!")
    seite = client.get(f"/gateway/{gateway_id}/druckregeln")
    assert seite.status_code == 200
    assert "Übersicht – was wird wann gedruckt?" in seite.text
    assert "Wenn ein Einsatz angelegt wird" in seite.text
    assert "Fremde Regel" not in seite.text
    simulation = client.get(
        f"/gateway/{gateway_id}/druckregeln/simulation",
        params={"alarm_code": "F7", "uhrzeit": "10:00"},
    )
    assert simulation.status_code == 200
    assert "✅ Übersichtsregel druckt:" in simulation.text

    recorder_gateway = _setup("uebersicht_route_recorder", "recorder")
    _login(client, "uebersicht_route_recorder", "Test1234!")
    assert client.get(f"/gateway/{recorder_gateway}/druckregeln/simulation").status_code == 403
