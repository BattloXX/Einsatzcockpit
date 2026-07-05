"""Fehlerseiten & Login-Redirects: Browser (Accept: text/html) bekommt schöne
HTML-Fehlerseiten bzw. wird bei geschützten Seiten zum Login geführt; API/Fetch
(Accept: */*) behält das bisherige JSON-/Status-Verhalten."""
HTML = {"accept": "text/html"}


def test_public_alarm_invalid_token_html_error_page(client):
    r = client.get("/alarm/nicht_existierender_token", headers=HTML)
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]
    assert "Link ungültig oder abgelaufen" in r.text
    assert "Nicht gefunden" in r.text


def test_public_alarm_invalid_token_json_unchanged_for_api(client):
    # Ohne text/html-Accept (Default TestClient) bleibt das JSON-Verhalten erhalten
    r = client.get("/alarm/nicht_existierender_token")
    assert r.status_code == 404
    assert r.json()["detail"] == "Link ungültig oder abgelaufen"


def test_unmatched_route_html_error_page(client):
    r = client.get("/gibt-es-nicht-xyz", headers=HTML)
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]
    assert "Nicht gefunden" in r.text


def test_protected_page_anonymous_redirects_to_login(client):
    # Geschützte Seite (require_role) ohne Login im Browser → Redirect auf /login?next=...
    r = client.get("/admin/benutzer", headers=HTML, follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/login?next=")
    assert "benutzer" in loc


def test_hydranten_json_anonymous_stays_json(client):
    # .json-Endpunkt: kein Login-Redirect, sondern JSON/Status (fetch-freundlich)
    r = client.get("/einsatz/999999/hydranten.json")
    assert r.status_code in (401, 403, 404)
    assert "application/json" in r.headers["content-type"]


def test_login_next_roundtrip_safe(client):
    # interner next wird an das Formular durchgereicht …
    r = client.get("/login?next=/objekte", headers=HTML)
    assert r.status_code == 200
    assert 'name="next" value="/objekte"' in r.text
    # … ein externer next wird verworfen (Open-Redirect-Schutz greift beim POST)
    from app.routers.auth import _safe_next
    assert _safe_next("https://evil.example/x") == "/"
    assert _safe_next("//evil.example") == "/"
    assert _safe_next("/einsatz/5/info") == "/einsatz/5/info"
