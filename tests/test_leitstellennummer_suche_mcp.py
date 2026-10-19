"""Leitstellennummer-Suche in MCP und allen Board-Karten-Renderpfaden."""

import asyncio
import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.context import MCPContext
from app.mcp.tools.einsatz import einsaetze_liste, einsatz_lesen
from app.mcp.tools.gsl import gsl_ressource_details, gsl_ressourcen_liste
from app.mcp.tools.gsl_lage import gsl_einsatzstelle_lesen, gsl_einsatzstellen_liste, gsl_lagebild
from app.models.incident import Incident
from app.models.major_incident import EinheitSiteDispatch, IncidentSite, LageEinheit, SitePriority
from app.models.user import User
from tests.test_gsl_sektoren import _login
from tests.test_gsl_site_leitstellennummer import _nummer_site


def _context(db, username):
    user = db.query(User).filter(User.username == username).one()
    return MCPContext(db=db, user=user, org_id=user.org_id)


def _search_values(html):
    values = re.findall(r'data-search="([^"]*)"', html)
    assert values
    return " ".join(values)


def test_einsaetze_mcp_sucht_und_liefert_leitstellennummer(setup_db):
    username = "lis_suche_einsatz_mcp"
    _, site_id = _nummer_site(username, "F120102")
    with SessionLocal() as db:
        set_tenant_context(db, None)
        context = _context(db, username)
        incident_id = db.get(IncidentSite, site_id).incident_id
        result = asyncio.run(einsaetze_liste(context, suchtext="f1201"))
        assert [row["id"] for row in result["items"]] == [incident_id]
        assert result["items"][0]["lis_operation_number"] == "F120102"
        detail = asyncio.run(einsatz_lesen(context, incident_id))
        assert detail["lis_operation_number"] == "F120102"


@pytest.mark.parametrize(
    ("nummer", "fremd", "verknuepft", "erwartet"),
    [(" F120102 ", False, True, "F120102"), ("F120102", True, True, None),
     (None, False, True, None), (" \t ", False, True, None), (None, False, False, None)],
)
def test_gsl_mcp_liste_detail_lagebild_und_suche(setup_db, nummer, fremd, verknuepft, erwartet):
    username = f"lis_suche_mcp_{fremd}_{verknuepft}_{repr(nummer).encode().hex()}"
    lage_id, site_id = _nummer_site(username, nummer, fremd=fremd, verknuepft=verknuepft)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        context = _context(db, username)
        result = asyncio.run(gsl_einsatzstellen_liste(context, lage_id))
        assert result["einsatzstellen"][0]["leitstellennummer"] == erwartet
        detail = asyncio.run(gsl_einsatzstelle_lesen(context, lage_id, site_id, ["allgemein"]))
        assert detail["einsatzstelle"]["allgemein"]["leitstellennummer"] == erwartet
        # Das Lagebild enthaelt nur offene Stellen mit Prio 1/2.
        site = db.get(IncidentSite, site_id)
        site.priority = SitePriority(1)
        db.flush()
        bild = asyncio.run(gsl_lagebild(context, lage_id))
        assert bild["lagebild"]["offene_stellen_prio_1_2"][0]["leitstellennummer"] == erwartet
        for term, count in [("f1201", int(erwartet is not None)), ("F999999", 0), ("87654321", 0)]:
            result = asyncio.run(gsl_einsatzstellen_liste(context, lage_id, suchtext=term))
            assert len(result["einsatzstellen"]) == count


def test_gsl_mcp_nummern_werden_per_batch_geladen(setup_db):
    username = "lis_suche_batch"
    lage_id, site_id = _nummer_site(username, "F120102")
    with SessionLocal() as db:
        set_tenant_context(db, None)
        context = _context(db, username)
        for index in range(4):
            incident = Incident(primary_org_id=context.org_id, lis_operation_number=f"F12010{index + 3}")
            db.add(incident)
            db.flush()
            db.add(IncidentSite(org_id=context.org_id, major_incident_id=lage_id,
                                incident_id=incident.id, bezeichnung=f"Batch {index}"))
        db.commit()
        queries = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            if re.search(r"FROM incident\b", statement):
                queries.append(statement)

        event.listen(db.get_bind(), "before_cursor_execute", capture)
        try:
            result = asyncio.run(gsl_einsatzstellen_liste(context, lage_id, suchtext="f1201"))
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", capture)
        assert len(result["einsatzstellen"]) == 5
        assert len(queries) == 1


@pytest.mark.parametrize("fremd", [False, True])
def test_board_karte_phase_und_oob_behalten_suchnummer(client, setup_db, fremd):
    username = f"lis_suche_html_{fremd}"
    lage_id, site_id = _nummer_site(username, " F120102 ", fremd=fremd)
    _login(client, username)
    url = f"/lage/{lage_id}/stellen/{site_id}"
    responses = [client.get(f"/lage/{lage_id}"), client.get(f"{url}/card"),
                 client.get(f"/lage/{lage_id}/phase/eingegangen/inhalt"),
                 client.post(f"{url}/log", data={"_csrf": client.cookies.get("ec_csrf"),
                             "art": "lagemeldung", "text": "Wasser sinkt"},
                             headers={"HX-Request": "true"})]
    for response in responses:
        assert response.status_code == 200
        search = _search_values(response.text)
        assert ("f120102" in search) is (not fremd)
        assert "f999999" not in search
        assert "87654321" not in search
    for suffix in ["", "?partial=1"]:
        response = client.get(f"/lage/{lage_id}/zeitreise{suffix}")
        assert response.status_code == 200
        assert ("F120102" in _search_values(response.text)) is (not fremd)
    response = client.get(f"/lage/{lage_id}/druck")
    assert response.status_code == 200
    assert ('"leitstellennummer": "F120102"' in response.text) is (not fremd)


@pytest.mark.parametrize("fremd", [False, True])
def test_ressourcen_mcp_auftraege_liefern_leitstellennummer(setup_db, fremd):
    username = f"lis_suche_ressource_{fremd}"
    lage_id, site_id = _nummer_site(username, " F120102 ", fremd=fremd)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        context = _context(db, username)
        einheit = LageEinheit(lage_id=lage_id, label="TLF", status="im_einsatz", incident_site_id=site_id)
        db.add(einheit)
        db.flush()
        db.add(EinheitSiteDispatch(
            einheit_id=einheit.id, site_id=site_id, einheit_status="vor_ort",
            dispatched_at=datetime.now(UTC).replace(tzinfo=None),
        ))
        db.commit()
        erwartet = None if fremd else "F120102"
        detail = asyncio.run(gsl_ressource_details(context, lage_id, einheit.id, ["einsaetze"]))
        assert detail["einsaetze"]["aktuell"]["leitstellennummer"] == erwartet
        result = asyncio.run(gsl_ressourcen_liste(context, lage_id))
        assert result["ressourcen"][0]["aktueller_einsatz"]["leitstellennummer"] == erwartet
