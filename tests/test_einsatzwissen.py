"""Phase 1: Kategorien, Freigabegrenzen, Altbestand, Verlauf und additive Migration."""

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanAenderung, KatplanNutzung, KatplanPlan
from app.models.master import OrgSettings, SystemSettings
from app.models.user import User
from app.services.einsatzwissen_kategorien import KATEGORIEN
from app.services.katplan_freigabe_service import freigeben
from app.services.katplan_service import lege_element_an
from tests.conftest import TestingSession
from tests.test_katplan_k1b import login, post
from tests.test_katplan_k1b import umgebung as basis_umgebung  # noqa: F401
from tests.test_mcp_katplan import rufe


@pytest.fixture
def umgebung(basis_umgebung):  # noqa: F811
    return basis_umgebung


def veroeffentliche(info, plan_id):
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        user = db.query(User).filter_by(username=info["users"]["katplan_freigeber"]).one()
        plan = db.get(KatplanPlan, plan_id)
        lege_element_an(db, plan, user, typ="info", titel="Kontrolliere die Pegelmessung")
        version = freigeben(db, plan, user)
        db.commit()
        return version.id


def anlegen(client, **werte):
    response = post(client, "/plaene/neu", titel="Wissenseintrag", planart="massnahmenplan", **werte)
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[1])


@pytest.mark.parametrize("kategorie", KATEGORIEN)
def test_sechs_kategorien(client, umgebung, kategorie):
    login(client, umgebung)
    titel = f"Eintrag {kategorie}"
    response = post(client, "/plaene/neu", titel=titel, planart="massnahmenplan", kategorie=kategorie,
                    unterkategorie="Ausbildung", schlagwoerter="Atemschutz, Führung, Atemschutz",
                    kurzbeschreibung="Ein kompakter Lehrbehelf")
    plan_id = int(response.headers["location"].rsplit("/", 1)[1])
    page = client.get(f"/einsatzwissen?kategorie={kategorie}&fassung=arbeitsstand")
    assert page.status_code == 200 and titel in page.text
    assert "Ein kompakter Lehrbehelf" in page.text and "Ausbildung" in page.text
    assert titel not in client.get("/einsatzwissen").text
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, plan_id)
        assert plan.planart == "massnahmenplan"
        assert json.loads(plan.schlagwoerter_json) == ["Atemschutz", "Führung"]
    assert 'value="' + kategorie + '" selected' in client.get(f"/plaene/{plan_id}/stammdaten").text
    if kategorie != "einsatzplaene":
        assert titel not in client.get("/einsatzwissen?kategorie=einsatzplaene&fassung=arbeitsstand").text


def test_freigabesnapshot_suche_und_mcp(client, umgebung):
    login(client, umgebung)
    plan_id = anlegen(client, kategorie="fuehrungswissen", unterkategorie="Stab",
                      schlagwoerter="Funkordnung", kurzbeschreibung="Freigegebener Kurztext")
    veroeffentliche(umgebung, plan_id)
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        version = db.get(KatplanPlan, plan_id).version
    assert post(client, f"/plaene/{plan_id}/stammdaten", version=version, titel="Geheimer Entwurf",
                planart="massnahmenplan", kategorie="fachwissen", unterkategorie="Geheim",
                schlagwoerter="Geheimwort", kurzbeschreibung="Vertraulicher Kurztext").status_code == 200
    login(client, umgebung, "readonly")
    for query in ("", "?q=Funkordnung", "?q=Pegelmessung", "?unterkategorie=Stab", "?fassung=arbeitsstand"):
        page = client.get("/einsatzwissen" + query)
        assert page.status_code == 200 and "Wissenseintrag" in page.text
        assert "Geheimer Entwurf" not in page.text and "Geheimwort" not in page.text
        assert "Vertraulicher Kurztext" not in page.text and ">Geheim<" not in page.text
    assert "Wissenseintrag" not in client.get("/einsatzwissen?q=Geheimwort").text
    assert "Wissenseintrag" not in client.get("/einsatzwissen?kategorie=fachwissen").text
    assert "Freigegebener Kurztext" in client.get(f"/plaene/{plan_id}").text
    assert "Kontrolliere die Pegelmessung" in client.get("/plaene/suche?q=Pegelmessung").text
    info = {**umgebung, "plan_id": plan_id}
    kopf = rufe(info, "katplan_lesen", "readonly", plan_id=plan_id)["kopf"]
    assert kopf["kategorie"] == "fuehrungswissen" and kopf["schlagwoerter"] == ["Funkordnung"]
    assert rufe(info, "katplan_liste", "readonly")["plaene"][0]["kurzbeschreibung"] == "Freigegebener Kurztext"


def test_alte_clients_erhalten_metadaten_und_formular_kann_leeren(client, umgebung):
    login(client, umgebung)
    plan_id = anlegen(client, kategorie="fachwissen", unterkategorie="Training", schlagwoerter="ABC",
                      kurzbeschreibung="Text")
    assert post(client, f"/plaene/{plan_id}/stammdaten", version=0, titel="Altclient",
                planart="massnahmenplan").status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, plan_id)
        assert plan.kategorie == "fachwissen" and plan.kurzbeschreibung == "Text"
    assert post(client, f"/plaene/{plan_id}/stammdaten", version=1, titel="Altclient",
                planart="massnahmenplan", kategorie="", unterkategorie="", schlagwoerter="",
                kurzbeschreibung="").status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, plan_id)
        assert plan.kategorie is None and plan.kurzbeschreibung is None
        assert plan.unterkategorie is None and plan.schlagwoerter_json is None
        assert {a.feld for a in db.query(KatplanAenderung).filter_by(plan_id=plan_id)} >= {
            "kategorie", "unterkategorie", "schlagwoerter_json", "kurzbeschreibung"}


def test_mcp_metadaten_schreiben_und_konflikt(client, umgebung):
    info = umgebung
    result = rufe(info, "katplan_anlegen", "katplan_verwalter", felder={
        "titel": "MCP-Wissen", "planart": "massnahmenplan", "kategorie": "fachwissen",
        "unterkategorie": "Übung", "kurzbeschreibung": "Kurz", "schlagwoerter": "A, B",
    })
    plan_id = result["plan_id"]
    assert result["plan"]["schlagwoerter"] == ["A", "B"]
    assert result["plan"]["kategorie"] == "fachwissen"
    result = rufe(info, "katplan_stammdaten_aendern", "katplan_verwalter", plan_id=plan_id, version=0,
                  felder={"kategorie": "fuehrungswissen", "schlagwoerter": "", "kurzbeschreibung": None})
    assert result["plan"]["schlagwoerter"] == [] and result["plan"]["kurzbeschreibung"] == ""
    assert set(result["geaendert"]) == {"kategorie", "schlagwoerter", "kurzbeschreibung"}
    with pytest.raises(ValueError, match="Versionskonflikt"):
        rufe(info, "katplan_stammdaten_aendern", "katplan_verwalter", plan_id=plan_id, version=0,
             felder={"kategorie": "fachwissen"})


def test_alte_snapshots_und_letzte_zugriffe(client, umgebung):
    from app.models.katplan import KatplanVersion

    login(client, umgebung)
    vid = veroeffentliche(umgebung, umgebung["plan_id"])
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        v = db.get(KatplanVersion, vid)
        daten = json.loads(v.plan_json)
        for feld in ("kategorie", "unterkategorie", "schlagwoerter", "kurzbeschreibung", "planart"):
            daten.pop(feld, None)
        # Historischen Bestand simulieren, ohne den ORM-Schutz produktiver Freigaben zu ändern.
        db.execute(sa.update(KatplanVersion).where(KatplanVersion.id == vid).values(plan_json=json.dumps(daten)))
        db.commit()
    login(client, umgebung, "readonly")
    page = client.get("/einsatzwissen?kategorie=katastrophenschutz")
    assert umgebung["titel"] in page.text
    assert umgebung["titel"] not in client.get("/einsatzwissen?kategorie=einsatzplaene").text
    assert client.get(f'/plaene/{umgebung["plan_id"]}').status_code == 200
    assert client.get(f'/plaene/{umgebung["plan_id"]}').status_code == 200
    assert len(client.get("/einsatzwissen").context["zuletzt"]) == 1
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        assert db.query(KatplanNutzung).filter_by(plan_id=umgebung["plan_id"]).count() == 1
        db.get(KatplanPlan, umgebung["plan_id"]).status = "archiviert"
        db.commit()
    assert client.get("/einsatzwissen").context["zuletzt"] == []
    assert client.get(f'/plaene/{umgebung["plan_id"]}').status_code == 404
    login(client, umgebung, "fremd")
    assert umgebung["titel"] not in client.get("/einsatzwissen?fassung=arbeitsstand").text
    assert client.get("/einsatzwissen").context["zuletzt"] == []


def test_schalter_und_nachschlagewerk(client, umgebung):
    login(client, umgebung, "readonly")
    assert "/nachschlagewerke/gefahrgut" not in client.get("/einsatzwissen").text
    with TestingSession() as db:
        set_tenant_context(db, None)
        org = db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one()
        org.katplan_modul_aktiv = False
        org.nachschlagewerke_module_enabled = True
        flag = db.query(SystemSettings).filter_by(key="nachschlagewerke_module_enabled").first()
        vorher = flag.value if flag else None
        if not flag:
            flag = SystemSettings(key="nachschlagewerke_module_enabled")
            db.add(flag)
        flag.value = "true"
        db.commit()
    try:
        page = client.get("/einsatzwissen?kategorie=nachschlagewerk")
        assert page.status_code == 200 and "/nachschlagewerke/gefahrgut" in page.text
        assert client.get("/nachschlagewerke/").status_code == 200
        assert client.get("/plaene").status_code == 404
        with TestingSession() as db:
            set_tenant_context(db, None)
            db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one().nachschlagewerke_module_enabled = False
            db.commit()
        assert client.get("/einsatzwissen").status_code == 404
    finally:
        with TestingSession() as db:
            set_tenant_context(db, None)
            flag = db.query(SystemSettings).filter_by(key="nachschlagewerke_module_enabled").one()
            if vorher is None:
                db.delete(flag)
            else:
                flag.value = vorher
            db.commit()


@pytest.mark.parametrize("werte", [
    {"kategorie": "unbekannt"}, {"unterkategorie": "x" * 101}, {"kurzbeschreibung": "x" * 501},
    {"schlagwoerter": "x" * 51}, {"schlagwoerter": ",".join(str(i) for i in range(21))},
])
def test_metadaten_validierung(client, umgebung, werte):
    login(client, umgebung)
    assert post(client, "/plaene/neu", titel="Ungültig", planart="massnahmenplan", **werte).status_code == 422
    assert client.get("/einsatzwissen?kategorie=unbekannt").status_code == 422


def test_migration_bewahrt_bestand():
    pfad = Path(__file__).resolve().parents[1] / "alembic/versions/0308_einsatzwissen.py"
    spec = importlib.util.spec_from_file_location("migration_einsatzwissen", pfad)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for name in ("fire_dept", "user"):
            conn.execute(sa.text(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE katplan_plan (id INTEGER PRIMARY KEY, planart TEXT, titel TEXT)"))
        conn.execute(sa.text("INSERT INTO katplan_plan VALUES (7, 'massnahmenplan', 'Altbestand')"))
        conn.execute(sa.text("CREATE TABLE katplan_version (id INTEGER PRIMARY KEY, plan_json TEXT)"))
        conn.execute(sa.text("INSERT INTO katplan_version VALUES (3, '{\"titel\":\"Altbestand\"}')"))
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        assert conn.execute(sa.text("SELECT id, planart, titel FROM katplan_plan")).one() == (
            7, "massnahmenplan", "Altbestand")
        assert conn.execute(sa.text("SELECT kategorie FROM katplan_plan")).scalar_one() is None
        assert conn.execute(sa.text("SELECT plan_json FROM katplan_version")).scalar_one() == '{"titel":"Altbestand"}'
        assert {c["name"] for c in sa.inspect(conn).get_columns("katplan_nutzung")} == set(
            KatplanNutzung.__table__.columns.keys())
        migration.downgrade()
        assert conn.execute(sa.text("SELECT count(*) FROM katplan_plan")).scalar_one() == 1
        assert "katplan_nutzung" not in sa.inspect(conn).get_table_names()
    engine.dispose()
