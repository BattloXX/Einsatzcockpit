"""Regressionen für die mobile Erfassung im Stellen-Detail."""

from tests.test_gsl_material_board import _material_lage
from tests.test_gsl_sektoren import _login


def test_stellen_detail_zeigt_erfassung_und_foto_vor_ressourcen(client, setup_db):
    lage_id, _, site_id = _material_lage("site_detail_layout")
    _login(client, "site_detail_layout")

    response = client.get(f"/lage/{lage_id}/stellen/{site_id}")

    assert response.status_code == 200
    html = response.text
    assert html.index("siteLogForm-") < html.index(">RESSOURCEN<")
    assert html.index('/medien"') < html.index(">RESSOURCEN<")
    assert '<textarea name="text"' in html


def test_lagemeldung_post_bleibt_mit_textarea_feld_moeglich(client, setup_db):
    lage_id, _, site_id = _material_lage("site_detail_log_post")
    _login(client, "site_detail_log_post")

    response = client.post(
        f"/lage/{lage_id}/stellen/{site_id}/log",
        data={
            "_csrf": client.cookies.get("ec_csrf"),
            "art": "lagemeldung",
            "text": "Wasserstand sinkt.",
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "Wasserstand sinkt." in response.text
