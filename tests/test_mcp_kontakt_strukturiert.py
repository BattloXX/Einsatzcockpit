"""Regression coverage for the additive structured MCP contact import."""
from tests.test_mcp_objekte import _rufe, _seed, _token


def test_structured_preview_execute_and_second_import_updates_same_contact(client):
    seed = _seed("kontakt-structured-import", {"admin": "kontakt_verwalter"})
    token = _token(client, seed, "admin")
    contact = {
        "typ": "person", "vorname": "Thomas", "nachname": "Plangger",
        "anzeigename": "Plangger Thomas", "externe_id": "lwz-wolfurt-23",
        "email_adressen": [{"email": "thomas.plangger@wolfurt.at", "typ": "beruf", "bevorzugt": True}],
        "telefone": [{"nummer": "+43 699 16840023", "typ": "mobil", "verwendung": "beruf", "bevorzugt": True}],
        "organisationen": [{"name": "Bauhof Wolfurt", "funktion": "Ansprechpartner"}, {"name": "AMT - Wasserversorgung Wolfurt", "funktion": "Leiter Wasserwerk"}],
        "adressen": [{"typ": "dienst", "strasse": "Schulstraße", "hausnummer": "1", "plz": "6922", "ort": "Wolfurt", "land": "AT"}],
    }
    preview = _rufe(client, token, "kontakt_import_vorschau", quelle="LWZ Vorarlberg", kontakte=[contact])
    assert preview["neue_kontakte"] == 1
    run = _rufe(client, token, "kontakt_import_ausfuehren", preview_id=preview["preview_id"], idempotency_key="wolfurt-first")
    assert run["erfolg"] and run["angelegt"] == 1
    created_id = run["ergebnisse"][0]["kontakt_id"]
    detail = _rufe(client, token, "kontakt_lesen", kontakt_id=created_id)
    assert len(detail["email_adressen"]) == 1
    assert len(detail["organisationen"]) == 2
    preview2 = _rufe(client, token, "kontakt_import_vorschau", quelle="LWZ Vorarlberg", kontakte=[contact])
    assert preview2["aktualisierungen"] == 1
    run2 = _rufe(client, token, "kontakt_import_ausfuehren", preview_id=preview2["preview_id"], idempotency_key="wolfurt-second")
    assert run2["erfolg"] and run2["ergebnisse"][0]["kontakt_id"] == created_id
