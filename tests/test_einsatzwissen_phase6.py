"""Einsatzansicht, persönliche Schnellzugriffe und begrenzte Offlineberechtigung."""

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanElement, KatplanNutzung, KatplanPlan
from app.models.master import OrgSettings
from app.models.user import User
from app.services.katplan_freigabe_service import freigeben
from app.services.katplan_service import lege_abschnitt_an, lege_element_an
from tests.conftest import TestingSession
from tests.test_katplan_k1b import login, post
from tests.test_katplan_k1b import umgebung as umgebung


@pytest.fixture
def einsatzwissen(umgebung):
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        user = db.query(User).filter_by(username=umgebung["users"]["katplan_freigeber"]).one()
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        plan.titel = "Stromausfall – Einsatzplan"
        plan.kategorie = "einsatzplaene"
        plan.gefahrenarten_json = '["blackout"]'
        plan.schlagwoerter_json = '["Notstrom"]'
        plan.kurzbeschreibung = "Kernwissen für den Stromausfall"
        elemente = {}
        for typ, titel, prio, daten, vertraulich in [
            ("massnahme", "Sofort absperren", 1, {"schritte": [{"text": "Zufahrt sperren"}]}, False),
            ("info", "Spannung beachten", 2, {"warnhinweis": "Abstand halten"}, False),
            ("massnahme", "Versorgung aufbauen", 2, {}, False),
            ("ressource", "Stromerzeuger", 2, {"lagerort": "Feuerwehrhaus"}, False),
            ("medien", "Lagekarte", 2, {}, False),
            ("info", "Funkordnung", 2, {}, False),
            ("massnahme", "Vertraulicher Auftrag", 1, {}, True),
        ]:
            e = lege_element_an(db, plan, user, typ=typ, titel=titel)
            e.prioritaet, e.vertraulich = prio, vertraulich
            e.daten_json, e.beschreibung_html = json.dumps(daten), f"<p>{titel} Inhalt</p>"
            elemente[titel] = e.uuid
        version = freigeben(db, plan, user)
        plan.wiedervorlage_am = date(2020, 1, 1)
        db.commit()
        return {**umgebung, "version_id": version.id, "elemente": elemente}


def test_einsatzansicht_reihenfolge_fassung_und_vertraulichkeit(client, einsatzwissen):
    login(client, einsatzwissen, "readonly")
    response = client.get(f'/einsatzwissen/{einsatzwissen["plan_id"]}')
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    bereiche = response.context["bereiche"]
    assert [b["id"] for b in bereiche] == ["sofort", "sicherheit", "massnahmen", "dokumente", "kontakte", "verwandt"]
    assert [e.titel for e in bereiche[0]["elemente"]] == ["Sofort absperren"]
    assert "Spannung beachten" in [e.titel for e in bereiche[1]["elemente"]]
    assert "Prüfung fällig" in response.text
    assert "Vertraulicher Auftrag" not in response.text
    with TestingSession() as db:
        set_tenant_context(db, einsatzwissen["org_id"])
        plan = db.get(KatplanPlan, einsatzwissen["plan_id"])
        plan.titel, plan.status = "Geheimer neuer Arbeitsstand", "in_ueberarbeitung"
        db.commit()
    login(client, einsatzwissen, "katplan_freigeber")
    response = client.get(f'/einsatzwissen/{einsatzwissen["plan_id"]}')
    assert "Geheimer neuer Arbeitsstand" not in response.text
    assert "Vertraulicher Auftrag" in response.text


def test_favoriten_haeufig_und_verlauf_isoliert(client, einsatzwissen):
    login(client, einsatzwissen, "readonly")
    plan_id = einsatzwissen["plan_id"]
    assert post(client, f"/einsatzwissen/{plan_id}/favorit", aktiv="true").status_code == 303
    assert client.get("/einsatzwissen?auswahl=favoriten").context["eintraege"][0]["id"] == plan_id
    assert client.get("/einsatzwissen?auswahl=haeufig").context["eintraege"] == []
    for _ in range(3):
        assert client.get(f"/einsatzwissen/{plan_id}").status_code == 200
    assert client.get("/einsatzwissen?auswahl=haeufig").context["eintraege"][0]["id"] == plan_id
    with TestingSession() as db:
        set_tenant_context(db, einsatzwissen["org_id"])
        n = db.query(KatplanNutzung).filter_by(plan_id=plan_id).one()
        assert n.aufrufe == 3 and n.favorit
    login(client, einsatzwissen, "katplan_verwalter")
    assert client.get("/einsatzwissen").context["favoriten"] == []
    assert client.get("/einsatzwissen").context["haeufig"] == []
    login(client, einsatzwissen, "readonly")
    assert post(client, f"/einsatzwissen/{plan_id}/favorit", aktiv="false").status_code == 303
    assert client.get("/einsatzwissen").context["favoriten"] == []


@pytest.mark.parametrize("q", ["Notstrom", "Stromausfall", "Blackout", "Einsatzpläne", "absperren"])
def test_suche_metadaten_gefahren_kategorien_und_sprung(client, einsatzwissen, q):
    login(client, einsatzwissen, "readonly")
    response = client.get("/einsatzwissen", params={"q": q, "gefahr": "blackout"})
    assert [p["id"] for p in response.context["eintraege"]] == [einsatzwissen["plan_id"]]
    if q == "absperren":
        assert f'/einsatzwissen/{einsatzwissen["plan_id"]}#element-' in response.text
        assert "<mark>absperren</mark>" in response.text
    assert client.get("/einsatzwissen?gefahr=sturm").context["eintraege"] == []


def test_offline_nur_freigegebene_kerntexte_ohne_vertrauliche_daten(client, einsatzwissen):
    login(client, einsatzwissen, "katplan_freigeber")
    response = client.get(f'/einsatzwissen/offline-paket?ids={einsatzwissen["plan_id"]}')
    assert response.status_code == 200
    data = response.json()
    assert 0 < data["gueltig_bis"] - data["erstellt"] <= 900000
    assert data["eintraege"][0]["version_id"] == einsatzwissen["version_id"]
    assert data["eintraege"][0]["pruefung_faellig"]
    assert response.headers["cache-control"] == "no-store"
    for text in ("Vertraulicher Auftrag", "Stromerzeuger", "Lagekarte", "Funkordnung", "anhaenge_json", "links_json"):
        assert text not in response.text
    assert "Zufahrt sperren" in response.text
    login(client, einsatzwissen, "fremd")
    assert client.get(f'/einsatzwissen/offline-paket?ids={einsatzwissen["plan_id"]}').status_code == 404
    assert client.get(f'/einsatzwissen/{einsatzwissen["plan_id"]}').status_code == 404
    assert post(client, f'/einsatzwissen/{einsatzwissen["plan_id"]}/favorit', aktiv="true").status_code == 404


def test_entzug_archiv_und_modul_sperren_auch_offlinepaket(client, einsatzwissen):
    login(client, einsatzwissen, "readonly")
    plan_id = einsatzwissen["plan_id"]
    with TestingSession() as db:
        set_tenant_context(db, einsatzwissen["org_id"])
        db.get(KatplanPlan, plan_id).status = "archiviert"
        db.commit()
    assert client.get(f"/einsatzwissen/offline-paket?ids={plan_id}").status_code == 404
    assert client.get(f"/einsatzwissen/{plan_id}").status_code == 404
    assert client.get("/einsatzwissen").context["zuletzt"] == []
    with TestingSession() as db:
        set_tenant_context(db, einsatzwissen["org_id"])
        db.query(OrgSettings).filter_by(org_id=einsatzwissen["org_id"]).one().katplan_modul_aktiv = False
        db.commit()
    assert client.get(f"/einsatzwissen/offline-paket?ids={plan_id}").status_code == 404


@pytest.mark.parametrize("ids", ["", "a", ",".join(str(i) for i in range(21))])
def test_offline_auswahl_validieren(client, einsatzwissen, ids):
    login(client, einsatzwissen, "readonly")
    assert client.get("/einsatzwissen/offline-paket", params={"ids": ids}).status_code == 422


def test_offlinefreigabe_beachtet_kurzes_sessionfenster(client, einsatzwissen, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "SESSION_INACTIVITY_SECONDS", 120)
    login(client, einsatzwissen, "readonly")
    data = client.get(f'/einsatzwissen/offline-paket?ids={einsatzwissen["plan_id"]}').json()
    assert data['gueltig_bis'] - data['erstellt'] <= 120000


def test_erstmassnahmen_unterkapitel_und_neue_lokale_version(client, einsatzwissen):
    with TestingSession() as db:
        set_tenant_context(db, einsatzwissen['org_id'])
        plan = db.get(KatplanPlan, einsatzwissen['plan_id'])
        user = db.query(User).filter_by(username=einsatzwissen['users']['katplan_freigeber']).one()
        haupt = lege_abschnitt_an(db, plan, user, titel='Erstmaßnahmen')
        kind = lege_abschnitt_an(db, plan, user, titel='Versorgung', parent_id=haupt.id)
        e = db.query(KatplanElement).filter_by(plan_id=plan.id, titel='Versorgung aufbauen').one()
        e.abschnitt_id = kind.id
        version = freigeben(db, plan, user)
        neue_version_id = version.id
        db.commit()
    login(client, einsatzwissen, 'readonly')
    view = client.get(f'/einsatzwissen/{einsatzwissen["plan_id"]}')
    assert 'Versorgung aufbauen' in [e.titel for e in view.context['bereiche'][0]['elemente']]
    paket = client.get(f'/einsatzwissen/offline-paket?ids={einsatzwissen["plan_id"]}').json()
    assert paket['eintraege'][0]['version_id'] == neue_version_id
    assert paket['eintraege'][0]['version'] == 2


def test_migration_0310_bewahrt_verlauf_und_rueckbau():
    path = Path("alembic/versions/0310_einsatzwissen_schnellzugriff.py")
    spec = importlib.util.spec_from_file_location("migration_phase6", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE katplan_nutzung (id INTEGER PRIMARY KEY, user_id INT, plan_id INT)"))
        conn.execute(sa.text("INSERT INTO katplan_nutzung VALUES (7, 3, 11)"))
        migration.op = Operations(MigrationContext.configure(conn))
        for _ in range(2):
            migration.upgrade()
            row = conn.execute(sa.text("SELECT id, user_id, plan_id, favorit, aufrufe FROM katplan_nutzung")).one()
            assert row == (7, 3, 11, 0, 1)
            migration.downgrade()
            assert conn.execute(sa.text("SELECT * FROM katplan_nutzung")).one() == (7, 3, 11)
    engine.dispose()
