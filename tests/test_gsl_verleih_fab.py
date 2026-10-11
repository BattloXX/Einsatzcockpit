"""Mobil erreichbarer GSL-Verleih und dessen kontextbezogener FAB."""

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings
from app.models.verleih import VerleihAusleihe
from tests.test_gsl_sektoren import _login, _user_lage


def test_verleih_hat_mobilen_anlegen_button_und_verleih_fab(client, setup_db):
    lage_id, org_id = _user_lage("verleih_fab", "verleih-fab")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        settings = db.query(OrgSettings).filter_by(org_id=org_id).first()
        if settings is None:
            settings = OrgSettings(org_id=org_id)
            db.add(settings)
        settings.mi_feature_geraeteverleih = True
        db.commit()
    finally:
        db.close()

    _login(client, "verleih_fab")
    response = client.get(f"/lage/{lage_id}/verleih")

    assert response.status_code == 200
    assert 'class="lage-mobile-actions"' in response.text
    assert '+ Ausleihe' in response.text
    assert 'data-verleih-neu' in response.text
    assert 'aria-label="Neue Ausleihe anlegen"' in response.text
    assert f'hx-get="/lage/{lage_id}/verleih/neu"' in response.text
    assert "verleih-detail-panel" in response.text


def test_verleih_anlegen_und_zurueckgeben_bleibt_htmx_partial(client, setup_db):
    lage_id, _ = _user_lage("verleih_htmx", "verleih-htmx")
    _login(client, "verleih_htmx")
    csrf = client.cookies.get("ec_csrf")

    created = client.post(
        f"/lage/{lage_id}/verleih/neu",
        data={
            "_csrf": csrf,
            "name": "Max Mustermann",
            "pos_bezeichnung[]": "C-Schlauch",
            "pos_artikel_nr[]": "C-42",
            "pos_menge[]": "2",
            "pos_artikel_id[]": "",
        },
        headers={"HX-Request": "true"},
    )
    assert created.status_code == 200
    assert "ausleihe-card" in created.text
    assert "verleihChanged" in created.headers["HX-Trigger"]

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        ausleihe = db.query(VerleihAusleihe).filter_by(lage_id=lage_id).one()
        ausleihe_id = ausleihe.id
    finally:
        db.close()

    returned = client.post(
        f"/lage/{lage_id}/verleih/{ausleihe_id}/alle-zurueck",
        data={"_csrf": csrf},
        headers={"HX-Request": "true"},
    )
    assert returned.status_code == 200
    assert "Zurückgegeben" in returned.text
    assert "verleihKarteAktualisieren" in returned.headers["HX-Trigger"]
