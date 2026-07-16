"""Regressionstests: GSL-Reload-Audit (Session 2026-07-16).

Vormals loesten mehrere WS-Events und Formulare im GSL-Modul (Grossschadenslage)
einen kompletten location.reload() bzw. einen klassischen Browser-Redirect aus,
statt nur das betroffene Element per HTMX nachzuladen (Verstoss gegen die
CLAUDE.md-Pflicht "Sofortige Darstellung nach Eingabe"). Diese Tests pruefen
die neuen HTMX-Pfade (erkannt an HX-Request: true):
- Fragment-Endpoints (Phasen-Spalte, Kopfzeile) liefern 200 + erwarteten Inhalt.
- Zuvor volltreload-ausloesende Mutationen antworten per HTMX mit 204/Partial
  statt mit einem 303-Redirect auf die volle Seite (der Redirect bleibt fuer
  Nicht-HTMX-Aufrufer als Fallback erhalten).
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import (
    CitizenReport,
    IncidentSite,
    LageEinheit,
    MajorIncident,
    MajorIncidentStatus,
    Sector,
    SitePhase,
)
from app.models.master import FireDept
from app.models.user import Role, User, UserRole


def _login(client, username: str, password: str):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={"username": username, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )


def _make_user_with_lage(username: str, org_slug: str, rolle: str = "incident_leader") -> int:
    """Legt Org + User (aktive Rolle) + eine aktive Lage an, gibt lage_id zurueck."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=org_slug, name=org_slug, color="#ff0000", bos="Feuerwehr")
        db.add(org)
        db.flush()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name=username, org_id=org.id, active=True)
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == rolle).first()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        lage = MajorIncident(org_id=org.id, name="Testlage", status=MajorIncidentStatus.active)
        db.add(lage)
        db.commit()
        return lage.id
    finally:
        db.close()


def _add_site(lage_id: int, org_id: int, *, phase: SitePhase = SitePhase.eingegangen,
              bezeichnung: str = "Teststelle") -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        site = IncidentSite(major_incident_id=lage_id, org_id=org_id,
                            bezeichnung=bezeichnung, phase=phase)
        db.add(site)
        db.commit()
        return site.id
    finally:
        db.close()


def _org_id_for_lage(lage_id: int) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.get(MajorIncident, lage_id).org_id
    finally:
        db.close()


HX = {"HX-Request": "true"}


# ── site_create: HTMX 204 statt Redirect, Karte per WS-getriggertem Swap ─────

def test_site_create_htmx_gibt_204_ohne_redirect(client, setup_db):
    lage_id = _make_user_with_lage("gsl_site_htmx", "gsl-site-htmx")
    _login(client, "gsl_site_htmx", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(f"/lage/{lage_id}/stellen/neu",
                    data={"_csrf": csrf, "bezeichnung": "Neue Stelle"},
                    headers=HX, follow_redirects=False)
    assert r.status_code == 204

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = db.get(MajorIncident, lage_id)
        assert any(s.bezeichnung == "Neue Stelle" for s in lage.sites)
    finally:
        db.close()


def test_site_create_ohne_htmx_redirectet_weiterhin(client, setup_db):
    """Backward-Compat: ein klassischer Browser-Formular-Submit (kein HX-Request-
    Header) bekommt weiterhin den 303-Redirect."""
    lage_id = _make_user_with_lage("gsl_site_plain", "gsl-site-plain")
    _login(client, "gsl_site_plain", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(f"/lage/{lage_id}/stellen/neu",
                    data={"_csrf": csrf, "bezeichnung": "Plain Stelle"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/lage/{lage_id}"


# ── Phasen-Spalten-Fragment (Refresh-Ziel fuer sitePhaseChanged) ─────────────

def test_phase_column_partial_zeigt_sites_dieser_phase(client, setup_db):
    lage_id = _make_user_with_lage("gsl_phase_col", "gsl-phase-col")
    org_id = _org_id_for_lage(lage_id)
    _add_site(lage_id, org_id, phase=SitePhase.in_arbeit, bezeichnung="In Arbeit Stelle")
    _add_site(lage_id, org_id, phase=SitePhase.erledigt, bezeichnung="Erledigte Stelle")

    _login(client, "gsl_phase_col", "Test1234!")
    r = client.get(f"/lage/{lage_id}/phase/in_arbeit/inhalt")
    assert r.status_code == 200
    assert "In Arbeit Stelle" in r.text
    assert "Erledigte Stelle" not in r.text


def test_phase_column_partial_unbekannte_phase_404(client, setup_db):
    lage_id = _make_user_with_lage("gsl_phase_404", "gsl-phase-404")
    _login(client, "gsl_phase_404", "Test1234!")
    r = client.get(f"/lage/{lage_id}/phase/quatsch/inhalt")
    assert r.status_code == 404


# ── Kopfzeilen-OOB-Fragment (lage_updated) ───────────────────────────────────

def test_lage_kopf_oob_zeigt_name_und_status(client, setup_db):
    lage_id = _make_user_with_lage("gsl_kopf", "gsl-kopf")
    _login(client, "gsl_kopf", "Test1234!")
    r = client.get(f"/lage/{lage_id}/kopf")
    assert r.status_code == 200
    assert "Testlage" in r.text
    assert 'hx-swap-oob="true"' in r.text
    assert 'id="lageCtxBarInfo"' in r.text


# ── Sektor-CRUD: HTMX liefert das aktualisierte Grid-Partial ────────────────

def test_sektor_create_htmx_liefert_grid_partial(client, setup_db):
    lage_id = _make_user_with_lage("gsl_sektor_create", "gsl-sektor-create")
    _login(client, "gsl_sektor_create", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(f"/lage/{lage_id}/sektoren",
                    data={"_csrf": csrf, "name": "Abschnitt Nord"},
                    headers=HX, follow_redirects=False)
    assert r.status_code == 200
    assert "Abschnitt Nord" in r.text
    # Kein Redirect/volle Seite -- nur das Grid-Partial (kein <html>-Grundgeruest)
    assert "<html" not in r.text.lower()


def test_sektor_delete_htmx_entfernt_sektor(client, setup_db):
    lage_id = _make_user_with_lage("gsl_sektor_del", "gsl-sektor-del")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        sector = Sector(major_incident_id=lage_id, name="Weg damit")
        db.add(sector)
        db.commit()
        sector_id = sector.id
    finally:
        db.close()

    _login(client, "gsl_sektor_del", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/lage/{lage_id}/sektoren/{sector_id}/loeschen",
                    data={"_csrf": csrf}, headers=HX, follow_redirects=False)
    assert r.status_code == 200
    assert "Weg damit" not in r.text

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(Sector, sector_id) is None
    finally:
        db.close()


# ── Einheiten-CRUD (Kraefteuebersicht): HTMX 204 statt Redirect ─────────────

def test_einheit_sektor_zuweisen_htmx_204(client, setup_db):
    lage_id = _make_user_with_lage("gsl_einh_sektor", "gsl-einh-sektor")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        sector = Sector(major_incident_id=lage_id, name="Abschnitt A")
        db.add(sector)
        einheit = LageEinheit(lage_id=lage_id, label="RLF WOL")
        db.add(einheit)
        db.commit()
        sector_id, einheit_id = sector.id, einheit.id
    finally:
        db.close()

    _login(client, "gsl_einh_sektor", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/lage/{lage_id}/einheiten/{einheit_id}/sektor",
                    data={"_csrf": csrf, "sector_id": sector_id},
                    headers=HX, follow_redirects=False)
    assert r.status_code == 204

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(LageEinheit, einheit_id).sector_id == sector_id
    finally:
        db.close()


def test_einheit_loeschen_htmx_204(client, setup_db):
    lage_id = _make_user_with_lage("gsl_einh_del", "gsl-einh-del")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        einheit = LageEinheit(lage_id=lage_id, label="Zu loeschen")
        db.add(einheit)
        db.commit()
        einheit_id = einheit.id
    finally:
        db.close()

    _login(client, "gsl_einh_del", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/lage/{lage_id}/einheiten/{einheit_id}/loeschen",
                    data={"_csrf": csrf}, headers=HX, follow_redirects=False)
    assert r.status_code == 204

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(LageEinheit, einheit_id) is None
    finally:
        db.close()


# ── Buergermeldungen: HTMX liefert die aktualisierte Karte statt Redirect ───

def test_meldung_ablehnen_htmx_liefert_karte_mit_status(client, setup_db):
    lage_id = _make_user_with_lage("gsl_meldung_ablehnen", "gsl-meldung-ablehnen")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        report = CitizenReport(major_incident_id=lage_id, description="Rauch im Keller")
        db.add(report)
        db.commit()
        report_id = report.id
    finally:
        db.close()

    _login(client, "gsl_meldung_ablehnen", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/lage/{lage_id}/meldungen/{report_id}/ablehnen",
                    data={"_csrf": csrf}, headers=HX, follow_redirects=False)
    assert r.status_code == 200
    assert f'id="mel-{report_id}"' in r.text
    assert "Abgelehnt" in r.text
    assert "<html" not in r.text.lower()

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(CitizenReport, report_id).status == "rejected"
    finally:
        db.close()


def test_meldung_annehmen_ohne_htmx_redirectet_weiterhin(client, setup_db):
    lage_id = _make_user_with_lage("gsl_meldung_plain", "gsl-meldung-plain")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        report = CitizenReport(major_incident_id=lage_id, description="Ueberschwemmung")
        db.add(report)
        db.commit()
        report_id = report.id
    finally:
        db.close()

    _login(client, "gsl_meldung_plain", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/lage/{lage_id}/meldungen/{report_id}/annehmen",
                    data={"_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/lage/{lage_id}/meldungen"
