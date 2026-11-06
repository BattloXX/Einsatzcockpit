"""P2: Lesende Verbraucher zeigen Einzeichnungen, Originalauswahl bleibt moeglich."""
import base64
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from pypdf import PdfReader

from app.config import settings
from app.models.incident import Incident, Message, Task
from app.models.objekt import ObjektDokumentSeite
from app.services import dokumentdruck_service, objekt_pdf_service
from app.services import objekt_seite_annotation as annotation
from app.services.mcp_dokument_seiten_service import bild_anhaengen, seite_ergebnis
from app.services.objekt_dokument_service import sammel_pdf
from app.services.print_artifact_service import _render_objekt_dokument, _render_objekt_sammel
from app.services.takeover_service import uebernehme_seiten
from tests.test_objekt_dokumente_wartend_ui import _login
from tests.test_objekt_seite_annotation import _png, _save
from tests.test_objekt_seite_annotation import env as env  # noqa: F401


def _gestempelte_seite(env):
    env.dokument.ist_aktuelle_version = True
    env.dokument.freigabe_status = "freigegeben"
    _save(env, overlay=_png(color=(255, 0, 0, 255)))
    env.db.commit()


def _pdf_seite(daten, index=0):
    return PdfReader(io.BytesIO(daten)).pages[index]


def _assert_stempel(page, quelle):
    assert page.mediabox == quelle.mediabox
    assert page.get_contents().get_data() == quelle.get_contents().get_data()
    assert len(page.images) == len(quelle.images) == 1
    assert page.images[0].image.convert("RGB").getpixel((40, 60)) == (255, 0, 0)


@pytest.mark.parametrize("format,helfer", [
    ("bild", annotation.seite_bild_datei), ("thumb", annotation.seite_thumb_datei),
    ("pdf", annotation.seite_pdf_datei),
])
def test_medien_einzeichnung_und_original(env, client, format, helfer):
    original = helfer(env.db, env.seite).read_bytes()
    _gestempelte_seite(env)
    erwartet = helfer(env.db, env.seite).read_bytes()
    assert erwartet != original
    _login(client, env.users["readonly"].username)
    url = f"/objekt-medien/seite/{env.seite.id}/{format}"
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == erwartet
    assert response.headers["cache-control"] == "private, no-cache"
    response = client.get(url + "?original=1")
    assert response.status_code == 200
    assert response.content == original


@pytest.mark.parametrize("format", ["bild", "thumb"])
def test_medien_rotation_auch_mit_einzeichnung(env, client, format):
    env.seite.rotation = 90
    _save(env, png=_png((120, 80)))
    env.db.commit()
    _login(client, env.users["readonly"].username)
    for suffix, rot in [("", (255, 0, 0)), ("?original=1", (255, 255, 255))]:
        response = client.get(f"/objekt-medien/seite/{env.seite.id}/{format}{suffix}")
        assert response.status_code == 200
        with Image.open(io.BytesIO(response.content)) as bild:
            assert bild.size == ((30, 20) if format == "thumb" and suffix else (120, 80))
            assert all(abs(a - b) <= 2 for a, b in zip(bild.convert("RGB").getpixel((10, 10)), rot))


def test_sammel_pdf_und_route_enthalten_stempel(env, client):
    _gestempelte_seite(env)
    quelle = PdfReader(annotation.seite_pdf_datei(env.db, env.seite)).pages[0]
    original = PdfReader(annotation.seite_pdf_datei(env.db, env.seite, original=True)).pages[0]
    assert not original.images
    daten = sammel_pdf(env.seiten, env.db)
    assert len(PdfReader(io.BytesIO(daten)).pages) == 2
    _assert_stempel(_pdf_seite(daten), quelle)
    _login(client, env.users["readonly"].username)
    response = client.get(f"/objekte/{env.objekt.id}/dokumente/sammel-pdf")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    _assert_stempel(_pdf_seite(response.content), quelle)


def test_druck_renderer_und_objektblatt(env, monkeypatch):
    _gestempelte_seite(env)
    erwartet = annotation.seite_pdf_datei(env.db, env.seite).read_bytes()
    job = SimpleNamespace(artifact_ref=str(env.seite.id), objekt_id=env.objekt.id, org_id=env.org.id)
    assert _render_objekt_dokument(env.db, job) == erwartet
    job.artifact_ref = ""
    _assert_stempel(_pdf_seite(_render_objekt_sammel(env.db, job)), _pdf_seite(erwartet))
    env.seite.bei_einsatz_drucken = True
    env.db.flush()
    blatt = annotation.seite_pdf_datei(env.db, env.seite, original=True).read_bytes()
    monkeypatch.setattr(objekt_pdf_service, "render_objektblatt_pdf", lambda *args, **kwargs: blatt)
    daten = objekt_pdf_service.objektblatt_mit_anhang(env.objekt, env.org, env.db)
    assert len(PdfReader(io.BytesIO(daten)).pages) == 2
    _assert_stempel(_pdf_seite(daten, 1), _pdf_seite(erwartet))


def test_dokumentdruck_seiten_pdf_und_original(env):
    _gestempelte_seite(env)
    url = f"/objekt-medien/seite/{env.seite.id}/pdf"
    for original in (False, True):
        daten, mime, _ = dokumentdruck_service.quell_datei(
            env.db, env.users["readonly"], url + ("?original=1" if original else ""))
        assert mime == "application/pdf"
        assert daten == annotation.seite_pdf_datei(env.db, env.seite, original=original).read_bytes()


@pytest.mark.parametrize("host_typ", ["task", "message"])
def test_uebernahme_kopiert_flaches_png(env, monkeypatch, tmp_path, host_typ):
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path / "einsatz"))
    _gestempelte_seite(env)
    incident = Incident(primary_org_id=env.org.id)
    env.db.add(incident)
    env.db.flush()
    host = Task(incident_id=incident.id, title="Plan") if host_typ == "task" else Message(incident_id=incident.id, title="Plan")
    env.db.add(host)
    env.db.flush()
    media, = uebernehme_seiten(env.db, host_typ, host, [env.seite.id], env.users["objekt_verwalter"], env.org.id)
    with Image.open(Path(settings.MEDIA_STORAGE_DIR) / media.storage_path) as bild:
        assert bild.size == (80, 120)
        rot, gruen, blau = bild.getpixel((40, 60))
        assert rot > 245 and gruen < 5 and blau < 5
    with Image.open(annotation.seite_bild_datei(env.db, env.seite, original=True)) as original:
        assert original.getpixel((40, 60)) == (255, 255, 255)


def test_mcp_zeigt_einzeichnung_und_metadaten(env):
    for gespeichert in (False, True):
        if gespeichert:
            _gestempelte_seite(env)
        ergebnis = seite_ergebnis(env.dokument, env.seite, env.db)
        bild_anhaengen(ergebnis, env.seite, 1600, db=env.db)
        assert ergebnis["hat_einzeichnung"] is gespeichert
        with Image.open(io.BytesIO(base64.b64decode(ergebnis["bild_base64"]))) as bild:
            rot, gruen, blau = bild.getpixel((40, 60))
            assert rot > 245
            assert (gruen < 5 and blau < 5) if gespeichert else (gruen > 245 and blau > 245)


@pytest.mark.parametrize("format,helfer", [
    ("bild", annotation.seite_bild_datei), ("thumb", annotation.seite_thumb_datei),
    ("pdf", annotation.seite_pdf_datei),
])
def test_ohne_einzeichnung_wie_bisher(env, client, format, helfer):
    _login(client, env.users["readonly"].username)
    original = helfer(env.db, env.seite, original=True).read_bytes()
    for suffix in ("", "?original=1"):
        response = client.get(f"/objekt-medien/seite/{env.seite.id}/{format}{suffix}")
        assert response.status_code == 200
        assert response.content == original
    if format == "pdf":
        job = SimpleNamespace(artifact_ref=str(env.seite.id))
        assert _render_objekt_dokument(env.db, job) == original
        assert not _pdf_seite(sammel_pdf([env.seite], env.db)).images


@pytest.mark.parametrize("format,helfer", [
    ("bild", annotation.seite_bild_datei), ("thumb", annotation.seite_thumb_datei),
    ("pdf", annotation.seite_pdf_datei),
])
def test_einheit_auslieferung_einzeichnung_und_original(env, monkeypatch, format, helfer):
    from app.routers.ui_einheit import auftrag_plan
    from app.services import einheit_vorschau_service

    _gestempelte_seite(env)
    monkeypatch.setattr("app.routers.ui_einheit.auftrag_laden", lambda *args: SimpleNamespace())
    monkeypatch.setattr(einheit_vorschau_service, "einsatzobjekte", lambda *args: [env.objekt])
    monkeypatch.setattr(einheit_vorschau_service, "einsatzseiten", lambda *args: env.db.query(ObjektDokumentSeite))
    for original in (False, True):
        response = auftrag_plan(1, env.seite.id, format, SimpleNamespace(), env.db, original=original)
        assert Path(response.path).read_bytes() == helfer(env.db, env.seite, original=original).read_bytes()
        assert response.headers["cache-control"] == "no-cache"
