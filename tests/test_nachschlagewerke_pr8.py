"""Nachschlagewerke PR 8: Gausssches Ausbreitungsmodell (Footprint-Plausibilitaet)."""
from app.services import ausbreitung_service as ab


def _flaeche_bbox(geo: dict) -> float:
    """Grobe Ausdehnung (bbox-Diagonale in Grad^2) als Groessen-Proxy."""
    ring = geo["coordinates"][0]
    if not ring:
        return 0.0
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def test_sigma_waechst_mit_distanz():
    sy1, sz1 = ab._sigma_yz(100, "D")
    sy2, sz2 = ab._sigma_yz(1000, "D")
    assert sy2 > sy1 and sz2 > sz1


def test_stabil_schmaler_als_labil():
    # F (stabil) hat kleinere sigma_y als A (labil) -> schmaler
    sy_a, _ = ab._sigma_yz(1000, "A")
    sy_f, _ = ab._sigma_yz(1000, "F")
    assert sy_f < sy_a


def test_footprint_ist_polygon_mit_flaeche():
    geo = ab.gauss_footprint(47.0, 9.7, 180.0, quellstaerke_g_s=50.0,
                             windgeschw_ms=2.0, stabilitaet="D", grenzwert_mg_m3=1.0)
    assert geo["type"] == "Polygon"
    ring = geo["coordinates"][0]
    assert ring and ring[0] == ring[-1]
    assert _flaeche_bbox(geo) > 0


def test_hoehere_freisetzung_groesserer_footprint():
    klein = ab.gauss_footprint(47.0, 9.7, 180.0, 10.0, 2.0, "D", 1.0)
    gross = ab.gauss_footprint(47.0, 9.7, 180.0, 200.0, 2.0, "D", 1.0)
    assert _flaeche_bbox(gross) > _flaeche_bbox(klein)


def test_hoeherer_grenzwert_kleinerer_footprint():
    streng = ab.gauss_footprint(47.0, 9.7, 180.0, 50.0, 2.0, "D", grenzwert_mg_m3=50.0)
    locker = ab.gauss_footprint(47.0, 9.7, 180.0, 50.0, 2.0, "D", grenzwert_mg_m3=0.5)
    assert _flaeche_bbox(locker) > _flaeche_bbox(streng)


def test_footprint_zeigt_downwind_sued():
    # richtung 180 -> Fahne suedlich der Quelle (lat < 47)
    geo = ab.gauss_footprint(47.0, 9.7, 180.0, 100.0, 2.0, "D", 1.0)
    ring = geo["coordinates"][0]
    innen = [p for p in ring if p != [9.7, 47.0]]
    assert innen and all(p[1] <= 47.0001 for p in innen)


def test_kein_bereich_ueber_grenzwert_leerer_ring():
    # winzige Quelle, riesiger Grenzwert -> nichts ueberschreitet -> leerer Ring
    geo = ab.gauss_footprint(47.0, 9.7, 180.0, 0.001, 5.0, "F", grenzwert_mg_m3=1_000_000.0)
    assert geo["coordinates"] == [[]]


def test_stabilitaetsklassen_konstante():
    assert ab.STABILITAETSKLASSEN == ("A", "B", "C", "D", "E", "F")
