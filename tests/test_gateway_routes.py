"""Regressionsschutz gegen Route-Shadowing im Gateway-Router.

Bug (Prod 2026-07-08): `GET /gateway/printers.json` wurde von `GET /gateway/{gateway_id}`
abgefangen, weil `{gateway_id}` als String jede Sub-Ressource matchte → FastAPI
versuchte "printers.json" als int zu parsen → 422. Der Druck-Dialog bekam so nie
eine Druckerliste und druckte immer direkt lokal. Fix: Pfad-Konverter `{gateway_id:int}`.
"""
from __future__ import annotations

from starlette.routing import Match

from tests.conftest import flatten_routes


def _match_endpoint(path: str, method: str = "GET") -> str | None:
    import app.main as m
    scope = {"type": "http", "method": method, "path": path}
    for r in flatten_routes(m.app.router.routes):
        matches = getattr(r, "matches", None)
        if matches is None:
            continue
        match, _ = matches(scope)
        if match == Match.FULL:
            return getattr(getattr(r, "endpoint", None), "__name__", None)
    return None


def test_printers_json_not_shadowed_by_gateway_detail():
    assert _match_endpoint("/gateway/printers.json") == "printers_json"


def test_gateway_detail_still_matches_numeric_id():
    assert _match_endpoint("/gateway/42") == "gateway_detail"


# ── HTML-Render-Route (Leaflet-Karten fürs Gateway-Chromium) ────────────────────

def test_render_route_is_registered():
    assert _match_endpoint("/api/v1/print/render/1") == "get_render_page"


def test_render_route_rejects_bad_signature(client):
    # Ungültige Signatur → 403 (vor DB-Zugriff); keine offene HTML-Auslieferung.
    r = client.get("/api/v1/print/render/1?sig=bogus")
    assert r.status_code == 403
