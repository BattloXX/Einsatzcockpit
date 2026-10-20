"""K7b: PDF-Planquellen, Versionen, Quota, Snapshots und Auslieferungsrechte."""

import importlib.util
import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.katplan import (
    KatplanAenderung, KatplanDokument, KatplanDokumentSeite, KatplanElement, KatplanPlan,
)
from app.models.master import SystemSettings
from app.models.user import User
from app.services import katplan_freigabe_service as freigabe
from app.services import katplan_quelle_service as service
from app.services import pdf_seiten_pipeline as pipeline
from tests.conftest import TestingSession
from tests.test_katplan_k1a import _user
from tests.test_katplan_k1a import db, org  # noqa: F401
from tests.test_katplan_k1b import login, post, umgebung  # noqa: F401
from tests.test_katplan_k2a import anlegen, basis, element_form, objekte
from tests.test_katplan_k6a import png, usage
from tests.test_pdf_seiten_pipeline import OCR_TEXT, TEXT, _pdf_bytes


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path / "media"))
    monkeypatch.setattr(settings, "OBJEKT_OCR_ENABLED", True)
    monkeypatch.setattr(settings, "OBJEKT_OCR_MIN_CHARS", 20)
    monkeypatch.setattr("app.db.SessionLocal", TestingSession)
    monkeypatch.setattr(pipeline, "render_seite_png_poppler", lambda *_: png())
    monkeypatch.setattr(pipeline, "ocr_tesseract", lambda _: OCR_TEXT)
    return tmp_path / "media"


def quelle(db, org, **werte):
    plan = KatplanPlan(org_id=org.id, titel="Hochwasser")
    db.add(plan)
    db.flush()
    user = _user("katplan_verwalter", org.id)
    dokument = service.quelle_hochladen(db, plan, user, _pdf_bytes(), "plan.pdf", titel="Planquelle", **werte)
    return plan, user, dokument


def upload(client, info, data=None, **werte):
    return client.post(basis(info) + "/quellen", data={
        "_csrf": client.cookies.get("ec_csrf"), "titel": "Planquelle", **werte,
    }, files={"datei": ("plan.pdf", _pdf_bytes() if data is None else data, "application/pdf")})


def test_upload_verarbeitung_und_neuverarbeitung(db, org, storage):
    plan, user, dokument = quelle(db, org, herausgeber="Gemeinde", stand=date(2026, 10, 19))
    assert dokument.status == "neu" and dokument.seitenzahl == 2
    assert usage(db, org.id) == len(_pdf_bytes())
    original = storage / dokument.pfad
    assert original == storage / f"{org.id}/katplan/{plan.uuid}/quellen/{dokument.uuid}/original.pdf"
    db.commit()
    render, ocr = Mock(side_effect=lambda *_: png()), Mock(return_value=OCR_TEXT)
    service.verarbeite_quelle(dokument.id, render, ocr)
    db.expire_all()
    assert dokument.status == "fertig" and dokument.fehler_text is None
    seiten = db.query(KatplanDokumentSeite).filter_by(dokument_id=dokument.id).order_by(
        KatplanDokumentSeite.seiten_nr).all()
    assert [(s.volltext, s.text_quelle) for s in seiten] == [(TEXT, "pdf"), (OCR_TEXT, "ocr")]
    assert all(s.plan_id == plan.id and s.org_id == org.id for s in seiten)
    assert render.call_count == 2 and ocr.call_count == 1
    belegt = sum(p.stat().st_size for p in original.parent.iterdir())
    assert usage(db, org.id) == dokument.belegt_bytes == belegt > dokument.groesse_bytes
    db.commit()
    service.verarbeite_quelle(dokument.id)
    db.expire_all()
    assert db.query(KatplanDokumentSeite).filter_by(dokument_id=dokument.id).count() == 2
    assert usage(db, org.id) == dokument.belegt_bytes == belegt
    assert original.read_bytes() == _pdf_bytes()


@pytest.mark.parametrize("fall,status", [("mime", 415), ("bytes", 413), ("seiten", 413)])
def test_pdf_limits_service_und_route(client, umgebung, fall, status):
    login(client, umgebung)
    data = b"kein PDF" if fall == "mime" else _pdf_bytes()
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        if fall != "mime":
            key = "objekt_pdf_max_bytes" if fall == "bytes" else "objekt_pdf_max_seiten"
            row = db.query(SystemSettings).filter_by(key=key).first()
            vorher = row.value if row else None
            if row is None:
                row = SystemSettings(key=key)
                db.add(row)
            row.value = "1"
            db.commit()
    try:
        assert upload(client, umgebung, data=data).status_code == status
        assert objekte(umgebung, KatplanDokument) == []
    finally:
        if fall != "mime":
            with TestingSession() as db:
                set_tenant_context(db, None)
                row = db.query(SystemSettings).filter_by(key=key).one()
                if vorher is None:
                    db.delete(row)
                else:
                    row.value = vorher
                db.commit()


def test_quota_fehler_und_cleanup(db, org, storage):
    plan, user, dokument = quelle(db, org)
    org.storage_quota_bytes = len(_pdf_bytes())
    db.commit()
    service.verarbeite_quelle(dokument.id)
    db.expire_all()
    assert dokument.status == "fehler" and dokument.fehler_text
    assert db.query(KatplanDokumentSeite).filter_by(dokument_id=dokument.id).count() == 0
    assert [p.name for p in (storage / dokument.pfad).parent.iterdir()] == ["original.pdf"]
    assert usage(db, org.id) == dokument.belegt_bytes == dokument.groesse_bytes
    with pytest.raises(HTTPException) as exc:
        service.quelle_hochladen(db, plan, user, _pdf_bytes(), "test.pdf", titel="Weitere")
    assert exc.value.status_code == 413
    assert len(list(storage.rglob("original.pdf"))) == 1


def test_neue_version_snapshot_und_lesersicht(db, org, storage):
    plan, user, alt = quelle(db, org)
    db.add(KatplanElement(org_id=org.id, plan_id=plan.id, typ="info", titel="Info"))
    version = freigabe.freigeben(db, plan, user)
    snapshot = version.plan_json
    assert json.loads(snapshot)["quellen"] == [{
        "id": alt.id, "uuid": alt.uuid, "titel": alt.titel, "versionsnummer": 1,
        "seitenzahl": 2, "herausgeber": "", "stand": None,
    }]
    neu = service.quelle_hochladen(db, plan, user, _pdf_bytes(), "neu.pdf", titel="Neue Quelle",
                                   ersetzt_dokument_id=alt.id)
    db.flush()
    assert (neu.versionsnummer, neu.dokument_gruppe_id, neu.ist_aktuelle_version) == (2, alt.id, True)
    assert not alt.ist_aktuelle_version and (storage / alt.pfad).is_file()
    assert plan.status == "in_ueberarbeitung" and version.plan_json == snapshot
    assert service.sichtbare_quellen(db, plan, _user("readonly", org.id)) == [alt]
    assert set(service.sichtbare_quellen(db, plan, user)) == {alt, neu}
    assert "neue Quellversion" in {a.aktion for a in db.query(KatplanAenderung).filter_by(plan_id=plan.id)}
    dritte = service.quelle_hochladen(db, plan, user, _pdf_bytes(), "3.pdf", titel="Dritte",
                                     ersetzt_dokument_id=alt.id)
    assert dritte.versionsnummer == 3 and not neu.ist_aktuelle_version


def test_hash_nur_quellidentitaet_version_und_titel(db, org):
    plan, user, dokument = quelle(db, org)
    db.add(KatplanElement(org_id=org.id, plan_id=plan.id, typ="info", titel="Info"))
    version = freigabe.freigeben(db, plan, user)
    dokument.herausgeber, dokument.stand, dokument.status = "Andere Stelle", date(2026, 1, 1), "fertig"
    plan.status = "in_ueberarbeitung"
    with pytest.raises(ValueError, match="Keine Änderungen"):
        freigabe.freigeben(db, plan, user)
    dokument.titel = "Neuer Titel"
    assert freigabe.freigeben(db, plan, user).inhalt_hash != version.inhalt_hash


@pytest.mark.parametrize("fremde_org", [False, True])
def test_element_quelle_fremd_und_seiten_validieren(client, umgebung, fremde_org):
    login(client, umgebung)
    element = anlegen(client, umgebung)
    assert upload(client, umgebung).status_code == 200
    dokument = objekte(umgebung, KatplanDokument)[0]
    with TestingSession() as db:
        set_tenant_context(db, None)
        org_id = (db.query(User).filter_by(username=umgebung["users"]["fremd"]).one().org_id
                  if fremde_org else umgebung["org_id"])
        fremdplan = KatplanPlan(org_id=org_id, titel="Anderer Plan")
        db.add(fremdplan)
        db.flush()
        fremd = service.quelle_hochladen(db, fremdplan, _user("katplan_verwalter", org_id),
                                        _pdf_bytes(), "fremd.pdf", titel="Fremd")
        db.commit()
        fremd_id = fremd.id
    path = basis(umgebung) + f"/elemente/{element.id}"
    assert post(client, path, **element_form(element, quelle_dokument_id=fremd_id)).status_code == 422
    for seiten in ("0", "3", "2-1", "1-3", "1,3", "x"):
        assert post(client, path, **element_form(element, quelle_dokument_id=dokument.id,
                                                quelle_seiten=seiten)).status_code == 422
    r = post(client, path, **element_form(element, quelle_dokument_id=dokument.id, quelle_seiten="1-2"))
    assert r.status_code == 200, r.text
    gespeichert = objekte(umgebung, KatplanElement)[0]
    assert (gespeichert.quelle_dokument_id, gespeichert.quelle_seiten, gespeichert.herkunft) == (dokument.id, "1-2", "manual")
    login(client, umgebung, "katplan_freigeber")
    assert post(client, basis(umgebung) + "/freigeben").status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        _, kopf, elemente = freigabe.lade_version(db, db.get(KatplanPlan, umgebung["plan_id"]))
        assert elemente[0].quelle_dokument_id == dokument.id and elemente[0].quelle_seiten == "1-2"
        assert kopf["quellen"][0]["id"] == dokument.id


@pytest.mark.parametrize("referenz", ["arbeitsstand", "version_element", "version_kopf", "gruppe", "keine"])
def test_entfernen_referenzen_oder_quota_freigeben(db, org, storage, referenz):
    plan, user, dokument = quelle(db, org)
    element = KatplanElement(org_id=org.id, plan_id=plan.id, typ="info", titel="Info")
    db.add(element)
    if referenz in ("arbeitsstand", "version_element", "gruppe"):
        element.quelle_dokument_id = dokument.id
    if referenz == "version_element":
        dokument.archiviert = True  # Nur Elementreferenz, kein Eintrag im Kopf.
    if referenz in ("version_element", "version_kopf"):
        freigabe.freigeben(db, plan, user)
        element.quelle_dokument_id = None
    if referenz == "gruppe":
        dokument = service.quelle_hochladen(db, plan, user, _pdf_bytes(), "neu.pdf", titel="Neu",
                                            ersetzt_dokument_id=dokument.id)
    db.flush()
    if referenz == "keine":
        db.commit()
        service.verarbeite_quelle(dokument.id)
        db.expire_all()
        assert db.query(KatplanDokumentSeite).filter_by(dokument_id=dokument.id).count() == 2
    pfad, vorher = storage / dokument.pfad, usage(db, org.id)
    service.quelle_entfernen(db, plan, user, dokument)
    if referenz == "keine":
        assert db.get(KatplanDokument, dokument.id) is None and not pfad.exists()
        assert usage(db, org.id) == vorher - dokument.belegt_bytes
        assert not pfad.parent.exists()
        assert db.query(KatplanDokumentSeite).filter_by(dokument_id=dokument.id).count() == 0
    else:
        assert dokument.archiviert and pfad.is_file() and usage(db, org.id) == vorher


def test_auslieferung_rechte_versionen_status_und_neuverarbeitung(client, umgebung, storage):
    login(client, umgebung)
    anlegen(client, umgebung)
    response = upload(client, umgebung)
    assert response.status_code == 200 and 'hx-trigger="every 2s"' in response.text
    alt = objekte(umgebung, KatplanDokument)[0]
    url = f"/katastrophenplaene/quelle/{alt.uuid}"
    status = basis(umgebung) + f"/quellen/{alt.id}/status"
    r = client.get(status)
    assert "fertig" in r.text and "every 2s" not in r.text
    assert client.get(url + "/original").content == _pdf_bytes()
    assert client.get(url + "/seite/1/text").json() == {"volltext": TEXT, "text_quelle": "pdf"}
    for art, mime in (("bild", "image/png"), ("thumb", "image/jpeg"), ("pdf", "application/pdf")):
        r = client.get(url + f"/seite/1/{art}")
        assert r.status_code == 200 and r.headers["content-type"] == mime
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["content-disposition"].startswith("inline")
        assert client.get(url + f"/seite/3/{art}").status_code == 404
    login(client, umgebung, "readonly")
    assert client.get(url + "/original").status_code == client.get(status).status_code == 404
    assert upload(client, umgebung).status_code == 403
    login(client, umgebung, "katplan_freigeber")
    assert post(client, basis(umgebung) + "/freigeben").status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        v1 = db.get(KatplanPlan, umgebung["plan_id"]).aktuelle_version_id
    login(client, umgebung)
    assert upload(client, umgebung, ersetzt_dokument_id=alt.id).status_code == 200
    neu = objekte(umgebung, KatplanDokument)[-1]
    neu_url = f"/katastrophenplaene/quelle/{neu.uuid}"
    login(client, umgebung, "readonly")
    assert client.get(url + "/original").status_code == 200
    assert client.get(neu_url + "/original").status_code == 404
    login(client, umgebung, "katplan_freigeber")
    assert post(client, basis(umgebung) + "/freigeben").status_code == 200
    login(client, umgebung, "readonly")
    assert client.get(url + "/original").status_code == 404
    assert client.get(url + f"/original?version_id={v1}").status_code == 200
    assert client.get(neu_url + "/original").status_code == 200
    assert client.get(neu_url + f"/original?version_id={v1}").status_code == 404
    assert client.get(neu_url + "/original?version_id=99999999").status_code == 404
    login(client, umgebung, "fremd")
    assert client.get(url + "/original").status_code == 404
    assert client.get(neu_url + "/seite/1/text").status_code == 404
    assert client.get(status).status_code == 404
    login(client, umgebung)
    assert post(client, basis(umgebung) + f"/quellen/{neu.id}/neu-verarbeiten").status_code == 200
    assert objekte(umgebung, KatplanDokument)[-1].status == "fertig"
    assert post(client, basis(umgebung) + f"/quellen/{alt.id}/loeschen").status_code == 200
    assert (storage / alt.pfad).is_file()
    assert objekte(umgebung, KatplanDokument)[0].archiviert
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        db.get(KatplanDokument, neu.id).pfad = "../geheim.pdf"
        db.commit()
    assert client.get(neu_url + "/original").status_code == 404


def test_migration_0298_up_down_sqlite():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0298_katplan_quellen.py"
    spec = importlib.util.spec_from_file_location("katplan_quellen_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            tables = {"fire_dept", "user", "katplan_plan"}
            for name in tables:
                conn.execute(sa.text(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)"))
            migration.op = Operations(MigrationContext.configure(conn))
            migration.upgrade()
            assert migration.revision == "0298" and migration.down_revision == "0297"
            inspector = sa.inspect(conn)
            for model in (KatplanDokument, KatplanDokumentSeite):
                assert {c["name"]: c["nullable"] for c in inspector.get_columns(model.__tablename__)} == {
                    c.name: c.nullable for c in model.__table__.columns
                    if c.name not in {"ki_status", "ki_fehler", "ki_analysiert_am"}}
                assert {tuple(i["column_names"]) for i in inspector.get_indexes(model.__tablename__)} == {
                    tuple(c.name for c in i.columns) for i in model.__table__.indexes}
            assert {fk["constrained_columns"][0]: fk["options"].get("ondelete")
                    for fk in inspector.get_foreign_keys("katplan_dokument")} == {
                "org_id": "SET NULL", "plan_id": "CASCADE", "dokument_gruppe_id": "SET NULL",
                "hochgeladen_von_id": "SET NULL"}
            migration.downgrade()
            assert set(sa.inspect(conn).get_table_names()) == tables
    finally:
        engine.dispose()
