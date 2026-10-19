"""K6a: Uploads, Versionsreferenzen, Quota und tenantgebundene Auslieferung."""

import importlib.util
import io
import json
import zipfile
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from PIL import Image
from pypdf import PdfWriter

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanAbschnitt, KatplanAenderung, KatplanAnhang, KatplanElement, KatplanPlan
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import Objekt, ObjektDokument
from app.models.user import Role, User, UserRole
from app.services import katplan_anhang_service as service
from app.services import katplan_freigabe_service as freigabe
from tests.conftest import TestingSession
from tests.test_admin_rollenrechte import _user
from tests.test_katplan_k1a import _user as rollen_user
from tests.test_katplan_k1a import db, org  # noqa: F401
from tests.test_katplan_k1b import login, post, umgebung  # noqa: F401
from tests.test_katplan_k2a import anlegen, basis, objekte
from tests.test_katplan_k3a import arbeitsstand, lade_plan


def png():
    out = io.BytesIO()
    Image.new("RGB", (24, 16), "red").save(out, "PNG")
    return out.getvalue()


def pdf():
    out = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(out)
    return out.getvalue()


def docx():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    return out.getvalue()


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path / "media"))
    return tmp_path / "media"


def upload(client, info, data=None, name="foto.png", mime="image/png", **fields):
    return client.post(basis(info) + "/anhaenge", data={"_csrf": client.cookies.get("ec_csrf"), **fields},
                       files={"datei": (name, png() if data is None else data, mime)},
                       follow_redirects=False)


def usage(db, org_id):
    return db.execute(sa.text("SELECT used_bytes FROM org_storage_usage WHERE org_id=:org"),
                      {"org": org_id}).scalar() or 0


def test_foto_pipeline_quota_metadaten_und_entfernen(db, org, storage):
    plan, element, user = arbeitsstand(db, org)
    a = service.anhang_hochladen(db, plan, user, png(), "../foto.png", "image/png", element=element,
                                beschriftung="Sperre", aufnahme_datum=date(2026, 1, 2))
    assert a.art == "foto" and a.mime == "image/jpeg" and (a.breite, a.hoehe) == (24, 16)
    assert a.dateiname == "foto.png" and element.version == 1
    assert a.pfad.startswith(f"{org.id}/katplan/{plan.uuid}/{a.uuid}/")
    assert (storage / a.thumb_pfad).is_file()
    assert usage(db, org.id) == a.groesse_bytes == sum(p.stat().st_size for p in (storage / a.pfad).parent.iterdir())
    user = rollen_user("katplan_verwalter", org.id)
    assert service.sichtbare_anhaenge(db, plan, user, element_id=element.id) == [a]
    service.anhang_aendern(db, plan, user, a, beschriftung="Neu", urheber="Anna",
                          aufnahme_datum=None, vertraulich=True, sort=12)
    assert a.sort == 12 and element.version == 2
    assert service.sichtbare_anhaenge(db, plan, rollen_user("readonly", org.id), element_id=element.id) == []
    assert service.sichtbare_anhaenge(db, plan, rollen_user("incident_leader", org.id), element_id=element.id) == [a]
    path, thumb = storage / a.pfad, storage / a.thumb_pfad
    service.anhang_entfernen(db, plan, user, a)
    assert not path.exists() and not thumb.exists() and usage(db, org.id) == 0
    assert db.query(KatplanAnhang).filter_by(id=a.id).first() is None
    assert {p.aktion for p in db.query(KatplanAenderung).filter_by(entitaet="anhang")} == {
        "hochgeladen", "geaendert", "entfernt"}


@pytest.mark.parametrize("data,name,mime", [(b"MZexecutable", "test.exe", "image/png"),
                                            (b"<html>Test</html>", "test.html", "text/html"),
                                            (b"<html>Test</html>", "test.png", "image/png"),
                                            (b"MZexecutable", "test.txt", "text/plain")])
def test_verbotene_uploads_422(client, umgebung, data, name, mime):
    login(client, umgebung)
    response = upload(client, umgebung, data, name, mime)
    assert response.status_code == 422 and "Dateityp nicht erlaubt" in response.text


def test_groesse_und_quota_413_ohne_dateireste(client, umgebung, storage, monkeypatch):
    login(client, umgebung)
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES_IMAGE", 8)
    assert upload(client, umgebung).status_code == 413
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES_IMAGE", 1024 * 1024)
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        db.get(FireDept, umgebung["org_id"]).storage_quota_bytes = 1
        db.commit()
    assert upload(client, umgebung).status_code == 413
    assert list(storage.rglob("*.jpg")) == []
    assert objekte(umgebung, KatplanAnhang) == []


def test_links_routen_validierung_und_schreibrechte(client, umgebung):
    login(client, umgebung)
    path = basis(umgebung) + "/anhaenge"
    assert post(client, path, url="javascript:alert(1)").status_code == 422
    assert post(client, path, url="https://example.org/plan", beschriftung="Plan").status_code == 200
    a = objekte(umgebung, KatplanAnhang)[0]
    assert a.art == "link"
    assert client.get(f"/katastrophenplaene/anhang/{a.uuid}").status_code == 404
    assert post(client, path + f"/{a.uuid}", beschriftung="Neu", urheber="Anna",
                aufnahme_datum="2026-10-19", sort=4).status_code == 200
    assert objekte(umgebung, KatplanAnhang)[0].aufnahme_datum == date(2026, 10, 19)
    assert post(client, path + f"/{a.uuid}", aufnahme_datum="ungueltig").status_code == 422
    assert post(client, path + f"/{a.uuid}", beschriftung="x" * 301).status_code == 422
    assert post(client, path, url="https://example.org", element_id=999999).status_code == 422
    login(client, umgebung, "readonly")
    assert post(client, path, url="https://example.org").status_code == 403
    assert post(client, path + f"/{a.uuid}/loeschen").status_code == 403
    login(client, umgebung, "fremd")
    assert post(client, path + f"/{a.uuid}/loeschen").status_code == 404
    login(client, umgebung)
    assert post(client, path + f"/{a.uuid}/loeschen").status_code == 200
    assert objekte(umgebung, KatplanAnhang) == []


def test_objektdokument_validierung_und_redirect(client, umgebung):
    login(client, umgebung)
    with TestingSession() as db:
        set_tenant_context(db, None)
        flag = db.query(SystemSettings).filter_by(key="objekt_module_enabled").first()
        vorher = flag.value if flag else None
        if flag is None:
            flag = SystemSettings(key="objekt_module_enabled")
            db.add(flag)
        flag.value = "true"
        org_settings = db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one()
        org_settings.objekt_module_enabled = True
        fremd_id = db.query(FireDept.id).filter(FireDept.id != umgebung["org_id"]).first()[0]
        obj = Objekt(org_id=umgebung["org_id"], name="Schule")
        fremd = Objekt(org_id=fremd_id, name="Fremd")
        db.add_all([obj, fremd])
        db.flush()
        dokument = ObjektDokument(org_id=obj.org_id, objekt_id=obj.id, dateiname_original="plan.pdf",
                                  pfad="plan.pdf", seitenzahl=4)
        anderes = ObjektDokument(org_id=fremd_id, objekt_id=fremd.id, dateiname_original="fremd.pdf",
                                 pfad="fremd.pdf", seitenzahl=4)
        db.add_all([dokument, anderes])
        db.commit()
        did, aid = dokument.id, anderes.id
    try:
        path = basis(umgebung) + "/anhaenge"
        assert post(client, path, objekt_dokument_id=aid).status_code == 422
        for seiten in ("0", "3-2", "1-5", "2,3", "-1", "1-2-3"):
            assert post(client, path, objekt_dokument_id=did, seiten=seiten).status_code == 422
        assert post(client, path, objekt_dokument_id=did, seiten="2-3").status_code == 200
        a = objekte(umgebung, KatplanAnhang)[0]
        url = f"/katastrophenplaene/anhang/{a.uuid}"
        assert client.get(url, follow_redirects=False).status_code == 404  # Nur Katplan-Verwalter.
        # Kombinierte Rollen fuer Arbeitsstand und Objekt-Leserecht.
        with TestingSession() as db:
            set_tenant_context(db, None)
            user = db.query(User).filter_by(username=umgebung["users"]["katplan_verwalter"]).one()
            db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code="objekt_verwalter").one().id))
            db.commit()
        login(client, umgebung)
        result = client.get(url, follow_redirects=False)
        assert result.status_code == 303 and result.headers["location"] == f"/objekt-medien/dokument/{did}/original"
        assert client.get(url + "/thumb").status_code == 404
        with TestingSession() as db:
            set_tenant_context(db, None)
            db.get(ObjektDokument, did).ist_aktuelle_version = False
            db.commit()
        assert post(client, path, objekt_dokument_id=did).status_code == 422
        with TestingSession() as db:
            set_tenant_context(db, None)
            db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one().objekt_module_enabled = False
            db.commit()
        assert post(client, path, objekt_dokument_id=did, seiten="2").status_code == 422
        assert client.get(url, follow_redirects=False).status_code == 404
    finally:
        with TestingSession() as db:
            set_tenant_context(db, None)
            flag = db.query(SystemSettings).filter_by(key="objekt_module_enabled").one()
            if vorher is None:
                db.delete(flag)
            else:
                flag.value = vorher
            db.commit()


def test_snapshot_soft_delete_hash_und_bindungen(db, org, storage):
    plan, element, user = arbeitsstand(db, org)
    a = service.anhang_hochladen(db, plan, user, png(), "foto.png", "image/png", element=element)
    b = service.link_hinzufuegen(db, plan, user, "https://example.org", "Kopf")
    kapitel = db.get(KatplanAbschnitt, element.abschnitt_id)
    c = service.anhang_hochladen(db, plan, user, pdf(), "plan.pdf", "application/pdf", abschnitt=kapitel)
    v = freigabe.freigeben(db, plan, user)
    db.flush()
    _, kopf, elemente = freigabe.lade_version(db, plan)
    assert json.loads(elemente[0].anhaenge_json) == [service.anhang_snapshot(a)]
    assert {x["uuid"]: x["abschnitt_id"] for x in kopf["anhaenge"]} == {b.uuid: None, c.uuid: kapitel.id}
    assert "pfad" not in v.plan_json and "pfad" not in elemente[0].anhaenge_json
    original_hash = v.inhalt_hash
    a.dateiname = "umbenannt.png"
    a.mime = "image/png"
    with pytest.raises(ValueError, match="Keine Änderungen"):
        freigabe.freigeben(db, plan, user)
    assert v.inhalt_hash == original_hash
    path = storage / a.pfad
    before = usage(db, org.id)
    service.anhang_entfernen(db, plan, user, a)
    service.anhang_entfernen(db, plan, user, c)
    assert a.geloescht_am is not None and c.geloescht_am is not None
    assert path.exists() and usage(db, org.id) == before and plan.status == "in_ueberarbeitung"
    assert any(x["uuid"] == a.uuid for x in service.anhaenge_aus_version(db, plan, v.id))
    assert service.sichtbare_anhaenge(db, plan, rollen_user("org_admin", org.id), element_id=element.id) == []
    v2 = freigabe.freigeben(db, plan, user)
    assert v2.inhalt_hash != original_hash
    # Referenzen in aelteren Versionen reichen zum Aufbewahren.
    service.anhang_entfernen(db, plan, user, b)
    assert b.geloescht_am
    other = KatplanPlan(org_id=org.id, titel="Anderer Plan")
    db.add(other)
    db.flush()
    with pytest.raises(ValueError, match="gehört"):
        service.link_hinzufuegen(db, other, user, "https://example.org", element=element)
    with pytest.raises(ValueError, match="gehört"):
        service.link_hinzufuegen(db, other, user, "https://example.org", abschnitt=kapitel)


@pytest.mark.parametrize("data,name,mime,disposition", [(png, "bild.png", "image/png", "inline"),
                                                        (pdf, "plan.pdf", "application/pdf", "inline"),
                                                        (docx, "plan.docx", "application/octet-stream", "attachment")])
def test_download_header(client, umgebung, data, name, mime, disposition):
    login(client, umgebung)
    assert upload(client, umgebung, data(), name, mime).status_code == 200
    a = objekte(umgebung, KatplanAnhang)[0]
    response = client.get(f"/katastrophenplaene/anhang/{a.uuid}")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith(disposition + ";")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_download_rechte_freigabe_soft_delete_und_pfadschutz(client, umgebung, storage):
    login(client, umgebung)
    element = anlegen(client, umgebung, typ="info")
    assert upload(client, umgebung, element_id=element.id).status_code == 200
    a = objekte(umgebung, KatplanAnhang)[0]
    assert upload(client, umgebung, vertraulich="true").status_code == 200
    secret = objekte(umgebung, KatplanAnhang)[1]
    login(client, umgebung, "katplan_freigeber")
    assert post(client, basis(umgebung) + "/freigeben").status_code == 200
    login(client, umgebung)
    assert upload(client, umgebung).status_code == 200
    draft = objekte(umgebung, KatplanAnhang)[2]
    url = lambda x: f"/katastrophenplaene/anhang/{x.uuid}"
    assert client.get(url(draft)).status_code == 200
    login(client, umgebung, "readonly")
    assert client.get(url(a)).status_code == 200
    assert client.get(url(a) + "/thumb").status_code == 200
    assert client.get(url(draft)).status_code == 404
    assert client.get(url(secret)).status_code == 404
    assert client.get(url(a) + "?version_id=999999").status_code == 404
    with TestingSession() as db:
        set_tenant_context(db, None)
        name = "leitung-" + uuid4().hex
        _user(db, name, umgebung["org_id"], "incident_leader")
        db.commit()
    umgebung["users"]["incident_leader"] = name
    login(client, umgebung, "incident_leader")
    assert client.get(url(secret)).status_code == 200
    login(client, umgebung, "fremd")
    assert client.get(url(a)).status_code == 404
    login(client, umgebung)
    path = storage / a.pfad
    assert post(client, basis(umgebung) + f"/anhaenge/{a.uuid}/loeschen").status_code == 200
    assert path.exists() and objekte(umgebung, KatplanAnhang)[0].geloescht_am is not None
    login(client, umgebung, "readonly")
    assert client.get(url(a)).status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        db.get(KatplanAnhang, a.id).pfad = "../outside.txt"
        db.commit()
    (storage.parent / "outside.txt").write_text("secret")
    assert client.get(url(a)).status_code == 404


def test_duplizieren_ohne_anhaenge(client, umgebung):
    login(client, umgebung)
    element = anlegen(client, umgebung, typ="info")
    assert upload(client, umgebung, element_id=element.id).status_code == 200
    assert post(client, basis(umgebung) + f"/elemente/{element.id}/duplizieren").status_code == 200
    assert len(objekte(umgebung, KatplanElement)) == 2
    assert len(objekte(umgebung, KatplanAnhang)) == 1
    assert any("Anhänge nicht kopiert" in (p.neu or "") for p in objekte(umgebung, KatplanAenderung))


def test_migration_0297_up_down_sqlite():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0297_katplan_anhaenge.py"
    spec = importlib.util.spec_from_file_location("katplan_anhang_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            tables = {"fire_dept", "user", "katplan_plan", "katplan_abschnitt", "katplan_element"}
            for name in tables:
                conn.execute(sa.text(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)"))
            migration.op = Operations(MigrationContext.configure(conn))
            migration.upgrade()
            assert migration.revision == "0297" and migration.down_revision == "0296"
            inspector = sa.inspect(conn)
            assert {c["name"]: c["nullable"] for c in inspector.get_columns("katplan_anhang")} == {
                c.name: c.nullable for c in KatplanAnhang.__table__.columns}
            assert {tuple(i["column_names"]) for i in inspector.get_indexes("katplan_anhang")} == {
                tuple(c.name for c in i.columns) for i in KatplanAnhang.__table__.indexes}
            fks = inspector.get_foreign_keys("katplan_anhang")
            assert "objekt_dokument_id" not in {c for fk in fks for c in fk["constrained_columns"]}
            assert {fk["constrained_columns"][0]: fk["options"].get("ondelete") for fk in fks} == {
                "org_id": "SET NULL", "plan_id": "CASCADE", "abschnitt_id": "SET NULL",
                "element_id": "SET NULL", "erstellt_von_id": "SET NULL"}
            migration.downgrade()
            assert set(sa.inspect(conn).get_table_names()) == tables
    finally:
        engine.dispose()


@pytest.mark.parametrize("suffix,entry", [("xlsx", "xl/workbook.xml"), ("odt", "content.xml"),
                                         ("ods", "content.xml"), ("txt", None)])
def test_buerodokumente_download_only(db, org, suffix, entry):
    plan, _, user = arbeitsstand(db, org)
    out = io.BytesIO()
    if entry:
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr(entry, "<content/>")
        raw = out.getvalue()
    else:
        raw = "Hinweise zur Sperre".encode()
    anhang = service.anhang_hochladen(db, plan, user, raw, "hinweis." + suffix, "text/html")
    assert anhang.art == "datei" and anhang.thumb_pfad is None
    assert anhang.mime != "text/html"


def test_vertrauliches_element_und_historische_datei(client, umgebung):
    login(client, umgebung)
    element = anlegen(client, umgebung, typ="info")
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        db.get(KatplanElement, element.id).vertraulich = True
        db.commit()
    assert upload(client, umgebung, element_id=element.id).status_code == 200
    anhang = objekte(umgebung, KatplanAnhang)[0]
    url = f"/katastrophenplaene/anhang/{anhang.uuid}"
    login(client, umgebung, "katplan_freigeber")
    assert post(client, basis(umgebung) + "/freigeben").status_code == 200
    v1 = lade_plan(umgebung).aktuelle_version_id
    login(client, umgebung, "readonly")
    assert client.get(url).status_code == 404
    login(client, umgebung)
    assert post(client, basis(umgebung) + f"/anhaenge/{anhang.uuid}/loeschen").status_code == 200
    login(client, umgebung, "katplan_freigeber")
    assert post(client, basis(umgebung) + "/freigeben").status_code == 200
    assert client.get(url).status_code == 404
    assert client.get(url + f"?version_id={v1}").status_code == 200
    assert client.get(url + f"/thumb?version_id={v1}").status_code == 200
    login(client, umgebung, "readonly")
    assert client.get(url + f"?version_id={v1}").status_code == 404
