from datetime import datetime
from math import cos, radians

import pytest
from shapely.geometry import LineString, shape

from app.services import road_closure_geo_service as geo


def _offset(lat, lng, north=0, east=0):
    return lat + north / 111320, lng + east / (111320 * cos(radians(lat)))


def _point(lat, lng, north=0, east=0):
    point_lat, point_lng = _offset(lat, lng, north, east)
    return {"type": "Point", "coordinates": [point_lng, point_lat]}


@pytest.fixture
def route_data():
    lat, lng = 47.47, 9.75
    end = _offset(lat, lng, east=2000)
    return (
        lat,
        lng,
        end,
        {
            "type": "LineString",
            "coordinates": [[lng, lat], [end[1], end[0]]],
        },
    )


def _relevance(route, destination, closure):
    result = geo.classify(route, destination, [(1, closure)])
    return result[0].relevance if result else None


def test_line_on_route_is_route(route_data):
    lat, lng, end, route = route_data
    line_end = _offset(lat, lng, east=100)
    closure = {"type": "LineString", "coordinates": [[lng, lat], [line_end[1], line_end[0]]]}
    assert _relevance(route, end, closure) == "route"


def test_short_parallel_line_is_route(route_data):
    lat, lng, end, route = route_data
    start = _offset(lat, lng, north=20)
    line_end = _offset(lat, lng, north=20, east=100)
    closure = {"type": "LineString", "coordinates": [[start[1], start[0]], [line_end[1], line_end[0]]]}
    assert _relevance(route, end, closure) == "route"


def test_long_line_with_200_m_overlap_is_route(route_data):
    lat, lng, end, route = route_data
    on_route_end = _offset(lat, lng, east=200)
    off_route_end = _offset(on_route_end[0], on_route_end[1], north=800)
    closure = {
        "type": "LineString",
        "coordinates": [[lng, lat], [on_route_end[1], on_route_end[0]], [off_route_end[1], off_route_end[0]]],
    }
    assert _relevance(route, end, closure) == "route"


def test_perpendicular_crossing_is_nearby(route_data):
    lat, lng, end, route = route_data
    south = _offset(lat, lng, north=-100, east=500)
    north = _offset(lat, lng, north=100, east=500)
    closure = {"type": "LineString", "coordinates": [[south[1], south[0]], [north[1], north[0]]]}
    assert _relevance(route, end, closure) == "nearby"


def test_short_line_completely_on_route_is_route(route_data):
    lat, lng, end, route = route_data
    line_end = _offset(lat, lng, east=20)
    closure = {"type": "LineString", "coordinates": [[lng, lat], [line_end[1], line_end[0]]]}
    assert _relevance(route, end, closure) == "route"


def test_point_on_route_is_route(route_data):
    lat, lng, end, route = route_data
    assert _relevance(route, end, _point(lat, lng, east=500)) == "route"


def test_point_near_destination_but_not_route_is_destination(route_data):
    lat, lng, end, route = route_data
    destination = _offset(end[0], end[1], north=-150)
    closure = _point(destination[0], destination[1], north=100)
    assert _relevance(route, destination, closure) == "destination"


def test_polygon_crossing_route_is_route(route_data):
    lat, lng, end, route = route_data
    southwest = _offset(lat, lng, north=-30, east=500)
    northeast = _offset(lat, lng, north=30, east=550)
    closure = {
        "type": "Polygon",
        "coordinates": [
            [
                [southwest[1], southwest[0]],
                [northeast[1], southwest[0]],
                [northeast[1], northeast[0]],
                [southwest[1], northeast[0]],
                [southwest[1], southwest[0]],
            ]
        ],
    }
    assert _relevance(route, end, closure) == "route"


def test_point_300_m_from_route_is_nearby(route_data):
    lat, lng, end, route = route_data
    assert _relevance(route, end, _point(lat, lng, north=300, east=800)) == "nearby"


def test_distant_geometry_is_not_included(route_data):
    lat, lng, end, route = route_data
    assert _relevance(route, end, _point(lat, lng, north=2000)) is None


def test_without_route_only_destination_or_nearby(route_data):
    lat, lng, end, _ = route_data
    assert _relevance(None, end, _point(end[0], end[1], north=100)) == "destination"
    assert _relevance(None, end, _point(end[0], end[1], north=300)) == "nearby"


def test_validate_geometry_handles_feature_and_json():
    point = {"type": "Point", "coordinates": [9.75, 47.47]}
    assert geo.validate_geometry({"type": "Feature", "geometry": point}) == point
    assert geo.validate_geometry('{"type":"Point","coordinates":[9.75,47.47]}') == point
    assert geo.bbox({"type": "LineString", "coordinates": [[9.7, 47.4], [9.8, 47.5]]}) == (
        47.4,
        9.7,
        47.5,
        9.8,
    )


def test_validate_geometry_rejects_bad_polygons_and_feature_collections():
    unclosed = {
        "type": "Polygon",
        "coordinates": [[[9.7, 47.4], [9.8, 47.4], [9.8, 47.5], [9.7, 47.5]]],
    }
    collection = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [9.7, 47.4]}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [9.8, 47.5]}},
        ],
    }
    for value in (unclosed, collection, {"type": "Point", "coordinates": [181, 47]}):
        with pytest.raises(ValueError):
            geo.validate_geometry(value)


def test_avoid_polygon_and_distance():
    line = {"type": "LineString", "coordinates": [[9.75, 47.47], [9.751, 47.47]]}
    avoided = geo.avoid_polygon(line)
    assert shape(avoided).contains(LineString(line["coordinates"]).interpolate(0.5, normalized=True))
    point_lat, point_lng = _offset(47.47, 9.75, north=100, east=50)
    assert geo.distance_point_to_geometry_m(point_lat, point_lng, line) == pytest.approx(100, abs=2)


def test_fingerprint_is_order_independent():
    items = [(3, datetime(2026, 1, 2)), (1, None)]
    assert geo.fingerprint(items) == geo.fingerprint(list(reversed(items)))
