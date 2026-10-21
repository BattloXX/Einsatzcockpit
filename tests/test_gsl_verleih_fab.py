"""Mobil erreichbarer GSL-Verleih und dessen kontextbezogener FAB."""

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings
from app.models.major_incident import IncidentSite, Sector, SitePhase
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


def test_verleih_neu_form_liefert_einsatzstellen_mit_kontext_sortiert(client, setup_db):
    lage_id, org_id = _user_lage("verleih_sites", "verleih-sites")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        sector = Sector(major_incident_id=lage_id, name="Nord")
        db.add(sector)
        db.flush()
        db.add_all([
            IncidentSite(major_incident_id=lage_id, org_id=org_id, sector_id=sector.id,
                         bezeichnung="Keller überflutet", strasse="Hauptstraße", hausnr="5", ort="Wolfurt",
                         phase=SitePhase.eingegangen),
            IncidentSite(major_incident_id=lage_id, org_id=org_id,
                         bezeichnung="Keller überflutet", strasse="Bahnhofstraße", hausnr="2", ort="Wolfurt",
                         phase=SitePhase.in_arbeit),
            IncidentSite(major_incident_id=lage_id, org_id=org_id, bezeichnung="A erledigt",
                         phase=SitePhase.erledigt),
        ])
        db.commit()
    finally:
        db.close()

    _login(client, "verleih_sites")
    response = client.get(f"/lage/{lage_id}/verleih/neu")

    assert response.status_code == 200
    assert "Hauptstra" in response.text and "Wolfurt" in response.text
    assert '"phase_label": "Eingegangen"' in response.text
    assert '"sektor": "Nord"' in response.text
    assert response.text.index('"offen": true') < response.text.index('"bezeichnung": "A erledigt"')


def test_verleih_karte_zeigt_einsatzstellenname(client, setup_db):
    from app.core.icons import render_icon

    ort_icon = str(render_icon("map-pin", size="sm"))
    lage_id, org_id = _user_lage("verleih_karte_site", "verleih-karte-site")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        settings = OrgSettings(org_id=org_id, mi_feature_geraeteverleih=True)
        site = IncidentSite(major_incident_id=lage_id, org_id=org_id, bezeichnung="Straße überflutet",
                            strasse="Au", hausnr="3", ort="Wolfurt")
        db.add_all([settings, site])
        db.flush()
        db.add(VerleihAusleihe(lage_id=lage_id, org_id=org_id, site_id=site.id, name="Max Mustermann"))
        db.commit()
    finally:
        db.close()

    _login(client, "verleih_karte_site")
    response = client.get(f"/lage/{lage_id}/verleih")

    assert response.status_code == 200
    assert f"{ort_icon} Straße überflutet" in response.text
    assert f"{ort_icon} Einsatzstelle" not in response.text


def test_schnell_einsatzstelle_liefert_auswahl_kontext(client, setup_db):
    lage_id, _ = _user_lage("verleih_schnell_site", "verleih-schnell-site")
    _login(client, "verleih_schnell_site")
    csrf = client.cookies.get("ec_csrf")

    response = client.post(
        f"/lage/{lage_id}/verleih/schnell-einsatzstelle",
        data={"_csrf": csrf, "bezeichnung": "Keller überflutet", "ort": "Wolfurt"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "id": data["id"], "bezeichnung": "Keller überflutet", "adresse": "Wolfurt",
        "phase": "eingegangen", "phase_label": "Eingegangen", "sektor": "", "offen": True,
    }
