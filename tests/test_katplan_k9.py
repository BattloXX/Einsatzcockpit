"""K9: KI-Import bleibt bis zur ausdruecklichen Review ohne Elemente."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.async_bridge import run_async
from app.core.tenant import set_tenant_context
from app.models.katplan import (
    KatplanAbschnitt, KatplanDokument, KatplanDokumentSeite, KatplanElement,
    KatplanElementPhase, KatplanImportVorschlag, KatplanLink, KatplanPhase, KatplanPlan,
)
from app.models.kontakt import Kontakt
from app.models.master import OrgSettings, SystemSettings
from app.models.objekt import Objekt
from app.services import ai_service
from app.services import katplan_ki_service as service
from tests.conftest import TestingSession
from tests.test_katplan_k1a import _user
from tests.test_katplan_k1a import db, org  # noqa: F401
from tests.test_katplan_k1b import login, post, umgebung  # noqa: F401
from tests.test_katplan_k2a import basis


@pytest.fixture(autouse=True)
def ki_mock(monkeypatch):
    monkeypatch.setattr("app.db.SessionLocal", TestingSession)
    monkeypatch.setattr(ai_service, "is_enabled", lambda: True)
    monkeypatch.setattr(ai_service, "_get_active_variable", lambda *a: None)
    monkeypatch.setattr(ai_service, "complete", AsyncMock(return_value='{"elemente": []}'))
    monkeypatch.setattr(ai_service, "complete_vision", AsyncMock(return_value='{"elemente": []}'))


def dokument_anlegen(db, plan, *, seiten=2):
    dokument = KatplanDokument(org_id=plan.org_id, plan_id=plan.id, titel="Maßnahmenplan",
        dateiname_original="plan.pdf", pfad="plan.pdf", groesse_bytes=10, seitenzahl=seiten, status="fertig")
    db.add(dokument)
    db.flush()
    for nr in range(1, seiten + 1):
        db.add(KatplanDokumentSeite(org_id=plan.org_id, plan_id=plan.id, dokument_id=dokument.id,
            seiten_nr=nr, einzel_pdf_pfad="seite.pdf", volltext=f"Seite {nr}: Maßnahmen", text_quelle="pdf"))
    db.flush()
    return dokument


def vorschlag_anlegen(db, plan, dokument, **werte):
    v = KatplanImportVorschlag(org_id=plan.org_id, plan_id=plan.id, dokument_id=dokument.id,
        seiten_nr=1, typ="kontrollstelle", titel="Gasleitung", beschreibung_html="<p>Achten</p>",
        daten_json='{"kontrollhinweise": "Prüfen", "lat": 47}', kontakte_json="[]", **werte)
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def daten(db, org):
    plan = KatplanPlan(org_id=org.id, titel="Hochwasser")
    db.add(plan)
    db.add(OrgSettings(org_id=org.id, katplan_ki_import_enabled=True))
    db.flush()
    return plan, _user("katplan_verwalter", org.id), dokument_anlegen(db, plan)


def module_aktivieren(db, org_id):
    org_s = db.query(OrgSettings).filter_by(org_id=org_id).one()
    org_s.kontakte_module_enabled = org_s.objekt_module_enabled = True
    for key in ("kontakte_module_enabled", "objekt_module_enabled"):
        flag = db.query(SystemSettings).filter_by(key=key).first()
        if flag:
            flag.value = "true"
        else:
            db.add(SystemSettings(key=key, value="true"))
    db.flush()


def test_parse_begrenzung_sanitizing():
    roh = {"typ": "unbekannt", "titel": "x" * 250, "prioritaet": True,
           "beschreibung": ["Absatz <script>x</script>", "• Gasleitung", "- Bauhof <b>rufen</b>", "2. Prüfen"],
           "kapitel": "k" * 250, "kontakte": [{"telefon": "+43 (0) 123", "kontakt_id": 99}]}
    result = service._parse_antwort("```json\n" + json.dumps({"elemente": [roh] * 40}) + "\n```")
    assert len(result) == 30
    v = result[0]
    assert v["typ"] == "info" and len(v["titel"]) == len(v["kapitel"]) == 200
    assert v["prioritaet"] is None
    assert "<ul><li>Gasleitung</li>" in v["beschreibung_html"]
    assert "&lt;script&gt;" in v["beschreibung_html"] and "<script>" not in v["beschreibung_html"]
    k = json.loads(v["kontakte_json"])[0]
    assert k["telefon"] == "+43 (0) 123" and k["kontakt_id"] is None
    with pytest.raises(ValueError):
        service._parse_antwort("kaputt")


def test_analyse_seitenfehler_und_neuanalyse(db, daten, monkeypatch):
    plan, user, dokument = daten
    alt = vorschlag_anlegen(db, plan, dokument, status="uebernommen")
    verworfen = vorschlag_anlegen(db, plan, dokument, status="verworfen")
    vorschlag_anlegen(db, plan, dokument)
    db.commit()
    antwort = json.dumps({"elemente": [{"typ": "falsch", "titel": "Neu", "beschreibung": ["• Eins", "- <script>x</script>"]}]})
    mock = AsyncMock(side_effect=["kaputt", antwort])
    monkeypatch.setattr(ai_service, "complete", mock)
    assert run_async(service.analysiere_quelle, dokument.id) == 1
    db.expire_all()
    assert dokument.ki_status == "fertig" and dokument.ki_analysiert_am
    assert "1 Seite" in dokument.ki_fehler
    vs = db.query(KatplanImportVorschlag).filter_by(dokument_id=dokument.id).all()
    assert len(vs) == 3
    neu = next(v for v in vs if v.status == "offen")
    assert neu.typ == "info" and neu.seiten_nr == 2 and "<ul><li>" in neu.beschreibung_html
    assert not db.query(KatplanElement).filter_by(plan_id=plan.id).count()
    assert mock.call_args.kwargs["feature"] == "katplan_import"
    monkeypatch.setattr(ai_service, "complete", AsyncMock(return_value='{"elemente": []}'))
    assert run_async(service.analysiere_quelle, dokument.id) == 0
    db.expire_all()
    assert {v.id for v in db.query(KatplanImportVorschlag).filter_by(dokument_id=dokument.id)} == {alt.id, verworfen.id}
    monkeypatch.setattr(ai_service, "complete", AsyncMock(side_effect=ValueError("Fehler")))
    assert run_async(service.analysiere_quelle, dokument.id) == 0
    db.expire_all()
    assert dokument.ki_status == "fehler"


def test_vision_und_prompt(db, daten, monkeypatch):
    plan, user, dokument = daten
    db.commit()
    monkeypatch.setattr(service, "_lade_bild_fuer_ki", lambda s: b"png")
    monkeypatch.setattr(ai_service, "_get_active_variable", lambda key, org_id: "Org-Anweisung")
    assert run_async(service.analysiere_quelle, dokument.id, max_seiten=1) == 0
    ai_service.complete.assert_not_awaited()
    ai_service.complete_vision.assert_awaited_once()
    prompt = ai_service.complete_vision.call_args.args[0]
    assert "Org-Anweisung" in prompt and "kontrollstelle" in prompt and "NUR als JSON" in prompt
    assert len(ai_service.complete_vision.call_args.args[1]) < 6200


def test_abgleich_module_und_eindeutigkeit(db, daten):
    plan, user, dokument = daten
    module_aktivieren(db, plan.org_id)
    kontakt = Kontakt(org_id=plan.org_id, anzeigename="Bauhof", organisation="Gemeinde")
    objekt = Objekt(org_id=plan.org_id, name="Schule")
    db.add_all([kontakt, objekt])
    db.flush()
    def neu():
        v = vorschlag_anlegen(db, plan, dokument, objekt_name="schule")
        v.kontakte_json = json.dumps([{"name": "Bauhof", "organisation": "Gemeinde", "telefon": "123", "kontakt_id": None}])
        service._abgleichen(db, v)
        return v
    v = neu()
    assert json.loads(v.kontakte_json)[0]["kontakt_id"] == kontakt.id and v.objekt_id == objekt.id
    db.add(Kontakt(org_id=plan.org_id, anzeigename="Bauhof", organisation="Gemeinde"))
    db.flush()
    assert json.loads(neu().kontakte_json)[0]["kontakt_id"] is None
    db.query(OrgSettings).filter_by(org_id=plan.org_id).one().kontakte_module_enabled = False
    db.flush()
    assert json.loads(neu().kontakte_json)[0]["kontakt_id"] is None


def test_uebernehmen_und_kapitel_phase(db, daten):
    plan, user, dokument = daten
    module_aktivieren(db, plan.org_id)
    kontakt = Kontakt(org_id=plan.org_id, anzeigename="Bauhof")
    db.add(kontakt)
    db.flush()
    v = vorschlag_anlegen(db, plan, dokument, strasse="Rickenbacherstraße", hausnummer="9", prioritaet=1)
    plan.status = "freigegeben"
    element = service.vorschlag_uebernehmen(db, plan, user, v, werte={
        "neues_kapitel": "Hoher Abfluss", "neue_phase": "Alarm", "beschreibung_html": "<p>Text</p><script>x</script>",
    })
    assert element.herkunft == "ai_suggested" and element.quelle_dokument_id == dokument.id and element.quelle_seiten == "1"
    assert element.geo_status == "keine" and element.lat is None and element.lng is None
    assert element.prioritaet == 1 and element.strasse == "Rickenbacherstraße"
    assert element.beschreibung_html == "<p>Text</p>" and "lat" not in json.loads(element.daten_json)
    assert plan.status == "in_ueberarbeitung" and v.status == "uebernommen" and v.element_id == element.id
    assert db.query(KatplanElementPhase).filter_by(element_id=element.id).count() == 1
    assert db.query(KatplanLink).filter_by(element_id=element.id).count() == 0
    with pytest.raises(ValueError, match="bereits"):
        service.vorschlag_uebernehmen(db, plan, user, v, werte={})
    kapitel = db.query(KatplanAbschnitt).filter_by(plan_id=plan.id).one()
    phase = db.query(KatplanPhase).filter_by(plan_id=plan.id).one()
    v2 = vorschlag_anlegen(db, plan, dokument)
    e2 = service.vorschlag_uebernehmen(db, plan, user, v2, werte={
        "abschnitt_id": kapitel.id, "phase_id": phase.id, "kontakt_ids": [kontakt.id],
    })
    assert e2.abschnitt_id == kapitel.id and db.query(KatplanLink).filter_by(element_id=e2.id).one().ziel_id == kontakt.id


def test_sammel_und_verwerfen(db, daten):
    plan, user, dokument = daten
    vs = [vorschlag_anlegen(db, plan, dokument, kapitel="Abfluss", phase="Alarm") for _ in range(3)]
    service.vorschlag_verwerfen(db, plan, user, vs[2])
    assert vs[2].status == "verworfen" and vs[2].entschieden_am
    assert service.sammel_uebernehmen(db, plan, user, [vs[0].id, vs[1].id, vs[0].id]) == 2
    assert db.query(KatplanAbschnitt).filter_by(plan_id=plan.id).count() == 1
    assert db.query(KatplanPhase).filter_by(plan_id=plan.id).count() == 1
    assert db.query(KatplanElement).filter_by(plan_id=plan.id).count() == 2
    plan.status = "archiviert"
    v = vorschlag_anlegen(db, plan, dokument)
    with pytest.raises(ValueError):
        service.vorschlag_uebernehmen(db, plan, user, v, werte={})


def ui_daten(info):
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        plan = db.get(KatplanPlan, info["plan_id"])
        dokument = dokument_anlegen(db, plan)
        v = vorschlag_anlegen(db, plan, dokument, kapitel="Abfluss", phase="Alarm")
        db.commit()
        return dokument.id, v.id


def test_ui_gate_job_und_polling(client, umgebung, monkeypatch):
    login(client, umgebung)
    did, vid = ui_daten(umgebung)
    url = basis(umgebung) + f"/quellen/{did}/ki-analyse"
    job = AsyncMock(return_value=0)
    monkeypatch.setattr(service, "analysiere_quelle", job)
    assert "KI-Vorschläge erzeugen" not in client.get(basis(umgebung) + "/quellen").text
    response = post(client, url)
    assert response.status_code == 409 and "nicht aktiviert" in response.text
    job.assert_not_awaited()
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one().katplan_ki_import_enabled = True
        db.commit()
    assert "KI-Vorschläge erzeugen" in client.get(basis(umgebung) + "/quellen").text
    response = post(client, url)
    assert response.status_code == 200 and 'hx-trigger="every 3s"' in response.text
    job.assert_awaited_once_with(did)
    assert post(client, url).status_code == 409
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        d = db.get(KatplanDokument, did)
        d.ki_status = "fertig"
        d.status = "neu"
        db.commit()
    assert 'hx-trigger="every 3s"' not in client.get(basis(umgebung) + f"/quellen/{did}/ki-status").text
    assert post(client, url).status_code == 409
    monkeypatch.setattr(ai_service, "is_enabled", lambda: False)
    assert "KI-Vorschläge erzeugen" not in client.get(basis(umgebung) + "/quellen").text


def test_ui_review_aktionen_und_rechte(client, umgebung):
    did, vid = ui_daten(umgebung)
    login(client, umgebung)
    root = basis(umgebung) + "/import"
    page = client.get(root)
    assert page.status_code == 200 and "Neues Kapitel" in page.text and "value=\"Abfluss\"" in page.text
    assert 'form="kp-import-sammel"' in page.text
    assert client.get(root + "?quelle=&status=").status_code == 200
    seite = client.get(basis(umgebung) + f"/quellen/{did}?seite=1")
    assert "KI-Vorschläge zu dieser Seite" in seite.text
    response = post(client, root + f"/{vid}/uebernehmen", typ="info", titel="Bearbeitet",
                    abschnitt_id="neu", neues_kapitel="Kapitel", phase_id="neu", neue_phase="Alarm")
    assert response.status_code == 200 and "Element bearbeiten" in response.text
    assert post(client, root + f"/{vid}/uebernehmen").status_code == 409
    _, vid2 = ui_daten(umgebung)
    assert post(client, root + f"/{vid2}/verwerfen").status_code == 200
    _, vid3 = ui_daten(umgebung)
    assert post(client, root + "/sammel", ids=[vid3]).status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        fremdplan = KatplanPlan(org_id=umgebung["org_id"], titel="Anderer Plan")
        db.add(fremdplan)
        db.flush()
        fremddok = dokument_anlegen(db, fremdplan)
        fremdv = vorschlag_anlegen(db, fremdplan, fremddok)
        db.commit()
        fid = fremdv.id
    assert post(client, root + f"/{fid}/verwerfen").status_code == 404
    assert post(client, root + "/sammel", ids=[fid]).status_code == 404
    for rolle, code in [("readonly", 403), ("fremd", 404)]:
        login(client, umgebung, rolle)
        assert client.get(root).status_code == code
        for suffix in (f"/{vid}/uebernehmen", f"/{vid}/verwerfen", "/sammel"):
            assert post(client, root + suffix, ids=[vid]).status_code == code
        assert post(client, basis(umgebung) + f"/quellen/{did}/ki-analyse").status_code == code


def test_migration_0299_up_down_sqlite():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0299_katplan_ki_import.py"
    spec = importlib.util.spec_from_file_location("katplan_ki_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            tables = {"fire_dept", "user", "org_settings", "katplan_plan", "katplan_dokument", "katplan_dokument_seite"}
            for name in tables:
                conn.execute(sa.text(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)"))
            conn.execute(sa.text("INSERT INTO org_settings (id) VALUES (1)"))
            migration.op = Operations(MigrationContext.configure(conn))
            migration.upgrade()
            assert migration.revision == "0299" and migration.down_revision == "0298"
            inspector = sa.inspect(conn)
            model = KatplanImportVorschlag
            assert {c["name"]: c["nullable"] for c in inspector.get_columns(model.__tablename__)} == {
                c.name: c.nullable for c in model.__table__.columns}
            assert {tuple(i["column_names"]) for i in inspector.get_indexes(model.__tablename__)} == {
                tuple(c.name for c in i.columns) for i in model.__table__.indexes}
            assert conn.scalar(sa.text("SELECT katplan_ki_import_enabled FROM org_settings")) == 0
            fks = {f["constrained_columns"][0]: f["options"].get("ondelete") for f in inspector.get_foreign_keys(model.__tablename__)}
            assert fks == {"org_id": "SET NULL", "plan_id": "CASCADE", "dokument_id": "CASCADE", "seite_id": "SET NULL", "entschieden_von_id": "SET NULL"}
            migration.downgrade()
            assert set(sa.inspect(conn).get_table_names()) == tables
            for name in ("org_settings", "katplan_dokument"):
                assert [c["name"] for c in sa.inspect(conn).get_columns(name)] == ["id"]
    finally:
        engine.dispose()


def test_objekt_nur_explizit_und_mit_recht(db, daten):
    plan, user, dokument = daten
    module_aktivieren(db, plan.org_id)
    objekt = Objekt(org_id=plan.org_id, name="Schule")
    db.add(objekt)
    db.flush()
    v = vorschlag_anlegen(db, plan, dokument, objekt_id=objekt.id)
    e = service.vorschlag_uebernehmen(db, plan, user, v, werte={})
    assert db.query(KatplanLink).filter_by(element_id=e.id).count() == 0
    v2 = vorschlag_anlegen(db, plan, dokument, objekt_id=objekt.id)
    with pytest.raises(ValueError, match="Leserecht"), db.begin_nested():
        service.vorschlag_uebernehmen(db, plan, user, v2, werte={"objekt_verknuepfen": True})
    v3 = vorschlag_anlegen(db, plan, dokument, objekt_id=objekt.id)
    admin = _user("org_admin", plan.org_id)
    e3 = service.vorschlag_uebernehmen(db, plan, admin, v3, werte={"objekt_verknuepfen": True})
    assert db.query(KatplanLink).filter_by(element_id=e3.id).one().ziel_id == objekt.id
    db.query(OrgSettings).filter_by(org_id=plan.org_id).one().objekt_module_enabled = False
    db.flush()
    v4 = vorschlag_anlegen(db, plan, dokument, objekt_id=objekt.id)
    with pytest.raises(ValueError, match="verfügbar"):
        service.vorschlag_uebernehmen(db, plan, admin, v4, werte={"objekt_verknuepfen": True})


def test_prompt_verwaltung(client, umgebung):
    from app.routers.ui_ai_prompts import _VALID_KEYS
    from app.models.master import AIPromptVersion

    assert "katplan_import" in _VALID_KEYS
    login(client, umgebung, "org_admin")
    response = client.get("/admin/ki-prompts")
    assert response.status_code == 200 and "Katastrophenplan-Import" in response.text
    response = post(client, "/admin/ki-prompts/katplan_import", variable_part="Gemeinde-spezifische Anweisung")
    assert response.status_code == 303
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        prompt = db.query(AIPromptVersion).filter_by(org_id=umgebung["org_id"], prompt_key="katplan_import").one()
        assert prompt.variable_part == "Gemeinde-spezifische Anweisung"


def test_seitenfehler_beim_abgleich(db, daten, monkeypatch):
    plan, user, dokument = daten
    db.commit()
    antwort = json.dumps({"elemente": [{"typ": "info", "titel": "Eins"}, {"typ": "info", "titel": "Zwei"}]})
    monkeypatch.setattr(ai_service, "complete", AsyncMock(return_value=antwort))
    aufrufe = 0
    def abgleich(db, v):
        nonlocal aufrufe
        aufrufe += 1
        if aufrufe == 2:
            raise ValueError("Abgleich fehlgeschlagen")
    monkeypatch.setattr(service, "_abgleichen", abgleich)
    assert run_async(service.analysiere_quelle, dokument.id) == 2
    db.expire_all()
    vs = db.query(KatplanImportVorschlag).filter_by(dokument_id=dokument.id).all()
    assert len(vs) == 2 and {v.seiten_nr for v in vs} == {2}
    assert dokument.ki_status == "fertig"


def test_org_checkbox_unabhaengig_vom_objektmodul(client, umgebung):
    login(client, umgebung, "org_admin")
    with TestingSession() as db:
        set_tenant_context(db, None)
        flag = db.query(SystemSettings).filter_by(key="objekt_module_enabled").first()
        if flag:
            flag.value = "false"
        db.commit()
    page = client.get("/admin/settings")
    assert page.status_code == 200 and 'name="katplan_ki_import_raw"' in page.text
    assert post(client, "/admin/settings/org", katplan_ki_import_raw="1").status_code == 303
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        assert db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one().katplan_ki_import_enabled
    assert post(client, "/admin/settings/org").status_code == 303
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        assert not db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one().katplan_ki_import_enabled


def test_beschreibung_als_bearbeitbarer_text():
    from app.services.katplan_ki_service import html_zu_text, text_zu_html

    html = "<p>Engstelle beobachten</p><ul><li>Auf Gasleitung achten</li><li>VKW &amp; Bauhof</li></ul>"
    text = html_zu_text(html)
    assert text == "Engstelle beobachten\n• Auf Gasleitung achten\n• VKW & Bauhof"
    assert text_zu_html(text) == "<p>Engstelle beobachten</p><ul><li>Auf Gasleitung achten</li><li>VKW &amp; Bauhof</li></ul>"
    assert "<script>" not in text_zu_html("<script>alert(1)</script>")
