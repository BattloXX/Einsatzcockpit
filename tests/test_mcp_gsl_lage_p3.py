"""Regressionen für die schreibenden P3-GSL-Lagewerkzeuge."""

import json
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import CitizenReport, CrossSiteMarker, IncidentSite, LageJournalEntry, MajorIncident
from tests.test_mcp_gsl_ressourcen import _call, _new_seed
from tests.test_mcp_objekte import _token


def _daten(seed):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = MajorIncident(org_id=seed["org_id"], name="P3 Lage")
        db.add(lage)
        db.flush()
        site = IncidentSite(
            major_incident_id=lage.id, org_id=seed["org_id"], bezeichnung="Bestehende Stelle",
            ort="Wien", strasse="Testgasse",
        )
        report = CitizenReport(
            major_incident_id=lage.id, description="Wasser im Keller", ort="Wien", strasse="Testgasse",
            reporter_name="Privater Name", reporter_contact="0664 privat", source_ip="127.0.0.1",
        )
        marker = CrossSiteMarker(major_incident_id=lage.id, org_id=seed["org_id"], title="Marker")
        db.add_all([site, report, marker])
        db.commit()
        return lage.id, site.id, report.id, marker.id
    finally:
        db.close()


def _ohne_ki(monkeypatch):
    from app.services import gsl_stelle_service

    async def leer(*args, **kwargs):
        return None

    monkeypatch.setattr(gsl_stelle_service, "_geocode_site", leer)
    monkeypatch.setattr(gsl_stelle_service, "_apply_ai_prio", leer)


def test_p3_happy_path_und_datenschutz(client, monkeypatch):
    _ohne_ki(monkeypatch)
    seed = _new_seed(f"mcp-p3-{uuid4().hex[:8]}", {"editor": "recorder"})
    lage_id, site_id, report_id, marker_id = _daten(seed)
    token = _token(client, seed, "editor")
    copied = []

    def photo_copy(*args, **kwargs):
        copied.append(args[1])
        return None

    from app.services import lage_media_service
    monkeypatch.setattr(lage_media_service, "copy_citizen_photo_to_site", photo_copy)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(CitizenReport, report_id).photo_filename = "nicht-erforderlich.jpg"
        db.commit()
    finally:
        db.close()
    journal = _call(client, token, "gsl_journal_eintrag", lage_id=lage_id, betreff="Lage", text="<script>x</script>")
    funk = _call(client, token, "gsl_funkjournal_eintrag", lage_id=lage_id, text="Funk", site_id=site_id)
    assert journal["id"] and funk["id"]
    assert _call(client, token, "gsl_funkjournal_erledigt", lage_id=lage_id, entry_id=funk["id"])["erledigt"]
    accepted = _call(
        client, token, "gsl_meldung_bearbeiten", lage_id=lage_id, report_id=report_id, aktion="annehmen",
        trotz_duplikat=True,
    )
    assert accepted["bearbeitet"] and accepted["site_id"]
    assert copied == [accepted["site_id"]]
    created = _call(client, token, "gsl_uebergreifend_anlegen", lage_id=lage_id, titel="Pegel")
    changed = _call(client, token, "gsl_uebergreifend_aendern", lage_id=lage_id, marker_id=created["id"],
                    status="beobachtung", eintrag="kontrolliert")
    assert changed["status"] == "beobachtung"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert "&lt;script&gt;" in db.get(LageJournalEntry, journal["id"]).body_html
        assert db.get(CitizenReport, report_id).status == "accepted"
    finally:
        db.close()
    dump = json.dumps([journal, funk, accepted, created, changed], ensure_ascii=False)
    assert "Privater Name" not in dump and "0664 privat" not in dump and "127.0.0.1" not in dump
    assert marker_id


def test_p3_schutzpruefungen_und_duplikate(client, monkeypatch):
    _ohne_ki(monkeypatch)
    own = _new_seed(f"mcp-p3-own-{uuid4().hex[:8]}", {"editor": "recorder", "reader": "readonly"})
    foreign = _new_seed(f"mcp-p3-foreign-{uuid4().hex[:8]}", {"editor": "recorder"})
    lage_id, site_id, report_id, marker_id = _daten(own)
    foreign_lage, _, foreign_report, foreign_marker = _daten(foreign)
    editor, reader = _token(client, own, "editor"), _token(client, own, "reader")
    readonly = _call(client, reader, "gsl_journal_eintrag", lage_id=lage_id, betreff="x")
    assert "erforderlichen Berechtigungen" in readonly["__fehler__"]
    foreign_report_result = _call(
        client, editor, "gsl_meldung_bearbeiten", lage_id=lage_id, report_id=foreign_report, aktion="ablehnen"
    )
    assert "nicht gefunden" in foreign_report_result["__fehler__"]
    foreign_marker_result = _call(
        client, editor, "gsl_uebergreifend_aendern", lage_id=lage_id, marker_id=foreign_marker
    )
    assert "nicht gefunden" in foreign_marker_result["__fehler__"]
    duplicate = _call(client, editor, "gsl_meldung_bearbeiten", lage_id=lage_id, report_id=report_id, aktion="annehmen")
    assert duplicate["bearbeitet"] is False and duplicate["duplikate"]
    accepted = _call(
        client, editor, "gsl_meldung_bearbeiten", lage_id=lage_id, report_id=report_id, aktion="annehmen",
        trotz_duplikat=True,
    )
    assert accepted["bearbeitet"]
    processed = _call(
        client, editor, "gsl_meldung_bearbeiten", lage_id=lage_id, report_id=report_id, aktion="ablehnen"
    )
    assert "bereits bearbeitet" in processed["__fehler__"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(MajorIncident, lage_id).status = "closed"
        db.commit()
    finally:
        db.close()
    assert "nicht aktiv" in _call(client, editor, "gsl_funkjournal_eintrag", lage_id=lage_id, text="x")["__fehler__"]
    assert foreign_lage and site_id and marker_id


def test_p3_deaktiviertes_modul(client):
    from app.models.master import OrgSettings

    seed = _new_seed(f"mcp-p3-feature-{uuid4().hex[:8]}", {"editor": "recorder"})
    lage_id, _, report_id, _ = _daten(seed)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(OrgSettings).filter_by(org_id=seed["org_id"]).one().mi_feature_meldungen = False
        db.commit()
    finally:
        db.close()
    assert "__fehler__" in _call(client, _token(client, seed, "editor"), "gsl_meldung_bearbeiten",
                                  lage_id=lage_id, report_id=report_id, aktion="ablehnen")


def test_p3_annehmen_zu_bestehender_stelle_ohne_kontaktdaten_im_lesepfad(client, monkeypatch):
    from app.models.major_incident import SiteLogEntry

    _ohne_ki(monkeypatch)
    seed = _new_seed(f"mcp-p3-zuordnen-{uuid4().hex[:8]}", {"editor": "recorder"})
    lage_id, site_id, report_id, _ = _daten(seed)
    token = _token(client, seed, "editor")
    result = _call(client, token, "gsl_meldung_bearbeiten", lage_id=lage_id, report_id=report_id,
                   aktion="annehmen", site_id=site_id)
    assert result["site_id"] == site_id
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        texte = [row.text for row in db.query(SiteLogEntry).filter_by(incident_site_id=site_id)]
    finally:
        db.close()
    assert f"Bürgermeldung #{report_id} zugeordnet" in texte
    assert any("Privater Name" in text for text in texte)
    gelesen = _call(client, token, "gsl_einsatzstelle_lesen", lage_id=lage_id, site_id=site_id, bereiche=["log"])
    dump = json.dumps(gelesen, ensure_ascii=False)
    assert "Privater Name" not in dump and "0664 privat" not in dump
    assert "Kontaktdaten der Bürgermeldung ausgeblendet" in dump
