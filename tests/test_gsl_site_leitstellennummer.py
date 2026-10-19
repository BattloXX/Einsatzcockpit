"""Leitstellennummer in GSL-Detail und Druck, einschließlich Tenant-Grenzen."""

import re
from types import SimpleNamespace

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.major_incident import IncidentSite, MajorIncident
from app.services.print_artifact_service import render_map_html
from tests.test_gsl_material_board import _material_lage
from tests.test_gsl_sektoren import _login, _user_lage


def _detail_texte(html):
    title = re.search(r'<h3 class="modal__title"[^>]*>(.*?)</h3>', html, re.S)
    quelle = re.search(r'>Quelle</span>\s*<span[^>]*>(.*?)</span>', html, re.S)
    assert title is not None
    assert quelle is not None
    return tuple(" ".join(re.sub(r"<[^>]+>", "", part).split()) for part in (title[1], quelle[1]))


def _nummer_site(username, nummer, *, fremd=False, verknuepft=True, external_key="F999999"):
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
        site.external_key = external_key
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


@pytest.mark.parametrize("sammeldruck", [False, True], ids=["einzel", "sammel"])
@pytest.mark.parametrize("gateway", [False, True], ids=["ui", "gateway"])
@pytest.mark.parametrize(
    ("nummer", "fremd", "verknuepft", "erwartet"),
    [
        (" F120102 ", False, True, "F120102"),
        (None, False, True, None),
        ("", False, True, None),
        (" \t ", False, True, None),
        (None, False, False, None),
        ("F120102", True, True, None),
    ],
    ids=["lis", "nur-intern", "leer", "whitespace", "nur-api-key", "fremde-org"],
)
def test_druck_nutzt_nur_echte_leitstellennummer(
    client, setup_db, sammeldruck, gateway, nummer, fremd, verknuepft, erwartet,
):
    username = f"gsl_druck_{sammeldruck}_{gateway}_{fremd}_{verknuepft}_{repr(nummer).encode().hex()}"
    external_key = "4eed8f44-5c59-4517-a337-301589a25736"
    lage_id, site_id = _nummer_site(
        username, nummer, fremd=fremd, verknuepft=verknuepft, external_key=external_key,
    )
    if gateway:
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            lage = db.get(MajorIncident, lage_id)
            html = render_map_html(db, SimpleNamespace(
                document_type="stellen_karte" if sammeldruck else "site_karte",
                gsl_id=lage_id, org_id=lage.org_id,
                artifact_ref=f"ids={site_id}" if sammeldruck else str(site_id),
            ))
        finally:
            db.close()
    else:
        _login(client, username)
        url = (
            f"/lage/{lage_id}/druck/stellen?ids={site_id}" if sammeldruck
            else f"/lage/{lage_id}/stellen/{site_id}/druck"
        )
        response = client.get(url)
        assert response.status_code == 200
        html = response.text
    text = " ".join(re.sub(r"<[^>]+>", " ", html).split())
    if erwartet:
        label = "Leitstellen-Nr." if sammeldruck else "Leitstellen-Nr.:"
        assert f"{label} {erwartet}" in text
        assert " F120102 " not in html
    else:
        assert "Leitstellen-Nr." not in html
        assert "F120102" not in html
    assert external_key not in html
    assert "87654321" not in html
