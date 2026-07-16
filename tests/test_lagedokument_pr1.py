"""Lagedokument PR1: Grundgeruest ohne Realtime-Sync.

Gemeinsam bearbeitbares Dokument je Lage (eigenstaendig vom Einsatzjournal).
Nutzerseitig "Lagebericht" genannt (intern weiter LageDokument/lage_dokument);
zum KI-Entwurf-Button (ruft POST /lage/{id}/lagebericht auf) siehe
tests/test_lagedokument_pr7_ki_merge.py.
Prueft Zugriffsschutz (Login/Rolle/Org), Speichern+Sanitizing, Eindeutigkeit
pro Lage (kein Duplikat bei mehrfachem Speichern).
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import LageDokument, MajorIncident
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


def _make_user_with_lage(username: str, *, org_slug: str, rolle: str | None) -> tuple[int, int]:
    """Legt Org + User (+ optional Rolle) + eine Lage in dieser Org an."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=org_slug, name=f"Org {org_slug}", color="#445566", bos="Feuerwehr")
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


def test_lagedokument_erfordert_login(client, setup_db):
    _, lage_id = _make_user_with_lage("ld_anon", org_slug="ld-anon", rolle="incident_leader")
    r = client.get(f"/lage/{lage_id}/lagedokument", follow_redirects=False)
    assert r.status_code in (302, 401, 403)


def test_lagedokument_readonly_kann_lesen_aber_nicht_speichern(client, setup_db):
    _, lage_id = _make_user_with_lage("ld_readonly", org_slug="ld-ro", rolle="readonly")
    _login(client, "ld_readonly", "Test1234!")

    r = client.get(f"/lage/{lage_id}/lagedokument")
    assert r.status_code == 200
    assert "Lagebericht" in r.text

    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/lage/{lage_id}/lagedokument",
                    data={"_csrf": csrf, "content_html": "<p>x</p>"}, follow_redirects=False)
    assert r.status_code == 403


def test_lagedokument_ohne_rolle_kein_zugriff(client, setup_db):
    _, lage_id = _make_user_with_lage("ld_norole", org_slug="ld-norole", rolle=None)
    _login(client, "ld_norole", "Test1234!")
    r = client.get(f"/lage/{lage_id}/lagedokument", follow_redirects=False)
    assert r.status_code == 403


def test_lagedokument_fremde_org_kein_zugriff(client, setup_db):
    _, lage_id = _make_user_with_lage("ld_owner", org_slug="ld-owner", rolle="incident_leader")
    _make_user_with_lage("ld_fremd", org_slug="ld-fremd", rolle="incident_leader")
    _login(client, "ld_fremd", "Test1234!")
    r = client.get(f"/lage/{lage_id}/lagedokument", follow_redirects=False)
    # 404 statt 403: automatisches Tenant-Scoping macht die fremde Lage fuer
    # db.get() bereits unsichtbar (Information-Hiding), bevor der explizite
    # Org-Check ueberhaupt greift -- konsistent mit den uebrigen GSL-Routen.
    assert r.status_code in (403, 404)


def test_lagedokument_speichern_und_erneut_laden(client, setup_db):
    _, lage_id = _make_user_with_lage("ld_edit", org_slug="ld-edit", rolle="incident_leader")
    _login(client, "ld_edit", "Test1234!")

    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/lage/{lage_id}/lagedokument",
                    data={"_csrf": csrf, "content_html": "<p>Erster Stand</p>"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/lage/{lage_id}/lagedokument?gespeichert=1"

    r = client.get(f"/lage/{lage_id}/lagedokument")
    assert "Erster Stand" in r.text
    # Ohne ?gespeichert=1 kein serverseitig gerenderter Banner (die Pruefung
    # zielt auf die {% if gespeichert %}-Markup, nicht auf das statische JS
    # weiter unten im Dokument, das den Banner nur nach einem erfolgreichen
    # HTMX-Save clientseitig einfuegt, siehe htmx:afterRequest-Handler).
    assert 'class="alert alert--success"' not in r.text

    r = client.get(r.request.url.path + "?gespeichert=1")
    assert 'class="alert alert--success"' in r.text
    assert "Gespeichert" in r.text


def test_lagedokument_zweimal_speichern_erzeugt_keine_zweite_zeile(client, setup_db):
    _, lage_id = _make_user_with_lage("ld_idem", org_slug="ld-idem", rolle="incident_leader")
    _login(client, "ld_idem", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    client.post(f"/lage/{lage_id}/lagedokument", data={"_csrf": csrf, "content_html": "<p>A</p>"})
    client.post(f"/lage/{lage_id}/lagedokument", data={"_csrf": csrf, "content_html": "<p>B</p>"})

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        rows = db.query(LageDokument).filter(LageDokument.major_incident_id == lage_id).all()
        assert len(rows) == 1
        assert "B" in rows[0].content_html
    finally:
        db.close()


def test_lagedokument_content_html_wird_sanitisiert(client, setup_db):
    _, lage_id = _make_user_with_lage("ld_xss", org_slug="ld-xss", rolle="incident_leader")
    _login(client, "ld_xss", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    payload = "<p>Text</p><script>alert(1)</script>"
    client.post(f"/lage/{lage_id}/lagedokument", data={"_csrf": csrf, "content_html": payload})

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dokument = db.query(LageDokument).filter(LageDokument.major_incident_id == lage_id).first()
        assert "<script>" not in dokument.content_html
        assert "Text" in dokument.content_html
    finally:
        db.close()
