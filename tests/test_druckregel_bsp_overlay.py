"""P5b: freigegebene BSP-Ausschnitte in der bestehenden Druckregel-Pipeline."""
from datetime import datetime
from io import BytesIO

import pytest
from PIL import Image
from pypdf import PdfReader

from app.core.tenant import set_tenant_context
from app.core.timezones import format_local_datetime
from app.db import SessionLocal
from app.models.gateway import (
    DOC_BSP_OVERLAY, JOB_SOURCE_MANUAL, Gateway, Printer, PrintJob, PrintRule,
)
from app.models.incident import Incident
from app.models.master import AlarmType, FireDept, OrgSettings, SystemSettings
from app.models.objekt import BspOverlayEinsatz, ObjektDokument, ObjektDokumentSeite, ObjektEinsatz
from app.services import objekt_bsp_overlay_service as bsp
from app.services import print_dispatcher as dispatcher
from app.services.print_artifact_service import ArtifactError, render_job_pdf
from tests.test_bsp_overlay_service import umgebung as umgebung
from tests.test_gateway_druckregeln_render import ORG_ID, _login, _setup
from tests.test_objekt_georef import karte as karte, plan as plan


@pytest.fixture
def druck(umgebung, monkeypatch):
    db, overlay, user, plan = umgebung
    bsp.entwurf_aendern(db, overlay, {
        "name": "Hallenplan Nord", "plan_art": "geschoss", "geschoss": "EG", "bereich": "Halle Nord",
        "autodruck": True,
    }, user.id)
    bsp.freigeben(db, overlay, user)
    gateway = Gateway(org_id=overlay.org_id, name="BSP-Gateway", device_token_hash="bsp-test")
    incident = Incident(primary_org_id=overlay.org_id)
    db.add_all([gateway, incident])
    db.flush()
    printers = [Printer(org_id=overlay.org_id, gateway_id=gateway.id, name=f"BSP-{i}",
                        uri=f"ipp://bsp-{i}/print", defaults={"color": "monochrome"}) for i in range(2)]
    db.add_all(printers)
    db.flush()
    regel = PrintRule(org_id=overlay.org_id, name="BSP-Regel", trigger="einsatz_created",
                      documents=[], objekt_elements=["bsp_overlay"], printer_ids=[p.id for p in printers])
    zuordnung = ObjektEinsatz(org_id=overlay.org_id, objekt_id=overlay.objekt_id,
                              incident_id=incident.id, status="bestaetigt", quelle="manuell")
    db.add_all([regel, zuordnung])
    db.flush()
    monkeypatch.setattr("app.services.gateway_service.gateway_effective_enabled", lambda *_: True)
    yield db, overlay, user, regel, {"incident_id": incident.id}, zuordnung


def _dispatch(druck, context=None):
    db, overlay, _, regel, basis, _ = druck
    db.flush()  # SessionLocal hat autoflush=False; Domain-Hooks laufen nach dem Speichern.
    return dispatcher.on_event(db, overlay.org_id, regel.trigger, context if context is not None else basis)


def _jobs(db):
    return db.query(PrintJob).filter_by(document_type=DOC_BSP_OVERLAY).all()


def test_regel_ohne_element_kein_bsp_job(druck):
    druck[3].objekt_elements = []
    assert _dispatch(druck) == []
    assert _jobs(druck[0]) == []


def test_autodruck_false_kein_job(druck):
    druck[1].autodruck = False
    assert _dispatch(druck) == []
    assert _jobs(druck[0]) == []


@pytest.mark.parametrize("trigger", ["einsatz_created", "einsatz_created_gsl", "einsatz_updated"])
def test_freigegeben_genau_ein_job_je_drucker(druck, trigger):
    db, overlay, _, regel, _, _ = druck
    regel.trigger = trigger
    jobs = _dispatch(druck)
    assert len(jobs) == 2
    assert {job.printer_id for job in jobs} == set(regel.printer_ids)
    assert all(job.document_type == DOC_BSP_OVERLAY and job.objekt_id == overlay.objekt_id
               and job.artifact_ref == f"{overlay.id}:1" for job in jobs)
    assert all(job.options["color"] == "monochrome" for job in jobs)
    assert len(_jobs(db)) == 2


def test_zweimal_dispatch_idempotent_neue_freigabe_neuer_job(druck):
    db, overlay, user, regel, _, _ = druck
    regel.printer_ids = regel.printer_ids[:1]
    assert len(_dispatch(druck)) == 1
    assert _dispatch(druck) == []
    assert len(_jobs(db)) == 1
    bsp.zurueckziehen(db, overlay, user.id)
    bsp.freigeben(db, overlay, user)
    assert overlay.freigabe_nr == 2
    assert len(_dispatch(druck)) == 1
    assert {job.artifact_ref for job in _jobs(db)} == {f"{overlay.id}:1", f"{overlay.id}:2"}


@pytest.mark.parametrize("zustand", ["veraltet", "zurueckgezogen", "entwurf"])
def test_nicht_auslieferbar_kein_job_mit_warnung(druck, zustand, caplog):
    db, overlay, user, _, _, _ = druck
    if zustand == "veraltet":
        db.get(ObjektDokument, overlay.dokument_id).ist_aktuelle_version = False
    elif zustand == "zurueckgezogen":
        bsp.zurueckziehen(db, overlay, user.id)
    else:
        overlay.status = "entwurf"
    assert _dispatch(druck) == []
    assert _jobs(db) == []
    assert f"BSP-Overlay {overlay.id}" in caplog.text
    assert "nicht auslieferbar" in caplog.text


def test_nur_bestaetigte_objektzuordnung(druck):
    druck[5].status = "vorschlag"
    assert _dispatch(druck) == []
    druck[5].status = "bestaetigt"
    assert len(_dispatch(druck)) == 2


def test_bildschirm_und_einsatz_off_beeinflussen_regeldruck_nicht(druck):
    db, overlay, _, _, context, _ = druck
    overlay.standard_sichtbar = False
    db.add(BspOverlayEinsatz(org_id=overlay.org_id, overlay_id=overlay.id,
                            incident_id=context["incident_id"], sichtbarkeit="OFF"))
    assert len(_dispatch(druck)) == 2


def test_alarmstufen_abweichung_waehlt_objekt_element(druck):
    regel = druck[3]
    regel.objekt_elements = []
    regel.options = {"je_alarmstufe": {"F14": {"objekt_elements": ["bsp_overlay"], "copies": 3}}}
    assert _dispatch(druck) == []
    jobs = _dispatch(druck, druck[4] | {"alarm_code": "f14"})
    assert len(jobs) == 2
    assert all(job.options["copies"] == 3 for job in jobs)


def test_testbezug_und_testdruck(druck):
    db, _, _, regel, context, _ = druck
    bezug = dispatcher.resolve_test_context(db, regel)
    assert bezug is not None and bezug.context["incident_id"] == context["incident_id"]
    jobs = dispatcher.build_test_jobs(db, regel, bezug.context)
    assert len(jobs) == 2
    assert all(job.source == JOB_SOURCE_MANUAL and job.document_type == DOC_BSP_OVERLAY for job in jobs)


def test_bsp_element_ist_keine_dokumentart(druck):
    db, overlay, _, _, _, _ = druck
    seite = db.query(ObjektDokumentSeite).filter_by(dokument_id=overlay.dokument_id).one()
    seite.dokumentart = "bsp_overlay"
    seite.einzel_pdf_pfad = "irrtuemliche-dokumentart.pdf"
    assert dispatcher._seiten_for_elements(db, overlay.objekt_id, ["bsp_overlay"]) == []
    assert len(_dispatch(druck)) == 2
    assert db.query(PrintJob).filter_by(document_type="objekt_dokument").count() == 0


def test_render_pdf_mit_pflichtangaben(druck):
    db, overlay, _, _, _, _ = druck
    org = db.get(FireDept, overlay.org_id)
    org.timezone = "Europe/Vienna"
    overlay.freigegeben_am = datetime(2026, 1, 15, 12, 30)
    pdf = render_job_pdf(db, _dispatch(druck)[0])
    assert pdf.startswith(b"%PDF")
    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    for pflicht in ("Hallenplan Nord", "BSP-Overlay", "Musterbetrieb", "geschoss", "EG", "Halle Nord",
                    "plan.pdf", "Seite 1", "Version 1", "Freigabe-Nr. 1",
                    format_local_datetime(overlay.freigegeben_am, org)):
        assert pflicht in text
    assert "13:30" in text
    assert len(reader.pages[0].images) == 1


@pytest.mark.parametrize("fall", ["zurueckgezogen", "veraltet", "neue_freigabe", "fremde_org", "fremdes_objekt",
                                  "ungueltiger_bezug", "original_fehlt"])
def test_render_verweigert_ungueltigen_stand(druck, fall):
    db, overlay, user, _, _, _ = druck
    job = _dispatch(druck)[0]
    if fall == "zurueckgezogen":
        bsp.zurueckziehen(db, overlay, user.id)
    elif fall == "veraltet":
        db.get(ObjektDokument, overlay.dokument_id).ist_aktuelle_version = False
    elif fall == "neue_freigabe":
        bsp.zurueckziehen(db, overlay, user.id)
        bsp.freigeben(db, overlay, user)
    elif fall == "fremde_org":
        job.org_id += 1
    elif fall == "fremdes_objekt":
        job.objekt_id += 1
    elif fall == "ungueltiger_bezug":
        job.artifact_ref = "ungueltig"
    else:
        seite = db.query(ObjektDokumentSeite).filter_by(dokument_id=overlay.dokument_id).one()
        bsp._pfad(seite.bild_pfad).unlink()
    with pytest.raises(ArtifactError, match="BSP"):
        render_job_pdf(db, job)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_gemeinsamer_originalausschnitt_unverzerrt(druck, rotation):
    db, overlay, _, _, _, _ = druck
    seite = db.query(ObjektDokumentSeite).filter_by(dokument_id=overlay.dokument_id).one()
    # Nichtquadratischer, farbiger Plan: Orientierung und Ausschnitt unabhängig prüfen.
    bild = Image.new("RGB", (800, 400), "red")
    bild.paste("blue", (400, 0, 800, 400))
    bild.save(bsp._pfad(seite.bild_pfad))
    seite.rotation = rotation
    overlay.ausschnitt_json = '{"art":"rechteck","x0":0.25,"y0":0.1,"x1":0.75,"y1":0.9}'
    gedreht = bild.convert("RGBA").rotate(-rotation, expand=True)
    erwartet = gedreht.crop((int(gedreht.width * .25), int(gedreht.height * .1),
                            int(gedreht.width * .75), int(gedreht.height * .9)))
    with Image.open(BytesIO(bsp.original_ausschnitt_png(db, overlay))) as ausschnitt:
        assert ausschnitt.size == erwartet.size
        assert ausschnitt.tobytes() == erwartet.tobytes()


@pytest.mark.parametrize("objekt_aktiv", [True, False])
def test_regel_ui_zeigt_element_nur_mit_objektmodul(client, objekt_aktiv):
    username = f"bsp-regel-ui-{objekt_aktiv}"
    gateway_id = _setup(username, "org_admin")
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org_settings = db.query(OrgSettings).filter_by(org_id=ORG_ID).one()
        org_settings.objekt_module_enabled = objekt_aktiv
        flag = db.query(SystemSettings).filter_by(key="objekt_module_enabled").first()
        if flag is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            flag.value = "true"
        if not db.query(AlarmType).filter_by(org_id=ORG_ID, code="F14").first():
            db.add(AlarmType(org_id=ORG_ID, code="F14", label="Brand", category="F"))
        db.add(PrintRule(org_id=ORG_ID, name=username, trigger="einsatz_created",
                         objekt_elements=["bsp_overlay"], documents=[],
                         options={"je_alarmstufe": {"F14": {"objekt_elements": ["bsp_overlay"]}}}))
        db.commit()
    _login(client, username, "Test1234!")
    response = client.get(f"/gateway/{gateway_id}/druckregeln")
    assert response.status_code == 200
    assert ('name="objekt_elements" value="bsp_overlay"' in response.text) == objekt_aktiv
    assert ('name="stufe_F14_objekt_elements" value="bsp_overlay"' in response.text) == objekt_aktiv
    if objekt_aktiv:
        assert "BSP-Ausschnitt (freigegeben)" in response.text
        assert "Druckervorgabe. Bei S/W" in response.text
    assert 'name="documents" value="bsp_overlay"' not in response.text
    assert 'name="stufe_F14_doc_bsp_overlay"' not in response.text
