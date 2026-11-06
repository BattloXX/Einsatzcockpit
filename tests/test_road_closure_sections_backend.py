"""Backend-Leser und MCP für Sperren mit mehreren Straßenabschnitten."""

import json
from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.road_closure import RoadClosure
from app.services import road_closure_service as service
from app.services.road_closure_document_service import entwurf_aus_text
from app.services.road_closure_notify_service import build_road_closure_card
from app.services.road_closure_public_service import public_closure_dict
from app.services.road_closure_section_resolver import SectionResult
from tests.test_mcp_objekte import _token
from tests.test_mcp_strassensperren import _bereit, _rufe
from tests.test_strassensperren_ui import _login, _setup_user

LINE = {"type": "LineString", "coordinates": [[9.74, 47.47], [9.75, 47.48]]}


@pytest.fixture
def db(setup_db):
    session = SessionLocal()
    set_tenant_context(session, None)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def org(db):
    row = FireDept(slug=f"sections-backend-{uuid4().hex}", name="Backend-Abschnitte")
    db.add(row)
    db.flush()
    return row


def _sections(count=2):
    unique = uuid4().hex
    return [{"street": f"Backend-{unique}-{index}", "from_text": f"Anfang-{index}",
             "to_text": f"Ende-{index}", "direction": "both"} for index in range(count)]


def _create(db, org_id, sections):
    return service.create_closure(db, org_id, None, {
        "title": "Backend-Abschnitte", "valid_from": datetime(2026, 11, 6),
        "restriction_type": "closed", "sections": sections,
    })


def test_find_related_and_duplicates_match_second_section(db, org):
    sections = _sections()
    closure = _create(db, org.id, sections)
    values = dict(street=sections[1]["street"], from_text=sections[1]["from_text"],
                  to_text=sections[1]["to_text"], valid_from=closure.valid_from, valid_until=None)
    related = service.find_related(db, org.id, reference_number=None, geometry=None, **values)
    assert [item.closure.id for item in related] == [closure.id]
    assert service.find_duplicates(db, org.id, **values) == [closure]
    # Auch auf der Eingabeseite darf die passende Straße erst im zweiten Abschnitt stehen.
    values.update(street=None, from_text=None, to_text=None,
                  sections=[{"street": f"Andere-{uuid4().hex}"}, sections[1]])
    assert service.find_duplicates(db, org.id, **values) == [closure]


def test_public_short_text_and_teams_lines(db, org):
    sections = _sections()
    closure = _create(db, org.id, sections)
    data = public_closure_dict(closure, org)
    lines = [f"{item['street']}: {item['from_text']} – {item['to_text']}" for item in sections]
    assert data["abschnitt"] == "; ".join(lines)
    assert data["abschnitte"] == sections
    card = build_road_closure_card(data, ereignis="neu", detail_url=None, intern_url="/", map_url=None)
    facts = card["attachments"][0]["content"]["body"][3]["facts"]
    assert next(item["value"] for item in facts if item["title"] == "Abschnitt") == "\n\n".join(lines)


def test_detail_renders_numbered_sections_and_json(client, db):
    user = _setup_user("objekt_verwalter")
    sections = _sections()
    sections[1]["geometry_geojson"] = LINE
    closure = _create(db, user.org_id, sections)
    db.commit()
    _login(client, user)
    response = client.get(f"/strassensperren/{closure.id}")
    assert response.status_code == 200
    assert '<ol class="sperren-abschnitte">' in response.text
    for item in sections:
        assert item["street"] in response.text
        assert item["from_text"] in response.text
        assert item["to_text"] in response.text
    assert "Richtung: Beide Richtungen" in response.text
    assert "Geometrie:" in response.text
    payload = client.get(f"/strassensperren/{closure.id}/geometrie.json").json()
    assert payload["properties"]["abschnitte"][1]["street"] == sections[1]["street"]


def test_pdf_text_draft_has_five_sections():
    streets = ["Dammstraße", "Feldweg", "Inselstraße", "Röhleweg", "Sportplatzstraße"]
    draft = entwurf_aus_text("Verordnung zur Straßensperre 09.11.–18.12.2026: " + ", ".join(streets))
    assert draft["abschnitte"] == [{"street": street, "from_text": None, "to_text": None} for street in streets]
    assert draft["title_vorschlag"].endswith("Dammstraße, Feldweg +3")
    assert entwurf_aus_text("Straßensperre: Dammstraße, Feldweg")["title_vorschlag"].endswith("Dammstraße, Feldweg")


def _mcp_create(client, count=5):
    seed = _bereit(f"sections-mcp-{uuid4().hex}", {"obj": "objekt_verwalter"})
    token = _token(client, seed, "obj")
    sections = [{"street": item["street"], "von": item["from_text"], "bis": item["to_text"],
                 "richtung": "beide"} for item in _sections(count)]
    result = _rufe(client, token, "strassensperre_anlegen", title="Mehrere Straßen",
                   valid_from="2026-11-06T10:00:00", restriction_type="closed", abschnitte=sections)
    assert result.get("status") == "created", result
    return seed, token, result["strassensperre"]["id"], sections


def test_mcp_create_read_list_and_update_five_sections(client, db):
    seed, token, closure_id, sections = _mcp_create(client)
    assert db.query(RoadClosure).filter_by(org_id=seed["org_id"]).count() == 1
    closure = db.get(RoadClosure, closure_id)
    assert len(closure.sections) == 5
    result = _rufe(client, token, "strassensperre_lesen", road_closure_id=closure_id)
    assert len(result["abschnitte"]) == 5
    assert result["abschnitte"][2]["street"] == sections[2]["street"]
    assert result["abschnitte"][2]["richtung"] == "both"
    assert result["abschnitte"][2]["position"] == 2
    assert result["abschnitte"][2]["hat_geometrie"] is False
    listed = _rufe(client, token, "strassensperren_liste", status="all")
    assert len(listed["items"]) == 1
    assert listed["items"][0]["abschnitte"] == result["abschnitte"]
    sections[4]["von"] = "Neuer Anfang"
    updated = _rufe(client, token, "strassensperre_aktualisieren", road_closure_id=closure_id,
                    felder={}, abschnitte=sections)
    assert updated["strassensperre"]["abschnitte"][4]["von"] == "Neuer Anfang"


def test_mcp_resolve_and_confirm_only_index_two(client, db, monkeypatch):
    from app.services import road_closure_section_service
    seed, token, closure_id, sections = _mcp_create(client)
    resolver = AsyncMock(return_value=SectionResult(
        geometry=LINE, quality="mittel", geometry_status="needs_review", osm_name=sections[2]["street"],
        length_m=100, endpoints=[], mehrdeutigkeiten=[], hinweise=[],
    ))
    monkeypatch.setattr(road_closure_section_service, "resolve_section", resolver)
    before = [service._section_data(section) for section in db.get(RoadClosure, closure_id).sections]
    result = _rufe(client, token, "strassensperre_geometrie_ermitteln", road_closure_id=closure_id,
                   abschnitt_index=2, uebernehmen=True)
    assert "__fehler__" not in result, result
    assert resolver.call_args.args[2:5] == (sections[2]["street"], sections[2]["von"], sections[2]["bis"])
    db.expire_all()
    closure = db.get(RoadClosure, closure_id)
    assert json.loads(closure.sections[2].geometry_geojson) == LINE
    assert closure.sections[2].geometry_status == "needs_review"
    assert closure.sections[2].geometry_quality == "mittel"
    assert closure.sections[2].geometry_meta_json
    for index in (0, 1, 3, 4):
        assert service._section_data(closure.sections[index]) == before[index]
    result = _rufe(client, token, "strassensperre_geometrie_bestaetigen", road_closure_id=closure_id,
                   abschnitt_index=2)
    assert result["strassensperre"]["abschnitte"][2]["geometry_status"] == "ok"
    replacement = {"type": "Point", "coordinates": [9.76, 47.48]}
    result = _rufe(client, token, "strassensperre_geometrie_bestaetigen", road_closure_id=closure_id,
                   abschnitt_index=1, geometry_geojson=replacement)
    assert result["strassensperre"]["abschnitte"][1]["geometry_quality"] == "manuell"
    db.expire_all()
    closure = db.get(RoadClosure, closure_id)
    assert json.loads(closure.sections[1].geometry_geojson) == replacement
    assert closure.sections[2].geometry_status == "ok"
    for index in (0, 3, 4):
        assert service._section_data(closure.sections[index]) == before[index]


def test_mcp_takeover_without_index_or_invalid_index_fails(client, db, monkeypatch):
    from app.services import road_closure_section_service
    _, token, closure_id, _ = _mcp_create(client, count=2)
    resolver = AsyncMock()
    monkeypatch.setattr(road_closure_section_service, "resolve_section", resolver)
    result = _rufe(client, token, "strassensperre_geometrie_ermitteln", road_closure_id=closure_id,
                   uebernehmen=True)
    assert "abschnitt_index" in result["__fehler__"]
    for index in (-1, 2):
        for tool in ("strassensperre_geometrie_ermitteln", "strassensperre_geometrie_bestaetigen"):
            result = _rufe(client, token, tool, road_closure_id=closure_id, abschnitt_index=index)
            assert "abschnitt_index" in result["__fehler__"]
    resolver.assert_not_called()
    assert all(not section.geometry_geojson for section in db.get(RoadClosure, closure_id).sections)


def test_live_search_includes_section_street_and_endpoints_without_n_plus_one(db):
    from sqlalchemy import event

    from app.models.user import User
    from app.services.suche.live import suche_sperren
    from app.services.suche.vertrag import SuchAnfrage

    user = _setup_user("objekt_verwalter")
    sections = _sections(5)
    sections[4]["from_text"] = f"Suchanfang-{uuid4().hex}"
    sections[4]["to_text"] = f"Suchende-{uuid4().hex}"
    closure_id = _create(db, user.org_id, sections).id
    _create(db, user.org_id, _sections())
    db.flush()
    db.expire_all()
    user = db.get(User, user.id)
    section_queries = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if "FROM road_closure_section" in statement:
            section_queries.append(statement)

    event.listen(db.bind, "before_cursor_execute", record)
    try:
        for field in ("street", "from_text", "to_text"):
            section_queries.clear()
            db.expire_all()
            term = sections[4][field].lower()
            results = suche_sperren(db, user, SuchAnfrage(q=term, inkl_erledigt=True), [term])
            assert [item.quell_id for item in results] == [str(closure_id)]
            assert len(section_queries) == 1
    finally:
        event.remove(db.bind, "before_cursor_execute", record)


def test_mcp_pdf_draft_passes_sections_through(client, monkeypatch):
    import base64

    from app.mcp.tools import strassensperren
    from app.services import road_closure_section_service

    seed = _bereit(f"sections-pdf-{uuid4().hex}", {"obj": "objekt_verwalter"})
    token = _token(client, seed, "obj")
    draft = entwurf_aus_text("Straßensperre: Dammstraße, Feldweg, Inselstraße, Röhleweg, Sportplatzstraße")
    monkeypatch.setattr(strassensperren, "_document_sync", lambda *args: {"entwurf": draft})
    monkeypatch.setattr(road_closure_section_service, "resolve_section", AsyncMock(side_effect=ValueError("Offline")))
    result = _rufe(client, token, "strassensperre_entwurf_aus_pdf",
                   inhalt_base64=base64.b64encode(b"%PDF-test").decode())
    assert result["abschnitte"] == draft["abschnitte"]
    assert len(result["abschnitte"]) == 5
