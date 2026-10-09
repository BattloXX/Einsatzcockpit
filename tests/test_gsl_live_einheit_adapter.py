"""Adapter vom Einheitenauftrag zur bestehenden GSL-Widget-Warteschlange."""
from types import SimpleNamespace

from app.services import einheit_service
from app.services.gsl_live_service import build_my_lage_queue


def _auftrag(dispatch_id: int, bezeichnung: str) -> dict:
    return {
        "dispatch_id": dispatch_id, "site_id": dispatch_id + 100,
        "bezeichnung": bezeichnung, "einsatzgrund": f"Meldung {bezeichnung}",
        "adresse": "Testgasse 1, Testort", "lat": 47.0, "lng": 9.0,
        "gmaps_url": "https://maps.google.com/", "priority": "normal",
        "phase": "eingegangen", "auftrag": f"Auftrag {bezeichnung}",
        "einheit_status": "zugewiesen",
    }


def test_einheit_widget_nimmt_aktuellen_und_beschraenkt_vorschau(monkeypatch):
    ctx = SimpleNamespace(lage=SimpleNamespace(id=7, name="Testlage", is_exercise=False))
    monkeypatch.setattr(einheit_service, "kontext_fuer_geraet", lambda *_: ctx)
    monkeypatch.setattr(einheit_service, "auftraege_fuer_einheit", lambda *_: {
        "kategorien": {"aktuell": _auftrag(1, "Aktuell"), "weitere": [
            _auftrag(2, "Zwei"), _auftrag(3, "Drei"), _auftrag(4, "Vier"),
        ]},
    })
    token = SimpleNamespace(gsl_profil="einheit", user=SimpleNamespace(), user_id=1)

    payload = build_my_lage_queue(SimpleNamespace(), token)

    assert payload["lage_url"] == "/einheit"
    assert payload["einheit_url"] == "/einheit"
    assert payload["current"]["dispatch_id"] == 1
    assert [site["dispatch_id"] for site in payload["upcoming"]] == [2, 3]
    assert payload["remaining_count"] == 1
    assert payload["current"]["einheit_url"] == "/einheit/auftrag/1"


def test_einheit_widget_waehlt_ohne_aktuellen_den_ersten_auftrag(monkeypatch):
    ctx = SimpleNamespace(lage=SimpleNamespace(id=7, name="Testlage", is_exercise=False))
    monkeypatch.setattr(einheit_service, "kontext_fuer_geraet", lambda *_: ctx)
    monkeypatch.setattr(einheit_service, "auftraege_fuer_einheit", lambda *_: {
        "kategorien": {"aktuell": None, "weitere": [_auftrag(2, "Zwei"), _auftrag(3, "Drei")]},
    })
    token = SimpleNamespace(gsl_profil="einheit", user=SimpleNamespace(), user_id=1)

    payload = build_my_lage_queue(SimpleNamespace(), token)

    assert payload["current"]["dispatch_id"] == 2
    assert [site["dispatch_id"] for site in payload["upcoming"]] == [3]
    assert payload["remaining_count"] == 0
