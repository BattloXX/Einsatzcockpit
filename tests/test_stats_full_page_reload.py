"""Regressionstest: /statistik/inhalt lieferte bei direktem Aufruf (Reload/Bookmark
der von hx-push-url gesetzten URL) nur das nackte Partial ohne Basis-Layout –
dadurch fehlten Navbar, CSS und die Chart.js/Leaflet-<script>-Tags und die
Diagramme blieben leer (Vorfall 2026-08-14).
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.user import User


def _login(client, username: str, password: str):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={"username": username, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )


def _make_user(username: str, org_slug: str) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=org_slug, name=org_slug, color="#ff0000", bos="Feuerwehr")
        db.add(org)
        db.flush()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name=username, org_id=org.id, active=True)
        db.add(user)
        db.commit()
    finally:
        db.close()


def test_inhalt_ohne_htmx_liefert_volle_seite(client, setup_db):
    """Direkter Aufruf (kein HX-Request-Header, z. B. Reload/Bookmark) muss die
    volle Seite inkl. Navbar, CSS und Chart.js/Leaflet-Skripten liefern."""
    _make_user("stats_reload", "stats-reload-org")
    _login(client, "stats_reload", "Test1234!")

    r = client.get("/statistik/inhalt?von=2025-01-01&bis=2025-12-31")
    assert r.status_code == 200
    assert "chart.umd.min.js" in r.text
    assert "leaflet.min.js" in r.text
    assert 'id="stats-content"' in r.text


def test_inhalt_mit_htmx_liefert_nur_partial(client, setup_db):
    """Der Filter-Form-Request (mit HX-Request-Header) bekommt weiterhin nur das
    Partial fuer den HTMX-Swap, ohne Basis-Layout."""
    _make_user("stats_reload_hx", "stats-reload-hx-org")
    _login(client, "stats_reload_hx", "Test1234!")

    r = client.get(
        "/statistik/inhalt?von=2025-01-01&bis=2025-12-31",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "chart.umd.min.js" not in r.text
    assert 'id="stats-content"' not in r.text
