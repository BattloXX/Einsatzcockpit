"""Abschnitte: Grid-Kontext, Detailzugriff und readonly-Darstellung."""
from datetime import UTC, datetime, timedelta

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import (
    CommLogEntry,
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    MajorIncident,
    MajorIncidentStatus,
    Sector,
    SiteLogEntry,
    SitePhase,
    SitePriority,
    SiteResourceAssignment,
)
from app.models.master import FireDept
from app.models.user import Role, User, UserRole


def _login(client, username):
    client.get("/login")
    return client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")},
        follow_redirects=False,
    )


def _user_lage(username, slug, role_code="incident_leader"):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=slug, name=slug, color="#123456", bos="Feuerwehr")
        db.add(org)
        db.flush()
        user = User(
            username=username, display_name=username, password_hash=hash_password("Test1234!"),
            org_id=org.id, active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter(Role.code == role_code).one().id))
        lage = MajorIncident(org_id=org.id, name="Abschnittslage", status=MajorIncidentStatus.active)
        db.add(lage)
        db.commit()
        return lage.id, org.id
    finally:
        db.close()


def test_sektoren_grid_sortiert_zaehlt_und_zeigt_detaildaten(client, setup_db):
    lage_id, org_id = _user_lage("sektor_leiter", "sektor-leiter")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        sector = Sector(major_incident_id=lage_id, name="Nord")
        db.add(sector)
        db.flush()
        old = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
        done = IncidentSite(major_incident_id=lage_id, org_id=org_id, sector_id=sector.id,
                            bezeichnung="Erledigt", phase=SitePhase.erledigt, created_at=old)
        urgent = IncidentSite(major_incident_id=lage_id, org_id=org_id, sector_id=sector.id,
                              bezeichnung="Sofort zuerst", phase=SitePhase.in_arbeit,
                              priority=SitePriority.sofort, strasse="Hauptstraße", hausnr="7", ort="Musterort",
                              einsatzgrund="Wassereintritt", naechste_lagemeldung_at=old)
        normal = IncidentSite(major_incident_id=lage_id, org_id=org_id, sector_id=sector.id,
                               bezeichnung="Danach", phase=SitePhase.in_arbeit,
                               priority=SitePriority.normal, created_at=old + timedelta(minutes=1))
        db.add_all([done, urgent, normal])
        db.flush()
        einheit = LageEinheit(lage_id=lage_id, label="RLF Nord")
        db.add(einheit)
        db.flush()
        db.add(EinheitSiteDispatch(einheit_id=einheit.id, site_id=urgent.id, dispatched_at=old))
        db.add(SiteResourceAssignment(incident_site_id=urgent.id, resource_type="free_text", label="Pumpe"))
        foreign_org = FireDept(slug="sektor-fremd-org", name="sektor-fremd-org", color="#654321", bos="Feuerwehr")
        db.add(foreign_org)
        db.flush()
        foreign = IncidentSite(major_incident_id=lage_id, org_id=foreign_org.id, sector_id=sector.id,
                               bezeichnung="Fremde Organisation", phase=SitePhase.eingegangen)
        db.add(foreign)
        db.commit()
        urgent_id = urgent.id
    finally:
        db.close()

    _login(client, "sektor_leiter")
    response = client.get(f"/lage/{lage_id}/sektoren")
    assert response.status_code == 200
    assert "0 neu · 2 in Arbeit · 1 erledigt" in response.text
    assert response.text.index("Sofort zuerst") < response.text.index("Danach")
    assert "1 erledigte anzeigen" in response.text
    assert "Hauptstraße 7, Musterort · Wassereintritt" in response.text
    assert "Lagemeldung überfällig" in response.text
    assert "RLF Nord" in response.text and "Pumpe" in response.text
    assert "Fremde Organisation" not in response.text
    assert f'hx-get="/lage/{lage_id}/stellen/{urgent_id}"' in response.text
    assert f'href="/lage/{lage_id}?openSite={urgent_id}"' in response.text


def test_sektoren_unzugeordnet_steht_vor_abschnitten(client, setup_db):
    lage_id, org_id = _user_lage("sektor_inbox", "sektor-inbox")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        sector = Sector(major_incident_id=lage_id, name="West")
        db.add(sector)
        db.flush()
        db.add_all([
            IncidentSite(major_incident_id=lage_id, org_id=org_id, bezeichnung="Ohne Abschnitt"),
            IncidentSite(major_incident_id=lage_id, org_id=org_id, sector_id=sector.id, bezeichnung="West-Stelle"),
        ])
        db.commit()
    finally:
        db.close()
    _login(client, "sektor_inbox")
    response = client.get(f"/lage/{lage_id}/sektoren")
    assert response.text.index("Nicht zugeordnet") < response.text.index("West-Stelle")


def test_zeitreise_neueste_zuerst_und_partial_mit_site_sektor(client, setup_db):
    lage_id, org_id = _user_lage("zeitreise_leiter", "zeitreise-leiter")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        sector = Sector(major_incident_id=lage_id, name="Nord", color="#123456")
        db.add(sector)
        db.flush()
        site = IncidentSite(major_incident_id=lage_id, org_id=org_id, sector_id=sector.id, bezeichnung="Schule")
        db.add(site)
        db.flush()
        now = datetime.now(UTC).replace(tzinfo=None)
        db.add_all([
            SiteLogEntry(incident_site_id=site.id, ts=now - timedelta(minutes=5), kind="note", text="Zeitreise-Eintrag-Alt"),
            CommLogEntry(
                major_incident_id=lage_id, related_site_id=site.id, ts=now,
                direction="in", message="Zeitreise-Eintrag-Neu", is_request=True,
            ),
        ])
        db.commit()
    finally:
        db.close()
    _login(client, "zeitreise_leiter")
    page = client.get(f"/lage/{lage_id}/zeitreise")
    assert page.status_code == 200
    assert page.text.index("Zeitreise-Eintrag-Neu") < page.text.index("Zeitreise-Eintrag-Alt")
    assert "Offene Anfrage" in page.text
    partial = client.get(f"/lage/{lage_id}/zeitreise?partial=1")
    assert partial.status_code == 200
    assert 'id="zt-timeline"' in partial.text
    assert 'data-site="' in partial.text and 'data-sector="' in partial.text
    assert "zt-wrap" not in partial.text


def test_disponieren_optionen_nach_abschnitt_in_beiden_renderpfaden(client, setup_db):
    lage_id, org_id = _user_lage("dispatch_abschnitte", "dispatch-abschnitte")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        own = Sector(major_incident_id=lage_id, name="Nord")
        other = Sector(major_incident_id=lage_id, name="Süd")
        db.add_all([own, other])
        db.flush()
        site = IncidentSite(major_incident_id=lage_id, org_id=org_id, sector_id=own.id, bezeichnung="Nordstelle")
        db.add(site)
        db.flush()
        db.add_all([
            LageEinheit(lage_id=lage_id, label="Eigene Einheit", sector_id=own.id),
            LageEinheit(lage_id=lage_id, label="Pool Einheit"),
            LageEinheit(lage_id=lage_id, label="Süd Einheit", sector_id=other.id),
        ])
        db.commit()
        site_id = site.id
    finally:
        db.close()
    _login(client, "dispatch_abschnitte")
    for path in (f"/lage/{lage_id}/stellen/{site_id}", f"/lage/{lage_id}/stellen/{site_id}/disponieren-select"):
        html = client.get(path).text
        assert html.index("Eigener Abschnitt: Nord") < html.index("Ohne Abschnitt") < html.index("Abschnitt Süd")
        assert "Eigene Einheit · Nord" in html
        assert "Süd Einheit · Süd" in html


def test_sektoren_readonly_sieht_details_ohne_bearbeiten_und_cross_org_ist_gesperrt(client, setup_db):
    lage_id, org_id = _user_lage("sektor_readonly", "sektor-readonly", "readonly")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        site = IncidentSite(major_incident_id=lage_id, org_id=org_id, bezeichnung="Sichtbar")
        db.add(site)
        db.commit()
        site_id = site.id
    finally:
        db.close()
    _login(client, "sektor_readonly")
    grid = client.get(f"/lage/{lage_id}/sektoren")
    assert grid.status_code == 200
    assert "Sichtbar" in grid.text
    assert "Abschnitt bearbeiten" not in grid.text
    assert client.get(f"/lage/{lage_id}/stellen/{site_id}").status_code == 200

    other_lage, _ = _user_lage("sektor_fremd", "sektor-fremd")
    assert client.get(f"/lage/{other_lage}/stellen/{site_id}").status_code in {403, 404}


def test_lage_fab_oeffnet_stellen_dialog_und_ist_readonly_nicht_sichtbar(client, setup_db):
    lage_id, _ = _user_lage("fab_leiter", "fab-leiter")
    _login(client, "fab_leiter")

    response = client.get(f"/lage/{lage_id}/sektoren")
    assert response.status_code == 200
    assert 'aria-label="Einsatzstelle in dieser Lage anlegen"' in response.text
    assert 'data-ec-dialog="neuStelleDialog"' in response.text
    assert f'hx-post="/lage/{lage_id}/stellen/neu"' in response.text
    assert 'name="_csrf"' in response.text

    readonly_lage_id, _ = _user_lage("fab_readonly", "fab-readonly", "readonly")
    client.get("/logout")
    _login(client, "fab_readonly")
    readonly = client.get(f"/lage/{readonly_lage_id}/sektoren")
    assert readonly.status_code == 200
    assert "bottom-nav__fab" not in readonly.text
    assert "neuStelleDialog" not in readonly.text


def test_stellen_dialog_post_ist_per_csrf_und_org_geschuetzt(client, setup_db):
    lage_id, _ = _user_lage("fab_post", "fab-post")
    _login(client, "fab_post")
    csrf = client.cookies.get("ec_csrf")
    created = client.post(
        f"/lage/{lage_id}/stellen/neu",
        data={"_csrf": csrf, "bezeichnung": "Aus dem gemeinsamen Dialog"},
        headers={"HX-Request": "true"},
    )
    assert created.status_code == 204

    foreign_lage_id, _ = _user_lage("fab_fremd", "fab-fremd")
    forbidden = client.post(
        f"/lage/{foreign_lage_id}/stellen/neu",
        data={"_csrf": csrf, "bezeichnung": "Fremde Stelle"},
        headers={"HX-Request": "true"},
    )
    assert forbidden.status_code == 404
