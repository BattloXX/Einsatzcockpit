"""Tests für den Hydranten-/Löschwasser-Layer (OSM/OSMHydrant) und die
Einsatzinfo-Verdrahtung (QR-Ziel, SMS-{link}-Platzhalter, Routen-Registrierung)."""
from types import SimpleNamespace

from app.services import hydrant_service as hs


# ── Reine Parsing-/Distanz-Funktionen ──────────────────────────────────────────

def test_typ_aus_tags_ueberflur_unterflur_loeschwasser():
    assert hs._typ_aus_tags({"emergency": "fire_hydrant", "fire_hydrant:type": "pillar"}) == "ueberflur"
    assert hs._typ_aus_tags({"emergency": "fire_hydrant", "fire_hydrant:type": "underground"}) == "unterflur"
    # Ohne Typ-Tag: Default Überflur
    assert hs._typ_aus_tags({"emergency": "fire_hydrant"}) == "ueberflur"
    # Saugstelle / Löschteich → loeschwasser
    assert hs._typ_aus_tags({"emergency": "suction_point"}) == "loeschwasser"
    assert hs._typ_aus_tags({"emergency": "fire_water_pond"}) == "loeschwasser"


def test_parse_overpass_elements_sortiert_und_dedupliziert():
    lat, lng = 47.465, 9.750
    elements = [
        {"id": 1, "lat": 47.470, "lon": 9.755, "tags": {"emergency": "fire_hydrant"}},        # weiter weg
        {"id": 2, "lat": 47.4651, "lon": 9.7501, "tags": {"emergency": "fire_hydrant",
                                                           "fire_hydrant:type": "underground"}},  # nah
        {"id": 2, "lat": 47.4651, "lon": 9.7501, "tags": {"emergency": "fire_hydrant"}},        # Duplikat (id=2)
        {"id": 3, "tags": {"emergency": "fire_hydrant"}},                                        # ohne Koordinaten → raus
    ]
    res = hs.parse_overpass_elements(elements, lat, lng)
    assert [h["id"] for h in res] == ["osm-2", "osm-1"]          # sortiert nach Entfernung, dedupliziert
    assert res[0]["typ"] == "unterflur"
    assert res[0]["entfernung_m"] < res[1]["entfernung_m"]
    assert res[0]["quelle"] == "osm"
    assert res[0]["richtung"] in ("N", "NO", "O", "SO", "S", "SW", "W", "NW")


def test_parse_overpass_elements_respektiert_max_results():
    """Einsatzinfo (2-km-Radius) darf mehr als den Standard-Cap liefern; parse_overpass
    deckelt auf das übergebene max_results (None → Standard HYDRANT_MAX)."""
    lat, lng = 47.465, 9.750
    elements = [
        {"id": i, "lat": 47.465 + i * 0.001, "lon": 9.750, "tags": {"emergency": "fire_hydrant"}}
        for i in range(1, 11)
    ]
    assert len(hs.parse_overpass_elements(elements, lat, lng, max_results=3)) == 3
    assert len(hs.parse_overpass_elements(elements, lat, lng, max_results=50)) == 10


def test_overpass_query_nutzt_radius_und_max():
    """Der 2-km-Radius und das erhöhte Limit landen im Overpass-Query."""
    q = hs._overpass_query(47.465, 9.750, 2000, 120)
    assert "around:2000" in q
    assert "out body 240;" in q  # max_results * 2


def test_manuelle_objekt_hydranten_mapping_und_distanz():
    karten = [
        SimpleNamespace(id=10, typ="hydrant_ueberflur", lat=47.4655, lng=9.7505, label="H1"),
        SimpleNamespace(id=11, typ="hydrant_unterflur", lat=47.4660, lng=9.7510, label=None),
        SimpleNamespace(id=12, typ="feuerloescher", lat=47.4661, lng=9.7511, label="kein Hydrant"),
    ]
    res = hs.manuelle_objekt_hydranten(karten, 47.465, 9.750)
    assert [h["typ"] for h in res] == ["ueberflur", "unterflur"]  # feuerloescher ignoriert
    assert all(h["quelle"] == "objekt" for h in res)
    assert res[0]["entfernung_m"] is not None and res[0]["entfernung_m"] >= 0


def test_manuelle_objekt_hydranten_ohne_bezugspunkt():
    karten = [SimpleNamespace(id=10, typ="hydrant_ueberflur", lat=47.4655, lng=9.7505, label="H1")]
    res = hs.manuelle_objekt_hydranten(karten, None, None)
    assert res[0]["entfernung_m"] is None and res[0]["richtung"] is None


def test_merge_hydranten_sortiert_none_ans_ende():
    osm = [{"id": "osm-1", "entfernung_m": 120}, {"id": "osm-2", "entfernung_m": 30}]
    manuell = [{"id": "objekt-1", "entfernung_m": None}]
    res = hs.merge_hydranten(osm, manuell)
    assert [h["id"] for h in res] == ["osm-2", "osm-1", "objekt-1"]


# ── Verdrahtung: SMS-{link}-Platzhalter + Routen-Registrierung ──────────────────

def test_sms_link_platzhalter():
    from app.services.sms_dispatch_service import default_einsatzinfo_template, render_template
    assert "{link}" in default_einsatzinfo_template()
    text = render_template("Einsatz {stichwort}: {adresse}. {link}", {
        "stichwort": "B2", "adresse": "Senderstr. 1", "link": "https://x/alarm/abc",
    })
    assert text == "Einsatz B2: Senderstr. 1. https://x/alarm/abc"


def test_neue_routen_registriert():
    from app.main import app
    pfade = {getattr(r, "path", "") for r in app.routes}
    assert "/einsatz/{incident_id}/info" in pfade
    assert "/einsatz/{incident_id}/hydranten.json" in pfade
    assert "/objekte/{objekt_id}/hydranten.json" in pfade
    assert "/objekte/{objekt_id}/einsatz-fragment" in pfade
    assert "/alarm/{token}/hydranten.json" in pfade
