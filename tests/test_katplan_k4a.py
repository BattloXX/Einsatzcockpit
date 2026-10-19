"""K4a: Stammdatenlinks, Rechte, historische Snapshots und Beziehungen."""

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.katplan import KatplanAenderung, KatplanElement, KatplanLink, KatplanPlan, KatplanRelation
from app.models.kontakt import Kontakt, KontaktOrganisation, KontaktTelefon
from app.models.master import FireDept, OrgSettings, SystemSettings, VehicleMaster
from app.models.objekt import Objekt
from app.models.wasserstelle import Wasserstelle
from app.services import katplan_freigabe_service as freigabe
from app.services import katplan_link_service as service
from app.services.katplan_service import aktualisiere_element_suchtext, baue_suchtext
from tests.conftest import TestingSession
from tests.test_katplan_k1a import db, org  # noqa: F401
from tests.test_katplan_k1b import login, post, umgebung  # noqa: F401
from tests.test_katplan_k2a import anlegen, basis
from tests.test_katplan_k3a import arbeitsstand


@pytest.fixture
def daten(db, org):
    plan, element, user = arbeitsstand(db, org)
    andere = FireDept(slug=f"k4-{uuid4().hex}", name="Org B")
    db.add(andere)
    db.flush()
    db.add(OrgSettings(org_id=org.id, kontakte_module_enabled=True, objekt_module_enabled=True))
    for key in ("kontakte_module_enabled", "objekt_module_enabled"):
        flag = db.query(SystemSettings).filter_by(key=key).one_or_none()
        if flag:
            flag.value = "true"
        else:
            db.add(SystemSettings(key=key, value="true"))
    ziel = {
        "kontakt": Kontakt(org_id=org.id, anzeigename="Anna Leitner", funktion="Leitung", organisation="Bauhof"),
        "kontakt_organisation": KontaktOrganisation(org_id=org.id, name="Bauhof", kurzname="BH"),
        "objekt": Objekt(org_id=org.id, name="Schule", strasse="Dorfstraße", hausnummer="1", ort="Bregenz"),
        "fahrzeug": VehicleMaster(dept_id=org.id, name="Tank", code="TLF", funkrufname="Florian Tank"),
        "wasserstelle": Wasserstelle(org_id=org.id, bezeichnung="Dorfbrunnen", typ="brunnen"),
    }
    fremd = {
        "kontakt": Kontakt(org_id=andere.id, anzeigename="Fremd"),
        "kontakt_organisation": KontaktOrganisation(org_id=andere.id, name="Fremd"),
        "objekt": Objekt(org_id=andere.id, name="Fremd"),
        "fahrzeug": VehicleMaster(dept_id=andere.id, name="Fremd", code="F"),
        "wasserstelle": Wasserstelle(org_id=andere.id, bezeichnung="Fremd"),
    }
    db.add_all([*ziel.values(), *fremd.values()])
    db.flush()
    kontakt = ziel["kontakt"]
    kontakt.telefone = [KontaktTelefon(org_id=org.id, nummer="+43664111", verwendung="dienst", aktiv=True, sort=0),
                        KontaktTelefon(org_id=org.id, nummer="+43664222", verwendung="privat", aktiv=True, sort=1),
                        KontaktTelefon(org_id=org.id, nummer="+43664333", verwendung="dienst", aktiv=False, sort=2)]
    db.flush()
    return plan, element, user, ziel, fremd


def verknuepfe(db, daten, typ="kontakt", *, planebene=False, **kwargs):
    plan, element, user, ziel, _ = daten
    return service.link_anlegen(db, plan, user, element=None if planebene else element,
                                ziel_typ=typ, ziel_id=ziel[typ].id, rolle="zustaendig", **kwargs)


@pytest.mark.parametrize("typ", ["kontakt", "kontakt_organisation", "objekt", "fahrzeug", "wasserstelle"])
def test_links_validierung_suche_und_entfernung(db, org, daten, typ):
    plan, element, user, ziel, fremd = daten
    version = element.version
    link = verknuepfe(db, daten, typ)
    assert element.version == version + 1
    assert link.org_id == org.id
    titel = service.link_ansicht(db, link, voll=False)["titel"]
    assert titel.lower() in element.suchtext
    assert service.ziel_suchen(db, org.id, typ, "")[0]["titel"] == titel
    assert set(service.ziel_suchen(db, org.id, typ, "")[0]) == {"id", "titel", "untertitel"}
    assert service.ziel_suchen(db, org.id, typ, "", limit=0) == []
    with pytest.raises(ValueError, match="existiert"):
        verknuepfe(db, daten, typ)
    with pytest.raises(ValueError, match="nicht verfügbar"):
        service.link_anlegen(db, plan, user, ziel_typ=typ, ziel_id=fremd[typ].id, rolle="info")
    with pytest.raises(ValueError, match="Rolle"):
        service.link_anlegen(db, plan, user, ziel_typ=typ, ziel_id=ziel[typ].id, rolle="falsch")
    aktualisiere_element_suchtext(element)
    assert titel.lower() in element.suchtext
    service.link_entfernen(db, plan, user, link)
    assert titel.lower() not in baue_suchtext(element)
    assert {a.aktion for a in db.query(KatplanAenderung).filter_by(entitaet="link")} == {"verknuepft", "entknuepft"}


def test_arbeitskopie_planlinks_und_bindung(db, org, daten):
    plan, element, user, ziel, fremd = daten
    kopie = Objekt(org_id=org.id, name="Arbeitskopie", entwurf_von_id=ziel["objekt"].id)
    db.add(kopie)
    db.flush()
    with pytest.raises(ValueError, match="nicht verfügbar"):
        service.link_anlegen(db, plan, user, ziel_typ="objekt", ziel_id=kopie.id, rolle="info")
    verknuepfe(db, daten, planebene=True)
    with pytest.raises(ValueError, match="existiert"):
        verknuepfe(db, daten, planebene=True)
    anderer_plan = KatplanPlan(org_id=org.id, titel="Zweiter")
    db.add(anderer_plan)
    db.flush()
    with pytest.raises(ValueError, match="Element gehört"):
        service.link_anlegen(db, anderer_plan, user, element=element, ziel_typ="kontakt",
                             ziel_id=ziel["kontakt"].id, rolle="info")
    with pytest.raises(ValueError):
        service.link_entfernen(db, anderer_plan, user, db.query(KatplanLink).one())
    set_tenant_context(db, fremd["kontakt"].org_id)
    assert db.query(KatplanLink).all() == []


@pytest.mark.parametrize("system,org_aktiv", [(False, True), (True, False)])
def test_modul_aus(db, org, daten, system, org_aktiv):
    db.query(SystemSettings).filter_by(key="kontakte_module_enabled").one().value = str(system).lower()
    db.query(OrgSettings).filter_by(org_id=org.id).one().kontakte_module_enabled = org_aktiv
    for typ in ("kontakt", "kontakt_organisation"):
        assert not service.ziel_verfuegbar(db, org.id, typ)
        assert service.ziel_suchen(db, org.id, typ, "") == []
        with pytest.raises(ValueError, match="nicht verfügbar"):
            verknuepfe(db, daten, typ)
    assert service.ziel_verfuegbar(db, org.id, "fahrzeug")
    assert service.ziel_verfuegbar(db, org.id, "wasserstelle")
    assert not service.ziel_verfuegbar(db, org.id, "material")
    db.query(OrgSettings).filter_by(org_id=org.id).one().objekt_module_enabled = False
    assert not service.ziel_verfuegbar(db, org.id, "objekt")


def test_telefonrechte_und_urls(db, org, daten):
    link = verknuepfe(db, daten)
    kontakt = daten[3]["kontakt"]
    assert service.link_ansicht(db, link, voll=False)["telefone"] == []
    kontakt.einsatzrelevant = True
    assert service.link_ansicht(db, link, voll=False)["telefone"] == [{"nummer": "+43664111", "label": None}]
    assert len(service.link_ansicht(db, link, voll=True)["telefone"]) == 3
    assert service.link_ansicht(db, link, voll=True)["url"] is None
    assert service.link_ansicht(db, link, voll=True, user_rechte={"kontakt": True})["url"] == f"/kontakte/{kontakt.id}"
    assert service.link_ansicht(db, link, voll=False, user_rechte={"kontakt": True})["url"] is None
    objektlink = verknuepfe(db, daten, "objekt")
    assert service.link_ansicht(db, objektlink, voll=False, user_rechte={"objekt": True})["url"]
    kontakt.aktiv = False
    assert service.link_ansicht(db, link, voll=True)["fehlt"]
    assert service.link_ansicht(db, link, voll=True)["telefone"] == []


def test_snapshot_hash_liveaenderung_und_vergleich(db, org, daten):
    plan, element, user, ziel, _ = daten
    verknuepfe(db, daten)
    verknuepfe(db, daten, "objekt", planebene=True)
    anderer_plan = KatplanPlan(org_id=org.id, titel="Zweiter")
    db.add(anderer_plan)
    db.flush()
    anderes = KatplanElement(org_id=org.id, plan_id=anderer_plan.id, typ="info", titel="Nachbarplan")
    db.add(anderes)
    db.flush()
    relation = service.relation_anlegen(db, plan, user, element, anderes.uuid, "siehe_auch", "Details")
    v1 = freigabe.freigeben(db, plan, user)
    db.flush()
    _, alt_plan, alt = freigabe.lade_version(db, plan)
    links = json.loads(alt[0].links_json)
    assert links[0]["snapshot"]["titel"] == "Anna Leitner"
    assert len(links[0]["snapshot"]["telefone"]) == 3
    assert alt_plan["links"][0]["snapshot"]["titel"] == "Schule"
    assert json.loads(alt[0].relationen_json) == [{"zu_element_uuid": anderes.uuid, "art": "siehe_auch", "hinweis": "Details"}]
    ziel["kontakt"].telefone[0].nummer = "+43664999"
    ziel["kontakt"].anzeigename = "Anna Neu"
    aktualisiere_element_suchtext(element)
    with pytest.raises(ValueError, match="Keine Änderungen"):
        freigabe.freigeben(db, plan, user)
    set_tenant_context(db, org.id)
    ansicht = service.links_aus_version(db, links, voll=False, user_rechte={})[0]
    assert ansicht["geaendert_seit_freigabe"] and ansicht["titel"] == "Anna Neu"
    assert ansicht["telefone"] == []
    assert freigabe._aenderungen({"links_json": alt[0].links_json}, {"links_json": json.dumps([
        links[0] | {"snapshot": {"titel": "Andere Daten", "telefone": []}}])}) == []
    service.relation_entfernen(db, plan, user, relation)
    verknuepfe(db, daten, "wasserstelle")
    assert plan.status == "in_ueberarbeitung"
    v2 = freigabe.freigeben(db, plan, user)
    db.flush()
    assert v2.inhalt_hash != v1.inhalt_hash
    _, neu_plan, neu = freigabe.lade_version(db, plan)
    diff = freigabe.vergleiche_versionen(alt, neu, alt_plan, neu_plan)
    assert {f["label"] for f in diff["geaendert"][0]["felder"] if "label" in f} == {"Verknüpfungen", "Beziehungen"}
    with pytest.raises(ValueError, match="Keine Änderungen"):
        freigabe.freigeben(db, plan, user)


@pytest.mark.parametrize("weg", ["loeschen", "archivieren", "inaktiv"])
def test_fehlendes_ziel_snapshot_telefone_nur_voll(db, org, daten, weg):
    plan, _, user, ziel, _ = daten
    verknuepfe(db, daten)
    freigabe.freigeben(db, plan, user)
    db.flush()
    links = freigabe.lade_version(db, plan)[2][0].links_json
    kontakt = ziel["kontakt"]
    if weg == "loeschen":
        db.delete(kontakt)
    elif weg == "archivieren":
        kontakt.archiviert = True
    else:
        kontakt.aktiv = False
    db.flush()
    for voll in (False, True):
        ansicht = service.links_aus_version(db, links, voll=voll, user_rechte={"org_id": org.id})[0]
        assert ansicht["fehlt"] and ansicht["titel"] == "Anna Leitner"
        assert ansicht["url"] is None and ansicht["geaendert_seit_freigabe"]
        assert len(ansicht["telefone"]) == (3 if voll else 0)


def test_relationen_validierung_und_tenant(db, org, daten):
    plan, element, user, _, fremd = daten
    fremder_plan = KatplanPlan(org_id=fremd["kontakt"].org_id, titel="Fremd")
    db.add(fremder_plan)
    db.flush()
    fremdes = KatplanElement(org_id=fremder_plan.org_id, plan_id=fremder_plan.id, typ="info", titel="Fremd")
    eigenes = KatplanElement(org_id=org.id, plan_id=plan.id, typ="info", titel="Eigenes")
    db.add_all([fremdes, eigenes])
    db.flush()
    for uuid in (element.uuid, fremdes.uuid, str(uuid4())):
        with pytest.raises(ValueError):
            service.relation_anlegen(db, plan, user, element, uuid, "siehe_auch")
    with pytest.raises(ValueError):
        service.relation_anlegen(db, plan, user, element, eigenes.uuid, "falsch")
    relation = service.relation_anlegen(db, plan, user, element, eigenes.uuid, "folgt_auf")
    with pytest.raises(ValueError, match="existiert"):
        service.relation_anlegen(db, plan, user, element, eigenes.uuid, "folgt_auf")
    with pytest.raises(ValueError):
        service.relation_entfernen(db, fremder_plan, user, relation)
    service.relation_entfernen(db, plan, user, relation)
    service.relation_anlegen(db, plan, user, element, eigenes.uuid, "folgt_auf")
    db.flush()
    assert db.query(KatplanRelation).filter_by(plan_id=plan.id).count() == 1
    set_tenant_context(db, fremder_plan.org_id)
    assert db.query(KatplanRelation).all() == []


def test_erneute_freigabe_ui_422_bei_kontaktaenderung(client, umgebung):
    login(client, umgebung, "katplan_freigeber")
    element = anlegen(client, umgebung)
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        org_id = umgebung["org_id"]
        db.query(OrgSettings).filter_by(org_id=org_id).one().kontakte_module_enabled = True
        flag = db.query(SystemSettings).filter_by(key="kontakte_module_enabled").one_or_none()
        if flag:
            flag.value = "true"
        else:
            db.add(SystemSettings(key="kontakte_module_enabled", value="true"))
        kontakt = Kontakt(org_id=org_id, anzeigename="Testkontakt")
        db.add(kontakt)
        db.flush()
        db.add(KatplanLink(org_id=org_id, plan_id=element.plan_id, element_id=element.id,
                           ziel_typ="kontakt", ziel_id=kontakt.id, rolle="info"))
        db.commit()
        kontakt_id = kontakt.id
    assert post(client, basis(umgebung) + "/freigeben").status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, org_id)
        db.add(KontaktTelefon(org_id=org_id, kontakt_id=kontakt_id, nummer="+43664123"))
        db.commit()
    antwort = post(client, basis(umgebung) + "/freigeben")
    assert antwort.status_code == 422 and "Keine Änderungen" in antwort.text


def test_migration_0296_up_down_sqlite():
    pfad = Path(__file__).resolve().parents[1] / "alembic/versions/0296_katplan_verknuepfungen.py"
    spec = importlib.util.spec_from_file_location("katplan_link_migration", pfad)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            for name in ("fire_dept", "user", "katplan_plan", "katplan_element", "katplan_version_element"):
                conn.execute(sa.text(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)"))
            conn.execute(sa.text("INSERT INTO katplan_version_element (id) VALUES (1)"))
            migration.op = Operations(MigrationContext.configure(conn))
            migration.upgrade()
            assert migration.revision == "0296" and migration.down_revision == "0295"
            assert conn.execute(sa.text("SELECT relationen_json FROM katplan_version_element")).scalar() == "[]"
            inspector = sa.inspect(conn)
            for modell in (KatplanLink, KatplanRelation):
                name = modell.__tablename__
                assert {c["name"]: c["nullable"] for c in inspector.get_columns(name)} == {
                    c.name: c.nullable for c in modell.__table__.columns}
                assert {tuple(i["column_names"]) for i in inspector.get_indexes(name)} == {
                    tuple(c.name for c in i.columns) for i in modell.__table__.indexes}
                assert {tuple(u["column_names"]) for u in inspector.get_unique_constraints(name)} == {
                    tuple(c.name for c in u.columns) for u in modell.__table__.constraints if isinstance(u, sa.UniqueConstraint)}
                assert set(Base.metadata.tables[name].columns.keys()) == {c["name"] for c in inspector.get_columns(name)}
            assert "ziel_id" not in {c for fk in inspector.get_foreign_keys("katplan_link") for c in fk["constrained_columns"]}
            migration.downgrade()
            assert set(sa.inspect(conn).get_table_names()) == {
                "fire_dept", "user", "katplan_plan", "katplan_element", "katplan_version_element"}
            assert [c["name"] for c in sa.inspect(conn).get_columns("katplan_version_element")] == ["id"]
    finally:
        engine.dispose()
