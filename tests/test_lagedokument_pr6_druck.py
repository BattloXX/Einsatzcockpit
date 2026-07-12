"""Lagedokument PR6: Druck/Export + Politur (Verbindungsstatus, Zeitstempel).

Deckt die neue Druckroute (Browser-Print wie die uebrigen /druck-Routen, kein
Server-PDF), den Zugriffsschutz darauf, sowie die Client-Politur (Status-Pill,
Drucken-Button mit Speichern-vorher) im gerenderten HTML ab. Ausserdem einen
Regressionstest fuer den in diesem PR gefundenen Bug, dass die periodische
Yjs-Autospeicherung `updated_at` nie aktualisiert hatte (der sichtbare
"zuletzt gespeichert"-Zeitstempel waere dadurch systematisch veraltet).
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import LageDokument, MajorIncident
from app.models.master import FireDept
from app.models.user import Role, User, UserRole
from app.services import lagedokument_collab as collab


def _login(client, username: str, password: str):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={"username": username, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )


def _make_user_with_lage(username: str, *, org_slug: str, rolle: str | None) -> tuple[int, int]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=org_slug, name=f"Org {org_slug}", color="#223344", bos="Feuerwehr")
        db.add(org)
        db.flush()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Testuser", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        if rolle:
            role = db.query(Role).filter(Role.code == rolle).first()
            db.add(UserRole(user_id=user.id, role_id=role.id))
        lage = MajorIncident(org_id=org.id, name="Testlage")
        db.add(lage)
        db.commit()
        return org.id, lage.id
    finally:
        db.close()


def test_druck_erfordert_login(client, setup_db):
    _, lage_id = _make_user_with_lage("ld6_anon", org_slug="ld6-anon", rolle="incident_leader")
    r = client.get(f"/lage/{lage_id}/lagedokument/druck", follow_redirects=False)
    assert r.status_code in (302, 401, 403)


def test_druck_zeigt_gespeicherten_inhalt(client, setup_db):
    _, lage_id = _make_user_with_lage("ld6_edit", org_slug="ld6-edit", rolle="incident_leader")
    _login(client, "ld6_edit", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    client.post(f"/lage/{lage_id}/lagedokument", data={"_csrf": csrf, "content_html": "<p>Druckinhalt</p>"})

    r = client.get(f"/lage/{lage_id}/lagedokument/druck")
    assert r.status_code == 200
    assert "Druckinhalt" in r.text
    assert "window.print()" in r.text


def test_druck_readonly_darf_lesen(client, setup_db):
    _, lage_id = _make_user_with_lage("ld6_ro", org_slug="ld6-ro", rolle="readonly")
    _login(client, "ld6_ro", "Test1234!")
    r = client.get(f"/lage/{lage_id}/lagedokument/druck")
    assert r.status_code == 200


def test_druck_fremde_org_kein_zugriff(client, setup_db):
    _, lage_id = _make_user_with_lage("ld6_owner", org_slug="ld6-owner", rolle="incident_leader")
    _make_user_with_lage("ld6_fremd", org_slug="ld6-fremd", rolle="incident_leader")
    _login(client, "ld6_fremd", "Test1234!")
    r = client.get(f"/lage/{lage_id}/lagedokument/druck", follow_redirects=False)
    assert r.status_code in (403, 404)


def test_editor_seite_hat_status_pill_und_drucken_button(client, setup_db):
    _, lage_id = _make_user_with_lage("ld6_ui", org_slug="ld6-ui", rolle="incident_leader")
    _login(client, "ld6_ui", "Test1234!")
    r = client.get(f"/lage/{lage_id}/lagedokument")
    assert r.status_code == 200
    assert 'id="lagedokument-status"' in r.text
    assert 'id="btn-lagedokument-drucken"' in r.text
    assert "onStatusChange" in r.text
    assert f"/lage/{lage_id}/lagedokument/druck" in r.text


def test_readonly_seite_hat_direkten_druck_link_ohne_speichern(client, setup_db):
    _, lage_id = _make_user_with_lage("ld6_ro_ui", org_slug="ld6-ro-ui", rolle="readonly")
    _login(client, "ld6_ro_ui", "Test1234!")
    r = client.get(f"/lage/{lage_id}/lagedokument")
    assert r.status_code == 200
    assert f'/lage/{lage_id}/lagedokument/druck' in r.text
    assert 'id="btn-lagedokument-drucken"' not in r.text


def test_autosave_aktualisiert_updated_at(setup_db):
    """Regression: die periodische Yjs-Autospeicherung darf updated_at nicht
    unveraendert lassen, sonst zeigt die Seite ein veraltetes 'zuletzt
    gespeichert' an, obwohl live weitergeschrieben wurde."""
    import time
    from datetime import UTC, datetime

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug="ld6-autosave", name="Org autosave", color="#123456", bos="Feuerwehr")
        db.add(org)
        db.flush()
        lage = MajorIncident(org_id=org.id, name="Testlage")
        db.add(lage)
        db.flush()
        alt = datetime(2020, 1, 1, tzinfo=UTC)
        dok = LageDokument(major_incident_id=lage.id, org_id=org.id, updated_at=alt)
        db.add(dok)
        db.commit()
        lage_id, org_id = lage.id, org.id
    finally:
        db.close()

    time.sleep(0.01)
    collab._save_ydoc_state(lage_id, b"\x00fake-state", org_id)

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        refreshed = db.query(LageDokument).filter(LageDokument.major_incident_id == lage_id).first()
        assert refreshed.updated_at.replace(tzinfo=None) > alt.replace(tzinfo=None)
    finally:
        db.close()
