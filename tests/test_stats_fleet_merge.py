from decimal import Decimal
from types import SimpleNamespace

from app.services.stats_service import _merge_fleet_by_code


def test_merge_fleet_by_code_merges_normalized_code_and_drops_external_only():
    external_duplicate = SimpleNamespace(
        id=1, code=" rlf-a ", name="RLF extern", is_adhoc=False, is_external=True,
        display_order=1, km_aktuell=0, betriebsstunden_aktuell=Decimal("0.0"),
    )
    own_vehicle = SimpleNamespace(
        id=2, code="RLF-A", name="RLF", is_adhoc=False, is_external=False,
        display_order=5, km_aktuell=10735, betriebsstunden_aktuell=Decimal("12.5"),
    )
    external_only = SimpleNamespace(
        id=3, code="DLK-X", name="Nachbar-DLK", is_adhoc=False, is_external=True,
        display_order=0, km_aktuell=20000, betriebsstunden_aktuell=Decimal("100.0"),
    )

    result = _merge_fleet_by_code(
        [external_duplicate, own_vehicle, external_only],
        {1: {"count": 49}, 2: {"count": 12}, 3: {"count": 8}},
    )

    assert result == [{
        "id": 2,
        "code": "RLF-A",
        "name": "RLF",
        "anzahl_einsaetze": 61,
        "km_aktuell": 10735,
        "betriebsstunden_aktuell": 12.5,
    }]
