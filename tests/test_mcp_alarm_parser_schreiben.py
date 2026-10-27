"""P7b: Schreibgrenzen, Validierung, Datenschutz und reine Probelaufwirkung."""
import asyncio
import json
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.context import MCPContext
from app.mcp.registry import TOOLS
from app.mcp.tools import alarm_parser as tools
from app.models.gateway import AlarmIngest, AlarmParserRegelsatz, AlarmParserTestfall, Gateway, PrintJob
from app.models.incident import Incident
from app.models.master import FireDept
from app.models.user import AuditLog, User
from app.services.alarm_parser import STANDARD_REGELSATZ
from app.services.alarm_parser.laufzeit import invalidiere_regelsatz
from tests import test_mcp_alarm_parser as lesen

# Gemeinsamer P7a-OAuth-/Gateway-Aufbau.
daten = lesen.daten
rufe = lesen.rufe
GEHEIM = lesen.GEHEIM


def direkt(daten, name, **kwargs):
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        return asyncio.run(TOOLS[name].handler(MCPContext(db, db.get(User, daten["id"]), daten["org_id"]), **kwargs))


def entwurf(client, daten):
    result = rufe(client, daten, "alarm_parser_entwurf_anlegen", notiz=GEHEIM)
    assert "__fehler__" not in result, result
    return result["version"]


def test_entwurf_kopie_aenderungen_audit(client, daten):
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        db.add(AlarmParserRegelsatz(org_id=daten["org_id"], version=7, status="aktiv",
                                   regeln={**STANDARD_REGELSATZ.model_dump(), "trennzeichen": ";"}))
        db.commit()
    invalidiere_regelsatz(daten["org_id"])
    version = entwurf(client, daten)
    assert version == 8
    aenderungen = [
        {"aktion": "hinzufuegen", "feld": "gemeinde_aliase", "schluessel": "wfurt", "wert": "Wolfurt"},
        {"aktion": "setzen", "feld": "gemeinde_aliase", "schluessel": "wfurt", "wert": "Bregenz"},
        {"aktion": "hinzufuegen", "feld": "meldebild_phrasen", "wert": "hilfe"},
        {"aktion": "entfernen", "feld": "meldebild_phrasen", "wert": "patientenrettung aus"},
        {"aktion": "setzen", "feld": "stichwort_muster", "wert": "[ft][0-9]"},
    ]
    result = rufe(client, daten, "alarm_parser_entwurf_aendern", version=version,
                  regel_aenderungen=aenderungen, notiz="angepasst")
    assert result == {"version": version, "status": "entwurf"}
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        row = db.query(AlarmParserRegelsatz).filter_by(org_id=daten["org_id"], version=version).one()
        assert row.basis_version == 7 and row.quelle == "mcp" and row.erstellt_von_user_id == daten["id"]
        assert row.regeln["trennzeichen"] == ";" and row.notiz == "angepasst"
        assert row.regeln["gemeinde_aliase"] == {"wfurt": "Bregenz"}
        assert row.regeln["meldebild_phrasen"] == ["hilfe"]
        # Ein UI-Entwurf ist ebenfalls bearbeitbar.
        row.quelle = "ui"
        db.commit()
    direkt(daten, "alarm_parser_entwurf_aendern", version=version,
           regel_aenderungen=[{"aktion": "entfernen", "feld": "gemeinde_aliase", "schluessel": "wfurt"}])
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        logs = db.query(AuditLog).filter_by(org_id=daten["org_id"], action="alarm_parser.mcp_entwurf").all()
        assert len(logs) == 3 and all(log.user_id == daten["id"] for log in logs)
        assert all("Max" not in log.payload_json and "angepasst" not in log.payload_json for log in logs)
    invalidiere_regelsatz(daten["org_id"])


@pytest.mark.parametrize("aenderung", [
    {"aktion": "setzen", "feld": "gemeinde_aliase", "wert": []},
    {"aktion": "setzen", "feld": "stichwort_muster", "wert": "(a+)+"},
    {"aktion": "setzen", "feld": "stichwort_muster", "wert": "a" * 201},
    {"aktion": "setzen", "feld": "stichwort_muster", "wert": "["},
    {"aktion": "setzen", "feld": "version", "wert": "aktiv"},
    {"aktion": "aktivieren", "feld": "trennzeichen", "wert": ";"},
    {"aktion": "setzen", "feld": "fremd", "wert": "x"},
    {"aktion": "hinzufuegen", "feld": "trennzeichen", "wert": "x"},
])
def test_ungueltig_atomar(client, daten, aenderung):
    version = entwurf(client, daten)
    with pytest.raises(ValueError):
        direkt(daten, "alarm_parser_entwurf_aendern", version=version, notiz="NICHT SPEICHERN",
               regel_aenderungen=[{"aktion": "setzen", "feld": "trennzeichen", "wert": ";"}, aenderung])
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        row = db.query(AlarmParserRegelsatz).filter_by(org_id=daten["org_id"], version=version).one()
        assert row.regeln == STANDARD_REGELSATZ.model_dump(mode="json") and row.notiz == GEHEIM
        assert db.query(AuditLog).filter_by(org_id=daten["org_id"], action="alarm_parser.mcp_entwurf").count() == 1


@pytest.mark.parametrize("status,quelle", [("aktiv", "mcp"), ("archiviert", "mcp"), ("entwurf", "import")])
def test_nur_entwuerfe(client, daten, status, quelle):
    version = entwurf(client, daten)
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        row = db.query(AlarmParserRegelsatz).filter_by(org_id=daten["org_id"], version=version).one()
        row.status, row.quelle = status, quelle
        db.commit()
    with pytest.raises(ValueError, match="Nur UI-/MCP"):
        direkt(daten, "alarm_parser_entwurf_aendern", version=version, regel_aenderungen=[])


def test_entwurf_limit(client, daten):
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        for version in range(1, 22):
            db.add(AlarmParserRegelsatz(org_id=daten["org_id"], version=version,
                                       status="archiviert" if version == 21 else "entwurf",
                                       quelle="ui" if version == 20 else "mcp", regeln=STANDARD_REGELSATZ.model_dump()))
        db.commit()
    assert entwurf(client, daten) == 22
    with pytest.raises(ValueError, match="20"):
        direkt(daten, "alarm_parser_entwurf_anlegen", notiz="Limit")
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        assert db.query(AlarmParserRegelsatz).filter_by(org_id=daten["org_id"]).count() == 22


def test_testfall_anonymisierung_limit(client, daten):
    result = rufe(client, daten, "alarm_parser_testfall_anlegen", ingest_id=daten["ingest_id"], titel=GEHEIM,
                  erwartet={"rest_text": GEHEIM, "meldebild": "Max Mustermann", "hinweise": ["max@example.at"]})
    assert "testfall_id" in result, result
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        row = db.query(AlarmParserTestfall).filter_by(org_id=daten["org_id"], id=result["testfall_id"]).one()
        for text in [row.titel, row.rohtext, json.dumps(row.erwartet)]:
            assert all(geheim not in text for geheim in ("Max", "Mustermann", "664", "max@example.at"))
        assert row.erwartet["meldebild"] == "[Name]"
        assert row.quelle == "mcp" and row.erstellt_von_user_id == daten["id"] and row.ingest_id == daten["ingest_id"]
        log = db.query(AuditLog).filter_by(org_id=daten["org_id"], action="alarm_parser.mcp_testfall").one()
        assert "Max" not in log.payload_json
        for _ in range(498):
            db.add(AlarmParserTestfall(org_id=daten["org_id"], titel="Test", rohtext="Test",
                                       erwartet={"status": "unklar"}))
        db.add(AlarmParserTestfall(org_id=daten["org_id"], titel="Inaktiv", rohtext="Test", erwartet={}, aktiv=False))
        db.commit()
    assert "testfall_id" in direkt(daten, "alarm_parser_testfall_anlegen", rohtext="Test", titel="500",
                                  erwartet={"status": "unklar"})
    with pytest.raises(ValueError, match="500"):
        direkt(daten, "alarm_parser_testfall_anlegen", rohtext="Test", titel="501", erwartet={"status": "unklar"})


@pytest.mark.parametrize("args", [
    {}, {"ingest_id": -1}, {"rohtext": "x", "ingest_id": -1}, {"rohtext": ""},
    {"rohtext": "x", "erwartet": {"anrufer": "privat"}},
    {"rohtext": "x", "erwartet": {"qualitaet": "falsch"}},
    {"rohtext": "x", "erwartet": {"hinweise": [{"name": "privat"}]}},
    {"rohtext": "x", "erwartet": {}},
])
def test_testfall_ungueltig(daten, args):
    with pytest.raises(ValueError):
        direkt(daten, "alarm_parser_testfall_anlegen", **({"titel": "Test", "erwartet": {"status": "unklar"}} | args))


def test_cross_org(client, daten):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=uuid4().hex, name="Fremd")
        db.add(org)
        db.flush()
        gw = Gateway(org_id=org.id, name="Fremd")
        db.add(gw)
        db.flush()
        ingest = AlarmIngest(org_id=org.id, gateway_id=gw.id, raw_hash=uuid4().hex, raw_text="FREMDES GEHEIMNIS")
        db.add(ingest)
        db.add(AlarmParserRegelsatz(org_id=org.id, version=999, quelle="mcp", regeln=STANDARD_REGELSATZ.model_dump()))
        db.add(AlarmParserTestfall(org_id=org.id, titel="FREMDES GEHEIMNIS", rohtext="FREMD", erwartet={}))
        db.commit()
        fremd_id = ingest.id
    for name, args in [
        ("alarm_parser_entwurf_aendern", {"version": 999, "regel_aenderungen": []}),
        ("alarm_parser_testen", {"version": 999}),
        ("alarm_parser_testfall_anlegen", {"ingest_id": fremd_id, "titel": "Test", "erwartet": {"status": "unklar"}}),
    ]:
        with pytest.raises(ValueError):
            direkt(daten, name, **args)
    assert rufe(client, daten, "alarm_parser_testen", version="aktiv", ingest_ids=[fremd_id])["datensaetze"] == []
    assert "999" not in json.dumps(rufe(client, daten, "alarm_parser_regelsatz_lesen"))


def zustand(daten):
    from app.models.incident import IncidentAlarmJob

    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        # Neben Zaehlern auch saemtliche Spalten vergleichen: keine stillen Updates.
        return {model.__name__: [tuple(getattr(row, col.name) for col in model.__table__.columns)
                                for row in db.query(model).order_by(model.id).all()]
                for model in (Incident, AlarmIngest, IncidentAlarmJob, PrintJob)}


def test_probelauf_maskiert_unveraendert_filter_limit_audit(client, daten):
    version = entwurf(client, daten)
    direkt(daten, "alarm_parser_entwurf_aendern", version=version,
           regel_aenderungen=[{"aktion": "hinzufuegen", "feld": "meldebild_phrasen", "wert": "max mustermann"}])
    direkt(daten, "alarm_parser_testfall_anlegen", titel="Erwartung", rohtext=GEHEIM,
           erwartet={"rest_text": "absichtlich falsch"})
    vorher = zustand(daten)
    result = rufe(client, daten, "alarm_parser_testen", version=version,
                  filter={"status": "needs_review", "qualitaet_max": 60, "von": "2026-10-26",
                          "bis": "2026-10-27", "leitstellennummer": "f26008809", "nur_ohne_einsatz": True}, limit=1)
    assert "datensaetze" in result, result
    assert len(result["datensaetze"]) == 2
    assert result["zusammenfassung"]["testfall_fehler"] == 1
    assert {r["art"] for r in result["datensaetze"]} == {"ingest", "testfall"}
    assert all("klassifikation" in r and "diff" in r for r in result["datensaetze"])
    assert any(r["diff"] for r in result["datensaetze"])
    assert zustand(daten) == vorher
    assert all(geheim not in json.dumps(result) for geheim in ("Max", "Mustermann", "664", "max@example.at"))
    # Testfaelle laufen auch bei leerer Ingest-Auswahl.
    assert len(rufe(client, daten, "alarm_parser_testen", version="aktiv", ingest_ids=[])["datensaetze"]) == 1
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        log = db.query(AuditLog).filter_by(org_id=daten["org_id"], action="alarm_ingest.mcp_gelesen").first()
        assert log and "Max" not in log.payload_json and "f26008809" not in log.payload_json
    for args in ({"limit": 201}, {"limit": 0}, {"ingest_ids": list(range(201))},
                 {"filter": {"unbekannt": True}}, {"ingest_ids": [], "filter": {}}, {"version": "falsch"},
                 {"filter": {"qualitaet_max": "kaputt"}}, {"filter": {"von": 123}}):
        with pytest.raises(ValueError):
            direkt(daten, "alarm_parser_testen", **({"version": "aktiv"} | args))
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        context = MCPContext(db, db.get(User, daten["id"]), daten["org_id"])
        while tools._limiter("").hit(tools.RateLimitItemPerHour(300), "alarm-parser-mcp",
                                    str(daten["org_id"]), str(daten["id"])):
            continue
        with pytest.raises(ValueError, match="300"):
            asyncio.run(tools.alarm_parser_testen(context, "aktiv"))


def test_entwurf_kann_nicht_aktiviert_werden(client, daten):
    version = entwurf(client, daten)
    assert {name for name in TOOLS if name.startswith("alarm_")} == lesen.ALLE_NAMEN
    # Kein Session-Cookie: ein MCP-Bearer ersetzt keine UI-Anmeldung.
    client.cookies.clear()
    csrf = uuid4().hex
    client.cookies.set("ec_csrf", csrf)
    response = client.post(f"/gateway/{daten['gateway_id']}/alarmempfang/parser/aktivieren",
                           headers={"Authorization": f"Bearer {daten['token']}", "X-CSRF-Token": csrf},
                           data={"version": version}, follow_redirects=False)
    assert response.status_code in (401, 403, 302, 303, 307)
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        row = db.query(AlarmParserRegelsatz).filter_by(org_id=daten["org_id"], version=version).one()
        assert row.status == "entwurf" and row.aktiviert_am is None
