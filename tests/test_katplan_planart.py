"""E1b: Planarten, Vorlagen und kompatible Freigaben."""

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanAbschnitt, KatplanAenderung, KatplanPhase, KatplanPlan, KatplanVersion
from app.models.user import User
from app.services.katplan_freigabe_service import erstelle_snapshot, freigeben, lade_version
from app.services.katplan_pdf_service import render_katplan_html
from app.services.katplan_service import lege_element_an
from app.services.katplan_typen import PLANARTEN
from tests.conftest import TestingSession
from tests.test_katplan_k1b import login, post
from tests.test_katplan_k1b import umgebung as basis_umgebung  # noqa: F401
from tests.test_mcp_katplan import rufe


@pytest.fixture
def umgebung(basis_umgebung):  # noqa: F811
    return basis_umgebung


def _user(db, info, rolle="katplan_freigeber"):
    return db.query(User).filter_by(username=info["users"][rolle]).one()


def _anlegen(client, planart):
    response = post(client, "/plaene/neu", titel="Planart-Test", planart=planart)
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[1])


@pytest.mark.parametrize("methode,pfad,ziel", [
    ("GET", "/katastrophenplaene", "/plaene"),
    ("GET", "/katastrophenplaene/5?x=1", "/plaene/5?x=1"),
    ("POST", "/katastrophenplaene/neu", "/plaene/neu"),
])
def test_redirect(client, methode, pfad, ziel):
    client.get("/login")
    response = client.request(methode, pfad, data={"_csrf": client.cookies.get("ec_csrf")},
                              follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == ziel


@pytest.mark.parametrize("planart", ["alarm_einsatzplan", "massnahmenplan"])
def test_anlage_vorlage_und_formular(client, umgebung, planart):
    login(client, umgebung)
    html = client.get(f"/plaene/neu?planart={planart}").text
    assert f'value="{planart}" selected' in html
    plan_id = _anlegen(client, planart)
    vorlage = PLANARTEN[planart]
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, plan_id)
        assert plan.planart == planart and plan.gefahrenarten == []
        kapitel = db.query(KatplanAbschnitt).filter_by(plan_id=plan_id).order_by(KatplanAbschnitt.sort).all()
        phasen = db.query(KatplanPhase).filter_by(plan_id=plan_id).order_by(KatplanPhase.sort).all()
        assert tuple(a.titel for a in kapitel) == vorlage.kapitel
        assert tuple((p.name, p.farbe) for p in phasen) == vorlage.phasen
        assert [a.sort for a in kapitel] == list(range(10, len(kapitel) * 10 + 1, 10))
        assert [p.sort for p in phasen] == [10, 20, 30]
        assert db.query(KatplanAenderung).filter_by(plan_id=plan_id, aktion="angelegt").count() == (
            1 + len(kapitel) + len(phasen))
    assert vorlage.label in client.get(f"/plaene/{plan_id}").text
    assert f'value="{planart}" selected' in client.get(f"/plaene/{plan_id}/stammdaten").text


@pytest.mark.parametrize("planart,gefahrenarten,fehler", [
    ("katastrophenplan", [], "mindestens eine gültige Gefahrenart"),
    ("ungueltig", ["hochwasser"], "Bitte eine gültige Planart wählen."),
    ("alarm_einsatzplan", ["ungueltig"], "mindestens eine gültige Gefahrenart"),
])
def test_ungueltige_anlage(client, umgebung, planart, gefahrenarten, fehler):
    login(client, umgebung)
    response = post(client, "/plaene/neu", titel="Ungültig", planart=planart, gefahrenarten=gefahrenarten)
    assert response.status_code == 422
    assert fehler in response.text


def test_snapshot_pdf_und_alte_version(client, umgebung):
    login(client, umgebung)
    plan_id = _anlegen(client, "alarm_einsatzplan")
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, plan_id)
        user = _user(db, umgebung)
        assert erstelle_snapshot(db, plan)[0]["planart"] == "alarm_einsatzplan"
        assert "Alarm- und Einsatzplan: Planart-Test" in render_katplan_html(db, user, plan, entwurf=True)
        lege_element_an(db, plan, user, typ="info", titel="Hinweis")
        version = freigeben(db, plan, user)
        db.flush()
        assert lade_version(db, plan)[1]["planart"] == "alarm_einsatzplan"
        assert "Alarm- und Einsatzplan: Planart-Test" in render_katplan_html(db, user, plan)
        alt = json.loads(version.plan_json)
        del alt["planart"]
        db.execute(sa.update(KatplanVersion).where(KatplanVersion.id == version.id).values(plan_json=json.dumps(alt)))
        db.expire(version)
        assert lade_version(db, plan)[1]["planart"] == "katastrophenplan"
        assert "Katastrophenplan: Planart-Test" in render_katplan_html(db, user, plan)
        db.commit()
    login(client, umgebung, "readonly")
    assert "Katastrophenplan" in client.get(f"/plaene/{plan_id}").text
    assert "Planart-Test" in client.get("/plaene?planart=katastrophenplan").text
    assert "Planart-Test" not in client.get("/plaene?planart=alarm_einsatzplan").text
    info = {**umgebung, "plan_id": plan_id}
    assert rufe(info, "katplan_lesen", plan_id=plan_id)["kopf"]["planart"] == "katastrophenplan"
    assert any(p["id"] == plan_id and p["planart"] == "katastrophenplan"
               for p in rufe(info, "katplan_liste", planart="katastrophenplan")["plaene"])


def test_filter_mcp_und_stammdaten_ohne_neue_vorlage(client, umgebung):
    login(client, umgebung)
    plan_id = _anlegen(client, "alarm_einsatzplan")
    assert "Planart-Test" in client.get("/plaene?planart=alarm_einsatzplan").text
    assert "Planart-Test" not in client.get("/plaene?planart=massnahmenplan").text
    info = {**umgebung, "plan_id": plan_id}
    result = rufe(info, "katplan_liste", "katplan_verwalter", fassung="arbeitsstand", planart="alarm_einsatzplan")
    assert [(p["id"], p["planart"], p["planart_label"]) for p in result["plaene"]] == [
        (plan_id, "alarm_einsatzplan", "Alarm- und Einsatzplan")]
    assert not rufe(info, "katplan_liste", "katplan_verwalter",
                    fassung="arbeitsstand", planart="massnahmenplan")["plaene"]
    result = rufe(info, "katplan_stammdaten_aendern", "katplan_verwalter", plan_id=plan_id, version=0,
                  felder={"planart": "massnahmenplan"})
    assert result["geaendert"] == ["planart"]
    kopf = rufe(info, "katplan_lesen", "katplan_verwalter", plan_id=plan_id, fassung="arbeitsstand")["kopf"]
    assert (kopf["planart"], kopf["planart_label"]) == ("massnahmenplan", "Maßnahmenplan")
    for wert in ("ungueltig", "katastrophenplan"):
        with pytest.raises(ValueError):
            rufe(info, "katplan_stammdaten_aendern", "katplan_verwalter", plan_id=plan_id, version=1,
                 felder={"planart": wert})
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, plan_id)
        assert plan.planart == "massnahmenplan"
        assert db.query(KatplanAbschnitt).filter_by(plan_id=plan_id).count() == 6
        assert db.query(KatplanPhase).filter_by(plan_id=plan_id).count() == 3
        lege_element_an(db, plan, _user(db, umgebung), typ="info", titel="Hinweis")
        freigeben(db, plan, _user(db, umgebung))
        plan.planart = "alarm_einsatzplan"
        db.commit()
    login(client, umgebung, "readonly")
    assert "Planart-Test" in client.get("/plaene?planart=massnahmenplan").text
    assert "Planart-Test" not in client.get("/plaene?planart=alarm_einsatzplan").text
    liste = rufe(info, "katplan_liste", planart="massnahmenplan")["plaene"]
    assert [(p["id"], p["planart"]) for p in liste] == [(plan_id, "massnahmenplan")]


def test_ui_planart_aendern_und_kp_ohne_vorlage(client, umgebung):
    login(client, umgebung)
    response = post(client, "/plaene/neu", titel="KP-Test", planart="katastrophenplan", gefahrenarten=["hochwasser"])
    assert response.status_code == 303
    plan_id = int(response.headers["location"].rsplit("/", 1)[1])
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        assert db.query(KatplanAbschnitt).filter_by(plan_id=plan_id).count() == 0
        assert db.query(KatplanPhase).filter_by(plan_id=plan_id).count() == 0
    response = post(client, f"/plaene/{plan_id}/stammdaten", version=0,
                    titel="KP-Test", planart="alarm_einsatzplan")
    assert response.status_code == 200
    assert "Alarm- und Einsatzplan" in response.text
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        assert db.get(KatplanPlan, plan_id).planart == "alarm_einsatzplan"
        assert db.query(KatplanAbschnitt).filter_by(plan_id=plan_id).count() == 0
        assert db.query(KatplanPhase).filter_by(plan_id=plan_id).count() == 0
        assert db.query(KatplanAenderung).filter_by(plan_id=plan_id, feld="planart").one().neu == "alarm_einsatzplan"


def test_migration_bestand_default_index_und_downgrade():
    pfad = Path(__file__).resolve().parents[1] / "alembic/versions/0302_katplan_planart.py"
    spec = importlib.util.spec_from_file_location("migration_planart", pfad)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0302", "0301")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE katplan_plan (id INTEGER PRIMARY KEY, org_id INTEGER)"))
        conn.execute(sa.text("INSERT INTO katplan_plan (id, org_id) VALUES (1, 42)"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert conn.execute(sa.text("SELECT planart FROM katplan_plan")).scalar_one() == "katastrophenplan"
        conn.execute(sa.text("INSERT INTO katplan_plan (id, org_id) VALUES (2, 42)"))
        assert conn.execute(sa.text("SELECT planart FROM katplan_plan WHERE id=2")).scalar_one() == "katastrophenplan"
        spalte = next(c for c in sa.inspect(conn).get_columns("katplan_plan") if c["name"] == "planart")
        assert not spalte["nullable"]
        index = next(i for i in sa.inspect(conn).get_indexes("katplan_plan")
                     if i["name"] == "ix_katplan_plan_org_planart")
        assert index["column_names"] == ["org_id", "planart"]
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert "planart" not in {c["name"] for c in sa.inspect(conn).get_columns("katplan_plan")}
        assert not sa.inspect(conn).get_indexes("katplan_plan")
        assert conn.execute(sa.text("SELECT count(*) FROM katplan_plan")).scalar_one() == 2
    engine.dispose()
