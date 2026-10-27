"""P5b: Session-UI, Regelsatzfreigabe, Testfaelle und Tenant-Isolation."""

import json
from uuid import uuid4

import pytest

from app.models.gateway import AlarmParserRegelsatz, AlarmParserTestfall, Gateway
from app.models.master import FireDept
from app.models.user import AuditLog, Role, UserRole
from app.services.alarm_parser import STANDARD_REGELSATZ
from app.services.alarm_parser.laufzeit import aktiver_regelsatz, lege_entwurf_an
from tests import test_gateway_alarmempfang as basis

db = basis.db
diagnose = basis.diagnose


def url(diagnose):
    return f"/gateway/{diagnose[1].id}/alarmempfang/parser"


def post(diagnose, aktion, **data):
    client = diagnose[0]
    return client.post(url(diagnose) + "/" + aktion,
                       data={"_csrf": client.cookies.get("ec_csrf"), **data}, headers={"HX-Request": "true"})


def entwurf(diagnose, db):
    response = post(diagnose, "entwurf")
    assert response.status_code == 200, response.text
    return db.query(AlarmParserRegelsatz).filter_by(org_id=diagnose[1].org_id).order_by(
        AlarmParserRegelsatz.version.desc()).first()


def test_entwurf_validieren_aktiv_unveraenderlich(diagnose, db):
    client, gw, user, _ = diagnose
    response = client.get(url(diagnose))
    assert response.status_code == 200 and "Eingebaut" in response.text
    row = entwurf(diagnose, db)
    assert diagnose[3][-1] == (gw.org_id, {"type": "alarm_parser_changed"})
    assert row.status == "entwurf" and row.quelle == "ui" and row.erstellt_von_user_id == user.id
    data = STANDARD_REGELSATZ.model_dump(mode="json")
    data["gemeinde_aliase"] = {"testort": "Wolfurt"}
    response = post(diagnose, "speichern", version=row.version, regeln=json.dumps(data), notiz="Pruefung")
    db.refresh(row)
    assert row.notiz == "Pruefung" and row.regeln["gemeinde_aliase"] == data["gemeinde_aliase"]
    assert "gemeinde_aliase.testort" in response.text and "hinzugefuegt" in response.text
    assert post(diagnose, "aktivieren", version=row.version).status_code == 200
    db.refresh(row)
    assert row.status == "aktiv"
    assert aktiver_regelsatz(db, gw.org_id).version == str(row.version)
    before = row.regeln.copy()
    for aktion in ("speichern", "verwerfen"):
        response = post(diagnose, aktion, version=row.version, regeln="{}", notiz="Nein")
        assert "Nur Entwuerfe" in response.text
        db.refresh(row)
        assert row.regeln == before and row.notiz == "Pruefung" and row.status == "aktiv"
    kopie = entwurf(diagnose, db)
    assert kopie.basis_version == row.version
    assert {k: v for k, v in kopie.regeln.items() if k != "version"} == {
        k: v for k, v in row.regeln.items() if k != "version"
    }


@pytest.mark.parametrize("regeln, meldung", [
    ("{kaputt", "Expecting"), ("[]", "dictionary"),
    ('{"unbekannt": 1}', "unbekannt"),
    ('{"stichwort_muster": "(a+)+"}', "stichwort_muster"),
    ('{"trennzeichen": ""}', "trennzeichen"),
])
def test_ungueltig_als_meldung(diagnose, db, regeln, meldung):
    row = entwurf(diagnose, db)
    vorher = row.regeln.copy()
    response = post(diagnose, "speichern", version=row.version, regeln=regeln, notiz="Eingabe bleibt")
    assert response.status_code == 200 and meldung in response.text
    assert "Eingabe bleibt" in response.text
    db.refresh(row)
    assert row.regeln == vorher and row.status == "entwurf"


def test_testfall_blockiert_override_begruendung_audit(diagnose, db):
    _, gw, user, _ = diagnose
    row = entwurf(diagnose, db)
    db.add(AlarmParserTestfall(org_id=gw.org_id, titel="Fehler", rohtext="f26008809 t1 > wasser",
                             erwartet={"stichwort": "f2"}))
    db.commit()
    response = post(diagnose, "aktivieren", version=row.version)
    assert "Aktivierung blockiert" in response.text
    db.refresh(row)
    assert row.status == "entwurf"
    response = post(diagnose, "aktivieren", version=row.version, override="on", begruendung="  ")
    assert "Override erfordert" in response.text
    assert post(diagnose, "aktivieren", version=row.version, override="on",
                begruendung="Bewusst freigegeben").status_code == 200
    db.refresh(row)
    assert row.status == "aktiv" and aktiver_regelsatz(db, gw.org_id).version == str(row.version)
    log = db.query(AuditLog).filter_by(org_id=gw.org_id, user_id=user.id, action="alarm_parser.aktivieren").one()
    payload = json.loads(log.payload_json)
    assert payload["override"] is True and payload["begruendung"] == "Bewusst freigegeben"
    assert payload["zusammenfassung"]["testfall_fehler"] == 1


def test_regression_blockiert_und_zuruecksetzen(diagnose, db):
    _, gw, _, _ = diagnose
    basis.store(db, basis.nummer() + " t1 > wasser")
    erste = entwurf(diagnose, db)
    assert post(diagnose, "aktivieren", version=erste.version).status_code == 200
    zweite = entwurf(diagnose, db)
    daten = {**zweite.regeln, "stichwort_muster": r"z\d{1,2}"}
    post(diagnose, "speichern", version=zweite.version, regeln=json.dumps(daten))
    response = post(diagnose, "probelauf", version=zweite.version)
    assert "regression" in response.text and "Probelauf-Ergebnis" in response.text
    response = post(diagnose, "aktivieren", version=zweite.version)
    assert "Aktivierung blockiert" in response.text
    db.refresh(zweite)
    assert zweite.status == "entwurf"
    post(diagnose, "aktivieren", version=zweite.version, override="on", begruendung="Versuch")
    assert aktiver_regelsatz(db, gw.org_id).version == str(zweite.version)
    response = post(diagnose, "zuruecksetzen", version=erste.version)
    assert response.status_code == 200
    assert aktiver_regelsatz(db, gw.org_id).version == str(erste.version)
    db.refresh(zweite)
    assert zweite.status == "archiviert"
    # Auch beim Zuruecksetzen auf die schlechtere Vorversion greift die Sperre.
    response = post(diagnose, "zuruecksetzen", version=zweite.version)
    assert "Aktivierung blockiert" in response.text
    assert aktiver_regelsatz(db, gw.org_id).version == str(erste.version)
    assert db.query(AuditLog).filter_by(org_id=gw.org_id, action="alarm_parser.zuruecksetzen").count() == 1
    post(diagnose, "zuruecksetzen", version=zweite.version, override="on", begruendung="Bewusste Rueckkehr")
    assert aktiver_regelsatz(db, gw.org_id).version == str(zweite.version)
    log = db.query(AuditLog).filter_by(org_id=gw.org_id, action="alarm_parser.zuruecksetzen").order_by(
        AuditLog.id.desc()).first()
    assert json.loads(log.payload_json)["begruendung"] == "Bewusste Rueckkehr"


def test_testfall_anonymisiert_manuell_ingest_schalten_loeschen(diagnose, db):
    _, gw, _, _ = diagnose
    text = "f26008809 t1 > Notrufer Max Mustermann +43 664 1234567 max@example.at"
    ingest = basis.store(db, text)
    for kwargs in ({"ingest_id": ingest.id}, {"rohtext": text}):
        response = post(diagnose, "testfall-anlegen", titel="Notrufer Max Mustermann",
                        erwartet=json.dumps({"stichwort": "t1", "meldebild": "Notrufer Max Mustermann"}), **kwargs)
        assert response.status_code == 200
    tests = db.query(AlarmParserTestfall).filter_by(org_id=gw.org_id).order_by(AlarmParserTestfall.id).all()
    assert len(tests) == 2
    for test in tests:
        assert test.quelle == "ui" and test.aktiv
        assert "Max" not in test.rohtext and "0664" not in test.rohtext and "@" not in test.rohtext
        assert "[Telefon]" in test.rohtext and "[Name]" in test.rohtext
        assert test.titel == "Notrufer [Name]" and test.erwartet["meldebild"] == "Notrufer [Name]"
    assert tests[0].ingest_id == ingest.id and tests[1].ingest_id is None
    post(diagnose, "testfall-schalten", testfall_id=tests[0].id)
    db.refresh(tests[0])
    assert tests[0].aktiv is False
    post(diagnose, "testfall-loeschen", testfall_id=tests[0].id)
    assert db.query(AlarmParserTestfall).filter_by(org_id=gw.org_id).count() == 1
    response = post(diagnose, "testfall-anlegen", titel="Fehler", rohtext="text", erwartet='{"falsch": 1}')
    assert "unbekanntes Feld" in response.text


def test_verwerfen(diagnose, db):
    row = entwurf(diagnose, db)
    assert post(diagnose, "verwerfen", version=row.version).status_code == 200
    db.refresh(row)
    assert row.status == "archiviert"


@pytest.mark.parametrize("rolle, erwartet", [("recorder", 403), ("readonly", 403),
                                              ("org_admin", 200), ("admin", 200)])
def test_rollen(diagnose, db, rolle, erwartet):
    client, _, user, _ = diagnose
    ur = db.query(UserRole).filter_by(user_id=user.id).one()
    ur.role_id = db.query(Role).filter_by(code=rolle).one().id
    db.commit()
    assert client.get(url(diagnose)).status_code == erwartet
    assert post(diagnose, "entwurf").status_code == erwartet


def test_csrf_und_token_ohne_session(diagnose):
    client = diagnose[0]
    base = url(diagnose)
    assert client.post(base + "/entwurf").status_code == 403
    assert client.post(base + "/entwurf", data={"_csrf": "falsch"}).status_code == 403
    client.cookies.clear()
    headers = {"Authorization": "Bearer mcp-token"}
    assert client.get(base, headers=headers, follow_redirects=False).status_code in (302, 303, 401)
    assert client.post(base + "/entwurf", headers=headers, follow_redirects=False).status_code in (302, 303, 401, 403)


def test_cross_org_404(diagnose, db):
    client, gw, _, _ = diagnose
    org = FireDept(slug=uuid4().hex, name="Fremd")
    db.add(org)
    db.flush()
    fremd_gw = Gateway(org_id=org.id, name="Fremd")
    db.add(fremd_gw)
    row = lege_entwurf_an(db, org.id)
    test = AlarmParserTestfall(org_id=org.id, titel="Fremd", rohtext="Fremd", erwartet={"status": "ok"})
    db.add(test)
    db.commit()
    assert client.get(f"/gateway/{fremd_gw.id}/alarmempfang/parser").status_code == 404
    assert client.get(url(diagnose), params={"version": row.version}).status_code == 404
    for aktion in ("speichern", "verwerfen", "probelauf", "aktivieren", "zuruecksetzen"):
        assert post(diagnose, aktion, version=row.version, regeln="{}").status_code == 404
    for aktion in ("testfall-schalten", "testfall-loeschen"):
        assert post(diagnose, aktion, testfall_id=test.id).status_code == 404
    ingest = basis.store(db, basis.nummer() + " t1 > fremd", gw=fremd_gw)
    assert post(diagnose, "testfall-anlegen", titel="Fremd", ingest_id=ingest.id,
                erwartet='{"status": "ok"}').status_code == 404
    # Auch ein anderes Gateway derselben Org darf keine Ingest-Vorlage liefern.
    anderes = Gateway(org_id=gw.org_id, name="Anderes")
    db.add(anderes)
    db.commit()
    ingest = basis.store(db, basis.nummer() + " t1 > anderes", gw=anderes)
    assert post(diagnose, "testfall-anlegen", titel="Anderes", ingest_id=ingest.id,
                erwartet='{"status": "ok"}').status_code == 404


def test_echter_mcp_token_kann_keine_ui_aktivierung(diagnose, db):
    from app.models.master import OrgSettings, SystemSettings
    from tests.test_mcp_fundament import _oauth_tokens

    client, gw, user, _ = diagnose
    row = entwurf(diagnose, db)
    db.query(OrgSettings).filter_by(org_id=gw.org_id).one().mcp_modul_aktiv = True
    flag = db.get(SystemSettings, "mcp_module_enabled")
    if flag is None:
        db.add(SystemSettings(key="mcp_module_enabled", value="true"))
    else:
        flag.value = "true"
    db.commit()
    tokens = _oauth_tokens(client, {"username": user.username})
    csrf = client.cookies.get("ec_csrf")
    client.cookies.clear()
    client.cookies.set("ec_csrf", csrf)
    headers = {"Authorization": f"Bearer {tokens['access_token']}", "HX-Request": "true"}
    assert client.get(url(diagnose), headers=headers, follow_redirects=False).status_code in (302, 303, 401)
    response = client.post(url(diagnose) + "/aktivieren", data={"_csrf": csrf, "version": row.version},
                           headers=headers, follow_redirects=False)
    assert response.status_code in (302, 303, 401)
    db.refresh(row)
    assert row.status == "entwurf"
