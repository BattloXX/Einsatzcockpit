"""E2: Prüfzyklus, Bestätigung ohne Inhaltsversion und gestufte Erinnerungen."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.core.tenant import set_tenant_context
from app.mcp.context import MCPPermissionError
from app.mcp.registry import TOOLS
from app.models.katplan import KatplanAenderung, KatplanPlan, KatplanPruefung, KatplanVersion
from app.models.user import AuditLog, User
from app.services import katplan_freigabe_service as service
from app.services import mail_service, task_reminder
from app.services.katplan_service import stammdaten_aendern, validiere_stammdaten
from app.services.katplan_zugriff import darf_ueberpruefung_bestaetigen
from tests.conftest import TestingSession
from tests.test_katplan_k1a import db as db
from tests.test_katplan_k1a import org as org
from tests.test_katplan_k1b import form, login, post
from tests.test_katplan_k1b import umgebung as umgebung
from tests.test_katplan_k2a import anlegen, basis
from tests.test_katplan_k3a import arbeitsstand
from tests.test_katplan_k8 import daten  # noqa: F401
from tests.test_mcp_katplan import inhalte as inhalte
from tests.test_mcp_katplan import rufe


@pytest.mark.parametrize("planart,override,monate", [
    ("alarm_einsatzplan", None, 12), ("katastrophenplan", None, 24),
    ("massnahmenplan", None, 24), ("alarm_einsatzplan", 6, 6),
])
def test_freigabe_startet_pruefzyklus(db, org, monkeypatch, planart, override, monate):
    plan, _, user = arbeitsstand(db, org)
    plan.planart, plan.pruefintervall_monate = planart, override
    heute = date(2027, 1, 31)
    monkeypatch.setattr(service, "now_local", lambda o: datetime(2027, 1, 31, tzinfo=UTC))
    plan.wiedervorlage_erinnert_am = plan.wiedervorlage_vorab_erinnert_am = heute
    plan.wiedervorlage_eskaliert_am = heute
    service.freigeben(db, plan, user)
    assert plan.zuletzt_geprueft_am == heute
    assert plan.zuletzt_geprueft_von_id == user.id
    assert plan.wiedervorlage_am == service.naechste_pruefung(heute, monate)
    assert plan.wiedervorlage_erinnert_am is plan.wiedervorlage_vorab_erinnert_am is None
    assert plan.wiedervorlage_eskaliert_am is None


@pytest.mark.parametrize("von,monate,erwartet", [
    (date(2027, 1, 31), 1, date(2027, 2, 28)),
    (date(2028, 1, 31), 1, date(2028, 2, 29)),
    (date(2028, 2, 29), 12, date(2029, 2, 28)),
    (date(2027, 12, 31), 2, date(2028, 2, 29)),
])
def test_monatsende(von, monate, erwartet):
    assert service.naechste_pruefung(von, monate) == erwartet


def test_bestaetigung_ohne_neue_version(db, org, monkeypatch):
    plan, _, user = arbeitsstand(db, org)
    service.freigeben(db, plan, user)
    db.flush()
    version_id, lock = plan.aktuelle_version_id, plan.version
    monkeypatch.setattr(service, "now_local", lambda o: datetime(2030, 2, 28, tzinfo=UTC))
    service.ueberpruefung_bestaetigen(db, plan, user, "Alles aktuell")
    db.flush()
    assert (plan.status, plan.aktuelle_version_id, plan.version) == ("freigegeben", version_id, lock + 1)
    assert plan.zuletzt_geprueft_am == date(2030, 2, 28)
    assert plan.wiedervorlage_am == date(2032, 2, 28)
    assert db.query(KatplanVersion).filter_by(plan_id=plan.id).count() == 1
    pruefung = db.query(KatplanPruefung).filter_by(plan_id=plan.id, aktion="ueberprueft").one()
    assert (pruefung.version_id, pruefung.kommentar, pruefung.user_id) == (version_id, "Alles aktuell", user.id)
    assert db.query(KatplanAenderung).filter_by(plan_id=plan.id, aktion="ueberprueft").count() == 1
    with pytest.raises(ValueError, match="2000"):
        service.ueberpruefung_bestaetigen(db, plan, user, "x" * 2001)
    plan.status = "in_ueberarbeitung"
    with pytest.raises(ValueError, match="unveröffentlichte Änderungen"):
        service.ueberpruefung_bestaetigen(db, plan, user)
    plan.aktuelle_version_id = None
    with pytest.raises(ValueError, match="Nur freigegebene"):
        service.ueberpruefung_bestaetigen(db, plan, user)


def test_terminfelder_ohne_ueberarbeitung_und_hash(db, org):
    plan, _, user = arbeitsstand(db, org)
    user.org_id = org.id
    service.freigeben(db, plan, user)
    for werte in ({"wiedervorlage_am": date(2032, 1, 1)}, {"pruefintervall_monate": 3},
                  {"pruef_verantwortlich_user_id": None}):
        stammdaten_aendern(db, plan, user, version=plan.version, werte=werte)
        assert plan.status == "freigegeben"
    with pytest.raises(ValueError, match="Keine Änderungen seit Version 1"):
        service.freigeben(db, plan, user)
    stammdaten_aendern(db, plan, user, version=plan.version, werte={"titel": "Neuer Inhalt"})
    assert plan.status == "in_ueberarbeitung"


@pytest.mark.parametrize("intervall", [0, 61, -1, "abc", 2.5, True])
def test_intervall_validierung(intervall):
    with pytest.raises(ValueError):
        validiere_stammdaten(titel="Test", gefahrenarten=["hochwasser"], pruefintervall_monate=intervall)


def test_erinnerungsstufen_einmalig(db, org, monkeypatch):
    plan, _, _ = arbeitsstand(db, org)
    termin = date(2030, 2, 15)
    plan.wiedervorlage_am = termin
    for tage, art in ((-13, "vorab"), (0, "faellig"), (31, "eskalation")):
        jetzt = datetime.combine(termin + timedelta(days=tage), datetime.min.time())
        monkeypatch.setattr(service, "now_local", lambda o, jetzt=jetzt: jetzt)
        # Nur den eigenen Plan betrachten: die Worker-DB enthaelt Plaene anderer Tests.
        treffer = [t for t in service.pruefe_wiedervorlagen(db) if t["plan_id"] == plan.id]
        assert len(treffer) == 1 and treffer[0]["art"] == art
        assert treffer[0]["verantwortlich_user_id"] is None
        assert [t for t in service.pruefe_wiedervorlagen(db) if t["plan_id"] == plan.id] == []


def test_ueberpruefung_ui_rechte_lesedaten_und_konflikte(client, umgebung):
    login(client, umgebung)
    anlegen(client, umgebung)
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        person = db.query(User).filter_by(username=umgebung["users"]["katplan_verwalter"]).one()
        fremd = db.query(User).filter_by(username=umgebung["users"]["fremd"]).first()
        plan.pruef_verantwortlich_user_id = person.id
        assert darf_ueberpruefung_bestaetigen(person, plan)
        assert not darf_ueberpruefung_bestaetigen(fremd, plan)
        db.commit()
    login(client, umgebung, "katplan_freigeber")
    assert post(client, basis(umgebung) + "/freigeben").status_code == 200
    login(client, umgebung, "readonly")
    assert post(client, basis(umgebung) + "/ueberpruefung").status_code == 403
    login(client, umgebung, "katplan_verwalter")
    response = post(client, basis(umgebung) + "/ueberpruefung", kommentar="Geprüft")
    assert response.status_code == 200 and 'id="kp-kopf"' in response.text
    assert "Zuletzt geprüft" in response.text
    response = post(client, basis(umgebung) + "/ueberpruefung", version=9999)
    assert response.status_code == 409
    assert "Überprüft (ohne Änderung)" in client.get(basis(umgebung) + "/versionen").text
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        plan.pruef_verantwortlich_user_id = None
        db.commit()
    assert post(client, basis(umgebung) + "/ueberpruefung").status_code == 403
    login(client, umgebung, "katplan_freigeber")
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        plan.status = "in_ueberarbeitung"
        db.commit()
    response = post(client, basis(umgebung) + "/ueberpruefung")
    assert response.status_code == 422 and "unveröffentlichte Änderungen" in response.text


def test_zustaendige_person_validierung_und_neuanlage(client, umgebung):
    login(client, umgebung)
    with TestingSession() as db:
        set_tenant_context(db, None)
        ids = {rolle: db.query(User).filter_by(username=name).one().id
               for rolle, name in umgebung["users"].items()}
    for person_id in (ids["readonly"], ids["fremd"], 999999):
        response = post(client, basis(umgebung) + "/stammdaten", **form(
            version=0, pruef_verantwortlich_user_id=person_id))
        assert response.status_code == 422 and "Benutzer nicht verfügbar." in response.text
    response = post(client, "/plaene/neu", **form())
    assert response.status_code == 303
    plan_id = int(response.headers["location"].rsplit("/", 1)[1])
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, plan_id)
        assert plan.pruef_verantwortlich_user_id == ids["katplan_verwalter"]
        assert plan.pruefintervall_monate is None
        person = db.get(User, ids["katplan_verwalter"])
        person.active = False
        db.commit()
    login(client, umgebung, "katplan_freigeber")
    response = post(client, basis(umgebung) + "/stammdaten", **form(
        version=0, pruef_verantwortlich_user_id=ids["katplan_verwalter"]))
    assert response.status_code == 422


@pytest.mark.parametrize("tage,betreff,rollen", [
    (-13, "Überprüfung in 14 Tagen", {"katplan_verwalter"}),
    (0, "Überprüfung fällig", {"katplan_verwalter"}),
    (31, "Überprüfung überfällig", {"katplan_verwalter", "katplan_freigeber"}),
])
def test_erinnerungsmail_verantwortlich_und_eskalation(client, umgebung, monkeypatch, tage, betreff, rollen):
    termin = date(2030, 2, 15)
    monkeypatch.setattr(service, "now_local", lambda o: datetime.combine(
        termin + timedelta(days=tage), datetime.min.time()))
    with TestingSession() as db:
        set_tenant_context(db, None)
        # Andere Daten dürfen im gemeinsamen Testschema keinen Versand auslösen.
        for plan in db.query(KatplanPlan):
            plan.wiedervorlage_eskaliert_am = termin
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        plan.wiedervorlage_am = termin
        plan.wiedervorlage_eskaliert_am = None
        emails = {}
        for rolle in ("katplan_verwalter", "katplan_freigeber", "org_admin"):
            user = db.query(User).filter_by(username=umgebung["users"][rolle]).one()
            user.email = user.username + "@example.org"
            emails[rolle] = user.email
            if rolle == "katplan_verwalter":
                plan.pruef_verantwortlich_user_id = user.id
                person_name = user.display_name
        db.commit()
    monkeypatch.setattr(task_reminder, "SessionLocal", TestingSession)
    monkeypatch.setattr(task_reminder, "_letzter_katplan_check", None)
    monkeypatch.setattr(mail_service, "mail_available", lambda *a: True)
    monkeypatch.setattr(mail_service, "_org_smtp_cfg", lambda *a: {"host": "test"})
    liefern = AsyncMock()
    monkeypatch.setattr(mail_service, "deliver", liefern)
    asyncio.run(task_reminder._check_katplan_wiedervorlagen())
    assert {call.args[2]["To"] for call in liefern.await_args_list} == {emails[r] for r in rollen}
    assert liefern.await_count == len(rollen)
    for call in liefern.await_args_list:
        msg = call.args[2]
        assert msg["Subject"] == f"{betreff}: {umgebung['titel']}"
        assert "15.02.2030" in msg.get_content() and person_name in msg.get_content()
        assert basis(umgebung) in msg.get_content()
    monkeypatch.setattr(task_reminder, "_letzter_katplan_check", None)
    asyncio.run(task_reminder._check_katplan_wiedervorlagen())
    assert liefern.await_count == len(rollen)


def test_mcp_pruefzyklus_und_audit(inhalte):
    name = "katplan_ueberpruefung_bestaetigen"
    assert name in TOOLS
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, inhalte["plan_id"])
        person_id = db.query(User).filter_by(
            username=inhalte["users"]["katplan_verwalter"]).one().id
        plan.pruef_verantwortlich_user_id = None
        plan.status = "freigegeben"
        lock = plan.version
        version_id = plan.aktuelle_version_id
        db.commit()
    result = rufe(inhalte, "katplan_stammdaten_aendern", "katplan_verwalter", plan_id=inhalte["plan_id"],
                  version=lock, felder={"pruefintervall_monate": 6, "pruef_verantwortlich_user_id": person_id})
    assert result["plan"]["pruefintervall_monate"] == 6
    with pytest.raises(MCPPermissionError):
        rufe(inhalte, name, "readonly", plan_id=inhalte["plan_id"])
    with pytest.raises(ValueError, match="Versionskonflikt"):
        rufe(inhalte, name, "katplan_verwalter", plan_id=inhalte["plan_id"], version=lock)
    result = rufe(inhalte, name, "katplan_verwalter", plan_id=inhalte["plan_id"],
                  version=lock + 1, kommentar="Weiterhin aktuell")
    assert result["status"] == "freigegeben" and result["pruefintervall_monate"] == 6
    assert result["naechste_pruefung"] == service.naechste_pruefung(
        date.fromisoformat(result["zuletzt_geprueft_am"]), 6).isoformat()
    kopf = rufe(inhalte, "katplan_lesen", plan_id=inhalte["plan_id"])["kopf"]
    assert kopf["naechste_pruefung"] == result["naechste_pruefung"]
    assert kopf["pruef_verantwortlich"]
    liste = rufe(inhalte, "katplan_liste")["plaene"]
    assert next(p for p in liste if p["id"] == inhalte["plan_id"])["pruefintervall_monate"] == 6
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, inhalte["plan_id"])
        assert plan.aktuelle_version_id == version_id
        assert db.query(AuditLog).filter_by(action="mcp." + name, entity_id=str(plan.id)).count() == 1


def test_aktuelle_pruefdaten_in_leseansicht_filter_und_pdf(client, inhalte):
    from app.services.katplan_pdf_service import render_katplan_html

    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, inhalte["plan_id"])
        person = db.query(User).filter_by(username=inhalte["users"]["katplan_verwalter"]).one()
        leser = db.query(User).filter_by(username=inhalte["users"]["readonly"]).one()
        plan.pruef_verantwortlich_user_id = person.id
        plan.zuletzt_geprueft_am = date(2020, 2, 3)
        plan.zuletzt_geprueft_von_id = person.id
        plan.wiedervorlage_am = date(2021, 2, 3)
        html = render_katplan_html(db, leser, plan)
        assert "Zuletzt geprüft" in html and "03.02.2020" in html
        assert "Nächste Überprüfung" in html and "03.02.2021" in html
        db.commit()
    login(client, inhalte, "readonly")
    html = client.get(basis(inhalte)).text
    assert "03.02.2020" in html and "03.02.2021" in html
    assert inhalte["users"]["katplan_verwalter"] in html
    html = client.get("/plaene?pruefung=faellig").text
    assert 'kp-badge kp-badge--red">03.02.2021' in html
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, inhalte["plan_id"])
        plan.wiedervorlage_am = date(2099, 2, 3)
        db.commit()
    assert "03.02.2099" not in client.get("/plaene?pruefung=faellig").text
    assert "03.02.2099" in client.get("/plaene").text
