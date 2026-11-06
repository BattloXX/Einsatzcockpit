"""Abschnittslisten im Sperrenformular, einschließlich alter Clients."""

import json
import re
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.road_closure import RoadClosure
from app.routers.ui_road_closure import _initial_sections
from tests.test_strassensperren_ui import _login, _payload, _setup_user

LINE = {"type": "LineString", "coordinates": [[9.74, 47.46], [9.75, 47.47]]}


@pytest.fixture()
def manager(client):
    user = _setup_user("objekt_verwalter")
    _login(client, user)
    return client


def _sections(count):
    return [{"street": f"Abschnittweg {uuid4().hex}", "from_text": "Nr. 1", "to_text": "Nr. 5"}
            for _ in range(count)]


def _post(client, sections, **extra):
    title = f"Abschnittsformular {uuid4().hex}"
    return client.post("/strassensperren/neu", data=_payload(
        client, title=title, street="", sections_json=json.dumps(sections), **extra,
    ), follow_redirects=False), title


def _read(closure_id):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        row = db.get(RoadClosure, closure_id)
        return row.street, [(item.street, item.geometry_status, item.geometry_geojson) for item in row.sections], row.version


def test_anlegen_formular_abschnittsliste_csrf(manager):
    page = manager.get("/strassensperren/neu")
    assert page.status_code == 200
    assert 'name="sections_json"' in page.text
    assert "+ Weitere Straße" in page.text
    assert 'name="_csrf"' in page.text
    for field in ("street", "from_text", "to_text", "direction", "geometry_geojson",
                  "geometry_checked", "geometry_quality", "geometry_meta_json"):
        assert f'name="{field}"' not in page.text


def test_anlegen_zwei_abschnitte_mit_gepruefter_geometrie(manager):
    sections = _sections(2)
    sections[0].update(geometry_geojson=json.dumps(LINE), geometry_checked=True, geometry_quality="hoch")
    response, title = _post(manager, sections)
    assert response.status_code == 303, response.text
    closure_id = int(response.headers["location"].split("/")[-1])
    street, saved, _ = _read(closure_id)
    assert [item[0] for item in saved] == [item["street"] for item in sections]
    assert all(item["street"] in street for item in sections)
    assert saved[0][1] == "ok" and json.loads(saved[0][2]) == LINE
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.query(RoadClosure).filter_by(title=title).count() == 1


def test_bearbeiten_entfernt_abschnitt_und_belegt_vor(manager):
    sections = _sections(3)
    response, _ = _post(manager, sections)
    assert response.status_code == 303
    url = response.headers["location"]
    closure_id = int(url.split("/")[-1])
    page = manager.get(url + "/bearbeiten")
    assert page.status_code == 200
    assert all(item["street"] in page.text for item in sections)
    response = manager.post(url + "/bearbeiten", data=_payload(
        manager, street="", sections_json=json.dumps(sections[:2]), version=str(_read(closure_id)[2]),
    ), follow_redirects=False)
    assert response.status_code == 303, response.text
    street, saved, _ = _read(closure_id)
    assert [item[0] for item in saved] == [item["street"] for item in sections[:2]]
    assert sections[2]["street"] not in street


@pytest.mark.parametrize("sections", [[], [{}], [{"street": "   ", "from_text": "Nr. 4"}]])
def test_anlegen_ohne_gueltigen_abschnitt(manager, sections):
    response, title = _post(manager, sections)
    assert response.status_code == 422
    assert "Mindestens ein Abschnitt" in response.text
    assert title in response.text
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.query(RoadClosure).filter_by(title=title).count() == 0


@pytest.mark.parametrize("raw", ["kein JSON", "{}", '["ungueltig"]', ""])
def test_ungueltiges_sections_json_wird_im_formular_gemeldet(manager, raw):
    response = manager.post("/strassensperren/neu", data=_payload(manager, sections_json=raw), follow_redirects=False)
    assert response.status_code == 422
    assert "Ungültige Abschnittsliste" in response.text


def test_alte_einzelfelder_funktionieren_weiter(manager):
    street = f"Altformularweg {uuid4().hex}"
    response = manager.post("/strassensperren/neu", data=_payload(manager, street=street), follow_redirects=False)
    assert response.status_code == 303
    saved_street, sections, _ = _read(int(response.headers["location"].split("/")[-1]))
    assert saved_street == street and len(sections) == 1


def test_pdf_abschnitte_und_fehler_eingaben_haben_vorrang():
    sections = _sections(2)
    assert [item["street"] for item in _initial_sections({"abschnitte": sections}, None)] == [
        item["street"] for item in sections]
    assert _initial_sections({"sections": sections}, None)[1]["street"] == sections[1]["street"]
    assert _initial_sections({"sections_json": json.dumps(sections)}, None)[0]["street"] == sections[0]["street"]


def test_template_ohne_smart_quotes():
    template = Path("app/templates/road_closure/edit.html").read_text()
    assert not re.search(r"<[^>]*[“”„‘’][^>]*>", template)


def test_verwandt_pruefung_beruecksichtigt_zweiten_abschnitt(manager):
    existing = _sections(1)
    response, _ = _post(manager, existing)
    assert response.status_code == 303
    sections = _sections(1) + existing
    response, _ = _post(manager, sections)
    assert response.status_code == 409
    assert "Mögliche bestehende Sperre" in response.text
    assert all(item["street"] in response.text for item in sections)


def test_bearbeiten_ohne_abschnitt_belaesst_gespeicherte_daten(manager):
    sections = _sections(2)
    response, _ = _post(manager, sections)
    url = response.headers["location"]
    closure_id = int(url.split("/")[-1])
    response = manager.post(url + "/bearbeiten", data=_payload(
        manager, sections_json="[]", version=str(_read(closure_id)[2]),
    ), follow_redirects=False)
    assert response.status_code == 422
    assert "Mindestens ein Abschnitt" in response.text
    assert len(_read(closure_id)[1]) == 2
