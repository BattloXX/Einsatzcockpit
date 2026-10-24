"""Einsatzstellenverlauf: Herkunft, Sichtgrenzen, stabile Seiten und Bestätigung."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.major_incident import EinheitAktion, EinheitSiteDispatch, SiteLogEntry, SiteMedia
from tests.test_einheit_api import _daten, _db, _device, _h


def _verlauf(client, **params):
    return client.get("/einheit/api/meldungen", params=params)


def _bestand(client):
    ids = _daten()
    with _db() as db:
        d = db.get(EinheitSiteDispatch, ids["d1"])
        d.dispatched_at = datetime(2026, 1, 1, 9, tzinfo=UTC)
        d.auftrag = "Keller kontrollieren"
        d.geaendert_at = datetime(2026, 1, 1, 10, tzinfo=UTC)
        d.version = 2
        for minute, kind, text, einheit_id in [
            (1, "note", "Ist die Zufahrt frei?", None),
            (2, "lagemeldung", "Akute Gefahr festgestellt\nGasgeruch", ids["e1"]),
            (3, "lagemeldung", "Andere Einheit geheim", db.get(EinheitSiteDispatch, ids["d2"]).einheit_id),
            (4, "media", "Foto hochgeladen: technisch.jpg", ids["e1"]),
            (5, "status", "Debug: id=987", None),
            (6, "einheit", "TLF: Vor Ort", ids["e1"]),
            (7, "einheit", 'TLF von "A" abgezogen (automatisch nach Abschluss)', ids["e1"]),
        ]:
            db.add(SiteLogEntry(incident_site_id=ids["a"], ts=datetime(2026, 1, 1, 10, minute, tzinfo=UTC),
                                kind=kind, text=text, einheit_id=einheit_id))
        db.add(SiteMedia(incident_site_id=ids["a"], org_id=d.site.org_id, media_type="image",
                         original_filename="bild.png", stored_filename="bild.png", einheit_id=ids["e1"],
                         uploaded_at=datetime(2026, 1, 1, 10, 4, tzinfo=UTC), kommentar="Kellerfoto"))
        db.add(SiteLogEntry(incident_site_id=ids["b"], kind="note", text="Nicht zugewiesene Stelle geheim"))
        db.commit()
    _device(client, ids)
    return ids


def test_lesbarer_verlauf_mit_foto_frage_gefahr_und_kompaktem_status(client, setup_db):
    ids = _bestand(client)
    response = _verlauf(client, dispatch_id=ids["d1"], mit_status=True)
    assert response.status_code == 200, response.text
    rows = response.json()["eintraege"]
    assert len(rows) == 6  # Zuweisung, Änderung, Frage, Gefahr, Bild, Status.
    assert [r["ts"] for r in rows] == sorted([r["ts"] for r in rows], reverse=True)
    texte = "\n".join(r["text"] for r in rows)
    assert all(text not in texte for text in ("geheim", "technisch.jpg", "Debug", "automatisch"))
    assert next(r for r in rows if "Zufahrt" in r["text"])["hervorhebung"] == "rueckfrage"
    assert next(r for r in rows if "Gasgeruch" in r["text"])["hervorhebung"] == "gefahr"
    status = next(r for r in rows if r["art"] == "status")
    assert status["kompakt"] and status["text"] == "Vor Ort" and status["status"] == "uebermittelt"
    foto = next(r for r in rows if r["art"] == "foto")
    assert foto["foto"]["url"].startswith("/einheit/medien/") and foto["text"] == "Kellerfoto"
    assert foto["dispatch_id"] is None and foto["bezug"] == "Meldung zur Einsatzstelle"
    assert all(r["site_id"] == ids["a"] for r in rows)
    assert response.headers["cache-control"] == "no-store"
    zentral = _verlauf(client).json()["eintraege"]
    assert not any(r["art"] == "status" for r in zentral)


def test_stabile_pagination_auch_bei_gleichen_zeiten_und_neuer_meldung(client, setup_db):
    ids = _bestand(client)
    with _db() as db:
        for i in range(38):
            db.add(SiteLogEntry(incident_site_id=ids["a"], einheit_id=ids["e1"], kind="note",
                                text=f"Meldung {i}", ts=datetime(2026, 2, 1, 12, tzinfo=UTC)))
        db.commit()
    erste = _verlauf(client, limit=7).json()
    ergebnis = list(erste["eintraege"])
    with _db() as db:
        db.add(SiteLogEntry(incident_site_id=ids["a"], einheit_id=ids["e1"], kind="note",
                            text="Neue Meldung", ts=datetime(2026, 3, 1, 12, tzinfo=UTC)))
        db.commit()
    cursor = erste["cursor"]
    while cursor:
        antwort = _verlauf(client, limit=7, cursor=cursor)
        assert antwort.status_code == 200, antwort.text
        data = antwort.json()
        ergebnis.extend(data["eintraege"])
        cursor = data["cursor"]
    assert len(ergebnis) == len({r["id"] for r in ergebnis}) == 43
    assert sum(r["text"].startswith("Meldung ") for r in ergebnis) == 38
    assert _verlauf(client).json()["eintraege"][0]["text"] == "Neue Meldung"


@pytest.mark.parametrize("altes_fotoprotokoll", [False, True])
def test_uuid_und_genauer_auftrag_aus_existierendem_aktionsprotokoll(
    client, setup_db, tmp_path, monkeypatch, altes_fotoprotokoll,
):
    from app.services import lage_media_service
    from tests.test_einheit_api import _png

    monkeypatch.setattr(lage_media_service, "_LAGE_MEDIA_DIR", str(tmp_path))
    ids = _bestand(client)
    with _db() as db:
        zweiter = EinheitSiteDispatch(einheit_id=ids["e1"], site_id=ids["a"], dispatched_at=datetime.now(UTC),
                                      auftrag="Zweite Erkundung", auftragsart="erkundung")
        db.add(zweiter)
        db.commit()
        dispatch_id = zweiter.id
    uuid = str(uuid4())
    assert client.post(f"/einheit/api/auftrag/{dispatch_id}/meldung", headers=_h(client), json={
        "client_uuid": uuid, "art": "lagemeldung", "text": "Zweiter Auftrag geprüft",
        "erfasst_at": "2026-01-01T10:08:00Z",
    }).status_code == 200
    foto_uuid = str(uuid4())
    assert client.post(f"/einheit/api/auftrag/{dispatch_id}/foto", headers=_h(client),
                       data={"client_uuid": foto_uuid},
                       files={"file": ("test.png", _png(), "image/png")}).status_code == 200
    if altes_fotoprotokoll:
        with _db() as db:
            db.query(EinheitAktion).filter_by(client_uuid=foto_uuid).one().entity_id = None
            db.commit()
    data = _verlauf(client, dispatch_id=ids["d1"]).json()["eintraege"]
    for key in (uuid, foto_uuid):
        row = next(r for r in data if r["client_uuid"] == key)
        assert row["dispatch_id"] == dispatch_id and row["auftragsart"] == "erkundung"
        assert row["site_id"] == ids["a"] and row["eigene"] and row["status"] == "uebermittelt"
    legacy = next(r for r in data if r["text"].startswith("Akute Gefahr"))
    assert legacy["dispatch_id"] is None  # Keinen zweiten Auftrag an dieselbe Stelle hineininterpretieren.


@pytest.mark.parametrize("zugang", ["geraet", "gk", "qr"])
def test_keine_meldungen_oder_fotos_fremder_einheiten_und_keine_fremden_auftraege(client, setup_db, zugang):
    if zugang == "geraet":
        ids = _bestand(client)
        dispatch_id, eigener, fremd = ids["d1"], ids["e1"], ids["d2"]
    elif zugang == "gk":
        from tests.test_einheit_zugang import _zugang_mit_auftrag
        cookie, dispatch_id, eigener, _ = _zugang_mit_auftrag()
        client.cookies.clear()
        client.cookies.set("ec_gk", cookie)
        fremd = dispatch_id + 100000
    else:
        from tests.test_gk_qr_zugang import _eingeloest
        ids = _eingeloest(client)
        dispatch_id, eigener, fremd = ids["eigener"], ids["einheit"], ids["fremder"]
    with _db() as db:
        site = db.get(EinheitSiteDispatch, dispatch_id).site
        for einheit_id, text in ((eigener, "Eigene Meldung"), (None, "Rückfrage der Einsatzleitung?")):
            db.add(SiteLogEntry(incident_site_id=site.id, kind="note", text=text, einheit_id=einheit_id))
        db.commit()
    antwort = _verlauf(client, dispatch_id=dispatch_id)
    assert antwort.status_code == 200, antwort.text
    texte = "\n".join(r["text"] for r in antwort.json()["eintraege"])
    assert "Eigene Meldung" in texte and "geheim" not in texte
    assert ("Rückfrage der Einsatzleitung?" in texte) == (zugang == "geraet")
    assert _verlauf(client, dispatch_id=fremd).status_code == 404


@pytest.mark.parametrize("cursor", ["ungueltig!", "W10", "e30", "WyIyMDI2LTAxLTAxVDAwOjAwOjAwWiIsMSwxXQ"])
def test_ungueltiger_cursor_veraendert_die_sichtgrenzen_nicht(client, setup_db, cursor):
    _bestand(client)
    assert _verlauf(client, cursor=cursor).status_code == 422


def test_status_und_zuordnung_bei_offline_zeit_und_rueckzug(client, setup_db):
    ids = _bestand(client)
    zeit = datetime.now(UTC) - timedelta(minutes=3)
    key = str(uuid4())
    result = client.post(f"/einheit/api/auftrag/{ids['d1']}/status", headers=_h(client), json={
        "client_uuid": key, "status": "bestaetigt", "erfasst_at": zeit.isoformat(),
    })
    assert result.status_code == 200
    row = next(r for r in _verlauf(client, mit_status=True).json()["eintraege"] if r["client_uuid"] == key)
    assert row["dispatch_id"] == ids["d1"] and row["ts"].startswith(zeit.strftime("%Y-%m-%dT%H:%M"))
    with _db() as db:
        db.get(EinheitSiteDispatch, ids["d1"]).withdrawn_at = datetime.now(UTC)
        db.commit()
    rows = _verlauf(client, dispatch_id=ids["d1"], mit_status=True).json()["eintraege"]
    assert any(r["art"] == "rueckzug" for r in rows)
    assert not any(r["absender"] == "Einsatzleitung" and r["art"] == "meldung" for r in rows)


def test_notiz_hat_vor_der_aktionsantwort_eine_bestaendige_chronikreferenz(client, setup_db):
    ids = _bestand(client)
    key = str(uuid4())
    result = client.post(f"/einheit/api/auftrag/{ids['d1']}/meldung", headers=_h(client), json={
        "client_uuid": key, "art": "note", "text": "Schlüssel an Einsatzleitung übergeben",
    })
    assert result.status_code == 200 and isinstance(result.json()["entity_id"], int)
    rows = _verlauf(client).json()["eintraege"]
    notiz = next(row for row in rows if row["client_uuid"] == key)
    assert notiz["dispatch_id"] == ids["d1"] and notiz["eigene"] and notiz["status"] == "uebermittelt"
