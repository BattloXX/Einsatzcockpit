"""Routen-Smoke-Tests fuer die BMA-Review-Queue."""
from app.main import app


def test_scraper_admin_route_entfernt():
    assert "/admin/bma-import" not in {r.path for r in app.routes if hasattr(r, "path")}


def test_altpfad_und_queue_bleiben(client):
    r = client.get("/objekte/bma-import/datenblatt-upload", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/objekte/dokument-upload"


def test_datenblatt_post_altpfad_entfernt(client):
    assert client.post("/objekte/bma-import/datenblatt-upload").status_code in (403, 405)
