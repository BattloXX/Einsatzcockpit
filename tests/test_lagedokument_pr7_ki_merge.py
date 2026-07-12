"""Lagedokument/Lagebericht PR7: Zusammenfuehrung mit dem KI-Lagebericht.

Der ephemere "KI-Lagebericht"-Dashboard-Button entfaellt; die Funktion zieht
in den Lagebericht-Editor (weiterhin technisch LageDokument/lagedokument) um:
- Editor (can_edit): "KI-Entwurf"-Button, der die unveraenderte Route
  POST /lage/{id}/lagebericht aufruft und das Ergebnis in den Editor einfuegt.
- Lesend (readonly): "KI-Vorschlag anzeigen"-Button (dieselbe Route), zeigt
  das Ergebnis nur als Vorschau, kein Insert (kein Editor vorhanden).
- Die Route selbst markiert Fehlerantworten mit data-ai-error="1", damit der
  Client Fehler von einem echten Entwurf unterscheiden kann.
- Das Dashboard zeigt statt der alten KI-Karte einen schlichten Quick-Link
  auf den Lagebericht.
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import MajorIncident
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
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=org_slug, name=f"Org {org_slug}", color="#556677", bos="Feuerwehr")
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


def test_editor_seite_hat_ki_entwurf_button(client, setup_db):
    _, lage_id = _make_user_with_lage("ld7_edit", org_slug="ld7-edit", rolle="incident_leader")
    _login(client, "ld7_edit", "Test1234!")
    r = client.get(f"/lage/{lage_id}/lagedokument")
    assert r.status_code == 200
    assert 'id="btn-lagedokument-ki"' in r.text
    assert f"/lage/{lage_id}/lagebericht" in r.text
    assert "dangerouslyPasteHTML(index, html" in r.text


def test_readonly_seite_hat_ki_vorschau_button_statt_insert(client, setup_db):
    _, lage_id = _make_user_with_lage("ld7_ro", org_slug="ld7-ro", rolle="readonly")
    _login(client, "ld7_ro", "Test1234!")
    r = client.get(f"/lage/{lage_id}/lagedokument")
    assert r.status_code == 200
    assert 'id="btn-lagedokument-ki-vorschau"' in r.text
    assert 'id="btn-lagedokument-ki"' not in r.text
    assert f"/lage/{lage_id}/lagebericht" in r.text


def test_seiten_titel_und_nav_zeigen_lagebericht_nicht_mehr_lagedokument(client, setup_db):
    """Sichtbare Labels (Seitentitel, Section-Ueberschrift, Nav-Tab, Quill-Platzhalter)
    zeigen "Lagebericht" -- "Lagedokument" bleibt erlaubt in internen Bezeichnern
    (IDs, Routen, JS-Funktionsnamen wie initLagedokumentCollab), die werden hier
    bewusst NICHT geprueft."""
    _, lage_id = _make_user_with_lage("ld7_nav", org_slug="ld7-nav", rolle="incident_leader")
    _login(client, "ld7_nav", "Test1234!")
    r = client.get(f"/lage/{lage_id}/lagedokument")
    assert r.status_code == 200
    assert "Testlage – Lagebericht" in r.text  # {% block title %}
    assert '<div class="dash-section__title">📝 Lagebericht</div>' in r.text
    assert "placeholder: 'Lagebericht…'" in r.text


def test_ki_route_markiert_fehler_bei_deaktiviertem_ki_dienst(client, setup_db, monkeypatch):
    _, lage_id = _make_user_with_lage("ld7_off", org_slug="ld7-off", rolle="incident_leader")
    _login(client, "ld7_off", "Test1234!")
    monkeypatch.setattr("app.services.ai_service.settings.AI_ENABLED", False)

    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/lage/{lage_id}/lagebericht", data={"_csrf": csrf})
    assert r.status_code == 200
    assert 'data-ai-error="1"' in r.text
    assert "nicht aktiviert" in r.text


def test_ki_route_liefert_keinen_error_marker_bei_erfolg(client, setup_db, monkeypatch):
    _, lage_id = _make_user_with_lage("ld7_ok", org_slug="ld7-ok", rolle="incident_leader")
    _login(client, "ld7_ok", "Test1234!")
    monkeypatch.setattr("app.services.ai_service.settings.AI_ENABLED", True)
    monkeypatch.setattr("app.services.ai_service.settings.ANTHROPIC_API_KEY", "sk-test")

    async def _fake_brief(context, org_id=None):
        return "Testbericht: alles ruhig."
    # lage_ki_bericht importiert generate_situation_brief lazy (innerhalb der Funktion)
    # aus ai_service -- daher dort patchen, nicht auf dem Router-Modul.
    monkeypatch.setattr("app.services.ai_service.generate_situation_brief", _fake_brief)

    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/lage/{lage_id}/lagebericht", data={"_csrf": csrf})
    assert r.status_code == 200
    assert "data-ai-error" not in r.text
    assert "Testbericht: alles ruhig." in r.text


def test_dashboard_hat_keinen_ki_lagebericht_button_mehr(client, setup_db):
    _, lage_id = _make_user_with_lage("ld7_dash", org_slug="ld7-dash", rolle="incident_leader")
    _login(client, "ld7_dash", "Test1234!")
    r = client.get(f"/lage/{lage_id}/dashboard")
    assert r.status_code == 200
    assert "ki-bericht-result" not in r.text
    assert f"/lage/{lage_id}/lagedokument" in r.text
    assert "Lagebericht" in r.text
