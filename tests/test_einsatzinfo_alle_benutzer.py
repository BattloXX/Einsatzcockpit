"""Einsatzinformationen sind fuer jede Benutzerrolle lesbar, Schreibrechte bleiben eng."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from pypdf import PdfWriter

from app.core.permissions import EINSATZ_LESE_ROLLEN
from app.core.security import hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.kontakt import EinsatzKontakt, Kontakt
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import (
    GefahrenKatalog,
    Objekt,
    ObjektDokument,
    ObjektDokumentSeite,
    ObjektEinsatz,
    ObjektGefahr,
    ObjektKontakt,
)
from app.models.user import Role, User, UserRole


@pytest.fixture()
def info_daten(tmp_path):
    pdf = tmp_path / "plan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    bild = tmp_path / "seite.png"
    Image.new("RGB", (10, 10), "white").save(bild)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        name = uuid4().hex
        org = FireDept(slug=name, name="Info Org")
        fremd = FireDept(slug=name + "-fremd", name="Fremde Org")
        db.add_all([org, fremd])
        db.flush()
        for flag in ("objekt_module_enabled", "kontakte_module_enabled"):
            setting = db.get(SystemSettings, flag)
            if setting is None:
                db.add(SystemSettings(key=flag, value="true"))
            else:
                setting.value = "true"
        for org_id in (org.id, fremd.id):
            db.add(OrgSettings(org_id=org_id, objekt_module_enabled=True, kontakte_module_enabled=True))
        incident = Incident(primary_org_id=org.id, alarm_type_code="T1", status="active")
        objekt = Objekt(org_id=org.id, nummer=1, name="Info Objekt", status="freigegeben")
        gefahr = GefahrenKatalog(org_id=org.id, name="Info Gefahr")
        kontakt = Kontakt(org_id=org.id, anzeigename="Sichtbarer Einsatzkontakt")
        db.add_all([incident, objekt, gefahr, kontakt])
        db.flush()
        db.add_all([
            ObjektEinsatz(org_id=org.id, objekt_id=objekt.id, incident_id=incident.id,
                          quelle="manuell", status="bestaetigt"),
            ObjektGefahr(org_id=org.id, objekt_id=objekt.id, gefahr_id=gefahr.id, detail="Gefahrenhinweis Gaslager"),
            ObjektKontakt(org_id=org.id, objekt_id=objekt.id, kontakt_id=kontakt.id, einsatz_sichtbar=True),
            EinsatzKontakt(org_id=org.id, incident_id=incident.id, kontakt_id=kontakt.id),
        ])
        dokument = ObjektDokument(org_id=org.id, objekt_id=objekt.id, dateiname_original="Einsatzplan.pdf",
                                  pfad=str(pdf), status="fertig", seitenzahl=1)
        db.add(dokument)
        db.flush()
        seite = ObjektDokumentSeite(org_id=org.id, objekt_id=objekt.id, dokument_id=dokument.id,
                                   seiten_nr=1, titel="Dokumenttitel Gaslager", volltext="Gaslager Volltext",
                                   bild_pfad=str(bild), thumb_pfad=str(bild), einzel_pdf_pfad=str(pdf))
        db.add(seite)
        db.commit()
        return SimpleNamespace(org=org.id, fremd=fremd.id, incident=incident.id, objekt=objekt.id,
                               dokument=dokument.id, seite=seite.id)


def _anmelden(client, org_id, rolle):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        user = User(username=uuid4().hex, display_name="Info Benutzer", org_id=org_id,
                    password_hash=hash_password("Test1234!"), active=True)
        db.add(user)
        db.flush()
        role = db.query(Role).filter_by(code=rolle).one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        client.cookies.set("session", sign_session(user.id))


def _lesewege(d):
    return {
        f"/einsatz/{d.incident}/info": "Einsatzinformation",
        f"/objekte/einsatz-panel/{d.incident}?view=info": "Info Objekt",
        f"/objekte/{d.objekt}/einsatz-fragment?incident_id={d.incident}": "Gefahrenhinweis Gaslager",
        f"/objekte/{d.objekt}/einsatz?incident_id={d.incident}": "Sichtbarer Einsatzkontakt",
        f"/objekte/{d.objekt}/dokumente": "Dokumenttitel Gaslager",
        f"/objekte/{d.objekt}/dokumente/viewer": "Dokumenttitel Gaslager",
        f"/objekte/{d.objekt}/dokumente/suche.json?q=Gaslager": "Gaslager Volltext",
        f"/einsatz/{d.incident}/kontakte": "Sichtbarer Einsatzkontakt",
        f"/objekte/{d.objekt}/karte/einbettung": "Lagekarte",
        f"/objekte/{d.objekt}/karte/objekte.json": "Info Objekt",
        f"/objekte/{d.objekt}/hydranten.json": "hydranten",
        f"/einsatz/{d.incident}/nachbar-gefahren.json": "objekte",
        f"/einsatz/{d.incident}/route.json": "status",
        f"/objekte/{d.objekt}/dokumente/sammel-pdf": None,
        "/objekte/karten-symbole.json": "symbole",
        f"/objekt-medien/seite/{d.seite}/thumb": None,
        f"/objekt-medien/seite/{d.seite}/bild": None,
        f"/objekt-medien/seite/{d.seite}/pdf": None,
        f"/objekt-medien/dokument/{d.dokument}/original?download=1": None,
    }


@pytest.mark.parametrize("rolle", EINSATZ_LESE_ROLLEN)
def test_alle_rollen_lesen_einsatzinfo(client, info_daten, rolle):
    d = info_daten
    _anmelden(client, d.org, rolle)
    for path, inhalt in _lesewege(d).items():
        response = client.get(path)
        assert response.status_code == 200, (rolle, path, response.text[:300])
        if inhalt:
            assert inhalt in response.text, (rolle, path)
    fragment = client.get(f"/objekte/{d.objekt}/einsatz-fragment?incident_id={d.incident}")
    assert "Sichtbarer Einsatzkontakt" in fragment.text


def test_fremde_org_bleibt_ausgeschlossen(client, info_daten):
    _anmelden(client, info_daten.fremd, "uebungsleiter")
    for path in _lesewege(info_daten):
        if path == "/objekte/karten-symbole.json":
            continue  # Der Katalog der eigenen Org enthaelt keine fremden Objektdaten.
        assert client.get(path).status_code in (403, 404), path


def test_readonly_darf_nicht_schreiben(client, info_daten):
    d = info_daten
    _anmelden(client, d.org, "readonly")
    client.get(f"/einsatz/{d.incident}/info")
    csrf = client.cookies.get("ec_csrf")
    for path, daten in (
        (f"/objekte/einsatz-panel/{d.incident}/verknuepfen", {"objekt_id": d.objekt}),
        (f"/einsatz/{d.incident}/kontakte/zuordnen", {"kontakt_id": 1}),
        (f"/einsatz/{d.incident}/route/neu", {}),
    ):
        assert client.post(path, data={"_csrf": csrf, **daten}).status_code == 403


@pytest.mark.parametrize("rolle", ["readonly", "uebungsleiter", "katplan_verwalter"])
def test_kontaktverzeichnis_und_nicht_freigegebene_kontakte_bleiben_geschuetzt(client, info_daten, rolle):
    d = info_daten
    with SessionLocal() as db:
        set_tenant_context(db, d.org)
        for name, aktiv, archiviert, sichtbar in (
            ("Verborgener Kontakt", True, False, False),
            ("Inaktiver Kontakt", False, False, True),
            ("Archivierter Kontakt", True, True, True),
        ):
            kontakt = Kontakt(org_id=d.org, anzeigename=name, aktiv=aktiv, archiviert=archiviert)
            db.add(kontakt)
            db.flush()
            db.add(ObjektKontakt(org_id=d.org, objekt_id=d.objekt, kontakt_id=kontakt.id,
                                 einsatz_sichtbar=sichtbar))
        db.commit()
    _anmelden(client, d.org, rolle)
    assert client.get("/kontakte").status_code == 403
    response = client.get(f"/objekte/{d.objekt}/einsatz-fragment?incident_id={d.incident}")
    assert response.status_code == 200
    assert "Sichtbarer Einsatzkontakt" in response.text
    for verborgen in ("Verborgener Kontakt", "Inaktiver Kontakt", "Archivierter Kontakt"):
        assert verborgen not in response.text


def test_entwurf_bleibt_fuer_leser_unsichtbar(client, info_daten):
    d = info_daten
    with SessionLocal() as db:
        set_tenant_context(db, d.org)
        db.get(Objekt, d.objekt).status = "entwurf"
        db.commit()
    _anmelden(client, d.org, "uebungsleiter")
    for suffix in ("einsatz-fragment", "einsatz", "dokumente", "dokumente/viewer", "karte/objekte.json"):
        assert client.get(f"/objekte/{d.objekt}/{suffix}").status_code == 404
