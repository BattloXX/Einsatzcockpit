"""Gemeinsame Druck-PDF-Route (lokaler Druck): org-Scoping + Dokumenttyp-Prüfung.

Testet die reine Zugriffslogik (_verify_org) ohne echtes PDF-Rendering (WeasyPrint).
"""
import pytest
from fastapi import HTTPException
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.atemschutz_pruefung import AtemschutzPruefung
from app.models.incident import Incident
from app.models.master import FireDept
from app.routers.ui_druck import _verify_org

_ORG_A = 970001
_ORG_B = 970002


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    set_tenant_context(s, None)
    yield s
    s.close()
    Base.metadata.drop_all(bind=eng)


@pytest.fixture
def incident(db):
    db.add(FireDept(id=_ORG_A, slug="a", name="Org A", color="#f00", bos="Feuerwehr"))
    db.flush()
    inc = Incident(primary_org_id=_ORG_A, alarm_type_code="T1", status="active")
    db.add(inc)
    db.flush()
    return inc


def test_verify_org_own_incident_ok(db, incident):
    # Kein Fehler für die eigene Org
    _verify_org(db, _ORG_A, "einsatzinfo", incident.id, None, None, None)


def test_verify_org_foreign_incident_404(db, incident):
    with pytest.raises(HTTPException) as ei:
        _verify_org(db, _ORG_B, "einsatzinfo", incident.id, None, None, None)
    assert ei.value.status_code == 404


def test_verify_org_missing_incident_404(db, incident):
    with pytest.raises(HTTPException) as ei:
        _verify_org(db, _ORG_A, "einsatzinfo", None, None, None, None)
    assert ei.value.status_code == 404


def test_verify_org_unknown_type_400(db, incident):
    with pytest.raises(HTTPException) as ei:
        _verify_org(db, _ORG_A, "quatsch", incident.id, None, None, None)
    assert ei.value.status_code == 400


# ── Atemschutzprüfung (artifact_ref = kommagetrennte IDs) ───────────────────────

@pytest.fixture
def pruefungen(db):
    from datetime import date
    db.add(FireDept(id=_ORG_A, slug="a", name="Org A", color="#f00", bos="Feuerwehr"))
    db.flush()
    p1 = AtemschutzPruefung(org_id=_ORG_A, geraet_id=1, eingesetzt_am=date.today())
    p2 = AtemschutzPruefung(org_id=_ORG_A, geraet_id=1, eingesetzt_am=date.today())
    db.add_all([p1, p2])
    db.flush()
    return [p1, p2]


def test_verify_org_as_pruefung_own_ok(db, pruefungen):
    ref = ",".join(str(p.id) for p in pruefungen)
    _verify_org(db, _ORG_A, "as_pruefung", None, None, None, ref)  # kein Fehler


def test_verify_org_as_pruefung_foreign_404(db, pruefungen):
    ref = str(pruefungen[0].id)
    with pytest.raises(HTTPException) as ei:
        _verify_org(db, _ORG_B, "as_pruefung", None, None, None, ref)
    assert ei.value.status_code == 404


def test_verify_org_as_pruefung_partly_foreign_404(db, pruefungen):
    # Eine eigene + eine nicht existente ID → nicht alle gehören der Org → 404
    ref = f"{pruefungen[0].id},99999999"
    with pytest.raises(HTTPException) as ei:
        _verify_org(db, _ORG_A, "as_pruefung", None, None, None, ref)
    assert ei.value.status_code == 404


# ── Teilnehmerliste (Bezug-Org-Schutz gegen _bezug_meta-Leak) ───────────────────

def test_verify_org_teilnahme_own_einsatz_ok(db, incident):
    _verify_org(db, _ORG_A, "teilnahme", None, None, None, f"einsatz:{incident.id}")


def test_verify_org_teilnahme_foreign_einsatz_404(db, incident):
    with pytest.raises(HTTPException) as ei:
        _verify_org(db, _ORG_B, "teilnahme", None, None, None, f"einsatz:{incident.id}")
    assert ei.value.status_code == 404


def test_verify_org_teilnahme_bad_ref_404(db, incident):
    with pytest.raises(HTTPException) as ei:
        _verify_org(db, _ORG_A, "teilnahme", None, None, None, "einsatz")
    assert ei.value.status_code == 404


# ── Verleihschein (gsl_id + ausleihe_id) ────────────────────────────────────────

def test_verify_org_verleih_missing_404(db, incident):
    # Ohne gsl_id/ausleihe_id → keine Lage → 404 (kein Leak fremder Daten).
    with pytest.raises(HTTPException) as ei:
        _verify_org(db, _ORG_A, "verleih_schein", None, None, None, None)
    assert ei.value.status_code == 404


# ── QR-Einsatz + GSL-Bericht + Objektblatt (_verify_org) ────────────────────────

def test_verify_org_qr_einsatz_own_ok(db, incident):
    _verify_org(db, _ORG_A, "qr_einsatz", incident.id, None, None, None)


def test_verify_org_qr_einsatz_foreign_404(db, incident):
    with pytest.raises(HTTPException) as ei:
        _verify_org(db, _ORG_B, "qr_einsatz", incident.id, None, None, None)
    assert ei.value.status_code == 404


@pytest.fixture
def lage(db):
    from app.models.major_incident import MajorIncident
    db.add(FireDept(id=_ORG_A, slug="a", name="Org A", color="#f00", bos="Feuerwehr"))
    db.flush()
    lg = MajorIncident(org_id=_ORG_A, name="Testlage")
    db.add(lg)
    db.flush()
    return lg


def test_verify_org_gsl_bericht_own_ok(db, lage):
    _verify_org(db, _ORG_A, "gsl_bericht", None, lage.id, None, None)


def test_verify_org_gsl_bericht_foreign_404(db, lage):
    with pytest.raises(HTTPException) as ei:
        _verify_org(db, _ORG_B, "gsl_bericht", None, lage.id, None, None)
    assert ei.value.status_code == 404


# ── Leaflet-Karten: HTML-Render statt PDF (Gateway-Chromium) ────────────────────

def test_is_html_render_and_artifact_url_for_maps(db, lage):
    from types import SimpleNamespace

    from app.services.print_artifact_service import artifact_url, is_html_render

    map_job = SimpleNamespace(id=123, org_id=_ORG_A, document_type="lage_karte",
                              gsl_id=lage.id, artifact_ref="min_lat=1&fmt=A4 portrait")
    pdf_job = SimpleNamespace(id=124, org_id=_ORG_A, document_type="einsatzinfo",
                              gsl_id=None, artifact_ref=None)
    assert is_html_render(map_job) is True
    assert is_html_render(pdf_job) is False
    assert "/api/v1/print/render/123" in artifact_url(map_job)
    assert "/api/v1/print/artifacts/124" in artifact_url(pdf_job)


def test_render_map_html_lage_karte(db, lage):
    """render_map_html liefert die Leaflet-Seite im render_mode (Chromium-Signal)."""
    from types import SimpleNamespace

    from app.services.print_artifact_service import render_map_html

    job = SimpleNamespace(document_type="lage_karte", gsl_id=lage.id, org_id=_ORG_A,
                          artifact_ref="min_lat=48.1&min_lng=9.7&max_lat=48.2&max_lng=9.8&fmt=A4 landscape")
    html = render_map_html(db, job)
    assert "leaflet" in html.lower()
    assert "__ecpgReady = true" in html          # render_mode aktiv
    assert "window.print()" in html              # lokaler Zweig bleibt im Template


def test_render_map_html_foreign_org_raises(db, lage):
    from types import SimpleNamespace

    from app.services.print_artifact_service import ArtifactError, render_map_html

    job = SimpleNamespace(document_type="lage_karte", gsl_id=lage.id, org_id=_ORG_B,
                          artifact_ref="min_lat=48.1&min_lng=9.7&max_lat=48.2&max_lng=9.8&fmt=A4 portrait")
    with pytest.raises(ArtifactError):
        render_map_html(db, job)


def test_render_verleih_schein_pdf(db):
    """End-to-End: echte Zeilen → render_job_pdf liefert ein PDF (Template + Pipeline)."""
    from types import SimpleNamespace

    from app.models.major_incident import MajorIncident
    from app.models.verleih import VerleihAusleihe, VerleihStatus
    from app.services.print_artifact_service import render_job_pdf

    db.add(FireDept(id=_ORG_A, slug="a", name="Org A", color="#f00", bos="Feuerwehr"))
    db.flush()
    lage = MajorIncident(org_id=_ORG_A, name="Testlage")
    db.add(lage)
    db.flush()
    a = VerleihAusleihe(org_id=_ORG_A, lage_id=lage.id, name="Max Muster",
                        status=VerleihStatus.ausgeliehen)
    db.add(a)
    db.flush()
    job = SimpleNamespace(document_type="verleih_schein", gsl_id=lage.id, incident_id=None,
                          objekt_id=None, artifact_ref=str(a.id), org_id=_ORG_A)
    pdf = render_job_pdf(db, job)
    assert isinstance(pdf, bytes) and len(pdf) > 500
    assert pdf[:4] == b"%PDF"
