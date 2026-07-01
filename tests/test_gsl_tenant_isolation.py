"""Regressionstests PR2 (SEC-1) + PR7 (STAB-9):
- SEC-1: GSL-Modelle (MajorIncident/IncidentSite) müssen sowohl auf ORM- als auch
  auf HTTP-Ebene org-isoliert sein — Defense-in-Depth via _TENANT_TABLE_NAMES-Auto-
  Scope zusätzlich zu den manuellen _check_org_access()-Calls.
- STAB-9: Board/Site-Detail/Funkjournal-Templates rendern mit hx-sync/hx-indicator."""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import IncidentSite, MajorIncident, MajorIncidentStatus
from app.models.master import FireDept
from app.models.user import Role, User, UserRole


def _make_org(db, slug: str) -> FireDept:
    org = FireDept(slug=slug, name=slug, color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    return org


def _make_user(db, username: str, password: str, org_id: int, role_code: str = "readonly") -> User:
    role = db.query(Role).filter(Role.code == role_code).first()
    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=username,
        org_id=org_id,
        active=True,
    )
    db.add(user)
    db.flush()
    if role:
        db.add(UserRole(user_id=user.id, role_id=role.id))
    db.commit()
    db.refresh(user)
    return user


def _login(client, username: str, password: str):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={"username": username, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )


# ── ORM-Ebene: Auto-Scope-Backstop unabhängig von manuellen Checks ───────────

def test_major_incident_not_visible_across_org_via_get(setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_a = _make_org(db, "gsl-tenant-org-a")
        org_b = _make_org(db, "gsl-tenant-org-b")
        lage_b = MajorIncident(org_id=org_b.id, name="Lage Org B", status=MajorIncidentStatus.active)
        db.add(lage_b)
        db.commit()
        lage_b_id = lage_b.id
        org_a_id = org_a.id
    finally:
        db.close()

    db = SessionLocal()
    set_tenant_context(db, org_a_id)
    try:
        result = db.get(MajorIncident, lage_b_id)
        assert result is None, "Auto-Scope-Backstop greift nicht — Lage einer fremden Org sichtbar"
    finally:
        db.close()


def test_incident_site_not_visible_across_org(setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_a = _make_org(db, "gsl-tenant-org-c")
        org_b = _make_org(db, "gsl-tenant-org-d")
        lage_b = MajorIncident(org_id=org_b.id, name="Lage Org B", status=MajorIncidentStatus.active)
        db.add(lage_b)
        db.flush()
        site_b = IncidentSite(major_incident_id=lage_b.id, org_id=org_b.id, bezeichnung="Stelle B")
        db.add(site_b)
        db.commit()
        site_b_id = site_b.id
        org_a_id = org_a.id
    finally:
        db.close()

    db = SessionLocal()
    set_tenant_context(db, org_a_id)
    try:
        result = db.get(IncidentSite, site_b_id)
        assert result is None, "Auto-Scope-Backstop greift nicht — Einsatzstelle einer fremden Org sichtbar"
    finally:
        db.close()


# ── HTTP-Ebene: End-to-End 404 für fremde Org ────────────────────────────────

def test_lage_board_returns_404_for_foreign_org(client, setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_a = _make_org(db, "gsl-tenant-org-e")
        org_b = _make_org(db, "gsl-tenant-org-f")
        user_a = _make_user(db, "gsltenantuser", "Test1234!", org_a.id)
        lage_b = MajorIncident(org_id=org_b.id, name="Lage Org B", status=MajorIncidentStatus.active)
        db.add(lage_b)
        db.commit()
        lage_b_id = lage_b.id
        username = user_a.username
    finally:
        db.close()

    _login(client, username, "Test1234!")
    r = client.get(f"/lage/{lage_b_id}", follow_redirects=False)
    assert r.status_code == 404


def test_lage_board_renders_with_site_and_prio_buttons(client, setup_db):
    """Smoke-Test PR7 (STAB-9): board.html/_site_card.html/_site_detail.html/
    funkjournal.html müssen mit den neuen hx-sync/hx-indicator-Attributen
    fehlerfrei rendern (kein Jinja-Fehler)."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = _make_org(db, "gsl-tenant-org-g")
        user = _make_user(db, "gslboarduser", "Test1234!", org.id, role_code="recorder")
        lage = MajorIncident(org_id=org.id, name="Testlage", status=MajorIncidentStatus.active)
        db.add(lage)
        db.flush()
        site = IncidentSite(major_incident_id=lage.id, org_id=org.id, bezeichnung="Teststelle")
        db.add(site)
        db.commit()
        lage_id, site_id = lage.id, site.id
        username = user.username
    finally:
        db.close()

    _login(client, username, "Test1234!")

    r = client.get(f"/lage/{lage_id}", follow_redirects=False)
    assert r.status_code == 200
    assert "prio-quick-btn" in r.text
    assert 'hx-sync="closest .site-card__footer:replace"' in r.text

    r = client.get(f"/lage/{lage_id}/stellen/{site_id}", follow_redirects=False)
    assert r.status_code == 200
    assert "siteLogForm-" in r.text
    assert "hx-sync=\"this:drop\"" in r.text

    r = client.get(f"/lage/{lage_id}/funkjournal", follow_redirects=False)
    assert r.status_code == 200
    assert "fj-add-spin" in r.text


def test_lage_stab_and_karte_render_with_ws_resync(client, setup_db):
    """Smoke-Test PR13 (STAB-6): stab.html/karte.html muessen mit dem neuen
    WS-Reconnect+Resync-Code fehlerfrei rendern (kein Jinja-/JS-Templating-Fehler)."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = _make_org(db, "gsl-tenant-org-h")
        user = _make_user(db, "gslstabuser", "Test1234!", org.id, role_code="recorder")
        lage = MajorIncident(org_id=org.id, name="Testlage", status=MajorIncidentStatus.active)
        db.add(lage)
        db.commit()
        lage_id = lage.id
        username = user.username
    finally:
        db.close()

    _login(client, username, "Test1234!")

    r = client.get(f"/lage/{lage_id}/stab", follow_redirects=False)
    assert r.status_code == 200
    assert "ec_last_stab_ws_resync" in r.text

    r = client.get(f"/lage/{lage_id}/karte", follow_redirects=False)
    assert r.status_code == 200
    assert "ec_last_karte_ws_reload" in r.text
