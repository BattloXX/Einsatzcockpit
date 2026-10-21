"""E3: Akteurskatalog, gemeinsame Schreibregeln und stabile Fassungen."""

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanAenderung, KatplanAkteur, KatplanElement, KatplanPlan
from app.services.katplan_akteur_service import (
    akteur_loeschen,
    akteur_speichern,
    akteure,
    validiere_akteure,
)
from app.services.katplan_freigabe_service import freigeben, lade_version
from app.services.katplan_pdf_service import render_katplan_html
from app.services.katplan_qs_service import element_hinweise, plan_zusammenfassung
from app.services.katplan_service import baue_suchtext, lege_element_an
from tests.conftest import TestingSession
from tests.test_katplan_k1b import (
    login,
    post,
    umgebung,  # noqa: F401
)
from tests.test_katplan_planart import _anlegen, _user
from tests.test_mcp_katplan import rufe


def test_katalog_validierung_und_pflege(client, umgebung):  # noqa: F811
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        org_id = umgebung["org_id"]
        assert db.query(KatplanAkteur).count() == 0
        assert len(akteure(db, org_id)) == len(akteure(db, org_id)) == 7
        assert validiere_akteure(db, org_id, ["feuerwehr", "feuerwehr"]) == ["feuerwehr"]
        neu = akteur_speichern(db, org_id, None, "Örtliche Hilfe", "gruen", "home", True)
        key = neu.schluessel
        assert key.isascii()
        gleich = akteur_speichern(db, org_id, None, "Örtliche Hilfe", "grau", None, True)
        assert gleich.schluessel != key
        assert akteur_speichern(db, org_id, key, "Andere Hilfe", "blau", None, True).schluessel == key
        for farbe, icon in [("pink", None), ("rot", "nicht-vorhanden")]:
            with pytest.raises(ValueError):
                akteur_speichern(db, org_id, None, "Test", farbe, icon, True)
        akteur_speichern(db, org_id, key, "Andere Hilfe", "blau", None, False)
        with pytest.raises(ValueError, match="Unbekannter Akteur"):
            validiere_akteure(db, org_id, [key])
        with pytest.raises(ValueError):
            validiere_akteure(db, org_id, "feuerwehr")
        with pytest.raises(ValueError, match="10"):
            validiere_akteure(db, org_id, [str(n) for n in range(11)])
        akteur_loeschen(db, org_id, key)
        assert db.query(KatplanAkteur).filter_by(schluessel=key).count() == 0


def test_editor_snapshot_hash_und_ansicht(client, umgebung):  # noqa: F811
    login(client, umgebung)
    pid = _anlegen(client, "alarm_einsatzplan")
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, pid)
        element = lege_element_an(db, plan, _user(db, umgebung), typ="massnahme", titel="Strom abschalten")
        eid, kennung = element.id, element.kennung
        db.commit()
    assert 'aria-label="Gliederung"' not in client.get(f"/plaene/{pid}").text
    response = post(client, f"/plaene/{pid}/elemente/{eid}", titel="Strom abschalten", kennung=kennung,
                    version=0, akteure=["feuerwehr", "netzbetreiber"], daten_json="{}")
    assert response.status_code == 200
    assert 'Zuständig (Akteure)' in response.text
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        element = db.get(KatplanElement, eid)
        assert json.loads(element.akteure_json) == ["feuerwehr", "netzbetreiber"]
        assert "netzbetreiber" in baue_suchtext(element)
        assert db.query(KatplanAenderung).filter_by(element_id=eid, feld="akteure_json").count() == 1
        with pytest.raises(ValueError, match="deaktivieren"):
            akteur_loeschen(db, umgebung["org_id"], "feuerwehr")
        plan, user = db.get(KatplanPlan, pid), _user(db, umgebung)
        version = freigeben(db, plan, user)
        db.flush()
        vid = version.id
        kopf, rows = lade_version(db, plan)[1:]
        assert next(a for a in kopf["akteure"] if a["schluessel"] == "feuerwehr")["bezeichnung"] == "Feuerwehr"
        assert json.loads(rows[0].akteure_json) == ["feuerwehr", "netzbetreiber"]
        akteur_speichern(db, plan.org_id, "feuerwehr", "Neue Feuerwehr", "rot", "flame", True)
        assert "Neue Feuerwehr" not in render_katplan_html(db, user, plan)
        akteur_speichern(db, plan.org_id, "feuerwehr", "Feuerwehr", "rot", "flame", True)
        element.akteure_json = '["netzbetreiber"]'
        plan.status = "in_ueberarbeitung"
        assert freigeben(db, plan, user).inhalt_hash != version.inhalt_hash
        db.commit()
    response = post(client, f"/plaene/{pid}/elemente/{eid}", titel="Strom abschalten", kennung=kennung,
                    version=1, akteure=["unbekannt"], daten_json="{}")
    assert response.status_code == 422
    html = client.get(f"/plaene/{pid}/versionen/{vid}").text
    # Historical view keeps both actor labels and supports the actor grouping.
    assert 'aria-label="Gliederung"' in html
    assert "Netzbetreiber" in html and "Neue Feuerwehr" not in html
    arbeitsstand = client.get(f"/plaene/{pid}?ansicht=arbeitsstand").text
    assert 'data-akteure=' in arbeitsstand and 'aria-label="Akteurfilter"' in arbeitsstand
    response = post(client, f"/plaene/{pid}/elemente/{eid}/duplizieren")
    assert response.status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        kopie = db.query(KatplanElement).filter(KatplanElement.plan_id == pid, KatplanElement.id != eid).one()
        assert json.loads(kopie.akteure_json) == ["netzbetreiber"]


def test_pdf_auszug_und_gruppierung(client, umgebung):  # noqa: F811
    login(client, umgebung)
    pid = _anlegen(client, "massnahmenplan")
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan, user = db.get(KatplanPlan, pid), _user(db, umgebung)
        for titel, keys in [("Nur Feuerwehr", ["feuerwehr"]), ("Nur Netz", ["netzbetreiber"]),
                             ("Gemeinsam", ["feuerwehr", "netzbetreiber"]), ("Allgemein", [])]:
            e = lege_element_an(db, plan, user, typ="massnahme", titel=titel)
            e.akteure_json = json.dumps(keys)
        freigeben(db, plan, user)
        db.flush()
        html = render_katplan_html(db, user, plan, akteur="netzbetreiber")
        assert "Auszug für Netzbetreiber" in html and "Nur Netz" in html and "Gemeinsam" in html
        assert "Nur Feuerwehr" not in html and "Allgemein" not in html
        assert "Zuständig: Netzbetreiber" in html
        gruppiert = render_katplan_html(db, user, plan, gliederung="akteur")
        assert "<h2>Feuerwehr</h2>" in gruppiert and "<h2>Ohne Akteur</h2>" in gruppiert
        assert gruppiert.count(">Gemeinsam</h3>") == 2
        with pytest.raises(ValueError):
            render_katplan_html(db, user, plan, akteur="fremd")


@pytest.mark.parametrize("planart,erwartet", [("alarm_einsatzplan", True), ("massnahmenplan", True),
                                           ("katastrophenplan", False)])
def test_qs(client, umgebung, planart, erwartet):  # noqa: F811
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = KatplanPlan(org_id=umgebung["org_id"], titel="QS", planart=planart)
        db.add(plan)
        db.flush()
        e = lege_element_an(db, plan, _user(db, umgebung), typ="massnahme", titel="Maßnahme")
        assert ("massnahme_ohne_akteur" in plan_zusammenfassung(db, plan, date(2026, 10, 21))) is erwartet
        e.akteure_json = '["feuerwehr"]'
        assert not element_hinweise(db, plan, e, heute=date(2026, 10, 21))


def test_mcp(client, umgebung):  # noqa: F811
    login(client, umgebung)
    pid = _anlegen(client, "massnahmenplan")
    info = {**umgebung, "plan_id": pid}
    args = dict(plan_id=pid, fassung="arbeitsstand")
    katalog = rufe(info, "katplan_lesen", "katplan_verwalter", **args)["akteure"]
    assert {a["schluessel"] for a in katalog} == {
        "feuerwehr", "netzbetreiber", "behoerde", "polizei", "rettung", "gemeinde", "sonstige"}
    result = rufe(info, "katplan_element_entwurf_anlegen", "katplan_verwalter", plan_id=pid,
                  typ="massnahme", titel="Abschalten", akteure=["netzbetreiber", "netzbetreiber"])
    assert result["element"]["akteure"] == [{"schluessel": "netzbetreiber", "bezeichnung": "Netzbetreiber"}]
    assert "akteure" in result["geaendert"]
    assert len(rufe(info, "katplan_elemente", "katplan_verwalter", akteur="netzbetreiber", **args)["elemente"]) == 1
    assert not rufe(info, "katplan_elemente", "katplan_verwalter", akteur="feuerwehr", **args)["elemente"]
    for keys in [["fremd"], "feuerwehr"]:
        with pytest.raises(ValueError):
            rufe(info, "katplan_element_entwurf_aendern", "katplan_verwalter", plan_id=pid,
                 element_id=result["element_id"], version=result["version"], felder={"akteure": keys})
    geaendert = rufe(info, "katplan_element_entwurf_aendern", "katplan_verwalter", plan_id=pid,
                     element_id=result["element_id"], version=result["version"], felder={"akteure": None})
    assert geaendert["element"]["akteure"] == [] and geaendert["geaendert"] == ["akteure"]


@pytest.mark.parametrize("rolle", ["readonly", "katplan_verwalter"])
def test_katalog_rechte(client, umgebung, rolle):  # noqa: F811
    login(client, umgebung, rolle)
    assert client.get("/plaene/akteure").status_code == 403
    assert post(client, "/plaene/akteure", bezeichnung="Test").status_code == 403
    assert post(client, "/plaene/akteure/1/loeschen").status_code == 403


def test_katalog_seite(client, umgebung):  # noqa: F811
    login(client, umgebung, "katplan_freigeber")
    assert "Akteure verwalten" in client.get("/plaene").text
    assert "Feuerwehr" in client.get("/plaene/akteure").text
    assert post(client, "/plaene/akteure", bezeichnung="Wasserwerk", farbe="blau", aktiv="1").status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        a = db.query(KatplanAkteur).filter_by(schluessel="wasserwerk").one()
        aid = a.id
    assert post(client, f"/plaene/akteure/{aid}/loeschen").status_code == 200


def test_migration():
    pfad = Path(__file__).resolve().parents[1] / "alembic/versions/0304_katplan_akteure.py"
    spec = importlib.util.spec_from_file_location("migration_akteure", pfad)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0304", "0303")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for name in ("fire_dept", "katplan_element", "katplan_version_element"):
            conn.execute(sa.text(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("INSERT INTO katplan_element (id) VALUES (1)"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert conn.execute(sa.text("SELECT akteure_json FROM katplan_element")).scalar_one() is None
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert "katplan_akteur" not in sa.inspect(conn).get_table_names()
        assert conn.execute(sa.text("SELECT id FROM katplan_element")).scalar_one() == 1
    engine.dispose()
