"""422-Ablehnungen werden mit Feld und Fehlertyp geloggt, ohne Eingabewerte."""
import logging


def test_422_wird_mit_feld_geloggt(client, caplog):
    from app.main import app

    @app.post("/_test/validierung")
    async def _validierung(anzahl: int):
        return {"anzahl": anzahl}

    caplog.set_level(logging.WARNING)
    try:
        client.get("/login")
        response = client.post(
            "/_test/validierung?anzahl=geheimwert",
            headers={"X-CSRF-Token": client.cookies.get("ec_csrf")},
        )
    finally:
        # Testroute wieder entfernen, damit andere Tests (Routen-/OpenAPI-Pruefungen) sie nicht sehen.
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/_test/validierung"]
        app.openapi_schema = None
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    eintraege = [r.getMessage() for r in caplog.records if "Eingabe abgelehnt" in r.getMessage()]
    assert eintraege and "query.anzahl:int_parsing" in eintraege[0]
    assert "geheimwert" not in eintraege[0]
