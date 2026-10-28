"""Konkurrierende Freigaben mit zwei echten Sessions und altem Snapshot."""

import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app import db as app_db
from app.core.tenant import set_tenant_context
from app.models.master import FireDept
from app.models.objekt import Objekt, ObjektChange, ObjektKartenObjekt, ObjektKartenVorschlag
from app.models.user import Role, User, UserRole
from app.services import objekt_karte_freigabe_service as service
from tests.mariadb.test_high_risk_snapshot_claims import _race

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL fehlt")


@pytest.fixture
def database(monkeypatch):
    monkeypatch.setattr(app_db.settings, "DB_SNAPSHOT_ISOLATION_OFF", True)
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
        org = FireDept(name="Kartenfreigabe parallel", slug=uuid4().hex)
        db.add(org)
        db.flush()
        role = db.query(Role).filter_by(code="objekt_verwalter").one()
        user = User(org_id=org.id, username=uuid4().hex, display_name="Kartenfreigabe")
        objekt = Objekt(org_id=org.id, name="Kartenfreigabe parallel")
        db.add_all([user, objekt])
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        org_id, objekt_id, user_id = org.id, objekt.id, user.id
    try:
        yield session, objekt_id, user_id
    finally:
        with session() as db:
            # Nur eigene Testdaten entfernen; Vorschlaege referenzieren Marker/User.
            for model in (ObjektChange, ObjektKartenVorschlag, ObjektKartenObjekt, Objekt):
                db.query(model).filter_by(org_id=org_id).delete(synchronize_session=False)
            db.query(UserRole).filter_by(user_id=user_id).delete(synchronize_session=False)
            db.query(User).filter_by(id=user_id).delete(synchronize_session=False)
            db.query(FireDept).filter_by(id=org_id).delete(synchronize_session=False)
            db.commit()
        engine.dispose()


@pytest.mark.parametrize("fall", ["neu_doppelt", "aendern_doppelt", "aendern_konkurrenten"])
def test_genau_eine_freigabe(database, fall):
    session, objekt_id, user_id = database
    neu = fall == "neu_doppelt"
    with session() as db:
        org_id = db.get(Objekt, objekt_id).org_id
        marker = None
        if not neu:
            marker = ObjektKartenObjekt(org_id=org_id, objekt_id=objekt_id, typ="tor",
                                       revision=7, lat=47.1, lng=9.2)
            db.add(marker)
            db.flush()
        vorschlaege = [ObjektKartenVorschlag(
            org_id=org_id, objekt_id=objekt_id, operation="hinzufuegen" if neu else "aendern",
            symbol_code="tor", lat=47.101 + index * 0.001, lng=9.201,
            basis_marker_id=marker.id if marker else None, expected_revision=7 if marker else None,
            position_status="vorgeschlagen", warnungen_json="[]",
        ) for index in range(2 if fall == "aendern_konkurrenten" else 1)]
        db.add_all(vorschlaege)
        db.commit()
        ids = [v.id for v in vorschlaege]
        marker_id = marker.id if marker else None

    def load(db):
        # Beide Sessions halten vor dem ersten Lock denselben alten ORM-/DB-Snapshot.
        objekt, user = db.get(Objekt, objekt_id), db.get(User, user_id)
        alte_vorschlaege = db.query(ObjektKartenVorschlag).filter_by(objekt_id=objekt_id).all()
        alte_marker = db.query(ObjektKartenObjekt).filter_by(objekt_id=objekt_id).all()
        assert all(v.freigabe_status == "offen" for v in alte_vorschlaege)
        assert all(m.revision == 7 for m in alte_marker)
        return objekt, user, alte_vorschlaege, alte_marker

    def action(db, kontext, index):
        objekt, user, _, _ = kontext
        vorschlag_id = ids[index] if len(ids) == 2 else ids[0]
        try:
            antwort = service.freigeben(db, objekt, [vorschlag_id], user, {})
        except service.FreigabeKonflikt as exc:
            assert exc.status_code == 409
            assert exc.ergebnisse == [{
                "id": vorschlag_id, "status": "fehler", "konflikt": True,
                "ursache": "Vorschlag ist nicht mehr offen",
            }]
            # Der Service hat die gesamte fehlgeschlagene Transaktion zurueckgerollt.
            assert not db.in_transaction()
            return False
        assert antwort["ergebnisse"][0]["status"] == "angenommen"
        return True

    # Der zweite Lock wird versucht, waehrend der erste Worker den Objekt-Lock haelt.
    # Unerwartete DB-Fehler/Timeouts werden vom Helper weitergereicht, nie verschluckt.
    assert _race(session, load, action) == [True, False]
    with session() as db:
        marker = db.query(ObjektKartenObjekt).filter_by(objekt_id=objekt_id).one()
        assert marker.revision == (1 if neu else 8)
        assert marker_id is None or marker.id == marker_id
        assert marker.lat == pytest.approx(47.101)
        assert marker.position_status == "bestaetigt" and marker.geprueft_von == user_id
        assert [db.get(ObjektKartenVorschlag, ident).freigabe_status for ident in ids] == (
            ["angenommen", "ersetzt"] if len(ids) == 2 else ["angenommen"]
        )
        audit = db.query(ObjektChange).filter_by(objekt_id=objekt_id, bereich="karte").one()
        assert audit.feld == ("symbol_neu" if neu else "symbol_geaendert")
        assert json.loads(audit.after_json)["revision"] == marker.revision
        if neu:
            assert audit.before_json is None
        else:
            assert json.loads(audit.before_json)["revision"] == 7
