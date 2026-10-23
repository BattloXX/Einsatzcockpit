"""DOC-01: GSL-Gesamtbericht enthaelt Ressourcenuebersicht, Auftragsverlauf, Statushistorie und das volle Funkjournal."""
from __future__ import annotations

import io
import re
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import (
    CommLogEntry,
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    LageJournalEntry,
    MajorIncident,
    Sector,
    SiteLogEntry,
    SitePhase,
    SitePriority,
)
from app.models.master import FireDept
from app.routers.ui_major_incident import build_bericht_context
from app.services.print_artifact_service import _html_to_pdf

pypdf = pytest.importorskip("pypdf")
pytest.importorskip("weasyprint")


@pytest.fixture()
def lage():
    db = SessionLocal()
    set_tenant_context(db, None)
    org = FireDept(slug=f"gslpdf-{uuid4().hex[:8]}", name="GSL PDF", color="#d42225", bos="Feuerwehr")
    db.add(org)
    db.flush()
    lg = MajorIncident(org_id=org.id, name="Sturm Wolfurt", started_at=datetime(2026, 10, 20, 6, 0))
    db.add(lg)
    db.flush()
    sektor = Sector(major_incident_id=lg.id, name="Nord")
    db.add(sektor)
    db.flush()
    s1 = IncidentSite(major_incident_id=lg.id, org_id=org.id, bezeichnung="Baum auf Strasse", ort="Wolfurt",
                      priority=SitePriority.sofort, phase=SitePhase.erledigt, sector_id=sektor.id)
    s2 = IncidentSite(major_incident_id=lg.id, org_id=org.id, bezeichnung="Keller unter Wasser", ort="Rickenbach",
                      priority=SitePriority.normal, phase=SitePhase.disponiert)
    db.add_all([s1, s2])
    db.flush()
    tlf = LageEinheit(lage_id=lg.id, label="TLF Wolfurt", is_from_org=True, sector_id=sektor.id,
                      commander_label="Max Muster", committed_at=datetime(2026, 10, 20, 6, 30))
    rk = LageEinheit(lage_id=lg.id, label="RK Bregenz", resource_type="extern", org_name="Rotes Kreuz", bos="RK",
                     status="angefordert", requested_at=datetime(2026, 10, 20, 6, 40))
    db.add_all([tlf, rk])
    db.flush()
    db.add_all([
        EinheitSiteDispatch(einheit_id=tlf.id, site_id=s1.id, dispatched_at=datetime(2026, 10, 20, 6, 31),
                            auftrag="Baum entfernen", einheit_status="abgeschlossen",
                            vor_ort_at=datetime(2026, 10, 20, 6, 50), beendet_at=datetime(2026, 10, 20, 7, 30),
                            beendet_grund="Strasse frei", author_name="Stab"),
        EinheitSiteDispatch(einheit_id=tlf.id, site_id=s2.id, dispatched_at=datetime(2026, 10, 20, 7, 35),
                            auftrag="Keller auspumpen", einheit_status="anfahrt",
                            status_at=datetime(2026, 10, 20, 7, 40)),
        SiteLogEntry(incident_site_id=s2.id, ts=datetime(2026, 10, 20, 7, 50), kind="lagemeldung",
                     text="Wasserstand 40 cm, Pumpe laeuft", einheit_id=tlf.id, author_name="Max Muster"),
        LageJournalEntry(major_incident_id=lg.id, ts=datetime(2026, 10, 20, 6, 41), category="ressource",
                         text="RK Bregenz angefordert", einheit_id=rk.id, author_name="Stab"),
    ])
    for i in range(80):  # mehr als die frueheren 50 Eintraege
        db.add(CommLogEntry(major_incident_id=lg.id, ts=datetime(2026, 10, 20, 8, 0) + timedelta(minutes=i),
                            direction="in", channel="Funk", partner="Leitstelle", message=f"Funkspruch {i:03d}"))
    db.commit()
    db.refresh(lg)
    yield lg, db
    db.close()


def _pdf_text(lg, db):
    ctx = build_bericht_context(db, lg)
    ctx["user"] = SimpleNamespace(org=db.get(FireDept, lg.org_id))
    ctx["qr_datauri"] = None
    from app.core.templating import templates

    html_str = templates.env.get_template("incident_major/druck_bericht.html").render(**ctx)
    pdf = _html_to_pdf(html_str, "")
    seiten = [(p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(pdf)).pages]
    return html_str, seiten


def _n(text):
    # Ueberschriften sind per CSS gross geschrieben; Vergleich daher ohne Gross-/Kleinschreibung
    return re.sub(r"\s+", " ", text).lower()


def test_bericht_enthaelt_ressourcen_auftragsverlauf_statushistorie_und_ressourcenjournal(lage):
    lg, db = lage
    _, seiten = _pdf_text(lg, db)
    text = _n(" ".join(seiten))
    for erwartet in tuple(e.lower() for e in (
        "Ressourcenübersicht", "TLF Wolfurt", "Max Muster", "Rotes Kreuz", "Angefordert", "Nord",
        "Auftragsverlauf je Einsatzstelle", "Baum entfernen", "Strasse frei", "Keller auspumpen", "Auf Anfahrt",
        "Statushistorie und Lagemeldungen", "Wasserstand 40 cm, Pumpe laeuft",
        "Ressourcenjournal", "RK Bregenz angefordert",
        "Erledigt", "Sofort",
    )):
        assert erwartet in text, erwartet


def test_funkjournal_ist_vollstaendig_und_chronologisch(lage):
    lg, db = lage
    html_str, seiten = _pdf_text(lg, db)
    gesamt = " ".join(seiten)
    assert "funkjournal (80 einträge)" in _n(gesamt)
    for i in range(80):
        assert f"Funkspruch {i:03d}" in gesamt, i
    assert gesamt.index("Funkspruch 000") < gesamt.index("Funkspruch 079")


def test_seitenzaehler_und_a4_seitenformat(lage):
    lg, db = lage
    html_str, seiten = _pdf_text(lg, db)
    assert "@page" in html_str and "size: A4 portrait" in html_str
    assert len(seiten) >= 3
    assert any(re.search(r"Seite \d+ / \d+", s) for s in seiten)


def test_dokumentationstabellen_wiederholen_den_kopf_und_ragen_nicht_ueber_den_rand(lage):
    weasyprint = pytest.importorskip("weasyprint")
    lg, db = lage
    html_str, seiten = _pdf_text(lg, db)
    mit_kopf = [s for s in seiten if "Funkspruch" in s and re.search(r"Zeit\s+Richtung", s)]
    assert len(mit_kopf) >= 2
    dokument = weasyprint.HTML(string=html_str).render()
    seitenbreite = 595.0 * 96 / 72
    rechts = 0.0
    for seite in dokument.pages:
        stapel = [seite._page_box]
        while stapel:
            box = stapel.pop()
            if type(box).__name__ == "TextBox":
                rechts = max(rechts, box.position_x + box.width)
            stapel.extend(getattr(box, "children", []) or [])
    assert rechts <= seitenbreite - 12 * 96 / 25.4 + 2, rechts
