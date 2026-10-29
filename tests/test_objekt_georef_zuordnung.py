"""Synthetische Konturen in lokalen Metern, ohne Datenbank oder Netz."""

import math

import pytest

from app.services import objekt_georef_service as geo
from app.services import objekt_georef_zuordnung_service as zuordnung
from app.services.objekt_karte_service import ObjektKartenFehler

L_FORM = [(-50, -25), (45, -25), (45, -5), (-10, -5), (-10, 25), (-50, 25)]
RECHTECK = [(-50, -25), (45, -25), (45, 25), (-50, 25)]


def kontur(form=None, *, osm_id=123, versatz=0, lat0=47, lng0=9):
    """OSM in lokalen Metern; Bild durch inverse Aehnlichkeit bei 30 Grad."""
    form = L_FORM if form is None else form
    winkel = math.radians(30)
    faktor = geo.ERDRADIUS_M * math.pi / 180
    bild, ecken = [], []
    for i, (ost, nord) in enumerate(form):
        u = (ost * math.cos(winkel) + nord * math.sin(winkel)) / 120
        v = (-ost * math.sin(winkel) + nord * math.cos(winkel)) / 120
        bild.append(dict(x=0.5 + u / 2, y=0.5 - v))
        ecken.append(dict(nummer=i + 1, lat=lat0 + nord / faktor,
                          lng=lng0 + (ost + versatz) / (faktor * math.cos(math.radians(lat0)))))
    return bild, dict(osm_id=osm_id, ecken=ecken)


def bewerten(bild, gebaeude, modell="aehnlichkeit"):
    return zuordnung.hypothesen_bewerten(bild, gebaeude, 2000, 1000, modell)


@pytest.mark.parametrize("indizes", [list(range(6)), [3, 2, 1, 0, 5, 4]])
def test_l_form_startpunkt_und_umlaufrichtung(indizes):
    bild, gebaeude = kontur()
    ergebnis = bewerten([bild[i] for i in indizes], [gebaeude])
    assert ergebnis["klasse"] == "A", ergebnis
    beste = ergebnis["hypothesen"][0]
    assert [e["osm_nummer"] for e in beste["zuordnung"]] == [i + 1 for i in indizes]
    assert [e["bild_index"] for e in beste["zuordnung"]] == list(range(6))
    assert beste["rmse_m"] < 0.1 and beste["loo_max_m"] < 0.1
    assert ergebnis["gepruefte_hypothesen"] == 12
    assert len(ergebnis["hypothesen"]) == 3
    for i, punkt in enumerate(ergebnis["punkte"]):
        assert punkt["id"] == i + 1 and punkt["quelle"] == "osm_gebaeude"
        assert punkt["beschreibung"] == f"OSM 123 Ecke {indizes[i] + 1}"


def test_rechteck_mehrdeutig():
    bild, gebaeude = kontur(RECHTECK)
    ergebnis = bewerten(bild, [gebaeude])
    assert ergebnis["klasse"] == "C" and ergebnis["punkte"] is None
    assert "Mehrdeutig" in ergebnis["begruendung"]


def test_gleichartige_nachbargebaeude():
    bild, erstes = kontur()
    _, zweites = kontur(osm_id=456, versatz=80)
    ergebnis = bewerten(bild, [erstes, zweites])
    assert ergebnis["klasse"] == "C" and "Mehrdeutig" in ergebnis["begruendung"]
    assert bewerten(bild, [zweites])["klasse"] == "A"


def test_gespiegeltes_bild():
    bild, gebaeude = kontur()
    ergebnis = bewerten([p | {"x": 1 - p["x"]} for p in bild], [gebaeude])
    assert ergebnis["klasse"] == "C"
    assert "Keine Hypothese innerhalb" in ergebnis["begruendung"]


def test_falsche_ecke():
    bild, gebaeude = kontur()
    bild[0]["x"] += 15 / 240
    assert bewerten(bild, [gebaeude])["klasse"] == "C"


def test_kollineare_zwischenecke_originalnummern():
    bild, gebaeude = kontur()
    a, b = gebaeude["ecken"][:2]
    gebaeude["ecken"].insert(1, dict(nummer=77, lat=(a["lat"] + b["lat"]) / 2, lng=(a["lng"] + b["lng"]) / 2))
    ergebnis = bewerten(bild, [gebaeude])
    assert ergebnis["klasse"] == "A"
    assert [p["osm_nummer"] for p in ergebnis["hypothesen"][0]["zuordnung"]] == list(range(1, 7))
    assert ergebnis["eckenzahlen_osm"] == {123: 6}


@pytest.mark.parametrize(
    "fall", ["drei", "zu_viele", "ausserhalb", "doppelt", "nah", "nan", "bool", "kein_dict", "keine_liste"],
)
def test_ungueltige_bildecken(fall):
    bild, gebaeude = kontur()
    if fall == "drei":
        bild = bild[:3]
    elif fall == "zu_viele":
        bild *= 6
    elif fall == "ausserhalb":
        bild[0]["x"] = 1.01
    elif fall == "doppelt":
        bild[1] = bild[0].copy()
    elif fall == "nah":
        bild[1] = bild[0] | {"x": bild[0]["x"] + 0.004}
    elif fall == "nan":
        bild[0]["y"] = math.nan
    elif fall == "bool":
        bild[0]["x"] = True
    elif fall == "kein_dict":
        bild[0] = None
    else:
        bild = None
    with pytest.raises(ObjektKartenFehler):
        bewerten(bild, [gebaeude])


def test_unpassende_eckenzahl():
    bild, _ = kontur()
    _, gebaeude = kontur(RECHTECK)
    ergebnis = bewerten(bild, [gebaeude])
    assert ergebnis["klasse"] == "C" and ergebnis["gepruefte_hypothesen"] == 0
    assert "passender Eckenzahl" in ergebnis["begruendung"]
    assert ergebnis["eckenzahlen_osm"] == {123: 4}


@pytest.mark.parametrize("modell,form", [("affin", RECHTECK), ("homographie", L_FORM)])
def test_zu_wenige_ecken_fuer_gegenpruefung(modell, form):
    bild, gebaeude = kontur(form)
    ergebnis = bewerten(bild, [gebaeude], modell)
    assert ergebnis["klasse"] == "C"
    assert "Zu wenige Ecken fuer Gegenpruefung" in ergebnis["begruendung"]
    assert all(h["loo_max_m"] is None for h in ergebnis["hypothesen"])


def test_hypothesenlimit_vor_rechnung(monkeypatch):
    bild, gebaeude = kontur()

    def unerlaubte_rechnung(*args, **kwargs):
        pytest.fail("Hypothesenlimit muss vor der ersten Rechnung greifen")

    monkeypatch.setattr(geo, "transformation_berechnen", unerlaubte_rechnung)
    with pytest.raises(ObjektKartenFehler, match="Zu viele Kandidaten; osm_id angeben oder Radius verkleinern"):
        bewerten(bild, [gebaeude] * 167)


@pytest.mark.parametrize("modell", ["unbekannt", None, []])
def test_ungueltiges_modell(modell):
    bild, gebaeude = kontur()
    with pytest.raises(ObjektKartenFehler, match="Transformationsmodell"):
        bewerten(bild, [gebaeude], modell)


def test_gegenpruefung_verhindert_a_trotz_gueltigem_fit():
    bild, gebaeude = kontur()
    bild[0]["x"] += 5 / 240
    ergebnis = bewerten(bild, [gebaeude])
    beste = ergebnis["hypothesen"][0]
    assert beste["status"] == "gueltig" and beste["rmse_m"] < geo.MAX_RMSE_M
    assert beste["loo_max_m"] > geo.MAX_FEHLER_M
    assert ergebnis["klasse"] == "C" and ergebnis["punkte"] is None


def test_gegenpruefung_nutzt_parameter_auch_bei_abgelehntem_teilfit(monkeypatch):
    bild, gebaeude = kontur()
    original = geo.transformation_berechnen

    def teilfit_abgelehnt(punkte, breite, hoehe, modell):
        ergebnis = original(punkte, breite, hoehe, modell)
        if len(punkte) == 5:
            ergebnis["status"] = "abgelehnt"
            ergebnis["ablehngrund"] = "Teilfit abgelehnt; Parameter bleiben fuer Gegenpruefung nutzbar"
        return ergebnis

    monkeypatch.setattr(geo, "transformation_berechnen", teilfit_abgelehnt)
    ergebnis = bewerten(bild, [gebaeude])
    assert ergebnis["klasse"] == "A"
    assert ergebnis["hypothesen"][0]["loo_max_m"] < 0.1
