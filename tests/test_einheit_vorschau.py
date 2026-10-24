"""Vollständige Vorschau vor Bestätigung; Medien bleiben an eigene Aufträge gebunden."""
from datetime import UTC, datetime

import pytest

from app.config import settings
from app.models.incident import Incident
from app.models.major_incident import EinheitSiteDispatch, IncidentSite, LageEinheit, SiteLogEntry, SiteMedia
from app.models.master import OrgSettings, SystemSettings
from app.models.objekt import (
    GefahrenKatalog,
    MerkmalKatalog,
    Objekt,
    ObjektBMA,
    ObjektDokument,
    ObjektDokumentSeite,
    ObjektEinsatz,
    ObjektGefahr,
    ObjektMerkmal,
)
from tests.test_einheit_api import _daten, _db, _device


def _plaene(ids, tmp_path):
    with _db() as db:
        site = db.get(IncidentSite, ids["a"])
        org_id = site.org_id
        flag = db.get(SystemSettings, "objekt_module_enabled")
        if flag:
            flag.value = "true"
        else:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        org = db.query(OrgSettings).filter_by(org_id=org_id).first()
        if not org:
            org = OrgSettings(org_id=org_id)
            db.add(org)
        org.objekt_module_enabled = True
        incident = Incident(primary_org_id=org_id)
        db.add(incident)
        db.flush()
        site.incident_id = incident.id
        objekt = Objekt(org_id=org_id, name="Schulgebäude", status="freigegeben",
                        informationen="Schlüssel bei der BMZ", anfahrtsweg="Zufahrt über Nordtor")
        katalog = GefahrenKatalog(org_id=org_id, name="Gasflaschen")
        db.add_all([objekt, katalog])
        db.flush()
        db.add(ObjektEinsatz(org_id=org_id, objekt_id=objekt.id, incident_id=incident.id,
                            quelle="manuell", status="bestaetigt"))
        db.add(ObjektGefahr(org_id=org_id, objekt_id=objekt.id, gefahr_id=katalog.id, detail="Im Keller"))
        merkmal = MerkmalKatalog(org_id=org_id, name="Löschwassereinspeisung")
        db.add(merkmal)
        db.flush()
        db.add(ObjektMerkmal(org_id=org_id, objekt_id=objekt.id, merkmal_id=merkmal.id, hinweis="Westseite"))
        db.add(ObjektBMA(org_id=org_id, objekt_id=objekt.id, bmz_standort="Haupteingang"))
        seiten_ids = []
        for index, (freigabe, aktuell, status) in enumerate([
            ("freigegeben", True, "fertig"), ("wartet_freigabe", True, "fertig"),
            ("freigegeben", False, "fertig"), ("freigegeben", True, "verarbeitung"),
        ]):
            dokument = ObjektDokument(org_id=org_id, objekt_id=objekt.id, dateiname_original=f"Plan{index}.pdf",
                                       pfad=f"original{index}.pdf", freigabe_status=freigabe,
                                       ist_aktuelle_version=aktuell, status=status)
            db.add(dokument)
            db.flush()
            seite = ObjektDokumentSeite(org_id=org_id, objekt_id=objekt.id, dokument_id=dokument.id,
                                        seiten_nr=1, titel=f"Brandschutzplan {index}",
                                        einzel_pdf_pfad=f"seite{index}.pdf")
            db.add(seite)
            db.flush()
            seiten_ids.append(seite.id)
            (tmp_path / f"seite{index}.pdf").write_bytes(b"%PDF-1.4 Testplan")
        # Ein Entwurf darf auch mit bestaetigter Verknuepfung nicht im Einsatz erscheinen.
        entwurf = Objekt(org_id=org_id, name="Ungeprüfte Angaben", status="entwurf")
        db.add(entwurf)
        db.flush()
        db.add(ObjektEinsatz(org_id=org_id, objekt_id=entwurf.id, incident_id=incident.id,
                            quelle="manuell", status="bestaetigt"))
        # Freigegeben, aber ohne Bezug zum Auftrag: die erratene Seiten-ID genügt nicht.
        unverbunden = Objekt(org_id=org_id, name="Anderer Einsatz", status="freigegeben")
        db.add(unverbunden)
        db.flush()
        dokument = ObjektDokument(org_id=org_id, objekt_id=unverbunden.id, dateiname_original="Fremder Plan.pdf",
                                   pfad="fremd.pdf", status="fertig")
        db.add(dokument)
        db.flush()
        seite = ObjektDokumentSeite(org_id=org_id, objekt_id=unverbunden.id, dokument_id=dokument.id,
                                    seiten_nr=1, einzel_pdf_pfad="fremd.pdf")
        db.add(seite)
        db.flush()
        seiten_ids.append(seite.id)
        (tmp_path / "fremd.pdf").write_bytes(b"%PDF-1.4 Nicht berechtigt")
        db.commit()
        return seiten_ids, objekt.id


@pytest.mark.parametrize("zugang", ["geraet", "gk", "qr"])
def test_vorschau_vor_bestaetigung_mit_objekthinweisen_und_nur_freigegebenen_plaenen(
    client, setup_db, tmp_path, monkeypatch, zugang,
):
    if zugang == "geraet":
        ids = _daten()
        _device(client, ids)
    elif zugang == "gk":
        from tests.test_einheit_zugang import _zugang_mit_auftrag
        cookie, dispatch_id, einheit_id, _ = _zugang_mit_auftrag()
        client.cookies.clear()
        client.cookies.set("ec_gk", cookie)
        with _db() as db:
            ids = {"a": db.get(EinheitSiteDispatch, dispatch_id).site_id, "d1": dispatch_id, "e1": einheit_id}
    else:
        from tests.test_gk_qr_zugang import _eingeloest
        qr = _eingeloest(client)
        with _db() as db:
            ids = {"a": db.get(EinheitSiteDispatch, qr["eigener"]).site_id, "d1": qr["eigener"], "e1": qr["einheit"]}
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    seiten, objekt_id = _plaene(ids, tmp_path)
    url = f"/einheit/api/auftrag/{ids['d1']}"
    data = client.get(url).json()
    assert data["auftrag"]["einheit_status"] == "zugewiesen"
    assert len(data["objekte"]) == 1
    objekt = data["objekte"][0]
    assert objekt["informationen"] == "Schlüssel bei der BMZ" and objekt["anfahrtsweg"] == "Zufahrt über Nordtor"
    assert objekt["gefahren"] == [{"name": "Gasflaschen", "detail": "Im Keller", "un_nummer": None}]
    assert objekt["hinweise"] == [
        {"name": "Löschwassereinspeisung", "text": "Westseite"}, {"name": "Brandmeldezentrale", "text": "Haupteingang"},
    ]
    assert [seite["id"] for seite in objekt["seiten"]] == seiten[:1]
    basis = f"/einheit/medien/auftrag/{ids['d1']}/plan"
    assert client.get(f"{basis}/{seiten[0]}/pdf").content == b"%PDF-1.4 Testplan"
    for seite_id in seiten[1:]:
        assert client.get(f"{basis}/{seite_id}/pdf").status_code == 404
    with _db() as db:
        org_id = db.get(IncidentSite, ids["a"]).org_id
        db.query(OrgSettings).filter_by(org_id=org_id).one().objekt_module_enabled = False
        db.commit()
    assert client.get(url).json()["objekte"] == []
    assert client.get(f"{basis}/{seiten[0]}/pdf").status_code == 404
    with _db() as db:
        db.query(OrgSettings).filter_by(org_id=org_id).one().objekt_module_enabled = True
        db.commit()
    with _db() as db:
        db.get(EinheitSiteDispatch, ids["d1"]).withdrawn_at = datetime.now(UTC)
        db.commit()
    assert client.get(url).json()["auftrag"]["erlaubte_status"] == []
    assert client.get(f"{basis}/{seiten[0]}/pdf").status_code == 404
    # Auch ein freigegebenes Objekt gibt ohne aktuelle eigene Disposition keine Medien frei.
    with _db() as db:
        assert db.get(Objekt, objekt_id).status == "freigegeben"


def test_gk_medien_und_meldungen_bleiben_auf_eigene_einheit_beschraenkt(client, setup_db, tmp_path, monkeypatch):
    import app.routers.ui_einheit as router
    from tests.test_einheit_zugang import _zugang_mit_auftrag

    cookie, dispatch_id, einheit_id, _ = _zugang_mit_auftrag()
    with _db() as db:
        dispatch = db.get(EinheitSiteDispatch, dispatch_id)
        fremde = LageEinheit(lage_id=dispatch.einheit.lage_id, label="Fremde")
        db.add(fremde)
        db.flush()
        bilder = [SiteMedia(incident_site_id=dispatch.site_id, einheit_id=e,
                            stored_filename="test.jpg", original_filename="test.jpg", media_type="image")
                  for e in (einheit_id, fremde.id)]
        db.add_all(bilder)
        db.add(SiteLogEntry(incident_site_id=dispatch.site_id, einheit_id=einheit_id, text="Eigene ältere Meldung"))
        for _ in range(55):
            db.add(SiteLogEntry(incident_site_id=dispatch.site_id, einheit_id=fremde.id, text="Nicht berechtigt"))
        db.commit()
        bild_ids = [bild.id for bild in bilder]
    bild_path = tmp_path / "test.jpg"
    bild_path.write_bytes(b"bild")
    monkeypatch.setattr(router, "site_media_path", lambda media: bild_path)
    client.cookies.clear()
    client.cookies.set("ec_gk", cookie)
    data = client.get(f"/einheit/api/auftrag/{dispatch_id}").json()
    assert [bild["id"] for bild in data["fotos"]] == bild_ids[:1]
    assert [log["text"] for log in data["chronik"]] == ["Eigene ältere Meldung"]
    assert client.get(f"/einheit/medien/{bild_ids[0]}").status_code == 200
    assert client.get(f"/einheit/medien/{bild_ids[1]}").status_code == 404
