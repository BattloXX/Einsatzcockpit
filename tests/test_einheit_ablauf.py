"""HTTP-Vertrag aller bisherigen Statusübergänge und Konflikte mit dem GSL-Board."""
from datetime import UTC, datetime
from itertools import product
from uuid import uuid4

import pytest

from app.models.major_incident import (
    EINHEIT_STATUS_AKTIV,
    EINHEIT_STATUS_BEENDET,
    EINHEIT_STATUS_WERTE,
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    SitePhase,
)
from tests.test_einheit_api import _daten, _db, _device, _erkundungsergebnis, _h

# Unabhängiger API-Vertrag: direkte Schritte bleiben möglich, Endzustände nicht rücksetzbar.
ABARBEITUNG = {
    "zugewiesen": {"bestaetigt", "anfahrt", "vor_ort", "in_arbeit", "nicht_durchfuehrbar"},
    "bestaetigt": {"anfahrt", "vor_ort", "in_arbeit", "nicht_durchfuehrbar"},
    "anfahrt": {"bestaetigt", "vor_ort", "in_arbeit", "nicht_durchfuehrbar"},
    "vor_ort": {"bestaetigt", "in_arbeit", "abgeschlossen", "nicht_durchfuehrbar"},
    "in_arbeit": {"bestaetigt", "vor_ort", "abgeschlossen", "nicht_durchfuehrbar"},
    "abgeschlossen": set(), "nicht_durchfuehrbar": set(),
}
ERKUNDUNG = {
    "zugewiesen": {"bestaetigt", "anfahrt", "vor_ort", "abgeschlossen", "nicht_durchfuehrbar"},
    "bestaetigt": {"anfahrt", "vor_ort", "abgeschlossen", "nicht_durchfuehrbar"},
    "anfahrt": {"bestaetigt", "vor_ort", "abgeschlossen", "nicht_durchfuehrbar"},
    "vor_ort": {"bestaetigt", "abgeschlossen", "nicht_durchfuehrbar"},
    "in_arbeit": set(), "abgeschlossen": set(), "nicht_durchfuehrbar": set(),
}


@pytest.mark.parametrize("art,von", list(product(["erkundung", "abarbeitung"], EINHEIT_STATUS_WERTE)))
def test_alle_statusuebergaenge_ueber_die_einheiten_api(client, setup_db, art, von):
    ids = _daten()
    _device(client, ids)
    for nach in EINHEIT_STATUS_WERTE:
        with _db() as db:
            d = db.get(EinheitSiteDispatch, ids["d1"])
            d.auftragsart, d.einheit_status = art, von
            d.beendet_at = datetime.now(UTC) if von in EINHEIT_STATUS_BEENDET else None
            d.abgezogen_at = None
            d.site.phase = SitePhase.disponiert
            d.reihenfolge = 2
            d.einheit.incident_site_id = d.site_id if von in EINHEIT_STATUS_AKTIV else None
            db.commit()
        erwartet = nach == von or nach in (ERKUNDUNG if art == "erkundung" else ABARBEITUNG)[von]
        ergebnis_uuid = (
            _erkundungsergebnis(client, ids["d1"]) if art == "erkundung" and nach == "abgeschlossen" else None
        )
        response = client.post(f"/einheit/api/auftrag/{ids['d1']}/status", headers=_h(client), json={
            "ergebnis_client_uuid": ergebnis_uuid, "status": nach, "client_uuid": str(uuid4()),
            "auftrag_version": 1, "ausgangsstatus": von,
            "grund": "Zufahrt unbefahrbar" if nach == "nicht_durchfuehrbar" else None,
        })
        assert response.status_code == (200 if erwartet else 409), (art, von, nach, response.text)
        with _db() as db:
            d = db.get(EinheitSiteDispatch, ids["d1"])
            assert d.einheit_status == (nach if erwartet else von)
            assert (d.version, d.reihenfolge) == (1, 2)
        if not erwartet:
            assert response.json()["code"] == "ungueltiger_uebergang"


@pytest.mark.parametrize("art", ["erkundung", "abarbeitung"])
def test_veralteter_schritt_nach_funkstatus_des_boards_bleibt_konflikt(client, setup_db, art):
    from app.core.security import sign_session
    from tests.test_einheit_api import _als
    from tests.test_gsl_fuehrung_einheiten import _post

    ids = _daten()
    with _db() as db:
        db.get(EinheitSiteDispatch, ids["d1"]).auftragsart = art
        db.commit()
    _als(client, sign_session(ids["admin"]))
    board = _post(client, f"/lage/{ids['lage']}/stellen/{ids['a']}/einheit/{ids['d1']}/status", {"status": "vor_ort"})
    assert board.status_code == 200
    with _db() as db:
        phase_nach_board = db.get(IncidentSite, ids["a"]).phase
    _device(client, ids)
    stale = client.post(f"/einheit/api/auftrag/{ids['d1']}/status", headers=_h(client), json={
        "client_uuid": str(uuid4()), "status": "anfahrt", "ausgangsstatus": "zugewiesen", "auftrag_version": 1,
    })
    assert stale.status_code == 409 and stale.json()["code"] == "status_geaendert"
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == "vor_ort"
        assert db.get(LageEinheit, ids["e1"]).incident_site_id == ids["a"]
        assert db.get(IncidentSite, ids["a"]).phase == phase_nach_board
