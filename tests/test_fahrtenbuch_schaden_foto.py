"""Tests: Schadensfotos im Fahrtenbuch (Upload, signierte Teams-Bild-URL, Adaptive
Card, Ende-zu-Ende-Formular-Upload + Auslieferung)."""
from __future__ import annotations

import io

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

from app.core.tenant import set_tenant_context
from app.models.fahrtenbuch import Fahrt, FahrtErfassungsweg, FahrtKategorie, FahrtMedia, Fahrtzweck
from app.models.master import VehicleMaster
from app.models.user import Role, User, UserRole
from app.services.fahrtenbuch_service import erstelle_fahrt


def _fake_upload(filename: str, data: bytes, content_type: str = "image/jpeg") -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(data), headers={"content-type": content_type})


def _make_jpeg_bytes(w=400, h=300) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color=(10, 20, 30)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture()
def db_session(setup_db):
    from tests.conftest import TestingSession
    db = TestingSession()
    set_tenant_context(db, None)
    yield db
    db.rollback()
    db.close()


@pytest.fixture()
def org(db_session):
    from app.models.master import FireDept
    dept = db_session.query(FireDept).first()
    assert dept, "Keine Org in der Test-DB"
    return dept


@pytest.fixture()
def fahrzeug(db_session, org):
    fz = VehicleMaster(dept_id=org.id, code="FOTO-FZ", name="Foto-Testfahrzeug", type="Test",
                        display_order=97, erfasst_km=True)
    db_session.add(fz)
    db_session.flush()
    fz.km_aktuell = 1000
    db_session.flush()
    return fz


@pytest.fixture()
def zweck(db_session, org):
    z = Fahrtzweck(org_id=org.id, name="Foto-Testzweck", kategorie=FahrtKategorie.uebung)
    db_session.add(z)
    db_session.flush()
    return z


@pytest.fixture()
def fahrt_mit_schaden(db_session, org, fahrzeug, zweck):
    daten = {
        "org_id": org.id,
        "fahrzeug_id": fahrzeug.id,
        "zweck_id": zweck.id,
        "maschinist_name": "Max Mustermann",
        "km_stand_neu": 1010,
        "erfasst_via": FahrtErfassungsweg.web,
        "schaden_vorhanden": True,
        "schaden_betriebsfaehig": True,
        "schaden_beschreibung": "Testschaden",
    }
    fahrt = erstelle_fahrt(daten, db_session)
    db_session.commit()
    return fahrt


# ── media_service.store_upload_for_schaden_foto ─────────────────────────────

@pytest.mark.asyncio
async def test_store_upload_for_schaden_foto_creates_media(tmp_path, monkeypatch, db_session, org, fahrt_mit_schaden):
    from app.config import settings
    from app.services.media_service import store_upload_for_schaden_foto
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))

    upload = _fake_upload("schaden.jpg", _make_jpeg_bytes())
    media = await store_upload_for_schaden_foto(upload, fahrt_mit_schaden.id, org.id, db_session)
    db_session.commit()

    assert media.id is not None
    assert media.fahrt_id == fahrt_mit_schaden.id
    assert media.org_id == org.id
    assert media.mime_type == "image/jpeg"
    assert media.width == 400 and media.height == 300
    assert media.uploaded_by_user_id is None  # kein user (Token-Flow-Fall)
    assert (tmp_path / media.storage_path).exists()
    assert (tmp_path / media.thumb_path).exists()


@pytest.mark.asyncio
async def test_store_upload_for_schaden_foto_rejects_non_image(
    tmp_path, monkeypatch, db_session, org, fahrt_mit_schaden,
):
    from app.config import settings
    from app.services.media_service import store_upload_for_schaden_foto
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))

    upload = _fake_upload("boese.txt", b"kein bild, nur text", content_type="text/plain")
    with pytest.raises(HTTPException) as exc_info:
        await store_upload_for_schaden_foto(upload, fahrt_mit_schaden.id, org.id, db_session)
    assert exc_info.value.status_code == 415


@pytest.mark.asyncio
async def test_store_upload_for_schaden_foto_rejects_empty_file(
    tmp_path, monkeypatch, db_session, org, fahrt_mit_schaden,
):
    from app.config import settings
    from app.services.media_service import store_upload_for_schaden_foto
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))

    upload = _fake_upload("leer.jpg", b"")
    with pytest.raises(HTTPException) as exc_info:
        await store_upload_for_schaden_foto(upload, fahrt_mit_schaden.id, org.id, db_session)
    assert exc_info.value.status_code == 400


# ── app/core/security.py: sign/unsign fahrt foto token ──────────────────────

def test_fahrt_foto_token_roundtrip():
    from app.core.security import sign_fahrt_foto_token, unsign_fahrt_foto_token
    token = sign_fahrt_foto_token(42, 7)
    assert unsign_fahrt_foto_token(token) == (42, 7)


def test_fahrt_foto_token_rejects_tampering():
    from app.core.security import sign_fahrt_foto_token, unsign_fahrt_foto_token
    token = sign_fahrt_foto_token(42, 7)
    assert unsign_fahrt_foto_token(token + "x") is None
    assert unsign_fahrt_foto_token("kompletter-unsinn") is None


# ── teams_card.build_schaden_message_card ────────────────────────────────────

def test_build_schaden_message_card_ohne_foto_hat_kein_image_block(db_session, org, fahrzeug, fahrt_mit_schaden):
    from app.services.teams_card import build_schaden_message_card
    payload = build_schaden_message_card(
        fahrt_mit_schaden, fahrzeug, betreff="Schadenmeldung Test", foto_urls=[], detail_url=None,
    )
    body = payload["attachments"][0]["content"]["body"]
    assert not any(b["type"] in ("Image", "ImageSet") for b in body)


def test_build_schaden_message_card_ein_foto_nutzt_image_block(db_session, org, fahrzeug, fahrt_mit_schaden):
    from app.services.teams_card import build_schaden_message_card
    payload = build_schaden_message_card(
        fahrt_mit_schaden, fahrzeug, betreff="Schadenmeldung Test",
        foto_urls=["https://example.at/foto1.jpg"], detail_url="https://example.at/detail",
    )
    body = payload["attachments"][0]["content"]["body"]
    images = [b for b in body if b["type"] == "Image"]
    assert len(images) == 1
    assert images[0]["url"] == "https://example.at/foto1.jpg"
    actions = payload["attachments"][0]["content"]["actions"]
    assert actions[0]["url"] == "https://example.at/detail"


def test_build_schaden_message_card_mehrere_fotos_nutzt_imageset(db_session, org, fahrzeug, fahrt_mit_schaden):
    from app.services.teams_card import build_schaden_message_card
    urls = ["https://example.at/foto1.jpg", "https://example.at/foto2.jpg"]
    payload = build_schaden_message_card(
        fahrt_mit_schaden, fahrzeug, betreff="Schadenmeldung Test", foto_urls=urls, detail_url=None,
    )
    body = payload["attachments"][0]["content"]["body"]
    image_sets = [b for b in body if b["type"] == "ImageSet"]
    assert len(image_sets) == 1
    assert [img["url"] for img in image_sets[0]["images"]] == urls


# ── schaden_service._foto_urls: PUBLIC_BASE_URL-Gate ─────────────────────────

def test_foto_urls_leer_ohne_medien(fahrt_mit_schaden):
    from app.services.schaden_service import _foto_urls
    assert _foto_urls(fahrt_mit_schaden) == []


def test_foto_urls_leer_ohne_https_public_base_url(monkeypatch, db_session, org, fahrt_mit_schaden):
    from app.config import settings
    from app.services.schaden_service import _foto_urls
    db_session.add(FahrtMedia(
        fahrt_id=fahrt_mit_schaden.id, org_id=org.id, original_filename="a.jpg",
        storage_path="fahrt/1/1/a.jpg", mime_type="image/jpeg", bytes=100,
    ))
    db_session.flush()
    db_session.refresh(fahrt_mit_schaden)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr(settings, "APP_BASE_URL", "http://localhost:8000")
    assert _foto_urls(fahrt_mit_schaden) == []


def test_foto_urls_signiert_bei_https_public_base_url(monkeypatch, db_session, org, fahrt_mit_schaden):
    from app.config import settings
    from app.core.security import unsign_fahrt_foto_token
    from app.services.schaden_service import _foto_urls
    media = FahrtMedia(
        fahrt_id=fahrt_mit_schaden.id, org_id=org.id, original_filename="a.jpg",
        storage_path="fahrt/1/1/a.jpg", mime_type="image/jpeg", bytes=100,
    )
    db_session.add(media)
    db_session.flush()
    db_session.refresh(fahrt_mit_schaden)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://ec.example.at")

    urls = _foto_urls(fahrt_mit_schaden)
    assert len(urls) == 1
    assert urls[0].startswith(f"https://ec.example.at/api/v1/teams/fahrt-foto/{media.id}.jpg?sig=")
    sig = urls[0].split("sig=")[1]
    assert unsign_fahrt_foto_token(sig) == (media.id, org.id)


# ── Öffentliche No-Login-Route: /api/v1/teams/fahrt-foto/{id}.jpg ────────────

@pytest.mark.asyncio
async def test_public_foto_route_liefert_bild_mit_gueltiger_signatur(
    tmp_path, monkeypatch, db_session, org, fahrt_mit_schaden, client: TestClient,
):
    from app.config import settings
    from app.core.security import sign_fahrt_foto_token
    from app.services.media_service import store_upload_for_schaden_foto
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))

    upload = _fake_upload("schaden.jpg", _make_jpeg_bytes())
    media = await store_upload_for_schaden_foto(upload, fahrt_mit_schaden.id, org.id, db_session)
    db_session.commit()

    sig = sign_fahrt_foto_token(media.id, org.id)
    r = client.get(f"/api/v1/teams/fahrt-foto/{media.id}.jpg?sig={sig}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")


def test_public_foto_route_lehnt_ungueltige_signatur_ab(client: TestClient):
    r = client.get("/api/v1/teams/fahrt-foto/99999.jpg?sig=unsinn")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_public_foto_route_lehnt_falsche_media_id_ab(
    tmp_path, monkeypatch, db_session, org, fahrt_mit_schaden, client: TestClient,
):
    """Signatur ist für media_id=X gültig, wird aber für eine andere id in der URL
    verwendet — muss trotz gültiger Signatur abgelehnt werden (kein Cross-Media-Zugriff)."""
    from app.config import settings
    from app.core.security import sign_fahrt_foto_token
    from app.services.media_service import store_upload_for_schaden_foto
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))

    upload = _fake_upload("schaden.jpg", _make_jpeg_bytes())
    media = await store_upload_for_schaden_foto(upload, fahrt_mit_schaden.id, org.id, db_session)
    db_session.commit()

    sig = sign_fahrt_foto_token(media.id, org.id)
    r = client.get(f"/api/v1/teams/fahrt-foto/{media.id + 1}.jpg?sig={sig}")
    assert r.status_code == 403


# ── Ende-zu-Ende: Formular-Upload über /fahrtenbuch (multipart) ─────────────

def _login(client: TestClient, db_session, org, username: str, role_code: str = "readonly"):
    from app.core.security import hash_password
    user = User(
        username=username, password_hash=hash_password("Test1234!"),
        display_name=username, org_id=org.id, active=True,
    )
    db_session.add(user)
    db_session.flush()
    role = db_session.query(Role).filter(Role.code == role_code).first()
    if not role:
        role = Role(code=role_code, label=role_code)
        db_session.add(role)
        db_session.flush()
    db_session.add(UserRole(user_id=user.id, role_id=role.id))
    db_session.commit()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    r = client.post("/login", data={"username": username, "password": "Test1234!", "_csrf": csrf},
                    follow_redirects=False)
    assert r.status_code == 302
    return user


def test_fahrtenbuch_formular_upload_legt_fahrt_media_an(
    tmp_path, monkeypatch, client: TestClient, db_session, org, fahrzeug, zweck,
):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    _login(client, db_session, org, "foto_upload_tester")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(
        "/fahrtenbuch",
        data={
            "_csrf": csrf,
            "t": "",
            "fahrzeug_id": str(fahrzeug.id),
            "maschinist_name": "Erika Musterfrau",
            "km_stand_neu": "1020",
            "km_warnung_bestaetigt": "on",
            "zweck_id": str(zweck.id),
            "zeitpunkt": "2026-07-14T10:00",
            "schaden_vorhanden": "on",
            "schaden_betriebsfaehig": "on",
            "schaden_beschreibung": "Stoßstange verbeult",
        },
        files=[("schaden_fotos", ("schaden1.jpg", _make_jpeg_bytes(), "image/jpeg"))],
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text[:500]

    fahrt = db_session.query(Fahrt).filter(Fahrt.fahrzeug_id == fahrzeug.id).order_by(Fahrt.id.desc()).first()
    assert fahrt is not None and fahrt.schaden_vorhanden is True
    medien = db_session.query(FahrtMedia).filter(FahrtMedia.fahrt_id == fahrt.id).all()
    assert len(medien) == 1
    assert medien[0].uploaded_by_user_id is not None


def test_fahrtenbuch_formular_upload_zu_viele_fotos_wird_gekappt(
    tmp_path, monkeypatch, client: TestClient, db_session, org, fahrzeug, zweck,
):
    from app.config import settings
    from app.routers.ui_fahrtenbuch import MAX_SCHADEN_FOTOS
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    _login(client, db_session, org, "foto_cap_tester")
    csrf = client.cookies.get("ec_csrf")

    files = [
        ("schaden_fotos", (f"schaden{i}.jpg", _make_jpeg_bytes(), "image/jpeg"))
        for i in range(MAX_SCHADEN_FOTOS + 3)
    ]
    r = client.post(
        "/fahrtenbuch",
        data={
            "_csrf": csrf,
            "t": "",
            "fahrzeug_id": str(fahrzeug.id),
            "maschinist_name": "Erika Musterfrau",
            "km_stand_neu": "1030",
            "km_warnung_bestaetigt": "on",
            "zweck_id": str(zweck.id),
            "zeitpunkt": "2026-07-14T11:00",
            "schaden_vorhanden": "on",
            "schaden_betriebsfaehig": "on",
            "schaden_beschreibung": "Viele Fotos",
        },
        files=files,
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text[:500]

    fahrt = db_session.query(Fahrt).filter(Fahrt.fahrzeug_id == fahrzeug.id).order_by(Fahrt.id.desc()).first()
    medien = db_session.query(FahrtMedia).filter(FahrtMedia.fahrt_id == fahrt.id).all()
    assert len(medien) == MAX_SCHADEN_FOTOS


def test_fahrtenbuch_formular_upload_ungueltiges_foto_verwirft_ganze_fahrt(
    tmp_path, monkeypatch, client: TestClient, db_session, org, fahrzeug, zweck,
):
    """Ein abgelehntes Foto (falscher Dateityp) darf die Fahrt NICHT halb speichern —
    Zähler-Update und Fahrt-Anlage müssen mit dem Foto-Fehler zusammen zurückrollen."""
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    _login(client, db_session, org, "foto_reject_tester")
    csrf = client.cookies.get("ec_csrf")

    count_before = db_session.query(Fahrt).filter(Fahrt.fahrzeug_id == fahrzeug.id).count()
    r = client.post(
        "/fahrtenbuch",
        data={
            "_csrf": csrf,
            "t": "",
            "fahrzeug_id": str(fahrzeug.id),
            "maschinist_name": "Erika Musterfrau",
            "km_stand_neu": "1040",
            "km_warnung_bestaetigt": "on",
            "zweck_id": str(zweck.id),
            "zeitpunkt": "2026-07-14T12:00",
            "schaden_vorhanden": "on",
            "schaden_betriebsfaehig": "on",
            "schaden_beschreibung": "Kaputte Datei",
        },
        files=[("schaden_fotos", ("boese.exe", b"MZ...nicht wirklich ein bild", "application/octet-stream"))],
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert db_session.query(Fahrt).filter(Fahrt.fahrzeug_id == fahrzeug.id).count() == count_before
