"""Routentests für Phase 7: Probe-Medien, Skizzen und Tenant-Isolation."""

import base64
import io

from PIL import Image

from app.config import settings
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings
from app.models.media_annotation import MediaAnnotation
from app.models.probenplanung import ProbeMedia
from app.models.user import Role, User, UserRole
from app.services.probe_media_service import probe_media_path, probe_thumb_path
from tests.test_probenplanung_checkliste import ORG_ID, _flags, _login, _probe_anlegen, _probeart, _user


def _image_bytes(fmt: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (12, 8), (20, 80, 140)).save(output, format=fmt)
    return output.getvalue()


def _setup(client, suffix: str) -> tuple[str, int]:
    username = f"phase7_{suffix}"
    _user(username, "probenverwalter")
    _flags()
    csrf = _login(client, username)
    probeart_id = _probeart(0, f"Phase7 Art {suffix}")
    return csrf, _probe_anlegen(client, csrf, probeart_id, f"Phase7 Probe {suffix}")


def _upload(client, csrf: str, termin_id: int, data: bytes, filename: str, content_type: str, **fields):
    form = {
        "_csrf": csrf,
        "art": fields.get("art", "dokument"),
        "name": fields.get("name", filename),
        "typ": fields.get("typ", "Objektplan"),
        "beschreibung": fields.get("beschreibung", "Unterlage zur Probe"),
    }
    return client.post(
        f"/probenplanung/{termin_id}/medien",
        data=form,
        files={"datei": (filename, data, content_type)},
        follow_redirects=False,
    )


def _latest_media(termin_id: int) -> ProbeMedia:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.query(ProbeMedia).filter_by(termin_id=termin_id).order_by(ProbeMedia.id.desc()).first()
    finally:
        db.close()


def test_upload_jpg_png_pdf_und_metadaten(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROBE_MEDIA_DIR", str(tmp_path))
    csrf, termin_id = _setup(client, "formate")
    uploads = [
        (_image_bytes("JPEG"), "lage.jpg", "image/jpeg"),
        (_image_bytes("PNG"), "lage.png", "image/png"),
        (b"%PDF-1.4\n%%EOF\n", "plan.pdf", "application/pdf"),
    ]
    for index, (payload, filename, content_type) in enumerate(uploads):
        response = _upload(
            client, csrf, termin_id, payload, filename, content_type,
            name=f"Dokument {index}", typ="Brandschutzplan", beschreibung="Beschreibung",
        )
        assert response.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        rows = db.query(ProbeMedia).filter_by(termin_id=termin_id).order_by(ProbeMedia.id).all()
        assert [row.kind for row in rows] == ["image", "image", "pdf"]
        assert all(row.name.startswith("Dokument") and row.typ == "Brandschutzplan" for row in rows)
        assert all(row.beschreibung == "Beschreibung" and row.hochgeladen_von for row in rows)
        assert all(row.hochgeladen_am is not None for row in rows)
    finally:
        db.close()


def test_magic_bytes_statt_dateiendung_und_client_header(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROBE_MEDIA_DIR", str(tmp_path))
    csrf, termin_id = _setup(client, "magic")
    response = _upload(client, csrf, termin_id, b"kein bild", "fake.jpg", "image/jpeg")
    assert response.status_code == 415
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(ProbeMedia).filter_by(termin_id=termin_id).count() == 0
    finally:
        db.close()


def test_quota_verhindert_upload_ohne_dateileiche(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROBE_MEDIA_DIR", str(tmp_path))
    csrf, termin_id = _setup(client, "quota")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.get(FireDept, ORG_ID)
        org.storage_quota_bytes = 0
        db.commit()
    finally:
        db.close()
    try:
        response = _upload(client, csrf, termin_id, _image_bytes("JPEG"), "lage.jpg", "image/jpeg")
        assert response.status_code == 413
        assert not any(path.is_file() for path in tmp_path.rglob("*"))
    finally:
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            db.get(FireDept, ORG_ID).storage_quota_bytes = None
            db.commit()
        finally:
            db.close()


def _foreign_user(username: str) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"{username}-org", name="Fremde Organisation", color="#123456", bos="Feuerwehr")
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id, probenplanung_modul_aktiv=True))
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name=username,
            org_id=org.id,
            active=True,
        )
        db.add(user)
        db.flush()
        role = db.query(Role).filter_by(code="readonly").one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        return org.id
    finally:
        db.close()


def test_datei_und_thumbnail_sind_fuer_fremde_org_gesperrt(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROBE_MEDIA_DIR", str(tmp_path))
    csrf, termin_id = _setup(client, "isolation")
    assert _upload(client, csrf, termin_id, _image_bytes("PNG"), "lage.png", "image/png").status_code == 303
    media_id = _latest_media(termin_id).id
    assert client.get(f"/probenplanung/medien/{media_id}").status_code == 200
    assert client.get(f"/probenplanung/medien/{media_id}/thumb").status_code == 200

    username = "phase7_foreign"
    _foreign_user(username)
    _login(client, username)
    assert client.get(f"/probenplanung/medien/{media_id}").status_code in {403, 404}
    assert client.get(f"/probenplanung/medien/{media_id}/thumb").status_code in {403, 404}


def test_annotation_ueber_echte_routen_speichern_und_laden(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROBE_MEDIA_DIR", str(tmp_path))
    csrf, termin_id = _setup(client, "annotation")
    response = _upload(
        client, csrf, termin_id, _image_bytes("JPEG"), "lage.jpg", "image/jpeg", art="skizze",
    )
    assert response.status_code == 303
    media_id = _latest_media(termin_id).id
    redirect = client.get(f"/probenplanung/{termin_id}/skizze?media_id={media_id}", follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"] == f"/annotieren/probe/{media_id}"
    vector_data = '{"className":"Layer","children":[]}'
    saved = client.put(
        f"/api/annotation/probe/{media_id}",
        headers={"X-CSRF-Token": csrf},
        json={"annotation_json": vector_data},
    )
    assert saved.status_code == 200
    editor = client.get(f"/annotieren/probe/{media_id}")
    assert editor.status_code == 200
    assert "className" in editor.text and "children" in editor.text
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        annotation = db.query(MediaAnnotation).filter_by(media_typ="probe", media_id=media_id).one()
        assert annotation.annotation_json == vector_data
    finally:
        db.close()


def test_loeschen_entfernt_dateien_annotation_und_datenbankzeile(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROBE_MEDIA_DIR", str(tmp_path))
    csrf, termin_id = _setup(client, "delete")
    assert _upload(client, csrf, termin_id, _image_bytes("JPEG"), "lage.jpg", "image/jpeg").status_code == 303
    media = _latest_media(termin_id)
    path, thumb = probe_media_path(media), probe_thumb_path(media)
    assert path.exists() and thumb is not None and thumb.exists()
    png_data_url = "data:image/png;base64," + base64.b64encode(_image_bytes("PNG")).decode()
    assert client.put(
        f"/api/annotation/probe/{media.id}",
        headers={"X-CSRF-Token": csrf},
        json={"annotation_json": '{"children":[]}', "png": png_data_url},
    ).status_code == 200
    annotated_path = path.with_name(path.stem + "_annotated.png")
    assert annotated_path.exists()
    response = client.request(
        "DELETE",
        f"/probenplanung/{termin_id}/medien/{media.id}",
        headers={"X-CSRF-Token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert not path.exists() and not thumb.exists() and not annotated_path.exists()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(ProbeMedia, media.id) is None
        assert db.query(MediaAnnotation).filter_by(media_typ="probe", media_id=media.id).count() == 0
    finally:
        db.close()
