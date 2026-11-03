"""P1: Seiten-Einzeichnungen, Rechte, Quota und Dokument-Lebenszyklus."""
import base64
import io
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader, PdfWriter

from app.config import settings
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.media_annotation import MediaAnnotation, MediaAnnotationVersion
from app.models.objekt import Objekt, ObjektDokument, ObjektDokumentSeite
from app.models.user import User, UserRole
from app.services import annotation_service as ann
from app.services import objekt_dokument_service as dokument_svc
from app.services import objekt_seite_annotation as service
from app.services.storage_service import get_org_storage_info
from tests.test_objekt_dokumente_wartend_ui import _login, _rolle
from tests.test_objekt_pr3 import _test_pdf


def _png(size=(80, 120), color=(255, 0, 0, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    db = SessionLocal()
    set_tenant_context(db, None)
    token = uuid4().hex
    org = FireDept(slug=f"seite-ann-{token}", name="Seitenannotation", color="#123456", bos="Feuerwehr")
    fremd = FireDept(slug=f"seite-ann-fremd-{token}", name="Andere Org", color="#123456", bos="Feuerwehr")
    db.add_all([org, fremd])
    db.flush()
    flag = db.get(SystemSettings, "objekt_module_enabled")
    vorher = flag.value if flag else None
    if flag is None:
        flag = SystemSettings(key="objekt_module_enabled", value="true")
        db.add(flag)
    flag.value = "true"
    db.add(OrgSettings(org_id=org.id, objekt_module_enabled=True))
    users = {}
    for role in ("objekt_verwalter", "readonly", "recorder", "incident_leader"):
        user = User(username=f"seite-ann-{role}-{token}", display_name=role,
                    org_id=org.id, password_hash=hash_password("Test1234!"), active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, role).id))
        users[role] = user
    objekt = Objekt(org_id=org.id, nummer=1, name="Seiten-Test", status="freigegeben")
    db.add(objekt)
    db.flush()
    dokument = dokument_svc.store_dokument_bytes(_test_pdf(2), "plan.pdf", objekt, None, db)
    basis = dokument.pfad.rsplit("/", 1)[0]
    seiten = []
    for nummer in (1, 2):
        rel = f"{basis}/seite_{nummer:04d}"
        pdf = dokument_svc.absolute_pfad(rel + ".pdf")
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with pdf.open("wb") as fh:
            writer.write(fh)
        Image.new("RGB", (80, 120), "white").save(pdf.with_suffix(".png"))
        Image.new("RGB", (20, 30), "white").save(pdf.with_name(pdf.stem + "_thumb.jpg"))
        seite = ObjektDokumentSeite(org_id=org.id, objekt_id=objekt.id, dokument_id=dokument.id,
                                    seiten_nr=nummer, bild_pfad=rel + ".png", thumb_pfad=rel + "_thumb.jpg",
                                    einzel_pdf_pfad=rel + ".pdf")
        db.add(seite)
        seiten.append(seite)
    dokument.status = "fertig"
    db.commit()
    yield SimpleNamespace(db=db, org=org, fremd=fremd, users=users, objekt=objekt,
                          dokument=dokument, seite=seiten[0], seiten=seiten, tmp=tmp_path)
    db.rollback()
    flag = db.get(SystemSettings, "objekt_module_enabled")
    if vorher is None:
        db.delete(flag)
    else:
        flag.value = vorher
    db.commit()
    db.close()


def _save(env, png=None, overlay=None):
    return service.speichere_objekt_seite(env.db, env.users["objekt_verwalter"], env.seite,
                                         '{"className":"Stage"}', png or _png(), overlay)


def _used(env):
    return get_org_storage_info(env.db, env.org.id)["used_bytes"]


def _headers(client):
    return {"X-CSRF-Token": client.cookies.get("ec_csrf")}


def test_speichern_dateien_pfade_versionen_quota(env):
    db, seite = env.db, env.seite
    vorher = _used(env)
    original = [service.seite_bild_datei(db, seite), service.seite_thumb_datei(db, seite),
                service.seite_pdf_datei(db, seite)]
    original_bytes = [p.read_bytes() for p in original]
    a = _save(env, overlay=_png(color=(0, 0, 255, 100)))
    db.commit()
    dateien = service.annotations_dateien(seite)
    assert [p.name for p in dateien] == ["seite_0001_annotated.png", "seite_0001_annotated_thumb.jpg",
                                          "seite_0001_annotated.pdf"]
    assert all(p.is_file() for p in dateien)
    assert [p.read_bytes() for p in original] == original_bytes
    assert json.loads(a.annotation_json) == {"rotation": 0, "stage": {"className": "Stage"}}
    assert a.annotated_by == env.users["objekt_verwalter"].id
    assert a.annotated_at is not None
    assert [service.seite_bild_datei(db, seite), service.seite_thumb_datei(db, seite),
            service.seite_pdf_datei(db, seite)] == list(dateien)
    assert [service.seite_bild_datei(db, seite, True), service.seite_thumb_datei(db, seite, True),
            service.seite_pdf_datei(db, seite, True)] == original
    groesse = sum(p.stat().st_size for p in dateien)
    assert _used(env) == vorher + groesse
    assert ann.total_annotated_bytes(db, env.org.id) == groesse
    assert service.annotierte_seiten(db, [seite.id]) == {seite.id: int(a.annotated_at.timestamp())}
    alter_stand = a.annotation_json
    _save(env, png=_png((140, 200)))
    db.commit()
    assert _used(env) == vorher + sum(p.stat().st_size for p in dateien)
    assert db.query(MediaAnnotationVersion).filter_by(annotation_id=a.id).one().annotation_json == alter_stand
    dateien[1].unlink()
    assert service.seite_thumb_datei(db, seite) == original[1]
    assert service.annotierte_seiten(db, []) == {}


@pytest.mark.parametrize("overlay", [True, False])
def test_rotation_und_pdf_mediabox(env, overlay):
    env.seite.rotation = 90
    original = Image.new("RGBA", (80, 120), "white")
    original.putpixel((7, 19), (255, 0, 0, 255))
    buf = io.BytesIO()
    original.rotate(-90, expand=True).save(buf, "PNG")
    png = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    _save(env, png=png, overlay=_png((120, 80), (0, 0, 255, 100)) if overlay else None)
    env.db.commit()
    bild, _, pdf = service.annotations_dateien(env.seite)
    with Image.open(bild) as gespeichert:
        assert gespeichert.size == original.size
        assert gespeichert.tobytes() == original.tobytes()
    reader = PdfReader(pdf)
    quellseite = PdfReader(dokument_svc.absolute_pfad(env.seite.einzel_pdf_pfad)).pages[0]
    assert len(reader.pages) == 1
    assert reader.pages[0].mediabox == quellseite.mediabox
    assert json.loads(ann.get_annotation(env.db, "objekt_seite", env.seite.id).annotation_json)["rotation"] == 90
    if not overlay:
        assert "/ASCIIHexDecode" not in str(reader.pages[0]["/Resources"])
        assert pdf.stat().st_size < 15000


def test_ohne_einzel_pdf(env):
    env.seite.einzel_pdf_pfad = None
    _save(env)
    assert not service.annotations_dateien(env.seite)[2].exists()
    assert service.seite_pdf_datei(env.db, env.seite) is None


def test_quota_abgelehnt_vor_dateischreiben(env):
    env.org.storage_quota_bytes = _used(env) + 1
    env.db.commit()
    with pytest.raises(HTTPException) as exc:
        _save(env)
    assert exc.value.status_code == 413
    assert not any(p.exists() for p in service.annotations_dateien(env.seite))
    assert ann.get_annotation(env.db, "objekt_seite", env.seite.id) is None


@pytest.mark.parametrize("role", ["readonly", "recorder", "incident_leader"])
def test_leserecht_ohne_schreibrecht(env, client, role):
    assert _login(client, env.users[role].username).status_code == 302
    ident = env.seite.id
    assert client.get(f"/annotieren/objekt_seite/{ident}").status_code == 200
    assert client.get(f"/annotieren/objekt_seite/{ident}/original").status_code == 200
    assert client.get(f"/annotieren/objekt_seite/{ident}/bild").status_code == 200
    assert client.put(f"/api/annotation/objekt_seite/{ident}", json={"annotation_json": "{}", "png": _png()},
                      headers=_headers(client)).status_code == 403
    assert client.post(f"/api/annotation/objekt_seite/{ident}/lock", headers=_headers(client)).status_code == 403
    assert client.delete(f"/api/annotation/objekt_seite/{ident}", headers=_headers(client)).status_code == 403


def test_editor_speichern_locks_entfernen(env, client):
    _login(client, env.users["objekt_verwalter"].username)
    ident = env.seite.id
    assert client.get(f"/annotieren/objekt_seite/{ident}").status_code == 200
    assert client.post(f"/api/annotation/objekt_seite/{ident}/lock", headers=_headers(client)).status_code == 200
    r = client.put(f"/api/annotation/objekt_seite/{ident}", headers=_headers(client),
                   json={"annotation_json": "{}", "png": _png(), "overlay_png": _png(color=(0, 0, 0, 0))})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "annotated": True}
    assert client.delete(f"/api/annotation/objekt_seite/{ident}/lock", headers=_headers(client)).status_code == 200
    assert client.post(f"/objekte/{env.objekt.id}/dokumente/seite/{ident}/drehen", headers=_headers(client),
                       data={"richtung": "rechts"}).status_code == 409
    assert client.delete(f"/api/annotation/objekt_seite/{ident}", headers=_headers(client)).status_code == 200
    env.db.expire_all()
    assert ann.get_annotation(env.db, "objekt_seite", ident) is None
    assert not any(p.exists() for p in service.annotations_dateien(env.seite))


@pytest.mark.parametrize("fall", ["fremde_org", "modul_org_aus", "modul_system_aus"])
def test_nicht_sichtbar_404(env, client, fall):
    _login(client, env.users["objekt_verwalter"].username)
    if fall == "fremde_org":
        env.seite.org_id = env.fremd.id
    elif fall == "modul_org_aus":
        env.db.query(OrgSettings).filter_by(org_id=env.org.id).one().objekt_module_enabled = False
    else:
        env.db.get(SystemSettings, "objekt_module_enabled").value = "false"
    env.db.commit()
    ident = env.seite.id
    for suffix in ("", "/original", "/bild"):
        assert client.get(f"/annotieren/objekt_seite/{ident}{suffix}").status_code == 404
    assert client.put(f"/api/annotation/objekt_seite/{ident}", headers=_headers(client),
                      json={"annotation_json": "{}", "png": _png()}).status_code == 404
    assert client.post(f"/api/annotation/objekt_seite/{ident}/lock", headers=_headers(client)).status_code == 404
    assert client.delete(f"/api/annotation/objekt_seite/{ident}/lock", headers=_headers(client)).status_code == 404
    assert client.delete(f"/api/annotation/objekt_seite/{ident}", headers=_headers(client)).status_code == 404


@pytest.mark.parametrize("dokument_loeschen", [False, True])
def test_entfernen_und_dokument_loeschen(env, dokument_loeschen):
    vorher = _used(env)
    _save(env)
    a = _save(env)
    ident = a.id
    dateien = service.annotations_dateien(env.seite)
    env.db.commit()
    if dokument_loeschen:
        verzeichnis = dokument_svc.delete_dokument(env.dokument, env.db)
        env.db.commit()
        dokument_svc.raeume_dokument_verzeichnis_auf(verzeichnis)
        assert not verzeichnis.exists()
        assert _used(env) == 0
    else:
        ann.delete_annotation_and_files(env.db, "objekt_seite", env.seite)
        env.db.commit()
        assert _used(env) == vorher
        assert service.seite_bild_datei(env.db, env.seite) == dokument_svc.absolute_pfad(env.seite.bild_pfad)
    assert not any(p.exists() for p in dateien)
    assert env.db.get(MediaAnnotation, ident) is None
    assert env.db.query(MediaAnnotationVersion).filter_by(annotation_id=ident).count() == 0


@pytest.mark.parametrize("mit_analyse", [False, True])
def test_neuverarbeitung_haengt_um_und_entfernt_fehlende_seiten(env, mit_analyse):
    _save(env)
    a = _save(env)
    env.seite.rotation = 90
    alt_id, ann_id = env.seite.id, a.id
    dateien = service.annotations_dateien(env.seite)
    datei_bytes = [p.read_bytes() for p in dateien]
    env.seite = env.seiten[1]
    entfernt = _save(env)
    entfern_id = entfernt.id
    weg_dateien = service.annotations_dateien(env.seite)
    original = dokument_svc.absolute_pfad(env.dokument.pfad)
    original.write_bytes(_test_pdf(1))
    env.dokument.seitenzahl = 1
    env.dokument.status = "fehler"
    env.db.commit()
    png = base64.b64decode(_png().split(",")[1])
    if mit_analyse:
        dokument_svc.verarbeite_dokument_mit_analyse(env.dokument.id, [{}], db=env.db,
                                                   render_func=lambda *_: png)
        env.db.commit()
    else:
        dokument_svc.verarbeite_dokument(env.dokument.id, render_func=lambda *_: png, ocr_func=lambda *_: "")
        env.db.expire_all()
    neue_seite = env.db.query(ObjektDokumentSeite).filter_by(dokument_id=env.dokument.id).one()
    assert neue_seite.id != alt_id
    assert neue_seite.rotation == 90
    assert env.db.get(MediaAnnotation, ann_id).media_id == neue_seite.id
    assert env.db.query(MediaAnnotationVersion).filter_by(annotation_id=ann_id).count() == 1
    assert [p.read_bytes() for p in dateien] == datei_bytes
    assert env.db.get(MediaAnnotation, entfern_id) is None
    assert not any(p.exists() for p in weg_dateien)
    assert _used(env) == env.dokument.belegt_bytes + sum(p.stat().st_size for p in dateien)


def test_overlay_erhaelt_pdf_text_und_wird_zurueckgedreht(env):
    from weasyprint import HTML

    original = dokument_svc.absolute_pfad(env.seite.einzel_pdf_pfad)
    HTML(string='<style>@page {size: 595pt 842pt; margin: 0}</style><p>Original-Vektortext</p>').write_pdf(original)
    env.seite.rotation = 90
    _save(env, png=_png((120, 80)), overlay=_png((120, 80), (0, 0, 255, 100)))
    reader = PdfReader(service.annotations_dateien(env.seite)[2])
    assert "Original-Vektortext" in reader.pages[0].extract_text()
    bilder = list(reader.pages[0].images)
    assert len(bilder) == 1
    assert bilder[0].image.size == (80, 120)
    assert reader.pages[0].mediabox == PdfReader(original).pages[0].mediabox


def test_fallback_pdf_jpeg_dateigroesse(env):
    import random

    bild = Image.frombytes("RGB", (600, 900), random.Random(42).randbytes(600 * 900 * 3))
    buf = io.BytesIO()
    bild.save(buf, "PNG")
    png = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    _save(env, png=png)
    pdf = service.annotations_dateien(env.seite)[2]
    reader = PdfReader(pdf)
    xobjects = reader.pages[0]["/Resources"]["/XObject"]
    filter_werte = [str(obj.get_object().get("/Filter")) for obj in xobjects.values()]
    assert any("/DCTDecode" in wert for wert in filter_werte)
    assert all("/ASCIIHexDecode" not in wert for wert in filter_werte)
    jpeg = io.BytesIO()
    bild.save(jpeg, "JPEG", quality=92)
    assert pdf.stat().st_size < len(jpeg.getvalue()) + 10000


def test_quota_abgelehnt_behaelt_vorstand(env):
    a = _save(env)
    env.db.commit()
    dateien = service.annotations_dateien(env.seite)
    vorher = [p.read_bytes() for p in dateien]
    stand = a.annotation_json
    belegt = _used(env)
    env.org.storage_quota_bytes = belegt
    env.db.commit()
    with pytest.raises(HTTPException) as exc:
        _save(env, png=_png((600, 900)))
    assert exc.value.status_code == 413
    assert [p.read_bytes() for p in dateien] == vorher
    assert a.annotation_json == stand
    assert _used(env) == belegt


@pytest.mark.parametrize("png,overlay", [("data:image/png;base64,kaputt", None),
                                        (_png(), "data:image/png;base64,kaputt"),
                                        (_png(), _png((2, 2)))])
def test_ungueltige_bilder_schreiben_nichts(env, png, overlay):
    vorher = _used(env)
    with pytest.raises(HTTPException) as exc:
        _save(env, png=png, overlay=overlay)
    assert exc.value.status_code == 400
    assert _used(env) == vorher
    assert not any(p.exists() for p in service.annotations_dateien(env.seite))
