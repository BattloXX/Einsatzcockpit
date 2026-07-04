"""Tests für lis_geo.lis_unit_coords_to_wgs84: Koordinatentransformation der
LIS-Fahrzeugpositionen (LocationX/LocationY, vermutlich EPSG:31254 + 150000m
Versatz, siehe Modul-Docstring) sowie Plausibilitätsprüfung."""
from app.services.lis.lis_geo import lis_unit_coords_to_wgs84


def test_known_wolfurt_vehicle_position_is_plausible():
    """Werte aus dem Mitschnitt (Wolfurt RLF-A, S5 am Einsatzort) müssen nach
    Vorarlberg transformiert werden (lat ~47.48, lon ~9.74), nicht nach Tirol."""
    result = lis_unit_coords_to_wgs84(105308, 260817)
    assert result is not None
    lat, lon = result
    assert 47.4 < lat < 47.6
    assert 9.6 < lon < 9.9


def test_second_known_vehicle_is_close_to_the_first():
    """Wolfurt LFB-C steht am selben Feuerwehrhaus wie RLF-A — beide Positionen
    müssen nur wenige hundert Meter auseinanderliegen."""
    lat1, lon1 = lis_unit_coords_to_wgs84(105308, 260817)
    lat2, lon2 = lis_unit_coords_to_wgs84(105359, 260753)
    assert abs(lat1 - lat2) < 0.01
    assert abs(lon1 - lon2) < 0.01


def test_implausible_coordinates_are_rejected():
    """Offensichtlich falsche/leere Rohwerte dürfen nie als Position erscheinen."""
    assert lis_unit_coords_to_wgs84(1, 1) is None
    assert lis_unit_coords_to_wgs84(0, 0) is None
