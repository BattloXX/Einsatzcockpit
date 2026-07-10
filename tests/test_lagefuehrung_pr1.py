"""Lageführung-Modul (Phase 1 / MVP): Feature-Flag, Lagekarte, Feature-CRUD, Rollen.

Mirrors the UAS PR0/PR1 test pattern (Guard 404, Service-Logik, Integrationstest
mit echtem Login + DB). ORM-Auto-Scope (_TENANT_TABLE_NAMES) wird analog
test_gsl_tenant_isolation.py separat geprüft.
"""
from unittest.mock import MagicMock

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.lagefuehrung import LagefuehrungFeature
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.user import Role, User, UserRole
from app.services.lagefuehrung_service import lagefuehrung_effective_enabled, lagefuehrung_system_enabled

ORG_ID = 1  # FF Wolfurt (seeded)


# ── Service-Logik (ohne HTTP, Muster test_uas_pr0) ──────────────────────────────

class _Sys:
    def __init__(self, value=None):
        self.key = "lagefuehrung_modul_aktiv"
        self.value = value


class _OrgS:
    def __init__(self, enabled=False):
        self.lagefuehrung_modul_aktiv = enabled


def test_system_flag_missing_returns_false():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    assert lagefuehrung_system_enabled(db) is False


def test_effective_false_when_no_org():
    db = MagicMock()
    assert lagefuehrung_effective_enabled(None, db) is False


def test_effective_false_when_system_on_org_off():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [_Sys("true"), _OrgS(False)]
    assert lagefuehrung_effective_enabled(1, db) is False


def test_effective_true_when_both_on():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [_Sys("true"), _OrgS(True)]
    assert lagefuehrung_effective_enabled(1, db) is True


def test_router_importable():
    from app.routers.ui_lagefuehrung import require_lagefuehrung_enabled, router
    assert callable(require_lagefuehrung_enabled)
    assert router is not None


# ── Guard: HTTP 404 wenn nicht aktiv ─────────────────────────────────────────────

def test_guard_404_when_module_off(client):
    resp = client.get("/einsatz/1/lagefuehrung", follow_redirects=False)
    assert resp.status_code in (302, 404)


# ── Integration: Flag AN, Seite, Feature-CRUD, Rollen ────────────────────────────

def _login(client, username, password):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _rolle(db, code):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, name=code)
        db.add(role)
        db.flush()
    return role


def _setup(username, *, org_id=ORG_ID, rollen=("incident_leader",)):
    """User + Rollen anlegen, Modul system+org aktivieren, Einsatz mit Koordinaten anlegen."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Lft Test", org_id=org_id, active=True)
        db.add(user)
        db.flush()
        for code in rollen:
            db.add(UserRole(user_id=user.id, role_id=_rolle(db, code).id))

        sys_row = db.get(SystemSettings, "lagefuehrung_modul_aktiv")
        if sys_row is None:
            db.add(SystemSettings(key="lagefuehrung_modul_aktiv", value="true"))
        else:
            sys_row.value = "true"
        os_row = db.query(OrgSettings).filter_by(org_id=org_id).first()
        if os_row is None:
            os_row = OrgSettings(org_id=org_id)
            db.add(os_row)
        os_row.lagefuehrung_modul_aktiv = True

        incident = Incident(primary_org_id=org_id, alarm_type_code="T1", status="active",
                             lat=47.4652, lng=9.7503)
        db.add(incident)
        db.commit()
        return incident.id
    finally:
        db.close()


def test_seite_404_wenn_org_flag_aus(client):
    """Systemweit AN, Org-Flag AUS → weiterhin 404 (zweistufiges Gating)."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        sys_row = db.get(SystemSettings, "lagefuehrung_modul_aktiv")
        if sys_row is None:
            db.add(SystemSettings(key="lagefuehrung_modul_aktiv", value="true"))
        else:
            sys_row.value = "true"
        user = User(username="lft_orgoff_user", password_hash=hash_password("Test1234!"),
                    display_name="Lft OrgOff", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "incident_leader").id))
        os_row = db.query(OrgSettings).filter_by(org_id=ORG_ID).first()
        if os_row is None:
            os_row = OrgSettings(org_id=ORG_ID)
            db.add(os_row)
        os_row.lagefuehrung_modul_aktiv = False
        incident = Incident(primary_org_id=ORG_ID, alarm_type_code="T1", status="active")
        db.add(incident)
        db.commit()
        incident_id = incident.id
    finally:
        db.close()

    _login(client, "lft_orgoff_user", "Test1234!")
    resp = client.get(f"/einsatz/{incident_id}/lagefuehrung")
    assert resp.status_code == 404


def test_seite_laedt_und_feature_crud_lifecycle(client):
    incident_id = _setup("lft_crud_user")
    _login(client, "lft_crud_user", "Test1234!")

    r = client.get(f"/einsatz/{incident_id}/lagefuehrung")
    assert r.status_code == 200, r.text[:500]
    assert "Lageführung" in r.text
    assert "initLagefuehrungKarte" in r.text

    csrf = client.cookies.get("ec_csrf")

    # Leere Liste zu Beginn
    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/features.json")
    assert r.status_code == 200
    assert r.json() == []

    # Anlegen
    geometry = {"type": "Point", "coordinates": [9.75, 47.46]}
    r = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/features",
        json={"typ": "marker", "geometry": geometry, "label": "Testmarker"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 201, r.text[:500]
    feature = r.json()
    assert feature["version"] == 1
    assert feature["label"] == "Testmarker"
    feature_id = feature["id"]

    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/features.json")
    assert len(r.json()) == 1

    # Update mit korrekter Version
    r = client.patch(
        f"/einsatz/{incident_id}/lagefuehrung/features/{feature_id}",
        json={"label": "Umbenannt", "version": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text[:500]
    assert r.json()["version"] == 2
    assert r.json()["label"] == "Umbenannt"

    # Update mit veralteter Version → 409 (Optimistic Concurrency)
    r = client.patch(
        f"/einsatz/{incident_id}/lagefuehrung/features/{feature_id}",
        json={"label": "Konflikt", "version": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409

    # Löschen mit korrekter Version
    r = client.delete(
        f"/einsatz/{incident_id}/lagefuehrung/features/{feature_id}?version=2",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 204

    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/features.json")
    assert r.json() == []

    # Chronologie enthält alle drei Ereignisse
    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/events.json")
    typen = [e["event_typ"] for e in r.json()]
    assert "feature.created" in typen
    assert "feature.updated" in typen
    assert "feature.deleted" in typen


def test_lageführung_uebernehmen_setzt_fuehrer_und_broadcastet(client):
    incident_id = _setup("lft_fuehrer_user")
    _login(client, "lft_fuehrer_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/uebernehmen",
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = db.get(Incident, incident_id)
        user = db.query(User).filter(User.username == "lft_fuehrer_user").first()
        assert incident.lagefuehrung_fuehrer_user_id == user.id
    finally:
        db.close()

    r = client.get(f"/einsatz/{incident_id}/lagefuehrung")
    assert "lft_fuehrer_user" in r.text or "Lft Test" in r.text


def test_readonly_user_kann_nicht_zeichnen(client):
    """Rollen-Grundgerüst: ohne Bearbeiter-Rolle → 403 beim Anlegen eines Features."""
    incident_id = _setup("lft_readonly_user", rollen=("readonly",))
    _login(client, "lft_readonly_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.get(f"/einsatz/{incident_id}/lagefuehrung")
    assert r.status_code == 200

    r = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/features",
        json={"typ": "marker", "geometry": {"type": "Point", "coordinates": [9.75, 47.46]}},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 403


# ── ORM-Auto-Scope: Feature einer fremden Org ist nicht sichtbar ────────────────

def _make_org(db, slug: str) -> FireDept:
    org = FireDept(slug=slug, name=slug, color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    return org


def test_feature_nicht_sichtbar_ueber_fremde_org(setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_a = _make_org(db, "lft-tenant-org-a")
        org_b = _make_org(db, "lft-tenant-org-b")
        incident_b = Incident(primary_org_id=org_b.id, alarm_type_code="T1", status="active")
        db.add(incident_b)
        db.flush()
        feature_b = LagefuehrungFeature(
            org_id=org_b.id, incident_id=incident_b.id, typ="marker",
            geometry='{"type":"Point","coordinates":[9.75,47.46]}',
        )
        db.add(feature_b)
        db.commit()
        feature_b_id = feature_b.id
        org_a_id = org_a.id
    finally:
        db.close()

    db = SessionLocal()
    set_tenant_context(db, org_a_id)
    try:
        result = db.get(LagefuehrungFeature, feature_b_id)
        assert result is None, "Auto-Scope-Backstop greift nicht — Feature einer fremden Org sichtbar"
    finally:
        db.close()
