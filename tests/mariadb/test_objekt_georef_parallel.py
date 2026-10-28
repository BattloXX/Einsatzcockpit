"""Georef und Seitendrehung mit echten Locks und absichtlich altem Snapshot."""

import json
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app import db as app_db
from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.master import FireDept
from app.models.objekt import (
    Objekt,
    ObjektDokument,
    ObjektDokumentGeoref,
    ObjektDokumentSeite,
    ObjektKartenVorschlag,
)
from app.models.user import User
from app.routers import ui_objekt_dokumente as router
from app.services import objekt_georef_service as geo
from app.services.objekt_karte_service import ObjektKartenFehler
from tests.mariadb.test_high_risk_snapshot_claims import _race
from tests.test_objekt_georef import punkte

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL fehlt")


@pytest.fixture
def database(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DB_SNAPSHOT_ISOLATION_OFF", True)
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    (tmp_path / "plan.pdf").write_bytes(b"original")
    Image.new("RGB", (2000, 1000)).save(tmp_path / "plan.png")
    engine = create_engine(os.environ["MARIADB_TEST_URL"], isolation_level="REPEATABLE READ")
    event.listen(engine, "connect", app_db._set_db_timezone_utc)
    monkeypatch.setattr(app_db, "engine", engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)

    def session():
        db = factory()
        set_tenant_context(db, None)
        assert db.scalar(text("SELECT @@SESSION.innodb_snapshot_isolation")) == 0
        db.execute(text("SET SESSION innodb_lock_wait_timeout=10"))
        db.commit()
        return db

    with session() as db:
        org = FireDept(name="Georef parallel", slug=uuid4().hex)
        db.add(org)
        db.flush()
        user = User(org_id=org.id, username=uuid4().hex, display_name="Georef")
        objekt = Objekt(org_id=org.id, name="Georef parallel", status="aktiv")
        db.add_all([user, objekt])
        db.flush()
        dokument = ObjektDokument(org_id=org.id, objekt_id=objekt.id,
                                  dateiname_original="plan.pdf", pfad="plan.pdf")
        db.add(dokument)
        db.flush()
        seite = ObjektDokumentSeite(org_id=org.id, objekt_id=objekt.id, dokument_id=dokument.id,
                                    seiten_nr=1, bild_pfad="plan.png", rotation=0)
        db.add(seite)
        db.flush()
        g = geo.referenzpunkte(db, objekt, dokument.id, 1, "setzen", punkte(), None, user.id)["georeferenz"]
        lat, lng, genauigkeit = geo.bild_zu_geo(db.get(ObjektDokumentGeoref, g["id"]), 0.2, 0.3)
        v = ObjektKartenVorschlag(org_id=org.id, objekt_id=objekt.id, operation="hinzufuegen",
                                  symbol_code="tor", georef_id=g["id"], bild_x=0.2, bild_y=0.3,
                                  lat=lat, lng=lng, genauigkeit_m=genauigkeit,
                                  position_methode="plan_georeferenzierung", warnungen_json="[]")
        db.add(v)
        db.commit()
        ids = org.id, objekt.id, user.id, dokument.id, seite.id, g["id"], v.id
    # Nur die Darstellung ersetzen: Route samt Sperre, Drehung und Service-Hook bleibt echt.
    monkeypatch.setattr(router, "_galerie_context", lambda *args, **kwargs: {})
    monkeypatch.setattr(router.templates, "TemplateResponse", lambda *args, **kwargs: None)
    try:
        yield session, ids
    finally:
        with session() as db:
            for model in (ObjektKartenVorschlag, ObjektDokumentGeoref, ObjektDokumentSeite,
                          ObjektDokument, Objekt, User, FireDept):
                spalte = model.id if model is FireDept else model.org_id
                db.query(model).filter(spalte == ids[0]).delete(synchronize_session=False)
            db.commit()
        engine.dispose()


def _laden(db, ids, basis_revision=1):
    _, objekt_id, user_id, dokument_id, seite_id, _, _ = ids
    objekt, user = db.get(Objekt, objekt_id), db.get(User, user_id)
    seite = db.get(ObjektDokumentSeite, seite_id)
    georefs = db.query(ObjektDokumentGeoref).filter_by(dokument_id=dokument_id).all()
    vorschlaege = db.query(ObjektKartenVorschlag).filter_by(objekt_id=objekt_id).all()
    assert seite.rotation == 0 and [g.revision for g in georefs] == ([1] if basis_revision else [])
    return objekt, user, seite, georefs, vorschlaege


def _speichern(db, kontext, ids, index, basis_revision=1):
    p = punkte()
    for punkt in p:
        punkt["lat"] += (index + 1) * 0.0001
    return geo.web_georef(db, kontext[0], dict(dokument_id=ids[3], seite=1, punkte=p,
                          expected_revision=basis_revision, expected_rotation=0), ids[2])["georeferenz"]


@pytest.mark.parametrize("basis_revision", [0, 1])
def test_zwei_speicherungen_genau_eine_revision(database, basis_revision):
    session, ids = database
    if not basis_revision:
        with session() as db:
            db.delete(db.get(ObjektKartenVorschlag, ids[6]))
            db.flush()
            db.delete(db.get(ObjektDokumentGeoref, ids[5]))
            db.commit()

    def action(db, kontext, index):
        try:
            g = _speichern(db, kontext, ids, index, basis_revision)
        except ObjektKartenFehler as exc:
            assert exc.status_code == 409
            db.rollback()
            return False
        assert g["revision"] == basis_revision + 1 and g["status"] == "gueltig"
        return True

    assert _race(session, lambda db: _laden(db, ids, basis_revision), action) == [True, False]
    with session() as db:
        objekt = db.get(Objekt, ids[1])
        georefs = db.query(ObjektDokumentGeoref).filter_by(objekt_id=ids[1]).order_by(
            ObjektDokumentGeoref.revision).all()
        assert [(g.revision, g.status) for g in georefs] == (
            [(1, "veraltet"), (2, "gueltig")] if basis_revision else [(1, "gueltig")]
        )
        neu = georefs[-1]
        assert json.loads(neu.referenzpunkte_json)[0]["lat"] == pytest.approx(punkte()[0]["lat"] + 0.0001)
        assert json.loads(neu.parameter_json)["rotation"] == 0
        if basis_revision:
            v = db.get(ObjektKartenVorschlag, ids[6])
            assert v.georef_id == neu.id and v.position_status == "vorgeschlagen"
            lat, lng, genauigkeit = geo.bild_zu_geo(neu, v.bild_x, v.bild_y)
            assert (v.lat, v.lng, v.genauigkeit_m) == pytest.approx((lat, lng, genauigkeit))
        assert geo.georef_pruefen(db, objekt, neu) == "gueltig"


@pytest.mark.parametrize("drehen_zuerst", [True, False])
def test_drehen_und_speichern_keine_gueltige_alte_georef(database, drehen_zuerst):
    session, ids = database

    def action(db, kontext, index):
        try:
            if (index == 0) == drehen_zuerst:
                router.seite_drehen(ids[1], ids[4], Request({"type": "http"}), db=db,
                                    user=kontext[1], _guard=None, richtung="rechts", art="", suche="")
                return "gedreht"
            _speichern(db, kontext, ids, index)
            return "gespeichert"
        except (ObjektKartenFehler, HTTPException) as exc:
            assert exc.status_code == 409
            db.rollback()
            return "konflikt"

    resultate = _race(session, lambda db: _laden(db, ids), action)
    assert resultate[0] == ("gedreht" if drehen_zuerst else "gespeichert")
    assert resultate[1] in ({"konflikt"} if drehen_zuerst else {"gedreht", "konflikt"})
    with session() as db:
        objekt, seite = db.get(Objekt, ids[1]), db.get(ObjektDokumentSeite, ids[4])
        georefs = db.query(ObjektDokumentGeoref).filter_by(objekt_id=ids[1]).all()
        assert len(georefs) == (1 if drehen_zuerst else 2)
        v = db.get(ObjektKartenVorschlag, ids[6])
        if seite.rotation == 90:
            assert all(g.status == geo.georef_pruefen(db, objekt, g) == "veraltet" for g in georefs)
            assert v.position_status == "pruefung_erforderlich"
            assert {"code": "georef_ungueltig"} in json.loads(v.warnungen_json)
        else:
            assert seite.rotation == 0 and resultate[1] == "konflikt"
            assert v.georef_id == georefs[-1].id
