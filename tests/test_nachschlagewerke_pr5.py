"""Nachschlagewerke PR 5: Rettungskarten-Routen (UI, PDF-Auslieferung) + Templates."""
from pathlib import Path

from starlette.routing import Match

from tests.conftest import flatten_routes

TPL = Path(__file__).resolve().parent.parent / "app" / "templates" / "nachschlagewerke"


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


def test_rettungskarten_seite_registriert():
    assert _match_endpoint("/nachschlagewerke/rettungskarten") == "rettungskarten_seite"


def test_rettungskarten_suchen_post_registriert():
    assert _match_endpoint("/nachschlagewerke/rettungskarten/suchen", "POST") == "rettungskarten_suchen"


def test_pdf_route_unter_cache_praefix():
    # Muss unter /nachschlagewerk-cache/ liegen (SW cache-first) und :int erzwingen.
    assert _match_endpoint("/nachschlagewerk-cache/rettungskarten/7/original.pdf") == "rettungskarten_pdf"


def test_pdf_route_nur_numerische_id():
    # Nicht-numerische ID darf NICHT matchen (kein Shadowing/false positive).
    assert _match_endpoint("/nachschlagewerk-cache/rettungskarten/abc/original.pdf") is None


def test_templates_vorhanden():
    assert (TPL / "rettungskarten.html").exists()
    assert (TPL / "_rettungskarten_result.html").exists()


def test_pdf_404_ohne_login(client):
    # Ohne Session -> Redirect zum Login (302) statt offener Auslieferung.
    r = client.get("/nachschlagewerk-cache/rettungskarten/1/original.pdf", follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403, 404)
