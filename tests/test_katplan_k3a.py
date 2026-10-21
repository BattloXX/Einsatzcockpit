"""K3a: Freigabe, unveraenderliche Snapshots und Wiedervorlagen."""

import asyncio
import importlib.util
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.tenant import set_tenant_context
from app.core.timezones import now_local
from app.db import Base
from app.models.katplan import (
    KatplanAbschnitt, KatplanElement, KatplanElementPhase, KatplanPhase, KatplanPlan,
    KatplanPruefung, KatplanVersion, KatplanVersionElement,
)
from app.models.master import FireDept, OrgSettings
from app.models.user import User
from app.services import katplan_freigabe_service as service
from app.services import mail_service, task_reminder
from tests.conftest import TestingSession
from tests.test_katplan_k1a import db, org  # noqa: F401
from tests.test_katplan_k1b import form, login, post, umgebung  # noqa: F401
from tests.test_katplan_k2a import anlegen, basis, element_form, objekte


def lade_plan(info):
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        return db.get(KatplanPlan, info["plan_id"])


def arbeitsstand(db, org):
    plan = KatplanPlan(org_id=org.id, titel="Hochwasser", gefahrenarten_json='["hochwasser"]',
                       beschreibung_html="<p>Kopf</p>", wiedervorlage_am=date(2026, 10, 19))
    db.add(plan)
    db.flush()
    kapitel = KatplanAbschnitt(org_id=org.id, plan_id=plan.id, titel="Kapitel", sort=10)
    phase = KatplanPhase(org_id=org.id, plan_id=plan.id, name="Alarm", sort=10, farbe="rot")
    db.add_all([kapitel, phase])
    db.flush()
    e = KatplanElement(org_id=org.id, plan_id=plan.id, abschnitt_id=kapitel.id, typ="info", titel="Sperren",
                       beschreibung_html="<p>Prüfen</p>", daten_json='{"kategorie":"Wasser"}',
                       gsl_vorlage=True, gsl_dauer_min=30, gueltig_bis=date(2027, 1, 1), lat=47.2,
                       quelle_dokument_id=42, quelle_seiten="1-2", tags_json='["Alarm"]')
    db.add(e)
    db.flush()
    db.add(KatplanElementPhase(org_id=org.id, element_id=e.id, phase_id=phase.id))
    db.flush()
    return plan, e, SimpleNamespace(id=None)


def test_freigabe_snapshot_und_unveraenderte_vorgaengerversion(db, org):
    plan, element, user = arbeitsstand(db, org)
    v1 = service.freigeben(db, plan, user, "Erste Ausgabe")
    db.flush()
    version, kopf, elemente = service.lade_version(db, plan)
    assert version.id == plan.aktuelle_version_id == v1.id
    assert (plan.status, version.versionsnummer, version.anzahl_elemente) == ("freigegeben", 1, 1)
    assert kopf["titel"] == plan.titel and kopf["gefahrenarten"] == ["hochwasser"]
    assert kopf["abschnitte"][0]["id"] == element.abschnitt_id
    assert json.loads(elemente[0].phasen_json) == [kopf["phasen"][0]["id"]]
    assert json.loads(elemente[0].gsl_json)["gsl_dauer_min"] == 30
    assert elemente[0].gueltig_bis == date(2027, 1, 1)
    assert elemente[0].quelle_dokument_id == 42 and elemente[0].lat == 47.2
    assert elemente[0].links_json == elemente[0].anhaenge_json == "[]"
    assert db.query(KatplanPruefung).filter_by(plan_id=plan.id).one().version_id == v1.id
    original = v1.plan_json, v1.inhalt_hash, elemente[0].titel
    element.titel = "Neuer Titel"
    from app.services.katplan_service import markiere_ueberarbeitung
    markiere_ueberarbeitung(db, plan, user)
    assert plan.status == "in_ueberarbeitung"
    v2 = service.freigeben(db, plan, user)
    db.flush()
    assert v2.versionsnummer == 2 and v2.inhalt_hash != v1.inhalt_hash
    assert (v1.plan_json, v1.inhalt_hash, elemente[0].titel) == original
    assert service.lade_version(db, plan)[2][0].titel == "Neuer Titel"
    with pytest.raises(ValueError, match="Keine Änderungen seit Version 2"):
        service.freigeben(db, plan, user)


@pytest.mark.parametrize("modell", [KatplanVersion, KatplanVersionElement])
def test_unveraenderlichkeit(db, org, modell):
    plan, _, user = arbeitsstand(db, org)
    service.freigeben(db, plan, user)
    db.flush()
    version, _, elemente = service.lade_version(db, plan)
    obj = version if modell is KatplanVersion else elemente[0]
    if modell is KatplanVersion:
        obj.aenderungsnotiz = "Manipulation"
    else:
        obj.titel = "Manipulation"
    with pytest.raises(RuntimeError, match="Freigegebene Versionen sind unveränderlich"):
        db.flush()
    db.rollback()


def test_workflow_ui_rechte_fehler_und_reaktivieren(client, umgebung):
    login(client, umgebung)
    path = basis(umgebung)
    e = anlegen(client, umgebung)
    assert post(client, path + "/freigeben").status_code == 403
    assert post(client, path + "/einreichen", kommentar="Bitte prüfen").status_code == 200
    assert post(client, path + "/einreichen").status_code == 422
    login(client, umgebung, "katplan_freigeber")
    antwort = post(client, path + "/zurueckweisen", kommentar="  ")
    assert antwort.status_code == 422 and 'id="kp-kopf"' in antwort.text
    assert "Kommentar" in antwort.text
    assert post(client, path + "/zurueckweisen", kommentar="Ergänzen").status_code == 200
    assert lade_plan(umgebung).status == "entwurf"
    assert post(client, path + f"/elemente/{e.id}", **element_form(e, titel="Eigene Änderung")).status_code == 200
    assert post(client, path + "/freigeben", aenderungsnotiz="x" * 2001).status_code == 422
    r = post(client, path + "/freigeben", aenderungsnotiz="Eigene Arbeit")
    assert r.status_code == 200 and "Freigegebene Version 1 vom" in r.text
    r = post(client, path + "/freigeben")
    assert r.status_code == 422 and "Keine Änderungen" in r.text
    e = objekte(umgebung, KatplanElement)[0]
    assert post(client, path + f"/elemente/{e.id}", **element_form(e, titel="Weiter")).status_code == 200
    assert post(client, path + "/einreichen").status_code == 200
    assert post(client, path + "/zurueckweisen", kommentar="Nacharbeiten").status_code == 200
    assert lade_plan(umgebung).status == "in_ueberarbeitung"
    assert post(client, path + "/archivieren").status_code == 200
    assert post(client, path + "/freigeben").status_code == 422
    assert post(client, path + "/reaktivieren").status_code == 200
    assert lade_plan(umgebung).status == "freigegeben"


def test_leerer_plan_ui(client, umgebung):
    login(client, umgebung, "katplan_freigeber")
    r = post(client, basis(umgebung) + "/freigeben")
    assert r.status_code == 422 and "mindestens ein Element" in r.text


def test_vergleich_kopf_kapitel_phasen_und_elemente(db, org):
    plan, e, user = arbeitsstand(db, org)
    entfernt = KatplanElement(org_id=org.id, plan_id=plan.id, typ="info", titel="Entfernt")
    db.add(entfernt)
    db.flush()
    service.freigeben(db, plan, user)
    db.flush()
    v1, alt_plan, alt = service.lade_version(db, plan)
    e.titel = "Neu"
    e.daten_json = '{"kategorie":"Sturm"}'
    e.beschreibung_html = "<p>Neue Beschreibung</p>"
    db.delete(entfernt)
    db.add(KatplanElement(org_id=org.id, plan_id=plan.id, typ="info", titel="Hinzugefügt"))
    plan.titel = "Neu"
    db.query(KatplanAbschnitt).filter_by(plan_id=plan.id).one().titel = "Neu"
    db.query(KatplanPhase).filter_by(plan_id=plan.id).one().name = "Neu"
    plan.status = "in_ueberarbeitung"
    service.freigeben(db, plan, user)
    db.flush()
    _, neu_plan, neu = service.lade_version(db, plan)
    diff = service.vergleiche_versionen(alt, neu, alt_plan, neu_plan)
    assert len(diff["hinzugefuegt"]) == len(diff["entfernt"]) == len(diff["geaendert"]) == 1
    felder = {f["feld"]: f for f in diff["geaendert"][0]["felder"]}
    assert {"titel", "daten_json", "beschreibung_html"} <= felder.keys()
    assert felder["daten_json"]["neu"] == {"kategorie": "Sturm"}
    assert any(zeile.startswith("+Neue") for zeile in felder["beschreibung_html"]["diff"])
    assert diff["kopf"][0]["feld"] == "titel"
    assert diff["abschnitte"]["geaendert"] and diff["phasen"]["geaendert"]
    assert service._aenderungen({"beschreibung_html": "<b>Gleich</b>"},
                               {"beschreibung_html": "<p>Gleich</p>"}) == []
    assert service._aenderungen({"daten_json": '{"a":1,"b":2}'},
                               {"daten_json": '{"b": 2, "a": 1}'}) == []


def test_version_baum_vertraulich_und_hierarchie(db, org):
    plan, e, user = arbeitsstand(db, org)
    kind = KatplanAbschnitt(org_id=org.id, plan_id=plan.id, parent_id=e.abschnitt_id, titel="Kind", sort=20)
    db.add(kind)
    db.flush()
    db.add_all([KatplanElement(org_id=org.id, plan_id=plan.id, abschnitt_id=kind.id, typ="info",
                              titel="Geheim", vertraulich=True),
                KatplanElement(org_id=org.id, plan_id=plan.id, typ="info", titel="Ohne Kapitel")])
    service.freigeben(db, plan, user)
    db.flush()
    _, kopf, elemente = service.lade_version(db, plan)
    baum = service.version_baum(kopf, elemente, mit_vertraulichen=False)
    assert baum[0]["elemente"][0].element_uuid == e.uuid
    assert baum[0]["kinder"][0]["elemente"] == []
    assert baum[-1]["elemente"][0].titel == "Ohne Kapitel"
    assert service.version_baum(kopf, elemente, mit_vertraulichen=True)[0]["kinder"][0]["elemente"][0].vertraulich


def test_lade_version_org_und_plan_isolation(db, org):
    plan, _, user = arbeitsstand(db, org)
    service.freigeben(db, plan, user)
    db.flush()
    andere_org = FireDept(slug=uuid4().hex, name="Andere")
    db.add(andere_org)
    db.flush()
    fremd, _, _ = arbeitsstand(db, andere_org)
    fremd_version = service.freigeben(db, fremd, user)
    with pytest.raises(ValueError, match="nicht gefunden"):
        service.lade_version(db, plan, fremd_version.id)
    set_tenant_context(db, org.id)
    assert db.query(KatplanVersion).filter_by(id=fremd_version.id).first() is None
    assert db.query(KatplanVersionElement).filter_by(version_id=fremd_version.id).first() is None


def test_wiedervorlage_org_zeitzone_einmal_und_archiv(db, org, monkeypatch):
    plan, _, user = arbeitsstand(db, org)
    set_tenant_context(db, org.id)
    org.timezone = "Pacific/Auckland"
    # Der lokale Tag ist absichtlich anders als das aktuelle UTC-Datum.
    lokal = datetime(2030, 1, 2, 0, 5)
    monkeypatch.setattr(service, "now_local", lambda o: lokal if o.id == org.id else None)
    plan.wiedervorlage_am = lokal.date()
    db.add_all([KatplanPlan(org_id=org.id, titel="Archiv", status="archiviert", wiedervorlage_am=lokal.date()),
                KatplanPlan(org_id=org.id, titel="Später", wiedervorlage_am=lokal.date() + timedelta(days=15))])
    db.flush()
    assert [t["plan_id"] for t in service.pruefe_wiedervorlagen(db)] == [plan.id]
    assert plan.wiedervorlage_erinnert_am == lokal.date()
    assert service.pruefe_wiedervorlagen(db) == []
    service.freigeben(db, plan, user)
    assert plan.wiedervorlage_erinnert_am is None


def test_stammdaten_datum_setzt_marker_zurueck(client, umgebung):
    login(client, umgebung)
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        plan.wiedervorlage_am = date(2026, 10, 19)
        plan.wiedervorlage_erinnert_am = date(2026, 10, 19)
        db.commit()
    assert post(client, basis(umgebung) + "/stammdaten", **form(version=0, wiedervorlage_am="2026-10-19")).status_code == 200
    assert lade_plan(umgebung).wiedervorlage_erinnert_am is not None
    assert post(client, basis(umgebung) + "/stammdaten", **form(version=1, wiedervorlage_am="2026-10-20")).status_code == 200
    assert lade_plan(umgebung).wiedervorlage_erinnert_am is None


def test_migration_upgrade_downgrade_sqlite():
    pfad = Path(__file__).resolve().parents[1] / "alembic/versions/0295_katplan_versionen.py"
    spec = importlib.util.spec_from_file_location("katplan_version_migration", pfad)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for name in ("fire_dept", "user", "katplan_plan"):
            conn.execute(sa.text(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)"))
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        assert migration.revision == "0295" and migration.down_revision == "0294"
        inspector = sa.inspect(conn)
        for modell in (KatplanVersion, KatplanVersionElement):
            name = modell.__tablename__
            # 0296 und 0304 ergaenzen Felder; hier wird ausschliesslich 0295 geprueft.
            erwartete_spalten = set(Base.metadata.tables[name].columns.keys())
            if modell is KatplanVersionElement:
                erwartete_spalten -= {"relationen_json", "akteure_json"}
            assert {c["name"] for c in inspector.get_columns(name)} == erwartete_spalten
            assert {c["name"]: c["nullable"] for c in inspector.get_columns(name)} == {
                c.name: c.nullable for c in modell.__table__.columns if c.name not in ("relationen_json", "akteure_json")}
            assert {tuple(i["column_names"]) for i in inspector.get_indexes(name)} == {
                tuple(c.name for c in i.columns) for i in modell.__table__.indexes}
            assert {tuple(u["column_names"]) for u in inspector.get_unique_constraints(name)} == {
                tuple(c.name for c in u.columns) for u in modell.__table__.constraints
                if isinstance(u, sa.UniqueConstraint)}
        assert {fk["referred_table"] for fk in inspector.get_foreign_keys("katplan_version")} == {
            "katplan_plan", "user", "fire_dept"}
        assert {fk["referred_table"] for fk in inspector.get_foreign_keys("katplan_version_element")} == {
            "katplan_version", "fire_dept"}
        migration.downgrade()
        assert set(sa.inspect(conn).get_table_names()) == {"fire_dept", "user", "katplan_plan"}
    engine.dispose()


@pytest.mark.parametrize("fallback,aktiv,mail", [(False, True, True), (True, True, True),
                                                 (False, False, True), (False, True, False)])
def test_erinnerungsmail_empfaenger_modul_und_tagesmarker(client, umgebung, monkeypatch, fallback, aktiv, mail):
    with TestingSession() as db:
        set_tenant_context(db, None)
        # Andere Testdaten sollen keinen Versand ausloesen.
        for p in db.query(KatplanPlan).all():
            p.wiedervorlage_erinnert_am = now_local().date()
        p = db.get(KatplanPlan, umgebung["plan_id"])
        p.wiedervorlage_am = now_local().date()
        p.wiedervorlage_erinnert_am = None
        db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one().katplan_modul_aktiv = aktiv
        for rolle in ("katplan_freigeber", "org_admin", "readonly"):
            u = db.query(User).filter_by(username=umgebung["users"][rolle]).one()
            u.email = f"{uuid4().hex}@example.org"
            if rolle == "katplan_freigeber" and fallback:
                u.active = False
            if rolle == ("org_admin" if fallback else "katplan_freigeber"):
                erwartet = u.email
        db.commit()
    monkeypatch.setattr(task_reminder, "SessionLocal", TestingSession)
    monkeypatch.setattr(task_reminder, "_letzter_katplan_check", None)
    monkeypatch.setattr(mail_service, "mail_available", lambda *a: mail)
    monkeypatch.setattr(mail_service, "_org_smtp_cfg", lambda *a: {"host": "test"})
    liefern = AsyncMock()
    monkeypatch.setattr(mail_service, "deliver", liefern)
    asyncio.run(task_reminder._check_katplan_wiedervorlagen())
    if aktiv and mail:
        assert liefern.await_count == 1
        args = liefern.await_args.args
        assert args[1] == umgebung["org_id"] and args[2]["To"] == erwartet
        assert args[2]["Subject"] == f"Überprüfung fällig: {umgebung['titel']}"
        assert basis(umgebung) in args[2].get_content()
    else:
        liefern.assert_not_awaited()
        assert lade_plan(umgebung).wiedervorlage_erinnert_am is None
    asyncio.run(task_reminder._check_katplan_wiedervorlagen())
    assert liefern.await_count == (1 if aktiv and mail else 0)


@pytest.mark.parametrize("aktion,status", [
    ("einreichen", "freigegeben"), ("einreichen", "archiviert"),
    ("zurueckweisen", "entwurf"), ("zurueckweisen", "in_ueberarbeitung"),
    ("freigeben", "archiviert"), ("freigeben", "unbekannt"),
])
def test_ungueltige_uebergaenge_ohne_neue_version(db, org, aktion, status):
    plan, _, user = arbeitsstand(db, org)
    plan.status = status
    with pytest.raises(ValueError):
        getattr(service, aktion)(db, plan, user, "Kommentar")
    assert plan.status == status
    assert db.query(KatplanVersion).filter_by(plan_id=plan.id).count() == 0
    assert db.query(KatplanPruefung).filter_by(plan_id=plan.id).count() == 0


def test_erinnerungsfehler_wird_geloggt(monkeypatch, caplog):
    monkeypatch.setattr(task_reminder, "_letzter_katplan_check", None)
    def kaputt():
        raise RuntimeError("Datenbank nicht erreichbar")
    monkeypatch.setattr(task_reminder, "SessionLocal", kaputt)
    asyncio.run(task_reminder._check_katplan_wiedervorlagen())
    assert "Katastrophenplan-Wiedervorlage fehlgeschlagen" in caplog.text
