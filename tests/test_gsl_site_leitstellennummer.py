"""Leitstellennummer im GSL-Detail, einschließlich HTMX und Tenant-Grenzen."""

import re

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.major_incident import IncidentSite
from tests.test_gsl_material_board import _material_lage
from tests.test_gsl_sektoren import _login, _user_lage


def _detail_texte(html):
    title = re.search(r'<h3 class="modal__title"[^>]*>(.*?)</h3>', html, re.S)
    quelle = re.search(r'>Quelle</span>\s*<span[^>]*>(.*?)</span>', html, re.S)
    assert title is not None
    assert quelle is not None
    return tuple(" ".join(re.sub(r"<[^>]+>", "", part).split()) for part in (title[1], quelle[1]))


def _nummer_site(username, nummer, *, fremd=False, verknuepft=True):
    lage_id, org_id, site_id = _material_lage(username)
    if fremd:
        _, org_id = _user_lage(f"{username}_fremd", f"{username}_fremd")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        site = db.get(IncidentSite, site_id)
        site.bezeichnung = "garage ueberflutet"
        site.source = "api"
        site.alarm_stufe = "T9"
        # Selbst ein plausibel aussehender API-Key ist keine gesicherte Nummer.
        site.external_key = "F999999"
        if verknuepft:
            incident = Incident(primary_org_id=org_id, nummer=87654321, lis_operation_number=nummer)
            db.add(incident)
            db.flush()
            site.incident_id = incident.id
        db.commit()
    finally:
        db.close()
    return lage_id, site_id


def test_detail_zeigt_leitstellennummer_auch_nach_htmx_swaps(client, setup_db):
    username = "gsl_lis_detail"
    lage_id, site_id = _nummer_site(username, " F120102 ")
    _login(client, username)
    url = f"/lage/{lage_id}/stellen/{site_id}"
    responses = [
        client.get(url),
        client.post(
            f"{url}/log",
            data={"_csrf": client.cookies.get("ec_csrf"), "art": "lagemeldung", "text": "Wasser sinkt"},
            headers={"HX-Request": "true"},
        ),
        client.post(
            f"{url}/pin",
            data={"_csrf": client.cookies.get("ec_csrf"), "lat": "47.5", "lng": "9.7"},
            headers={"HX-Request": "true"},
        ),
    ]
    for response in responses:
        assert response.status_code == 200
        assert _detail_texte(response.text) == (
            "garage ueberflutet – F120102", "Leitstelle · T9 · F120102",
        )
        assert "87654321" not in response.text
        assert "F999999" not in response.text


@pytest.mark.parametrize("nummer", [None, "", " \t "])
def test_detail_ohne_leitstellennummer_zeigt_keine_internen_nummern(client, setup_db, nummer):
    username = f"gsl_lis_leer_{repr(nummer).encode().hex()}"
    lage_id, site_id = _nummer_site(username, nummer)
    _login(client, username)
    response = client.get(f"/lage/{lage_id}/stellen/{site_id}")
    assert response.status_code == 200
    assert _detail_texte(response.text) == ("garage ueberflutet", "Leitstelle · T9")
    assert "87654321" not in response.text
    assert "F999999" not in response.text


def test_detail_zeigt_keine_leitstellennummer_aus_fremder_org(client, setup_db):
    username = "gsl_lis_fremd"
    lage_id, site_id = _nummer_site(username, "F120102", fremd=True)
    _login(client, username)
    response = client.get(f"/lage/{lage_id}/stellen/{site_id}")
    assert response.status_code == 200
    assert _detail_texte(response.text) == ("garage ueberflutet", "Leitstelle · T9")
    assert "F120102" not in response.text


def test_api_site_ohne_einsatz_nutzt_external_key_nicht_als_nummer(client, setup_db):
    username = "gsl_lis_api_key"
    lage_id, site_id = _nummer_site(username, None, verknuepft=False)
    _login(client, username)
    response = client.get(f"/lage/{lage_id}/stellen/{site_id}")
    assert response.status_code == 200
    assert _detail_texte(response.text) == ("garage ueberflutet", "Leitstelle · T9")
    assert "F999999" not in response.text
