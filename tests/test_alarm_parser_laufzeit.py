"""DB-Katalog, versionierte Regeln und kompatibler Cloud-Ingest (P3b)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.tenant import TenantContextMissing, set_tenant_context
from app.db import SessionLocal
from app.models.gateway import AlarmParserRegelsatz, AlarmParserTestfall, Gateway
from app.models.incident import Incident
from app.models.master import AlarmType, FireDept
from app.models.objekt import Objekt
from app.models.planspiel import PlanspielAdresse
from app.models.wasserstelle import Wasserstelle
from app.services import serial_alarm_service as service
from app.services.alarm_parser import STANDARD_REGELSATZ
from app.services.alarm_parser.katalog import baue_kontext, invalidiere_kontext
from app.services.alarm_parser.laufzeit import (
    aktiver_regelsatz,
    aktiviere_regelsatz,
    invalidiere_regelsatz,
    lade_regelsatz,
    lege_entwurf_an,
)

DATEN = Path(__file__).parent / "data/alarm_wut"


@pytest.fixture
def db(setup_db):
    with SessionLocal() as session:
        set_tenant_context(session, None)
        orgs = [FireDept(slug="parser-" + uuid4().hex, name="Parser") for _ in range(2)]
        session.add_all(orgs)
        session.flush()
        session.add_all([
            AlarmType(org_id=org.id, code=code, label=code)
            for org in orgs for code in ("F2", "F3", "F11", "T1", "SONSTIGE")
        ])
        session.commit()
        session.info["parser_orgs"] = [org.id for org in orgs]
        yield session
        session.rollback()
        for org in orgs:
            invalidiere_kontext(org.id)
            invalidiere_regelsatz(org.id)


def org_ids(db):
    return db.info["parser_orgs"]


def gateway(db):
    row = Gateway(org_id=org_ids(db)[0], name="Parser-Gateway")
    db.add(row)
    db.commit()
    return row


def einsatz(db, row):
    return db.query(Incident).filter(
        Incident.primary_org_id == row.org_id, Incident.id == row.einsatz_id,
    ).one()


def verarbeite(db, gw, text, parsed=None):
    row, _ = service.store_raw(
        db, org_id=gw.org_id, gateway_id=gw.id, raw_text=text,
        parsed=parsed, parse_status="parsed" if parsed else "parse_failed",
    )
    return service.process_ingest(db, row.id, org_id=gw.org_id)


def test_katalog_quellen_org_isolation_cache_und_invalidierung(db):
    a, b = org_ids(db)
    org = db.query(FireDept).filter(FireDept.id == a).one()
    org.city, org.street, org.timezone = "Heimat", "Dorfstrasse", "Europe/Berlin"
    db.add_all([
        Objekt(org_id=a, name="Bad", ort="Nachbar", strasse="Badgasse"),
        Objekt(org_id=b, name="Fremd", ort="Geheim", strasse="Fremdgasse"),
        Wasserstelle(org_id=a, bezeichnung="Dorfteich"),
        PlanspielAdresse(org_id=a, quelle="osm", ort="OSM-Ort", strasse="OSM-Gasse",
                         lat=47, lng=9, qualitaet="A", ort_typen=["strasse"]),
        Incident(primary_org_id=a, address_city="Historie", address_street="Altgasse",
                 started_at=datetime.now(UTC).replace(tzinfo=None)),
        Incident(primary_org_id=a, address_city="Zu alt", address_street="Vergessen",
                 started_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=800)),
    ])
    db.commit()
    kontext = baue_kontext(db, a)
    assert set(kontext.gemeinden) == {"heimat", "nachbar", "osm ort", "historie"}
    assert kontext.zeitzone == "Europe/Berlin"
    assert kontext.gemeinden["heimat"].objekte == {"dorfteich": "Dorfteich"}
    assert kontext.gemeinden["nachbar"].strassen == {"badgasse": "Badgasse"}
    assert set(baue_kontext(db, b).gemeinden) == {"geheim"}
    kontext.gemeinden.clear()
    assert baue_kontext(db, a).gemeinden
    org.city = "Neu"
    db.commit()
    assert "heimat" in baue_kontext(db, a).gemeinden
    invalidiere_kontext(a)
    assert "neu" in baue_kontext(db, a).gemeinden


def test_versionen_aktivierung_invalidierung_und_org_isolation(db):
    a, b = org_ids(db)
    assert aktiver_regelsatz(db, a).version == "0"
    erste = lege_entwurf_an(db, a, STANDARD_REGELSATZ.model_dump(), quelle="mcp")
    zweite = lege_entwurf_an(db, a, {"gemeinde_aliase": {"wft": "Wolfurt"}}, basis_version=erste.version)
    fremde = lege_entwurf_an(db, b, STANDARD_REGELSATZ.model_dump())
    assert (erste.version, zweite.version, fremde.version) == (1, 2, 1)
    assert lade_regelsatz(db, b, 2) is None
    aktiviere_regelsatz(db, a, 1)
    db.commit()
    assert aktiver_regelsatz(db, a).version == "1"
    aktiviere_regelsatz(db, a, 2)
    assert aktiver_regelsatz(db, a).version == "2"
    db.commit()
    assert aktiver_regelsatz(db, a).version == "2"
    assert erste.status == "archiviert" and zweite.status == "aktiv"
    assert zweite.aktiviert_am is not None
    assert aktiver_regelsatz(db, b).version == "0"
    # Cache-Aenderungen duerfen einen Rollback nicht ueberleben.
    aktiviere_regelsatz(db, a, 1)
    assert aktiver_regelsatz(db, a).version == "1"
    db.rollback()
    assert aktiver_regelsatz(db, a).version == "2"


@pytest.mark.parametrize("regeln", [{}, {"stichwort_muster": "(a+)+"}, {"fremd": True}])
def test_ungueltige_aktive_regeln_fallback_und_aktivierung_verweigert(db, caplog, regeln):
    a, _ = org_ids(db)
    row = AlarmParserRegelsatz(org_id=a, version=1, status="aktiv", regeln=regeln)
    db.add(row)
    db.commit()
    assert aktiver_regelsatz(db, a) == STANDARD_REGELSATZ
    assert "Parser-Regelsatz-Fallback" in caplog.text
    with pytest.raises(ValueError):
        aktiviere_regelsatz(db, a, 1)
    assert row.status == "aktiv"


def test_neuer_entwurf_validiert_vor_speichern(db):
    with pytest.raises(ValueError):
        lege_entwurf_an(db, org_ids(db)[0], {"stichwort_muster": "(a+)+"})


@pytest.mark.parametrize("name", ["anhang_wolfurt", "anhang_buch", "anhang_lauterach"])
def test_drei_reale_beispiele_legen_einsatz_an(db, name):
    gw = gateway(db)
    # Ausschliesslich gespeicherte Org-Daten; keine fest eingebauten Ortsnamen.
    db.add_all([
        Objekt(org_id=gw.org_id, name="Feuerwehr", ort="Wolfurt", strasse="Frühlingstraße"),
        Objekt(org_id=gw.org_id, name="Feuerwehr Buch", ort="Buch"),
        Objekt(org_id=gw.org_id, name="bad lerchenau", ort="Lauterach"),
    ])
    regeln = lege_entwurf_an(db, gw.org_id)
    aktiviere_regelsatz(db, gw.org_id, regeln.version)
    db.commit()
    erwartet = json.loads((DATEN / f"{name}.expected.json").read_text())
    gateway_parsed = {"alarm_type_code": "T1", "city": "Falscher Ort", "reason": "Alt"}
    row = verarbeite(db, gw, (DATEN / f"{name}.txt").read_text(), gateway_parsed)
    inc = einsatz(db, row)
    assert row.process_status == "applied" and row.parse_status == "parsed"
    assert row.leitstellennummer == erwartet["leitstellennummer"]
    assert row.parser_version == "1"
    assert row.parsed["gateway_parsed"] == gateway_parsed
    assert inc.address_city == erwartet["gemeinde"]
    assert inc.address_street == erwartet["strasse"]
    assert inc.alarm_type_code == erwartet["alarm_type_code"]
    assert inc.started_at.isoformat() == erwartet["alarmzeit"]
    assert inc.reason == " > ".join([erwartet["meldebild"], *erwartet["zusatzinfo"]])


def test_parserfehler_nutzt_gateway_und_bleibt_applied(db, monkeypatch, caplog):
    gw = gateway(db)

    def kaputt(*args):
        raise RuntimeError("Parser kaputt")

    monkeypatch.setattr(service, "parse_alarm", kaputt)
    parsed = {"alarm_type_code": "F2", "street": "Altgasse", "city": "Altort", "house_no": "12"}
    row = verarbeite(db, gw, "Fehler " + uuid4().hex, parsed)
    inc = einsatz(db, row)
    assert row.process_status == "applied"
    assert inc.address_street == "Altgasse" and inc.address_city == "Altort"
    assert inc.alarm_type_code == "F2" and inc.address_no == "12"
    assert "Parser kaputt" in caplog.text and "Parser kaputt" in row.process_error
    assert row.parsed["gateway_parsed"] == parsed and row.parser_version == "0"


def test_alte_payload_ohne_cloud_treffer(db):
    gw = gateway(db)
    parsed = {"alarm_type_code": "F3", "street": "Altstrasse", "city": "Altstadt",
              "house_no": "5", "reason": "Gateway-Meldung", "is_exercise": True,
              "started_at": "2026-01-01T12:00:00Z"}
    row = verarbeite(db, gw, "Alt " + uuid4().hex, parsed)
    inc = einsatz(db, row)
    assert row.process_status == "applied" and row.parse_status == "parse_failed"
    assert row.parser_version == "0" and row.parsed["gateway_parsed"] == parsed
    assert inc.address_city == "Altstadt" and inc.address_street == "Altstrasse"
    assert inc.alarm_type_code == "F3" and inc.reason == "Gateway-Meldung" and inc.is_exercise
    assert inc.started_at == datetime(2026, 1, 1, 12, tzinfo=UTC).replace(tzinfo=None)


def test_teilweiser_cloud_treffer_verliert_keine_gateway_felder(db):
    gw = gateway(db)
    parsed = {"street": "Gatewaygasse", "house_no": "7", "city": "Gatewayort", "reason": "Gateway"}
    row = verarbeite(db, gw, "f26008809 f2 (06.10.2026 02:15 Uhr)", parsed)
    inc = einsatz(db, row)
    assert inc.alarm_type_code == "F2"
    assert (inc.address_city, inc.address_street, inc.address_no) == ("Gatewayort", "Gatewaygasse", "7")
    assert row.parsed["gateway_parsed"] == parsed


@pytest.mark.parametrize("model", [AlarmParserRegelsatz, AlarmParserTestfall])
def test_neue_tabellen_sind_tenant_pflichtig(db, model):
    db.info.pop("current_org_id")
    with pytest.raises(TenantContextMissing):
        db.query(model).all()


def test_cache_ttl_fuer_katalog_und_regelsatz(db, monkeypatch):
    from app.services.alarm_parser import katalog, laufzeit

    a, _ = org_ids(db)
    org = db.query(FireDept).filter(FireDept.id == a).one()
    org.city = "Vorher"
    row = lege_entwurf_an(db, a)
    aktiviere_regelsatz(db, a, row.version)
    db.commit()
    assert baue_kontext(db, a).gemeinden
    assert aktiver_regelsatz(db, a).version == "1"
    org.city = "Nachher"
    row.regeln = {"fremd": True}
    db.commit()
    assert "vorher" in baue_kontext(db, a).gemeinden
    assert aktiver_regelsatz(db, a).version == "1"
    jetzt = katalog.monotonic()
    monkeypatch.setattr(katalog, "monotonic", lambda: jetzt + 601)
    monkeypatch.setattr(laufzeit, "monotonic", lambda: jetzt + 61)
    assert "nachher" in baue_kontext(db, a).gemeinden
    assert aktiver_regelsatz(db, a).version == "0"


def test_parser_interner_fehler_nutzt_ebenfalls_gateway(db, monkeypatch):
    from app.services.alarm_parser import AlarmParseResult

    gw = gateway(db)
    monkeypatch.setattr(service, "parse_alarm", lambda *args: AlarmParseResult(
        original_text=args[0], hinweise=["parser_fehler"],
    ))
    row = verarbeite(db, gw, "Intern " + uuid4().hex, {"city": "Gatewayort"})
    assert row.process_status == "applied" and row.parse_status == "parse_failed"
    assert einsatz(db, row).address_city == "Gatewayort"
    assert "parser_fehler" in row.process_error


def test_cloud_ohne_gateway_block_nutzt_org_zeitzone(db):
    gw = gateway(db)
    org = db.query(FireDept).filter(FireDept.id == gw.org_id).one()
    org.city, org.street, org.timezone = "Wolfurt", "Frühlingstraße", "UTC"
    db.commit()
    row = verarbeite(db, gw, (DATEN / "anhang_wolfurt.txt").read_text())
    assert row.process_status == "applied" and row.parsed["gateway_parsed"] is None
    assert row.parser_version == "0" and row.leitstellennummer == "f26008809"
    assert einsatz(db, row).started_at == datetime(2026, 10, 6, 2, 15, tzinfo=UTC).replace(tzinfo=None)
