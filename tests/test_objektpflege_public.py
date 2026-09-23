"""HTTP-Integrationstests fuer den loginfreien Objektpflege-Link."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.kontakt import Kontakt
from app.models.master import FireDept
from app.models.objekt import (
    OBJEKT_STATUS_FREIGEGEBEN,
    KontaktAenderungsvorschlag,
    GefahrenKatalog,
    Objekt,
    ObjektBMA,
    ObjektChange,
    ObjektDokument,
    ObjektGefahr,
    ObjektKontakt,
    ObjektMerkmal,
    ObjektPflegeauftrag,
    ObjektWohnanlage,
    MerkmalKatalog,
)
from app.services.objekt_pflege_service import erstelle_pflegeauftrag


def _auftrag(name="Externe Pflege Test", bereiche=None):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        objekt = Objekt(org_id=org.id, nummer=99001, name=name, informationen="Produktiver Stand",
                        status=OBJEKT_STATUS_FREIGEGEBEN)
        # SQLite-Testdaten enthalten einen organisationsweiten Nummern-Unique-Index.
        objekt.nummer = int(datetime.now(UTC).timestamp() * 1000000) % 1000000000
        kontakt = Kontakt(org_id=org.id, anzeigename="Zentrale Kontaktperson", email="pflege@example.test",
                          funktion="Brandschutz")
        db.add_all([objekt, kontakt])
        db.flush()
        db.add(ObjektKontakt(org_id=org.id, objekt_id=objekt.id, kontakt_id=kontakt.id))
        db.flush()
        auftrag, token = erstelle_pflegeauftrag(
            db, objekt, kontakt, ersteller_id=None,
            bereiche=bereiche or ["stammdaten", "kontakte", "bma"],
        )
        db.commit()
        return auftrag.id, objekt.id, kontakt.id, token
    finally:
        db.close()


def _csrf(client, token):
    client.get(f"/objektpflege/{token}")
    return client.cookies.get("ec_csrf")


def test_landing_marks_first_access_and_invalid_and_expired_links(client):
    auftrag_id, objekt_id, _, token = _auftrag()
    response = client.get(f"/objektpflege/{token}")
    assert response.status_code == 200
    assert "Externe Pflege Test" in response.text
    assert client.get("/objektpflege/garbage-token").status_code == 404

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        auftrag = db.get(ObjektPflegeauftrag, auftrag_id)
        assert auftrag.erster_zugriff_am is not None
        assert auftrag.status == "in_bearbeitung"
        auftrag.gueltig_bis = datetime.now(UTC) - timedelta(days=1)
        db.commit()
    finally:
        db.close()
    assert "Link abgelaufen" in client.get(f"/objektpflege/{token}").text


def test_widerrufen_and_submission_require_complete_sections(client):
    auftrag_id, _, _, token = _auftrag("Widerrufen", ["stammdaten"])
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(ObjektPflegeauftrag, auftrag_id).status = "widerrufen"
        db.commit()
    finally:
        db.close()
    assert "Zugang wurde widerrufen" in client.get(f"/objektpflege/{token}").text

    auftrag_id, _, _, token = _auftrag("Einreichen", ["stammdaten", "bma"])
    csrf = _csrf(client, token)
    response = client.post(
        f"/objektpflege/{token}/einreichen", data={"_csrf": csrf, "bestaetigung": "1"},
    )
    assert response.status_code == 400
    for bereich in ("stammdaten", "bma"):
        response = client.post(f"/objektpflege/{token}/bereich/{bereich}/bestaetigen", data={"_csrf": csrf})
        assert response.status_code == 200
    response = client.post(f"/objektpflege/{token}/einreichen", data={"_csrf": csrf, "bestaetigung": "1"})
    assert response.status_code == 200
    assert "Prüfung eingereicht" in client.get(f"/objektpflege/{token}").text
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        auftrag = db.get(ObjektPflegeauftrag, auftrag_id)
        assert auftrag.status == "eingereicht"
        assert auftrag.kontakt_notiz is None
    finally:
        db.close()


def test_external_edits_create_copy_and_contact_proposal_without_mutating_source(client):
    auftrag_id, objekt_id, kontakt_id, token = _auftrag("Änderungsobjekt", ["stammdaten", "kontakte"])
    csrf = _csrf(client, token)
    response = client.post(f"/objektpflege/{token}/bereich/stammdaten/aendern", data={
        "_csrf": csrf, "name": "Neuer Objektname", "vulgoname": "Alias", "informationen": "Externer Vorschlag",
    }, follow_redirects=False)
    assert response.status_code == 303
    response = client.post(f"/objektpflege/{token}/bereich/kontakte/aendern", data={
        "_csrf": csrf, "funktion": "Neue Funktion", "telefon": "+43 555 1234",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert client.post(f"/objektpflege/{token}/bereich/bma/aendern", data={"_csrf": csrf}).status_code == 400

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        auftrag = db.get(ObjektPflegeauftrag, auftrag_id)
        produktiv = db.get(Objekt, objekt_id)
        kopie = db.get(Objekt, auftrag.arbeitskopie_id)
        assert kopie.informationen == "Externer Vorschlag"
        assert kopie.name == "Neuer Objektname" and kopie.vulgoname == "Alias"
        assert produktiv.informationen == "Produktiver Stand"
        change = db.query(ObjektChange).filter(ObjektChange.objekt_id == kopie.id).first()
        assert change.quelle == "extern_pflegeauftrag" and change.pflegeauftrag_id == auftrag_id
        vorschlag = db.query(KontaktAenderungsvorschlag).filter_by(pflegeauftrag_id=auftrag_id).first()
        assert vorschlag.status == "offen" and "Neue Funktion" in vorschlag.diff_json
        assert "+43 555 1234" in vorschlag.diff_json
        assert db.get(Kontakt, kontakt_id).funktion == "Brandschutz"
    finally:
        db.close()


def test_guest_can_open_only_current_document_in_native_viewer(client, tmp_path, monkeypatch):
    _, objekt_id, _, token = _auftrag("Dokumentansicht", ["dokumente"])
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = db.get(Objekt, objekt_id)
        dokument = ObjektDokument(
            org_id=objekt.org_id, objekt_id=objekt.id, dateiname_original="einsatzplan.pdf",
            pfad="test/einsatzplan.pdf", mime="application/pdf", groesse_bytes=12, seitenzahl=1,
        )
        db.add(dokument)
        db.commit()
        dokument_id = dokument.id
    finally:
        db.close()
    datei = Path(settings.OBJEKT_MEDIA_DIR) / "test" / "einsatzplan.pdf"
    datei.parent.mkdir(parents=True)
    datei.write_bytes(b"%PDF-test\n")

    viewer = client.get(
        f"/objektpflege/{token}/dokumente/{dokument_id}/anzeigen",
        follow_redirects=False,
    )
    assert viewer.status_code == 303
    assert viewer.headers["location"] == f"/objektpflege/{token}/dokumente/{dokument_id}/datei"
    response = client.get(f"/objektpflege/{token}/dokumente/{dokument_id}/datei")
    assert response.status_code == 200
    assert response.content == b"%PDF-test\n"
    assert "inline" in response.headers["content-disposition"]
    assert client.get(
        f"/objektpflege/{token}/dokumente/{dokument_id + 999999}/anzeigen",
        follow_redirects=False,
    ).status_code == 404


def test_einreichen_speichert_beschnittene_notiz_und_lehnt_zu_lange_ab(client):
    auftrag_id, _, _, token = _auftrag("Notiz", ["stammdaten"])
    csrf = _csrf(client, token)
    assert client.post(f"/objektpflege/{token}/bereich/stammdaten/bestaetigen", data={"_csrf": csrf}).status_code == 200
    response = client.post(
        f"/objektpflege/{token}/einreichen",
        data={"_csrf": csrf, "bestaetigung": "1", "kontakt_notiz": "  Bitte prüfen.  "},
    )
    assert response.status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        auftrag = db.get(ObjektPflegeauftrag, auftrag_id)
        assert auftrag.status == "eingereicht"
        assert auftrag.kontakt_notiz == "Bitte prüfen."
    finally:
        db.close()

    auftrag_id, _, _, token = _auftrag("Lange Notiz", ["stammdaten"])
    csrf = _csrf(client, token)
    assert client.post(f"/objektpflege/{token}/bereich/stammdaten/bestaetigen", data={"_csrf": csrf}).status_code == 200
    response = client.post(
        f"/objektpflege/{token}/einreichen",
        data={"_csrf": csrf, "bestaetigung": "1", "kontakt_notiz": "x" * 5001},
    )
    assert response.status_code == 400
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(ObjektPflegeauftrag, auftrag_id).status != "eingereicht"
    finally:
        db.close()


def test_pruefen_zeigt_zusaetzliche_lesedaten(client):
    _, objekt_id, _, token = _auftrag("Zusatzdaten", ["stammdaten", "kontakte", "bma", "gefahren"])
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = db.get(Objekt, objekt_id)
        merkmal = MerkmalKatalog(org_id=objekt.org_id, name="Tiefgarage", icon="P")
        weiterer = Kontakt(org_id=objekt.org_id, anzeigename="Weitere Person")
        gefahr = GefahrenKatalog(org_id=objekt.org_id, name="Chemie")
        db.add_all([merkmal, weiterer, gefahr])
        db.flush()
        weiterer_objektkontakt = ObjektKontakt(
            org_id=objekt.org_id, objekt_id=objekt.id, kontakt_id=weiterer.id,
            art="betreiber", erreichbarkeit="tagsüber",
        )
        db.add_all([
            ObjektMerkmal(org_id=objekt.org_id, objekt_id=objekt.id, merkmal_id=merkmal.id, hinweis="Garage"),
            weiterer_objektkontakt,
            ObjektBMA(org_id=objekt.org_id, objekt_id=objekt.id, benachrichtigung_sms="SMS-Kreis",
                      benachrichtigung_email="bma@example.test"),
            ObjektGefahr(org_id=objekt.org_id, objekt_id=objekt.id, gefahr_id=gefahr.id,
                         gefahrklasse="3", gefahrnummer="33",
                         links_json='[{"label":"Datenblatt","url":"https://example.test/datenblatt"}]'),
        ])
        db.flush()
        db.add(ObjektWohnanlage(
            org_id=objekt.org_id, objekt_id=objekt.id, wohneinheiten=12, geschosse=4, stiegen=2,
            hausverwaltung_kontakt_id=weiterer_objektkontakt.id, hinweise="Innenhof",
        ))
        db.commit()
    finally:
        db.close()
    response = client.get(f"/objektpflege/{token}/pruefen")
    assert response.status_code == 200
    for text in ("Tiefgarage", "Innenhof", "Weitere Person", "SMS-Kreis", "Gefahrklasse", "Datenblatt"):
        assert text in response.text
