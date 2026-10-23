"""DOC-01: Einsatz-PDF enthaelt die dokumentationsrelevanten Angaben, bricht mehrseitige Tabellen sauber um."""
from __future__ import annotations

import io
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import (
    Incident,
    IncidentChange,
    IncidentCommLog,
    IncidentLog,
    IncidentVehicle,
    Message,
    Task,
)
from app.models.master import AlarmType, FireDept, VehicleMaster
from app.models.objekt import (
    OBJEKT_EINSATZ_BESTAETIGT,
    GefahrenKatalog,
    Objekt,
    ObjektDokument,
    ObjektEinsatz,
    ObjektGefahr,
)
from app.models.user import User
from app.services import pdf_service
from app.services.incident_service import _create_fixed_columns

pypdf = pytest.importorskip("pypdf")
pytest.importorskip("weasyprint")

LANGES_WORT = "Superlangeswortohneleerzeichen" * 4


@pytest.fixture()
def einsatz():
    db = SessionLocal()
    set_tenant_context(db, None)
    kennung = uuid4().hex[:8]
    org = FireDept(slug=f"doc-{kennung}", name="Doku Org", color="#d42225", bos="Feuerwehr")
    db.add(org)
    db.flush()
    db.add(AlarmType(org_id=org.id, code="B2", label="Brand mittel", category="B"))
    user = User(username=f"doc-{kennung}", password_hash="x", display_name="Erika Ersteller", org_id=org.id,
                active=True)
    fahrzeug = VehicleMaster(dept_id=org.id, code="TLF", name="Tanklöschfahrzeug", type="TLF")
    db.add_all([user, fahrzeug])
    db.flush()
    inc = Incident(
        primary_org_id=org.id, alarm_type_code="B2", status="closed", reason="Brand Lagerhalle",
        report_text="Rauch aus Dach", address_street="Hauptstrasse", address_no="5", address_city="Wolfurt",
        started_at=datetime(2026, 10, 20, 8, 0), closed_at=datetime(2026, 10, 20, 10, 0), nummer=77,
    )
    db.add(inc)
    db.flush()
    _create_fixed_columns(db, inc)
    aktiv = next(c for c in inc.columns if c.code == "active")
    iv = IncidentVehicle(incident_id=inc.id, column_id=aktiv.id, vehicle_master_id=fahrzeug.id,
                         unit_status="Am Einsatzort")
    db.add(iv)
    db.flush()
    db.add_all([
        Task(incident_id=inc.id, title="Wasserversorgung aufbauen " + LANGES_WORT, created_by_user_id=user.id,
             created_at=datetime(2026, 10, 20, 8, 5), is_done=True, done_at=datetime(2026, 10, 20, 8, 40)),
        Message(incident_id=inc.id, title="Dach einsturzgefaehrdet", author_name="Rudi Recorder",
                created_at=datetime(2026, 10, 20, 8, 12), status="achtung"),
        IncidentChange(incident_id=inc.id, ts=datetime(2026, 10, 20, 8, 3), action="vehicle.status_set",
                       entity_type="incident_vehicle", entity_id=iv.id,
                       before_json=json.dumps({"unit_status": "Einsatz übernommen"}),
                       after_json=json.dumps({"unit_status": "Am Einsatzort"})),
        IncidentCommLog(incident_id=inc.id, ts=datetime(2026, 10, 20, 8, 20), direction="in", partner="Leitstelle",
                        channel="Funk 1", message="Lagemeldung: Vollbrand Halle", is_lage_relevant=True),
    ])
    for i in range(120):
        db.add(IncidentLog(incident_id=inc.id, ts=datetime(2026, 10, 20, 9, 0) + timedelta(seconds=i * 30),
                           text=f"Notiz {i:03d}", author_name="Autor"))
    kat = GefahrenKatalog(org_id=org.id, name="Gasanschluss", piktogramm_typ="gas", aktiv=True)
    db.add(kat)
    db.flush()
    objekt = Objekt(org_id=org.id, nummer=9, name="Lagerhalle Muster", status="freigegeben",
                    informationen="Dach mit Photovoltaik", anfahrtsweg="Zufahrt ueber Nordtor")
    db.add(objekt)
    db.flush()
    db.add_all([
        ObjektGefahr(org_id=org.id, objekt_id=objekt.id, gefahr_id=kat.id, stoffname="Fluessiggas"),
        ObjektDokument(org_id=org.id, objekt_id=objekt.id, dateiname_original="Feuerwehrplan_Halle.pdf",
                       pfad="x.pdf", status="fertig"),
        ObjektEinsatz(org_id=org.id, objekt_id=objekt.id, incident_id=inc.id, quelle="manuell",
                      status=OBJEKT_EINSATZ_BESTAETIGT),
    ])
    db.commit()
    db.refresh(inc)
    yield inc, db
    db.close()


def _seiten(pdf: bytes) -> list[str]:
    return [(p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(pdf)).pages]


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_pdf_enthaelt_einsatzdaten_ersteller_statusverlauf_funkjournal_und_objektdetails(einsatz):
    inc, _ = einsatz
    pdf = pdf_service.render_incident_pdf(inc)
    text = _normal(" ".join(_seiten(pdf)))
    for erwartet in (
        "Einsatzdaten", "Brand mittel", "Brand Lagerhalle", "Abgeschlossen", "Einsatzende",
        "Erika Ersteller", "Rudi Recorder",
        "Statusverlauf der Fahrzeuge", "Einsatz übernommen", "Am Einsatzort",
        "Funkjournal", "Lagemeldung: Vollbrand Halle", "Leitstelle", "Lage-relevant",
        "Dach mit Photovoltaik", "Zufahrt ueber Nordtor", "Feuerwehrplan_Halle.pdf", "Fluessiggas",
    ):
        assert erwartet in text, erwartet


def test_lange_woerter_werden_nicht_abgeschnitten(einsatz):
    inc, _ = einsatz
    text = re.sub(r"\s", "", " ".join(_seiten(pdf_service.render_incident_pdf(inc))))
    assert LANGES_WORT in text


def test_mehrseitige_tabelle_vollstaendig_mit_wiederholtem_kopf(einsatz):
    inc, _ = einsatz
    seiten = _seiten(pdf_service.render_incident_pdf(inc))
    assert len(seiten) >= 3
    gesamt = " ".join(seiten)
    for i in range(120):
        assert f"Notiz {i:03d}" in gesamt, i  # kein Deckel, nichts abgeschnitten
    # Tabellenkopf des Journals steht auf den Folgeseiten erneut ("Eintrag" nur in der Kopfzeile)
    mit_kopf = [s for s in seiten if "Notiz" in s and re.search(r"Zeit\s+Eintrag", s)]
    assert len(mit_kopf) >= 2


def test_chronologie_bleibt_erhalten(einsatz):
    inc, _ = einsatz
    text = " ".join(_seiten(pdf_service.render_incident_pdf(inc)))
    assert text.index("Notiz 000") < text.index("Notiz 119")


def test_kein_text_ragt_ueber_den_seitenrand(einsatz):
    """Layoutpruefung ueber den WeasyPrint-Boxbaum: auch sehr lange Woerter bleiben auf der Seite."""
    weasyprint = pytest.importorskip("weasyprint")
    inc, _ = einsatz
    erfasst = {}

    class Erfasser:
        def __init__(self, string=None, base_url=None):
            erfasst["html"] = string

        def write_pdf(self, buf):
            buf.write(b"%PDF-1.4")

    original = weasyprint.HTML
    weasyprint.HTML = Erfasser
    try:
        pdf_service.render_incident_pdf(inc)
    finally:
        weasyprint.HTML = original
    dokument = original(string=erfasst["html"]).render()
    breite = 595.0 * 96 / 72  # A4 in CSS-Pixeln
    rechts_max = 0.0
    for seite in dokument.pages:
        stapel = [seite._page_box]
        while stapel:
            box = stapel.pop()
            if type(box).__name__ == "TextBox":
                rechts_max = max(rechts_max, box.position_x + box.width)
            stapel.extend(getattr(box, "children", []) or [])
    # Seitenrand 15 mm = 56.7 px; kleine Toleranz fuer Rundung
    assert rechts_max <= breite - 15 * 96 / 25.4 + 2, rechts_max
