"""Schnellmeldungen bleiben bestehende, zugeordnete und idempotente Lagemeldungen."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.security import sign_session
from app.models.major_incident import EinheitSiteDispatch, SiteLogEntry, SitePhase
from app.services.site_log_service import SCHNELLMELDUNGEN, aktuelle_dringende_meldungen, meldung_hervorhebung
from tests.test_einheit_api import _als, _daten, _db, _device, _erkundungsergebnis, _h


def _meldung(client, dispatch_id, **body):
    return client.post(f"/einheit/api/auftrag/{dispatch_id}/meldung", headers=_h(client),
                       json={"client_uuid": str(uuid4()), "art": "lagemeldung", **body})


def _vorbereiten(client, art="erkundung"):
    ids = _daten()
    _device(client, ids)
    with _db() as db:
        d = db.get(EinheitSiteDispatch, ids["d1"])
        d.auftragsart, d.einheit_status = art, "vor_ort"
        d.einheit.incident_site_id = d.site_id
        db.commit()
    return ids


@pytest.mark.parametrize("art,code,label", [
    (art, code, label) for art, meldungen in SCHNELLMELDUNGEN.items() for code, label, aktion in meldungen
    if aktion == "meldung"
])
def test_schnellmeldung_ohne_freitext_ist_eine_idempotente_lagemeldung(client, setup_db, art, code, label):
    ids = _vorbereiten(client, art)
    key = str(uuid4())
    for _ in range(2):
        response = _meldung(client, ids["d1"], schnellmeldung=code, client_uuid=key)
        assert response.status_code == 200, response.text
    with _db() as db:
        entry = db.query(SiteLogEntry).filter_by(incident_site_id=ids["a"], kind="lagemeldung").one()
        assert entry.text == label and entry.einheit_id == ids["e1"]
        d = db.get(EinheitSiteDispatch, ids["d1"])
        assert (d.einheit_status, d.version, d.site.phase) == ("vor_ort", 1, SitePhase.disponiert)
        assert d.letzte_rueckmeldung_at is not None
    data = client.get(f"/einheit/api/auftrag/{ids['d1']}").json()
    assert data["chronik"][0]["hervorhebung"] == meldung_hervorhebung(label)


@pytest.mark.parametrize("code", ["arbeit_laeuft", "auftrag_erledigt", "nicht_durchfuehrbar", "unbekannt"])
def test_falsche_auftragsart_und_statusaktionen_sind_keine_schnellmeldungen(client, setup_db, code):
    ids = _vorbereiten(client)
    response = _meldung(client, ids["d1"], schnellmeldung=code)
    assert response.status_code == 422
    with _db() as db:
        assert db.query(SiteLogEntry).filter_by(incident_site_id=ids["a"]).count() == 0


@pytest.mark.parametrize("code,text,erwartet", [
    (None, "", 422), ("massnahmen_erforderlich", "   ", 422), ("akute_gefahr", "!", 422),
    ("weitere_erkundung", "  ", 422), ("keine_massnahmen", "", 200),
    ("massnahmen_erforderlich", "Keller abpumpen", 200), ("akute_gefahr", "Gasgeruch im Keller", 200),
])
def test_erkundungsergebnis_verlangt_eine_aussage(client, setup_db, code, text, erwartet):
    ids = _vorbereiten(client)
    response = _meldung(client, ids["d1"], schnellmeldung=code, text=text, erkundungsergebnis=True)
    assert response.status_code == erwartet, response.text
    with _db() as db:
        assert db.query(SiteLogEntry).filter_by(incident_site_id=ids["a"]).count() == (erwartet == 200)


def test_abschluss_braucht_ergebnis_desselben_auftrags_und_derselben_version(client, setup_db):
    ids = _vorbereiten(client)
    def abschliessen(ref=None):
        return client.post(f"/einheit/api/auftrag/{ids['d1']}/status", headers=_h(client), json={
            "client_uuid": str(uuid4()), "status": "abgeschlossen", "ergebnis_client_uuid": ref,
        })
    assert abschliessen().status_code == 422
    normal_uuid = str(uuid4())
    assert _meldung(client, ids["d1"], schnellmeldung="keine_massnahmen", client_uuid=normal_uuid).status_code == 200
    assert abschliessen(normal_uuid).status_code == 422  # Eine normale Rückmeldung ist kein Abschlussbericht.
    with _db() as db:
        zweiter = EinheitSiteDispatch(einheit_id=ids["e1"], site_id=ids["a"],
                                      dispatched_at=datetime.now(UTC), auftragsart="erkundung")
        db.add(zweiter)
        db.commit()
        zweiter_id = zweiter.id
    assert abschliessen(_erkundungsergebnis(client, zweiter_id)).status_code == 422
    ref = _erkundungsergebnis(client, ids["d1"])
    with _db() as db:
        db.get(EinheitSiteDispatch, ids["d1"]).version = 2
        db.commit()
    assert abschliessen(ref).status_code == 422
    ref = _erkundungsergebnis(client, ids["d1"])
    assert abschliessen(ref).status_code == 200
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == "abgeschlossen"


def test_gefahr_und_unterstuetzung_erscheinen_im_board_ohne_prioritaetsaenderung(client, setup_db):
    ids = _vorbereiten(client)
    assert _meldung(client, ids["d1"], schnellmeldung="akute_gefahr", text="Gasgeruch im Keller").status_code == 200
    _als(client, sign_session(ids["admin"]))
    card = client.get(f"/lage/{ids['lage']}/stellen/{ids['a']}/card")
    detail = client.get(f"/lage/{ids['lage']}/stellen/{ids['a']}")
    assert card.status_code == detail.status_code == 200
    assert 'einheit-meldung--gefahr' in card.text and "Gasgeruch im Keller" in card.text
    assert 'site-log__entry--gefahr' in detail.text
    for url in (f"/lage/{ids['lage']}", f"/lage/{ids['lage']}/phase/disponiert/inhalt"):
        response = client.get(url)
        assert response.status_code == 200
        assert 'einheit-meldung--gefahr' in response.text and "Gasgeruch im Keller" in response.text
    _device(client, ids)
    with _db() as db:
        d = db.get(EinheitSiteDispatch, ids["d1"])
        prio = d.site.priority
        d.auftragsart = "abarbeitung"
        db.commit()
    assert _meldung(client, ids["d1"], schnellmeldung="unterstuetzung",
                    text="Weitere Pumpe benötigt").status_code == 200
    _als(client, sign_session(ids["admin"]))
    card = client.get(f"/lage/{ids['lage']}/stellen/{ids['a']}/card")
    assert 'einheit-meldung--unterstuetzung' in card.text and "Weitere Pumpe benötigt" in card.text
    _device(client, ids)
    with _db() as db:
        site = db.get(EinheitSiteDispatch, ids["d1"]).site
        assert site.priority == prio
        hinweise = aktuelle_dringende_meldungen(site.log_entries)
        assert len(hinweise) == 1 and meldung_hervorhebung(hinweise[0].text) == "unterstuetzung"
    assert _meldung(client, ids["d1"], schnellmeldung="arbeit_laeuft").status_code == 200
    with _db() as db:
        assert not aktuelle_dringende_meldungen(db.get(EinheitSiteDispatch, ids["d1"]).site.log_entries)


@pytest.mark.parametrize("zugang", ["geraet", "gk", "qr"])
def test_schnellmeldungen_behalten_zuweisungsgrenzen(client, setup_db, zugang):
    if zugang == "geraet":
        ids = _vorbereiten(client, "abarbeitung")
        eigener, fremder = ids["d1"], ids["d2"]
    elif zugang == "gk":
        from tests.test_einheit_zugang import _zugang_mit_auftrag
        cookie, eigener, _, _ = _zugang_mit_auftrag()
        client.cookies.clear()
        client.cookies.set("ec_gk", cookie)
        assert client.get("/einheit/api/zustand").status_code == 200
        fremder = eigener + 10000
    else:
        from tests.test_gk_qr_zugang import _eingeloest
        ids = _eingeloest(client)
        eigener, fremder = ids["eigener"], ids["fremder"]
    assert _meldung(client, eigener, schnellmeldung="unterstuetzung").status_code == 200
    assert _meldung(client, fremder, schnellmeldung="unterstuetzung").status_code in (403, 404)


@pytest.mark.parametrize("schnellmeldung", [None, "akute_gefahr"])
def test_freitext_und_struktur_bleiben_erhalten_und_leere_meldung_wird_abgewiesen(client, setup_db, schnellmeldung):
    ids = _vorbereiten(client)
    assert _meldung(client, ids["d1"], text="  ").status_code == 400
    assert _meldung(client, ids["d1"], felder={"lage": 12}).status_code == 400
    assert _meldung(client, ids["d1"], schnellmeldung=schnellmeldung, text="Gasgeruch",
                    felder={"gefahren": "Zündquellen fernhalten"}).status_code == 200
    with _db() as db:
        text = db.query(SiteLogEntry).filter_by(incident_site_id=ids["a"]).one().text
        assert text == (("Akute Gefahr festgestellt\n" if schnellmeldung else "")
                        + "Gasgeruch\nFestgestellte Gefahren: Zündquellen fernhalten")


@pytest.mark.parametrize("grund", [None, "", "   ", 12])
def test_nicht_durchfuehrbar_ohne_begruendung_wird_auch_serverseitig_abgewiesen(client, setup_db, grund):
    ids = _vorbereiten(client, "abarbeitung")
    result = client.post(f"/einheit/api/auftrag/{ids['d1']}/status", headers=_h(client), json={
        "status": "nicht_durchfuehrbar", "client_uuid": str(uuid4()), "grund": grund,
    })
    assert result.status_code == 422 and result.json()["code"] == "grund_erforderlich"
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == "vor_ort"


def test_gefahr_nach_rueckzug_bleibt_erkennbar_und_bericht_replay_nach_aenderung_bleibt_idempotent(client, setup_db):
    ids = _vorbereiten(client)
    with _db() as db:
        db.get(EinheitSiteDispatch, ids["d1"]).withdrawn_at = datetime.now(UTC)
        db.commit()
    key = str(uuid4())
    assert _meldung(client, ids["d1"], schnellmeldung="akute_gefahr", client_uuid=key).status_code == 200
    with _db() as db:
        db.get(EinheitSiteDispatch, ids["d1"]).auftragsart = "abarbeitung"
        db.commit()
    replay = _meldung(client, ids["d1"], schnellmeldung="akute_gefahr", client_uuid=key)
    assert replay.status_code == 200 and replay.json()["hervorhebung"] == "gefahr"
    with _db() as db:
        entry = db.query(SiteLogEntry).filter_by(incident_site_id=ids["a"], kind="lagemeldung").one()
        assert entry.text == "Akute Gefahr festgestellt\n(nach Rückzug eingegangen)"
        assert meldung_hervorhebung(entry.text) == "gefahr"


def test_ungueltiges_ergebnis_in_strukturiertem_feld_hat_keine_aussage(client, setup_db):
    ids = _vorbereiten(client)
    result = _meldung(client, ids["d1"], schnellmeldung="akute_gefahr", erkundungsergebnis=True,
                      felder={"gefahren": "!!!"})
    assert result.status_code == 422
