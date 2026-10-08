from __future__ import annotations

import pytest

from app.services.osm_street_service import OsmNetwork, OsmWay
from app.services.road_closure_section_resolver import parse_endpoint, resolve


def _network(disjoint: bool = False) -> OsmNetwork:
    main = [
        OsmWay(1, "Rebberg", "residential", [(47.47, 9.74), (47.47, 9.745)], [1, 2]),
        OsmWay(2, "Rebberg", "residential", [(47.47, 9.745), (47.47, 9.75)], [2, 3]),
        OsmWay(3, "Kellaweg", "residential", [(47.469, 9.745), (47.47, 9.745)], [10, 2]),
        OsmWay(4, "Unterhub", "residential", [(47.47, 9.75), (47.471, 9.75)], [3, 11]),
    ]
    if disjoint:
        main.append(OsmWay(5, "Rebberg", "residential", [(47.49, 9.74), (47.49, 9.745)], [20, 21]))
    return OsmNetwork(main)


@pytest.mark.parametrize(
    ("text", "main", "kind", "value"),
    [("Kreuzung Kellaweg", "Rebberg", "cross", "Kellaweg"), ("Einmündung L 190", "Rebberg", "cross", "L 190"),
     ("Nr. 12", "Rebberg", "house", "12"), ("12a", "Rebberg", "house", "12a"),
     ("Rebberg 5", "Rebberg", "house", "5"), ("Kellaweg 5", "Rebberg", "cross", "Kellaweg"),
     ("", "Rebberg", "none", ""), ("ab Unterhub", "Rebberg", "cross", "Unterhub")],
)
def test_parse_endpoint(text, main, kind, value):
    endpoint = parse_endpoint(text, main)
    assert (endpoint.kind, endpoint.value) == (kind, value)


def test_cross_to_cross_is_high_quality():
    result = resolve(_network(), "Rebberg", parse_endpoint("Kellaweg"), parse_endpoint("Unterhub"), {})
    assert result.quality == "hoch" and result.geometry_status == "ok"
    assert result.geometry["coordinates"][0] == [9.745, 47.47]
    assert result.geometry["coordinates"][-1] == [9.75, 47.47]


@pytest.mark.parametrize("offset, quality", [(10, "hoch"), (45, "mittel")])
def test_house_to_cross_quality(offset, quality):
    point = (47.47 + offset / 111_000, 9.745)
    result = resolve(_network(), "Rebberg", parse_endpoint("12"), parse_endpoint("Unterhub"), {"12": point})
    assert result.quality == quality


def test_unknown_and_whole_street_need_review():
    unknown = resolve(_network(), "Rebberg", parse_endpoint("Nope"), parse_endpoint("Unterhub"), {})
    whole = resolve(_network(), "Rebberg", parse_endpoint(""), parse_endpoint(""), {})
    assert unknown.endpoints[0]["methode"] == "nicht_gefunden" and unknown.quality != "hoch"
    assert whole.quality == "hoch" and whole.geometry_status == "ok"


def test_disjoint_street_is_ambiguous():
    result = resolve(_network(True), "Rebberg", parse_endpoint("Kellaweg"), parse_endpoint("Unterhub"), {})
    assert any(item["art"] == "mehrere_strassenzuege" for item in result.mehrdeutigkeiten)
    assert result.quality != "hoch"


def test_fork_uses_path_between_non_branch_arms():
    network = OsmNetwork([
        OsmWay(1, "Rebberg", "residential", [(47.47, 9.74), (47.47, 9.745)], [1, 2]),
        OsmWay(2, "Rebberg", "residential", [(47.47, 9.745), (47.47, 9.75)], [2, 3]),
        OsmWay(3, "Rebberg", "residential", [(47.47, 9.745), (47.475, 9.745)], [2, 4]),
        OsmWay(4, "West", "residential", [(47.469, 9.74), (47.47, 9.74)], [10, 1]),
        OsmWay(5, "Ost", "residential", [(47.47, 9.75), (47.471, 9.75)], [3, 11]),
    ])
    result = resolve(network, "Rebberg", parse_endpoint("West"), parse_endpoint("Ost"), {})
    assert result.quality == "hoch"
    assert [9.745, 47.47] in result.geometry["coordinates"]
    assert [9.745, 47.475] not in result.geometry["coordinates"]


def test_disconnected_nearby_pieces_get_no_connection():
    network = OsmNetwork([
        OsmWay(1, "Rebberg", "residential", [(47.47, 9.74), (47.47, 9.741)], [1, 2]),
        OsmWay(2, "Rebberg", "residential", [(47.47, 9.7423), (47.47, 9.7433)], [3, 4]),
        OsmWay(3, "West", "residential", [(47.469, 9.74), (47.47, 9.74)], [10, 1]),
        OsmWay(4, "Ost", "residential", [(47.47, 9.7433), (47.471, 9.7433)], [4, 11]),
    ])
    result = resolve(network, "Rebberg", parse_endpoint("West"), parse_endpoint("Ost"), {})
    assert result.quality == "niedrig"
    assert any(item["art"] == "kein_zusammenhang" for item in result.mehrdeutigkeiten)


def test_ambiguous_crossing_picks_candidate_near_other_endpoint():
    network = OsmNetwork([
        OsmWay(1, "Rebberg", "residential", [(47.47, 9.74), (47.47, 9.745), (47.47, 9.75)], [1, 2, 3]),
        OsmWay(
            2,
            "Kellaweg",
            "residential",
            [(47.469, 9.745), (47.47, 9.745), (47.469, 9.75), (47.47, 9.75)],
            [10, 2, 12, 3],
        ),
        OsmWay(3, "Unterhub", "residential", [(47.47, 9.75), (47.471, 9.75)], [3, 11]),
    ])
    result = resolve(network, "Rebberg", parse_endpoint("Kellaweg"), parse_endpoint("Unterhub"), {})
    assert result.quality == "mittel"
    assert result.endpoints[0]["lng"] == 9.75
    assert any(item["art"] == "kreuzung" for item in result.mehrdeutigkeiten)


def test_house_endpoint_is_trimmed_to_projection():
    point = (47.47005, 9.746)
    result = resolve(_network(), "Rebberg", parse_endpoint("12"), parse_endpoint("Unterhub"), {"12": point})
    first = result.geometry["coordinates"][0]
    assert abs(first[0] - point[1]) < 0.00002
    assert abs(first[1] - 47.47) < 0.00002


def _street(name: str, length_m: float, pieces: int = 3) -> OsmNetwork:
    step = length_m / 75_000 / pieces
    ways = []
    for index in range(pieces):
        ways.append(
            OsmWay(
                100 + index,
                name,
                "residential",
                [(47.47, 9.74 + index * step), (47.47, 9.74 + (index + 1) * step)],
                [200 + index, 201 + index],
            )
        )
    return OsmNetwork(ways)


def test_ganze_kurze_strasse_mit_exaktem_namen_ist_eindeutig():
    result = resolve(_street("Unterhub", 400), "Unterhub", parse_endpoint(""), parse_endpoint(""), {})
    assert result.quality == "hoch" and result.geometry_status == "ok"
    assert result.hinweise == ["Ganze Straße übernommen"]


def test_ganze_lange_strasse_oder_teilwort_braucht_pruefung():
    long_street = resolve(_street("Unterhub", 2500), "Unterhub", parse_endpoint(""), parse_endpoint(""), {})
    assert long_street.quality == "mittel" and long_street.geometry_status == "needs_review"
    partial = resolve(_street("Unterhubstraße", 400), "Unterhub", parse_endpoint(""), parse_endpoint(""), {})
    assert partial.quality != "hoch"


def test_angegebene_enden_nicht_gefunden_meldet_klaren_hinweis():
    result = resolve(_network(), "Rebberg", parse_endpoint("Nope"), parse_endpoint("Auch nicht"), {})
    assert "Abschnittsenden nicht gefunden – ganze Straße übernommen" in result.hinweise
    assert result.quality == "mittel"


@pytest.mark.parametrize(("gap_m", "methode"), [(15, "kreuzung"), (40, "nicht_gefunden")])
def test_kreuzung_ohne_gemeinsamen_knoten_wird_geometrisch_gesucht(gap_m, methode):
    network = _network()
    lat = 47.47 - gap_m / 111_000
    loose = OsmWay(30, "Lose Gasse", "residential", [(lat - 0.001, 9.7475), (lat, 9.7475)], [40, 41])
    result = resolve(OsmNetwork([*network.ways, loose]), "Rebberg", parse_endpoint("Lose Gasse"),
                     parse_endpoint("Unterhub"), {})
    assert result.endpoints[0]["methode"] == methode
    assert result.quality == "mittel"
    if methode == "kreuzung":
        assert "Kreuzung mit Lose Gasse nur geometrisch ermittelt" in result.hinweise


def test_ganze_strasse_mit_abzweigung_umfasst_alle_aeste():
    ways = [
        OsmWay(1, "Unterhub", "residential", [(47.47, 9.74), (47.47, 9.743)], [1, 2]),
        OsmWay(2, "Unterhub", "residential", [(47.47, 9.743), (47.47, 9.746)], [2, 3]),
        OsmWay(3, "Unterhub", "residential", [(47.47, 9.743), (47.4715, 9.743)], [2, 4]),
    ]
    result = resolve(OsmNetwork(ways), "Unterhub", parse_endpoint(""), parse_endpoint(""), {})
    assert result.quality == "hoch" and result.geometry_status == "ok"
    assert result.geometry["type"] == "MultiLineString"
    total = sum(len(part) for part in result.geometry["coordinates"])
    assert total >= 4 and result.length_m > 600
