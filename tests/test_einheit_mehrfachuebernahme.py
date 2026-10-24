"""Mehrfachbestätigung und geschützter Wechsel verwenden die vorhandenen Dispositionen."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.major_incident import (
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    SiteLogEntry,
    SitePhase,
    SitePriority,
)
from tests.test_einheit_api import _db, _erkundungsergebnis, _h
from tests.test_einheit_uebersicht import _bestand


def _status(client, dispatch_id, status, **werte):
    return client.post(f"/einheit/api/auftrag/{dispatch_id}/status", headers=_h(client),
                       json={"status": status, "client_uuid": str(uuid4()), **werte})


def _vorbereiten(client):
    ids = _bestand(client)
    with _db() as db:
        for dispatch_id in ids["offen"]:
            db.get(EinheitSiteDispatch, dispatch_id).site.phase = SitePhase.disponiert
        db.commit()
    return ids


def test_drei_vorschauen_zwei_bestaetigungen_ein_aktiver_auftrag(client, setup_db):
    ids = _vorbereiten(client)
    for dispatch_id in ids["offen"]:
        detail = client.get(f"/einheit/api/auftrag/{dispatch_id}").json()
        assert detail["auftrag"]["einheit_status"] == "zugewiesen"
        assert "bestaetigt" in detail["auftrag"]["erlaubte_status"]
        assert {
            "stelle", "gefahren", "objekte", "strassensperren", "fotos", "andere_einheiten", "chronik",
        } <= set(detail)
    for dispatch_id in ids["offen"][:2]:
        response = _status(client, dispatch_id, "bestaetigt", auftrag_version=1, ausgangsstatus="zugewiesen")
        assert response.status_code == 200
    assert _status(client, ids["offen"][2], "in_arbeit").status_code == 200
    data = client.get("/einheit/api/zustand").json()
    assert data["kategorien"]["aktuell"]["dispatch_id"] == ids["offen"][2]
    assert [d["einheit_status"] for d in data["kategorien"]["weitere"]] == ["bestaetigt", "bestaetigt"]
    with _db() as db:
        for index, dispatch_id in enumerate(ids["offen"], start=1):
            dispatch = db.get(EinheitSiteDispatch, dispatch_id)
            assert (dispatch.reihenfolge, dispatch.version) == (index, 1)
            if index < 3:
                assert dispatch.bestaetigt_at and not dispatch.vor_ort_at
        assert db.get(IncidentSite, ids["a"]).priority == SitePriority.sofort


def test_bestaetigung_an_gleicher_stelle_entfernt_aktiven_auftrag_nicht(client, setup_db):
    ids = _vorbereiten(client)
    assert _status(client, ids["d1"], "vor_ort").status_code == 200
    with _db() as db:
        zweiter = EinheitSiteDispatch(einheit_id=ids["e1"], site_id=ids["a"], dispatched_at=datetime.now(UTC),
                                      auftragsart="erkundung")
        db.add(zweiter)
        db.commit()
        dispatch_id = zweiter.id
    assert _status(client, dispatch_id, "bestaetigt", ausgangsstatus="zugewiesen").status_code == 200
    assert client.get("/einheit/api/zustand").json()["kategorien"]["aktuell"]["dispatch_id"] == ids["d1"]
    assert _status(client, dispatch_id, "abgeschlossen",
                   ergebnis_client_uuid=_erkundungsergebnis(client, dispatch_id)).status_code == 200
    assert client.get("/einheit/api/zustand").json()["kategorien"]["aktuell"]["dispatch_id"] == ids["d1"]


def test_veraltete_bestaetigung_unterbricht_nie_eine_inzwischen_aktive_bearbeitung(client, setup_db):
    ids = _vorbereiten(client)
    assert _status(client, ids["d1"], "vor_ort").status_code == 200
    response = _status(client, ids["d1"], "bestaetigt", ausgangsstatus="zugewiesen")
    assert response.status_code == 409 and response.json()["code"] == "status_geaendert"
    assert client.get("/einheit/api/zustand").json()["kategorien"]["aktuell"]["dispatch_id"] == ids["d1"]


def test_geaendert_zurueckgezogen_und_idempotente_offline_bestaetigung(client, setup_db):
    ids = _vorbereiten(client)
    with _db() as db:
        dispatch = db.get(EinheitSiteDispatch, ids["d1"])
        dispatch.auftrag, dispatch.version = "Neuer vollständiger Auftrag", 2
        db.commit()
    response = _status(client, ids["d1"], "bestaetigt", auftrag_version=1, ausgangsstatus="zugewiesen")
    assert response.status_code == 409 and response.json()["code"] == "auftrag_geaendert"
    detail = client.get(f"/einheit/api/auftrag/{ids['d1']}").json()
    assert detail["auftrag"]["auftrag"] == "Neuer vollständiger Auftrag"
    uuid = str(uuid4())
    for _ in range(2):
        assert _status(client, ids["d1"], "bestaetigt", auftrag_version=2,
                       ausgangsstatus="zugewiesen", client_uuid=uuid).status_code == 200
    with _db() as db:
        assert db.query(SiteLogEntry).filter_by(incident_site_id=ids["a"], kind="einheit").count() == 1
        dispatch = db.get(EinheitSiteDispatch, ids["offen"][1])
        dispatch.withdrawn_at = datetime.now(UTC)
        db.commit()
    detail = client.get(f"/einheit/api/auftrag/{ids['offen'][1]}").json()
    assert detail["auftrag"]["withdrawn_at"] and not detail["auftrag"]["erlaubte_status"]
    response = _status(client, ids["offen"][1], "bestaetigt", auftrag_version=1)
    assert response.status_code == 409 and response.json()["code"] == "auftrag_zurueckgezogen"


def test_aktiver_wechsel_broadcastet_beide_stellen_und_sichert_entscheidung(client, setup_db, monkeypatch):
    import app.routers.ui_einheit as router

    ids = _vorbereiten(client)
    events = []

    async def send(lage_id, event):
        events.append(event)

    monkeypatch.setattr(router, "broadcast_lage", send)
    a, b, c = ids["offen"]
    assert _status(client, a, "vor_ort").status_code == 200
    response = _status(client, b, "anfahrt")
    assert response.status_code == 409 and response.json()["details"]["dispatch_id"] == a
    events.clear()
    assert _status(client, b, "anfahrt", unterbrechen=True, aktiver_dispatch_id=a).status_code == 200
    assert {e["site_id"] for e in events if e["type"] == "site:card_changed"} == {ids["a"], ids["b"]}
    assert {e["dispatch_id"] for e in events if e["type"] == "einheit:changed"} == {a, b}
    assert _status(client, c, "anfahrt", unterbrechen=True, aktiver_dispatch_id=b).status_code == 200
    # Die Entscheidung bezog sich auf A; inzwischen läuft C. C darf nicht still unterbrochen werden.
    response = _status(client, b, "vor_ort", unterbrechen=True, aktiver_dispatch_id=a)
    assert response.status_code == 409 and response.json()["details"]["dispatch_id"] == c
    with _db() as db:
        assert [db.get(EinheitSiteDispatch, d).einheit_status for d in (a, b, c)] == [
            "bestaetigt", "bestaetigt", "anfahrt",
        ]


def test_neuer_dringender_auftrag_veraendert_laufenden_nicht(client, setup_db):
    ids = _vorbereiten(client)
    a, b, c = ids["offen"]
    assert _status(client, b, "in_arbeit").status_code == 200
    with _db() as db:
        urgent = IncidentSite(org_id=db.get(IncidentSite, ids["a"]).org_id, major_incident_id=ids["lage"],
                              bezeichnung="Dringender neuer Auftrag", priority=SitePriority.sofort)
        db.add(urgent)
        db.flush()
        dispatch = EinheitSiteDispatch(einheit_id=ids["e1"], site_id=urgent.id, dispatched_at=datetime.now(UTC))
        db.add(dispatch)
        db.commit()
        neu_id = dispatch.id
    assert _status(client, neu_id, "bestaetigt").status_code == 200
    data = client.get("/einheit/api/zustand").json()
    assert data["kategorien"]["aktuell"]["dispatch_id"] == b
    assert [d["dispatch_id"] for d in data["kategorien"]["weitere"]] == [a, c, neu_id]


def test_snapshot_wiederholung_rollt_auch_journal_und_status_vollstaendig_zurueck(client, setup_db, monkeypatch):
    from sqlalchemy.exc import OperationalError

    import app.routers.ui_einheit as router

    ids = _vorbereiten(client)
    original = router.setze_einheit_status
    versuche = []

    def gleichzeitig(*args, **kwargs):
        result = original(*args, **kwargs)
        versuche.append(result)
        if len(versuche) == 1:
            raise OperationalError("Statuskonflikt nach Mutation", {}, Exception(1020, "Record changed"))
        return result

    monkeypatch.setattr(router, "setze_einheit_status", gleichzeitig)
    assert _status(client, ids["d1"], "bestaetigt", auftrag_version=1).status_code == 200
    assert len(versuche) == 2
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == "bestaetigt"
        assert db.query(SiteLogEntry).filter_by(incident_site_id=ids["a"], kind="einheit").count() == 1


@pytest.mark.parametrize("zugang", ["gk", "qr"])
def test_gk_qr_uebernehmen_mehrere_eigene_auftraege_aber_keine_fremden(client, setup_db, zugang):
    if zugang == "gk":
        from tests.test_einheit_zugang import _zugang_mit_auftrag
        cookie, eigener, einheit_id, _ = _zugang_mit_auftrag()
        client.cookies.clear()
        client.cookies.set("ec_gk", cookie)
    else:
        from tests.test_gk_qr_zugang import _eingeloest
        ids = _eingeloest(client)
        eigener, einheit_id = ids["eigener"], ids["einheit"]
    with _db() as db:
        einheit = db.get(LageEinheit, einheit_id)
        site_id = db.get(EinheitSiteDispatch, eigener).site_id
        weiterer = EinheitSiteDispatch(einheit_id=einheit_id, site_id=site_id, dispatched_at=datetime.now(UTC))
        fremde = LageEinheit(lage_id=einheit.lage_id, label="Fremde Einheit")
        db.add_all([weiterer, fremde])
        db.flush()
        fremder = EinheitSiteDispatch(einheit_id=fremde.id, site_id=site_id, dispatched_at=datetime.now(UTC))
        db.add(fremder)
        db.commit()
        weiterer_id, fremder_id = weiterer.id, fremder.id
    client.get("/einheit/api/zustand")
    for dispatch_id in (eigener, weiterer_id):
        detail = client.get(f"/einheit/api/auftrag/{dispatch_id}").json()
        assert detail["andere_einheiten_eingeschraenkt"] and not detail["andere_einheiten"]
        assert _status(client, dispatch_id, "bestaetigt", auftrag_version=1).status_code == 200
    assert client.get(f"/einheit/api/auftrag/{fremder_id}").status_code == 404
    assert _status(client, fremder_id, "bestaetigt").status_code == 404
