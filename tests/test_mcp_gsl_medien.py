"""End-to-end-Regressionen für GSL-Medien im MCP."""

import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import (
    CitizenReport,
    CrossMarkerMedia,
    CrossSiteMarker,
    IncidentSite,
    LageJournalEntry,
    LageJournalMedia,
    MajorIncident,
    SiteMedia,
)
from app.models.master import OrgSettings
from app.models.user import UserRole
from app.services.lage_media_service import cross_media_path, journal_media_path, site_media_path
from tests.test_mcp_gsl_ressourcen import _call, _new_seed
from tests.test_mcp_objekte import _token

JPEG = b"\xff\xd8\xff\xe0" + b"mcp-gsl-test" + b"\xff\xd9"
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0dIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _db():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


def _lage(seed, *, name="Medienlage"):
    db = _db()
    try:
        lage = MajorIncident(org_id=seed["org_id"], name=name, public_token=uuid4().hex)
        db.add(lage)
        db.flush()
        site = IncidentSite(major_incident_id=lage.id, org_id=seed["org_id"], bezeichnung="Medienstelle")
        journal = LageJournalEntry(major_incident_id=lage.id, text="Journal")
        marker = CrossSiteMarker(major_incident_id=lage.id, org_id=seed["org_id"], title="Marker")
        report = CitizenReport(
            major_incident_id=lage.id, description="Foto", reporter_name="Melder Geheim", reporter_contact="0664 Geheim"
        )
        db.add_all([site, journal, marker, report])
        db.flush()
        stelle = SiteMedia(incident_site_id=site.id, org_id=seed["org_id"], stored_filename="stelle.jpg",
                           original_filename="stelle.jpg", media_type="image", bytes=len(JPEG))
        journal_medium = LageJournalMedia(
            journal_entry_id=journal.id, org_id=seed["org_id"], stored_filename="journal.jpg",
            original_filename="journal.jpg", media_type="image", bytes=len(JPEG)
        )
        cross = CrossMarkerMedia(marker_id=marker.id, org_id=seed["org_id"], stored_filename="cross.jpg",
                                 original_filename="cross.jpg", media_type="image", bytes=len(JPEG))
        report.photo_filename = f"mcp-{report.id}.jpg"
        db.add_all([stelle, journal_medium, cross])
        db.commit()
        for medium, pfad in ((stelle, site_media_path(stelle)), (journal_medium, journal_media_path(journal_medium)),
                             (cross, cross_media_path(cross))):
            pfad.parent.mkdir(parents=True, exist_ok=True)
            pfad.write_bytes(JPEG)
        citizen = Path("app_storage/citizen_media") / report.photo_filename
        citizen.parent.mkdir(parents=True, exist_ok=True)
        citizen.write_bytes(JPEG)
        return {"lage": lage.id, "site": site.id, "stelle": stelle.id, "journal": journal_medium.id,
                "uebergreifend": cross.id, "meldung": report.id}
    finally:
        db.close()


@pytest.mark.parametrize("art", ["stelle", "journal", "uebergreifend", "meldung"])
def test_gsl_medien_download_aller_arten(client, art):
    seed = _new_seed(f"mcp-gsl-medien-{art}-{uuid4().hex[:8]}", {"reader": "readonly"})
    ids = _lage(seed)
    result = _call(client, _token(client, seed, "reader"), "gsl_medium_herunterladen",
                   lage_id=ids["lage"], art=art, medium_id=ids[art])
    response = client.get(urlsplit(result["download_url"]).path)
    assert response.status_code == 200, response.text
    assert response.content == JPEG
    assert response.headers["content-type"].startswith("image/jpeg")


def test_gsl_medien_sind_lage_und_organisationsgetrennt(client):
    own = _new_seed(f"mcp-gsl-medien-own-{uuid4().hex[:8]}", {"reader": "readonly"})
    foreign = _new_seed(f"mcp-gsl-medien-foreign-{uuid4().hex[:8]}", {"reader": "readonly"})
    own_ids, foreign_ids = _lage(own), _lage(foreign)
    token = _token(client, own, "reader")
    for lage_id, medium_id in ((own_ids["lage"], foreign_ids["stelle"]), (foreign_ids["lage"], foreign_ids["stelle"])):
        result = _call(client, token, "gsl_medium_herunterladen", lage_id=lage_id, art="stelle", medium_id=medium_id)
        assert "nicht gefunden" in result["__fehler__"]


def test_gsl_download_token_prueft_medium_und_rolle_live(client):
    seed = _new_seed(f"mcp-gsl-medien-live-{uuid4().hex[:8]}", {"reader": "readonly"})
    ids = _lage(seed)
    token = _token(client, seed, "reader")
    result = _call(client, token, "gsl_medium_herunterladen", lage_id=ids["lage"], art="stelle",
                   medium_id=ids["stelle"])
    db = _db()
    try:
        db.delete(db.get(SiteMedia, ids["stelle"]))
        db.commit()
    finally:
        db.close()
    assert client.get(urlsplit(result["download_url"]).path).status_code == 404

    ids = _lage(seed, name="Zweite Lage")
    result = _call(client, token, "gsl_medium_herunterladen", lage_id=ids["lage"], art="stelle",
                   medium_id=ids["stelle"])
    db = _db()
    try:
        db.query(UserRole).filter_by(user_id=seed["users"]["reader"]).delete()
        db.commit()
    finally:
        db.close()
    assert client.get(urlsplit(result["download_url"]).path).status_code == 403


@pytest.mark.parametrize(
    ("art", "flag"), [("meldung", "mi_feature_meldungen"), ("uebergreifend", "mi_feature_uebergreifend")]
)
def test_gsl_medien_download_blockiert_deaktivierte_feature(client, art, flag):
    seed = _new_seed(f"mcp-gsl-medien-flag-{uuid4().hex[:8]}", {"reader": "readonly"})
    ids = _lage(seed)
    result = _call(client, _token(client, seed, "reader"), "gsl_medium_herunterladen",
                   lage_id=ids["lage"], art=art, medium_id=ids[art])
    db = _db()
    try:
        setattr(db.query(OrgSettings).filter_by(org_id=seed["org_id"]).one(), flag, False)
        db.commit()
    finally:
        db.close()
    assert client.get(urlsplit(result["download_url"]).path).status_code == 403


def test_gsl_foto_upload_und_datenschutz(client):
    seed = _new_seed(f"mcp-gsl-medien-upload-{uuid4().hex[:8]}", {"edit": "recorder", "read": "readonly"})
    ids = _lage(seed)
    read = _token(client, seed, "read")
    assert "__fehler__" in _call(client, read, "gsl_einsatzstelle_foto_upload_vorbereiten",
                                  lage_id=ids["lage"], site_id=ids["site"], dateiname="x.jpg")
    prepared = _call(client, _token(client, seed, "edit"), "gsl_einsatzstelle_foto_upload_vorbereiten",
                     lage_id=ids["lage"], site_id=ids["site"], dateiname="foto.png", groesse_bytes=len(PNG))
    response = client.post(
        urlsplit(prepared["upload_url"]).path, headers={"Authorization": f"Bearer {prepared['upload_token']}"},
        files={"datei": ("foto.png", PNG, "image/png")}
    )
    assert response.status_code == 200, response.text
    done = _call(client, _token(client, seed, "edit"), "gsl_einsatzstelle_foto_uebergeben",
                 lage_id=ids["lage"], upload_id=prepared["upload_id"])
    db = _db()
    try:
        assert db.get(SiteMedia, done["medium_id"]) is not None
    finally:
        db.close()
    dump = json.dumps(_call(client, read, "gsl_meldungen", lage_id=ids["lage"]), ensure_ascii=False)
    assert "Melder Geheim" not in dump and "0664 Geheim" not in dump


def test_gsl_foto_upload_lehnt_nichtbild_und_fremde_stelle_ab(client):
    seed = _new_seed(f"mcp-gsl-medien-errors-{uuid4().hex[:8]}", {"edit": "recorder"})
    ids, other = _lage(seed), _lage(seed, name="Andere Lage")
    token = _token(client, seed, "edit")
    assert "nicht gefunden" in _call(client, token, "gsl_einsatzstelle_foto_upload_vorbereiten",
                                      lage_id=ids["lage"], site_id=other["site"], dateiname="x.jpg")["__fehler__"]
    prepared = _call(client, token, "gsl_einsatzstelle_foto_upload_vorbereiten",
                     lage_id=ids["lage"], site_id=ids["site"], dateiname="x.jpg")
    response = client.post(
        urlsplit(prepared["upload_url"]).path, headers={"Authorization": f"Bearer {prepared['upload_token']}"},
        files={"datei": ("x.txt", b"kein bild", "text/plain")}
    )
    assert response.status_code == 415
