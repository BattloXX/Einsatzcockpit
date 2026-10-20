"""Backend-Grundlage: Modulschalter, Rechte, Tenant-Grenzen und Planhelfer."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from starlette.requests import Request

from app.core.dependencies import _resolve_current_org, _set_module_states
from app.core.tenant import TenantContextMissing, set_tenant_context
from app.db import Base
from app.models.katplan import (
    KatplanAbschnitt,
    KatplanAenderung,
    KatplanElement,
    KatplanElementPhase,
    KatplanPhase,
    KatplanPlan,
    KatplanPruefung,
)
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.seed_data import ROLES
from app.services.katplan_flags import katplan_effective_enabled, katplan_system_enabled
from app.services.katplan_service import (
    KatplanKonflikt,
    baue_suchtext,
    naechste_kennung,
    protokolliere,
    pruefe_version,
)
from app.services.katplan_typen import ELEMENT_TYPEN, bereinige_daten
from app.services.katplan_zugriff import (
    darf_bearbeiten,
    darf_freigeben,
    darf_lesen,
    require_katplan_bearbeiten,
    require_katplan_freigeben,
    require_katplan_lesen,
    sieht_arbeitsstand,
    sieht_vertraulich,
)
from tests.conftest import TestingSession

MODELLE = (KatplanPlan, KatplanAbschnitt, KatplanPhase, KatplanElement,
           KatplanElementPhase, KatplanAenderung, KatplanPruefung)


@pytest.fixture
def db():
    with TestingSession() as session:
        set_tenant_context(session, None)
        yield session
        session.rollback()


@pytest.fixture
def org(db):
    wert = FireDept(slug=f"katplan-{uuid4().hex}", name="Katplan Test")
    db.add(wert)
    db.flush()
    return wert


def _user(rolle, org_id=None):
    return SimpleNamespace(roles=[SimpleNamespace(code=rolle)], org_id=org_id, id=None)


def _request(user=None):
    request = Request({"type": "http", "headers": [], "query_string": b""})
    request.state.user = user
    return request


@pytest.mark.parametrize("system", [None, "false", "TRUE", "true"])
@pytest.mark.parametrize("aktiv", [False, True])
def test_modul_flag(db, org, system, aktiv):
    db.add(OrgSettings(org_id=org.id, katplan_modul_aktiv=aktiv))
    if system is not None:
        db.add(SystemSettings(key="katplan_module_enabled", value=system))
    db.flush()
    erwartet = system == "true" and aktiv
    assert katplan_system_enabled(db) is (system == "true")
    assert katplan_effective_enabled(org.id, db) is erwartet
    assert katplan_effective_enabled(None, db) is False
    request = _request()
    _set_module_states(request, org.id, db)
    assert request.state.katplan_enabled is erwartet


def test_modul_defaults_und_fehlende_org_settings(db, org):
    db.add(SystemSettings(key="katplan_module_enabled", value="true"))
    db.flush()
    assert not katplan_effective_enabled(org.id, db)
    request = _request()
    _resolve_current_org(request, db)
    assert request.state.katplan_enabled is False


@pytest.mark.parametrize("rolle,lesen,bearbeiten,freigeben,vertraulich", [
    ("katplan_verwalter", True, True, False, True),
    ("katplan_freigeber", True, True, True, True),
    ("readonly", True, False, False, False),
    ("recorder", True, False, False, False),
    ("breathing_supervisor", True, False, False, False),
    ("objekt_verwalter", True, False, False, False),
    ("incident_leader", True, False, False, True),
    ("org_admin", True, True, True, True),
    ("admin", True, True, True, True),
    ("system_admin", True, True, True, True),
    ("mailing_sender", False, False, False, False),
    ("", False, False, False, False),
])
def test_rollen(rolle, lesen, bearbeiten, freigeben, vertraulich):
    user = _user(rolle)
    assert darf_lesen(user) is lesen
    assert darf_bearbeiten(user) is bearbeiten
    assert darf_freigeben(user) is freigeben
    assert sieht_arbeitsstand(user) is bearbeiten
    assert sieht_vertraulich(user) is vertraulich
    assert not darf_lesen(None)


@pytest.mark.parametrize("dependency,rolle,erlaubt", [
    (require_katplan_lesen, "readonly", True),
    (require_katplan_lesen, "mailing_sender", False),
    (require_katplan_bearbeiten, "readonly", False),
    (require_katplan_bearbeiten, "katplan_verwalter", True),
    (require_katplan_freigeben, "katplan_verwalter", False),
    (require_katplan_freigeben, "katplan_freigeber", True),
])
def test_dependencies(db, org, dependency, rolle, erlaubt):
    user = _user(rolle, org.id)
    request = _request(user)
    with pytest.raises(HTTPException) as exc:
        dependency(request, db)
    assert exc.value.status_code == 404
    db.add_all([OrgSettings(org_id=org.id, katplan_modul_aktiv=True),
                SystemSettings(key="katplan_module_enabled", value="true")])
    db.flush()
    if erlaubt:
        assert dependency(request, db) is user
    else:
        with pytest.raises(HTTPException) as exc:
            dependency(request, db)
        assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        dependency(_request(), db)
    assert exc.value.status_code == 401


def test_tenant_filter(db, org):
    andere_org = FireDept(slug=f"katplan-{uuid4().hex}", name="Andere Org")
    db.add(andere_org)
    db.flush()
    plan = KatplanPlan(org_id=org.id, titel="Nur Org A")
    db.add(plan)
    db.flush()
    abschnitt = KatplanAbschnitt(org_id=org.id, plan_id=plan.id, titel="Kapitel")
    phase = KatplanPhase(org_id=org.id, plan_id=plan.id, name="Warnung")
    element = KatplanElement(org_id=org.id, plan_id=plan.id, typ="info", titel="Hinweis")
    db.add_all([abschnitt, phase, element])
    db.flush()
    db.add_all([
        KatplanElementPhase(org_id=org.id, element_id=element.id, phase_id=phase.id),
        KatplanPruefung(org_id=org.id, plan_id=plan.id, aktion="eingereicht"),
        KatplanAenderung(org_id=org.id, plan_id=plan.id, entitaet="plan", aktion="erstellt"),
    ])
    db.flush()
    set_tenant_context(db, andere_org.id)
    for modell in MODELLE:
        assert db.query(modell).all() == []
    set_tenant_context(db, org.id)
    for modell in MODELLE:
        assert len(db.query(modell).all()) == 1
    db.info.clear()
    for modell in MODELLE:
        with pytest.raises(TenantContextMissing):
            db.query(modell).all()


def test_bereinige_daten():
    assert bereinige_daten("info", {"kategorie": "  Hochwasser  ", "fremd": "weg"}) == {
        "kategorie": "Hochwasser",
    }
    with pytest.raises(ValueError, match="Unbekannter Elementtyp"):
        bereinige_daten("unbekannt", {})
    assert len(ELEMENT_TYPEN) == 9
    assert bereinige_daten("info", {"warnhinweis": "x" * 6000}) == {"warnhinweis": "x" * 5000}
    assert bereinige_daten("massnahme", {"schritte": "falsch"}) == {}
    assert bereinige_daten("massnahme", {"schritte": [None, 7, " Text ",
        {"text": " Schritt ", "erledigt_hinweis": " OK ", "fremd": "weg"}]}) == {
        "schritte": ["7", "Text", {"text": "Schritt", "erledigt_hinweis": "OK"}],
    }
    assert len(bereinige_daten("kontrollstelle", {"gefahren": [" Gefahr "] * 150})["gefahren"]) == 100
    assert bereinige_daten("ablauf", {"bedingungen": [{"wenn": " A ", "dann": " B "}]}) == {
        "bedingungen": [{"wenn": "A", "dann": "B"}],
    }
    assert bereinige_daten("ressource", {"bedarf": ["weg", {"bezeichnung": " Sandsack ",
        "menge": " 10 ", "einheit": " Stück ", "verleih_id": 42}]}) == {
        "bedarf": [{"bezeichnung": "Sandsack", "menge": "10", "einheit": "Stück"}],
    }


def test_suchtext():
    element = KatplanElement(
        titel="Hochwasser", kennung="KS-01", beschreibung_html="<p>Pegel &amp; Höhe</p><p>prüfen</p>",
        daten_json=json.dumps({"schritte": [{"text": "Sandsäcke füllen"}], "werte": ["Bauhof", 17]}),
        strasse="Dorfstraße", hausnummer="1", plz="6900", ort="Bregenz",
        tags_json='["Warnung"]',
    )
    text = baue_suchtext(element)
    for inhalt in ("hochwasser", "ks-01", "pegel & höhe", "prüfen", "sandsäcke füllen", "bauhof",
                   "dorfstraße 1 6900 bregenz", "warnung"):
        assert inhalt in text
    assert "<" not in text and ">" not in text and "&amp;" not in text
    assert text == " ".join(text.lower().split())
    element.daten_json = "kaputt"
    element.tags_json = "{"
    assert "hochwasser" in baue_suchtext(element)
    element.titel = "X" * 21000
    assert len(baue_suchtext(element)) == 20000


@pytest.mark.parametrize("roh,erwartet", [
    (None, []), ("kaputt", []), ('{"a": "sturm"}', []), ('["sturm", 3, null]', ["sturm"]),
])
def test_gefahrenarten(roh, erwartet):
    assert KatplanPlan(gefahrenarten_json=roh).gefahrenarten == erwartet


def test_kennung(db, org):
    plan = KatplanPlan(org_id=org.id, titel="Plan A")
    anderer_plan = KatplanPlan(org_id=org.id, titel="Plan B")
    db.add_all([plan, anderer_plan])
    db.flush()
    assert naechste_kennung(db, plan, "kontrollstelle") == "KS-01"
    db.add(KatplanElement(org_id=org.id, plan_id=plan.id, typ="kontrollstelle", titel="Pegel", kennung="KS-01"))
    assert naechste_kennung(db, plan, "kontrollstelle") == "KS-02"
    assert naechste_kennung(db, plan, "massnahme") == "M-01"
    assert naechste_kennung(db, anderer_plan, "kontrollstelle") == "KS-01"
    db.add_all([KatplanElement(org_id=org.id, plan_id=plan.id, typ="kontrollstelle", titel="Alt", kennung="KS-09"),
                KatplanElement(org_id=org.id, plan_id=plan.id, typ="info", titel="Falsch", kennung="KS-xyz")])
    assert naechste_kennung(db, plan, "kontrollstelle") == "KS-10"
    with pytest.raises(ValueError):
        naechste_kennung(db, plan, "unbekannt")


@pytest.mark.parametrize("modell", [KatplanPlan, KatplanElement])
def test_version(modell):
    obj = modell(version=3)
    pruefe_version(obj, 3)
    assert obj.version == 3
    with pytest.raises(KatplanKonflikt, match="zwischenzeitlich"):
        pruefe_version(obj, 2)


def test_protokoll(db, org):
    plan = KatplanPlan(org_id=org.id, titel="Plan")
    db.add(plan)
    db.flush()
    element = KatplanElement(org_id=org.id, plan_id=plan.id, typ="info", titel="Hinweis")
    db.add(element)
    db.flush()
    eintrag = protokolliere(db, plan, "element", "geloescht", None,
                           feld="titel", alt="Hinweis", element_id=element.id)
    db.flush()
    assert eintrag.org_id == org.id
    assert eintrag.quelle == "ui"
    assert eintrag.alt == "Hinweis"
    assert eintrag.zeit.tzinfo is None
    assert plan.erstellt_am.tzinfo is None
    assert plan.aktualisiert_am.tzinfo is None
    element_id = element.id
    db.delete(element)
    db.flush()
    assert db.query(KatplanAenderung).filter_by(element_id=element_id).one() is eintrag


def test_seed_rollen():
    assert {"katplan_verwalter", "katplan_freigeber"} <= {rolle["code"] for rolle in ROLES}


def test_migration_upgrade_downgrade():
    pfad = Path(__file__).resolve().parents[1] / "alembic/versions/0294_katplan_basis.py"
    spec = importlib.util.spec_from_file_location("katplan_migration", pfad)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            for tabelle in ("fire_dept", "user", "kontakt", "org_settings"):
                conn.execute(sa.text(f"CREATE TABLE {tabelle} (id INTEGER PRIMARY KEY)"))
            migration.op = Operations(MigrationContext.configure(conn))
            migration.upgrade()
            inspector = sa.inspect(conn)
            assert migration.revision == "0294"
            assert migration.down_revision == "0293"
            for modell in MODELLE:
                tabelle = modell.__tablename__
                spalten = {c["name"]: c for c in inspector.get_columns(tabelle)}
                assert set(spalten) == set(Base.metadata.tables[tabelle].columns.keys())
                for spalte in modell.__table__.columns:
                    assert spalten[spalte.name]["nullable"] == spalte.nullable
                assert {tuple(i["column_names"]) for i in inspector.get_indexes(tabelle)} == {
                    tuple(c.name for c in i.columns) for i in modell.__table__.indexes
                }
                assert {tuple(u["column_names"]) for u in inspector.get_unique_constraints(tabelle)} == {
                    tuple(c.name for c in u.columns) for u in modell.__table__.constraints
                    if isinstance(u, sa.UniqueConstraint)
                }
            assert "katplan_modul_aktiv" in {c["name"] for c in inspector.get_columns("org_settings")}
            migration.downgrade()
            assert set(sa.inspect(conn).get_table_names()) == {"fire_dept", "user", "kontakt", "org_settings"}
            assert [c["name"] for c in sa.inspect(conn).get_columns("org_settings")] == ["id"]
    finally:
        engine.dispose()


def test_bereinige_daten_zahlen_und_verworfen():
    verworfen = []
    assert bereinige_daten("ressource", {"bedarf": [
        {"menge": 500.0, "einheit": 2.5, "lieferant": "X"}, {"menge": True}, None,
    ], "lagerort": 42, "fremd": "X"}, verworfen) == {
        "bedarf": [{"menge": "500", "einheit": "2.5"}], "lagerort": "42",
    }
    assert verworfen == ["daten.bedarf[0].lieferant: unbekanntes Feld",
                         "daten.bedarf[1].menge: ungueltiger Typ (bool)",
                         "daten.bedarf[1]: leerer oder ungueltiger Eintrag",
                         "daten.bedarf[2]: leerer oder ungueltiger Eintrag",
                         "daten.fremd: unbekanntes Feld"]
    verworfen = []
    assert bereinige_daten("kontrollstelle", {"gefahren": [500, 2.5, 500.0, False]}, verworfen) == {
        "gefahren": ["500", "2.5", "500"],
    }
    assert verworfen == ["daten.gefahren[3]: leerer oder ungueltiger Eintrag"]
    verworfen = []
    bereinige_daten("massnahme", {"schritte": ["x" * 5001] * 101}, verworfen)
    assert "daten.schritte: nur 100 Eintraege uebernommen" in verworfen
    assert "daten.schritte[0]: auf 5000 Zeichen gekuerzt" in verworfen
    verworfen = []
    assert bereinige_daten("objektverweis", {"hinweis": "x" * 5001}, verworfen) == {"hinweis": "x" * 5000}
    assert verworfen == ["daten.hinweis: auf 5000 Zeichen gekuerzt"]
