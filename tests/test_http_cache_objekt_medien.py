"""Bedingte Medienabrufe sparen Bytes und wahren die Organisationsgrenze."""

import io
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image
from starlette.requests import Request

from app.core.http_cache import datei_cache_header, unveraendert
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import ObjektDokumentSeite
from app.routers import ui_objekt_dokumente as medien
from app.services.logo_service import logo_verkleinern
from tests.test_objekt_medien_db_session import _url, einsatzinfo
from tests.test_objekt_medien_db_session import info_daten as info_daten, medien_daten as medien_daten


@pytest.mark.parametrize("variante", ["thumb", "bild", "pdf", "original", "symbol"])
def test_validatoren_und_fremde_org(client, medien_daten, variante):
    d = medien_daten
    einsatzinfo._anmelden(client, d.org, "readonly")
    url = _url(d, variante)
    antwort = client.get(url)
    assert antwort.status_code == 200
    assert antwort.headers["cache-control"] == "private, no-cache"
    etag = antwort.headers["etag"]
    assert etag.startswith('"') and not etag.startswith('W/')
    for headers in (
        {"If-None-Match": etag}, {"If-None-Match": '"alt", W/' + etag},
        {"If-None-Match": "*"}, {"If-Modified-Since": antwort.headers["last-modified"]},
    ):
        unveraendert = client.get(url, headers=headers)
        assert unveraendert.status_code == 304
        assert unveraendert.content == b""
        assert unveraendert.headers["etag"] == etag
        assert unveraendert.headers["cache-control"] == "private, no-cache"
    assert client.get(url, headers={"If-None-Match": '"alt"',
                                   "If-Modified-Since": antwort.headers["last-modified"]}).status_code == 200
    client.cookies.clear()
    einsatzinfo._anmelden(client, d.fremd, "readonly")
    assert client.get(url, headers={"If-None-Match": etag}).status_code == 404


@pytest.mark.parametrize("variante", ["bild", "thumb"])
def test_rotation_ohne_rendern_bei_304(client, medien_daten, monkeypatch, variante):
    d = medien_daten
    einsatzinfo._anmelden(client, d.org, "readonly")
    url = _url(d, variante)
    vorher = client.get(url).headers["etag"]
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.get(ObjektDokumentSeite, d.seite).rotation = 90
        db.commit()
    antwort = client.get(url, headers={"If-None-Match": vorher})
    assert antwort.status_code == 200
    assert antwort.headers["etag"] != vorher
    rendern = Mock(side_effect=AssertionError("Rendering bei 304"))
    monkeypatch.setattr(medien, "_bild_response", rendern)
    assert client.get(url, headers={"If-None-Match": antwort.headers["etag"]}).status_code == 304
    rendern.assert_not_called()


def test_annotationsversion_aendert_etag(client, medien_daten, monkeypatch):
    d = medien_daten
    einsatzinfo._anmelden(client, d.org, "readonly")
    url = _url(d, "bild")
    vorher = client.get(url).headers["etag"]
    for version in (1, 2):
        monkeypatch.setattr(medien, "annotierte_seiten", lambda db, ids: {d.seite: version})
        antwort = client.get(url, headers={"If-None-Match": vorher})
        assert antwort.status_code == 200
        assert antwort.headers["etag"] != vorher
        vorher = antwort.headers["etag"]


def test_dateiaenderung_und_ungueltiges_datum(tmp_path):
    pfad = tmp_path / "datei"
    pfad.write_bytes(b"alt")
    vorher = datei_cache_header(pfad, ["bild", False, 0, None])
    pfad.write_bytes(b"neuer Inhalt")
    assert datei_cache_header(pfad, ["bild", False, 0, None])["ETag"] != vorher["ETag"]
    for datum in ("kaputt", "Sun, 06 Nov 1994 08:49:37 GMT", "Sun, 06 Nov 1994 08:49:37"):
        request = Request({"type": "http", "headers": [(b"if-modified-since", datum.encode())]})
        assert unveraendert(request, vorher) is None


@pytest.mark.parametrize("suffix,cache", [("", "public, max-age=604800"),
    ("?v=123", "public, max-age=31536000, immutable"),
    ("?v=", "public, max-age=31536000, immutable")])
def test_static_fallback(client, suffix, cache):
    url = "/static/img/logo_einsatzcockpit.png" + suffix
    antwort = client.get(url)
    assert antwort.status_code == 200
    assert antwort.headers["cache-control"] == cache
    antwort = client.get(url, headers={"If-None-Match": antwort.headers["etag"]})
    assert antwort.status_code == 304
    assert antwort.headers["cache-control"] == cache


@pytest.mark.parametrize("formatname,endung", [("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp")])
def test_logo_format_exif_und_idempotenz(formatname, endung):
    bild = Image.new("RGBA" if formatname == "PNG" else "RGB", (1600, 800))
    exif = Image.Exif()
    exif[0x010E] = "Metadaten"
    ausgabe = io.BytesIO()
    bild.save(ausgabe, format=formatname, exif=exif)
    daten = logo_verkleinern(ausgabe.getvalue(), endung)
    with Image.open(io.BytesIO(daten)) as ergebnis:
        assert ergebnis.size == (512, 256)
        assert ergebnis.format == formatname
        assert not ergebnis.getexif()
        if formatname == "PNG":
            assert ergebnis.mode == "RGBA"
            assert ergebnis.getpixel((0, 0))[3] == 0
    assert logo_verkleinern(daten, endung) == daten


def test_cli_logos_verkleinern(tmp_path, monkeypatch, capsys):
    from app import cli
    from app.routers import ui_settings

    monkeypatch.setattr(ui_settings, "UPLOAD_DIR", tmp_path)
    pfad = tmp_path / "logo_org1.jpg"
    Image.new("RGB", (1500, 800)).save(pfad)
    svg = tmp_path / "logo_org2.svg"
    svg.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    monkeypatch.setattr("sys.argv", ["app.cli", "logos-verkleinern"])
    cli.main()
    assert "logo_org1.jpg:" in capsys.readouterr().out
    daten = pfad.read_bytes()
    stat = pfad.stat()
    cli.main()
    assert pfad.read_bytes() == daten
    assert pfad.stat().st_mtime_ns == stat.st_mtime_ns
    assert svg.read_bytes() == b'<svg xmlns="http://www.w3.org/2000/svg"/>'


def test_logo_datei_klein():
    pfad = Path("app/static/img/logo_einsatzcockpit.png")
    assert pfad.stat().st_size < 100_000
    with Image.open(pfad) as bild:
        assert bild.size == (512, 512)
        assert bild.format == "PNG"


def test_logo_upload_verkleinert(client, medien_daten, tmp_path, monkeypatch):
    from app.routers import ui_settings

    monkeypatch.setattr(ui_settings, "UPLOAD_DIR", tmp_path)
    einsatzinfo._anmelden(client, medien_daten.org, "org_admin")
    bild = io.BytesIO()
    Image.new("RGBA", (2000, 1000), (255, 0, 0, 80)).save(bild, format="PNG")
    client.get("/admin/settings/erscheinungsbild")
    antwort = client.post("/admin/settings/org", data={"_csrf": client.cookies.get("ec_csrf")},
                          files={"logo": ("logo.png", bild.getvalue(), "image/png")},
                          follow_redirects=False)
    assert antwort.status_code == 303
    with Image.open(tmp_path / f"logo_org{medien_daten.org}.png") as gespeichert:
        assert gespeichert.size == (512, 256)
        assert gespeichert.mode == "RGBA"
        assert gespeichert.getpixel((0, 0))[3] == 80


@pytest.mark.parametrize("variante", ["thumb", "bild", "pdf"])
def test_medienversion_private_immutable_nur_bei_passendem_v(client, medien_daten, variante):
    from app.services.objekt_seite_annotation import annotierte_seiten
    from app.services.objekt_service import build_sync_manifest, seite_medien_version

    d = medien_daten
    einsatzinfo._anmelden(client, d.org, "readonly")
    with SessionLocal() as db:
        set_tenant_context(db, None)
        seite = db.get(ObjektDokumentSeite, d.seite)
        version = seite_medien_version(seite, annotierte_seiten(db, [seite.id]).get(seite.id))
        manifest = build_sync_manifest(db, d.org)
        eintrag = next(s for o in manifest["objekte"] for s in o["seiten"] if s["seite_id"] == d.seite)
        assert eintrag["version"] == version
    url = _url(d, variante)
    for suffix, cache in (
        ("", "private, no-cache"), ("?v=alt", "private, no-cache"),
        ("?v=", "private, no-cache"), (f"?v={version}", "private, max-age=31536000, immutable"),
    ):
        antwort = client.get(url + suffix)
        assert antwort.status_code == 200
        assert antwort.headers["cache-control"] == cache
        for header in ({"If-None-Match": antwort.headers["etag"]},
                       {"If-Modified-Since": antwort.headers["last-modified"]}):
            bedingt = client.get(url + suffix, headers=header)
            assert bedingt.status_code == 304
            assert bedingt.headers["cache-control"] == cache
    client.cookies.clear()
    einsatzinfo._anmelden(client, d.fremd, "readonly")
    assert client.get(url + f"?v={version}", headers={"If-None-Match": antwort.headers["etag"]}).status_code == 404
