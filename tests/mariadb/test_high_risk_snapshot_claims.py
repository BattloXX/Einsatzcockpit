"""Echte Services mit zwei Worker-Sessions und garantiert veraltetem Snapshot.

Beide Worker lesen vor dem ersten Commit. Events erzwingen die Reihenfolge ohne
Sleeps; Future.result reicht jede unerwartete Exception (auch 1020/HTTP 500) weiter.
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, local
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app import db as app_db
from app.core.tenant import set_tenant_context
from app.models.admin_repair import AdminRepairRun
from app.models.gateway import Gateway
from app.models.major_incident import (
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    LageJournalEntry,
    MajorIncident,
    SiteLogEntry,
    SitePhase,
)
from app.models.master import FireDept, OrgStorageUsage
from app.models.objekt import Objekt, ObjektDokument, ObjektDokumentSeite
from app.models.sms import SmsDispatchAttempt, SmsDispatchJob
from app.models.user import AuditLog
from app.services import einheit_service, objekt_dokument_service, sms_outbox
from app.services import gateway_presence as presence
from app.services.repair.runner import claim_run

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL fehlt")


@pytest.fixture
def database(monkeypatch):
    monkeypatch.setattr(app_db.settings, "DB_SNAPSHOT_ISOLATION_OFF", True)
    engine = create_engine(os.environ["MARIADB_TEST_URL"], isolation_level="REPEATABLE READ")
    event.listen(engine, "connect", app_db._set_db_timezone_utc)
    monkeypatch.setattr(app_db, "engine", engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(app_db, "SessionLocal", factory)

    def session():
        db = factory()
        set_tenant_context(db, None)
        assert db.scalar(text("SELECT @@SESSION.innodb_snapshot_isolation")) == 0
        db.commit()
        return db

    with session() as db:
        org = FireDept(name="Phase 3", slug=uuid4().hex)
        db.add(org)
        db.commit()
        org_id = org.id
    try:
        yield session, org_id
    finally:
        with session() as db:
            # Nur die Daten dieser Test-Organisation loeschen, Kindtabellen zuerst.
            for model in (SmsDispatchAttempt, SmsDispatchJob, ObjektDokumentSeite, ObjektDokument,
                          Objekt):
                db.query(model).filter(model.org_id == org_id).delete(synchronize_session=False)
            lage_ids = db.query(MajorIncident.id).filter_by(org_id=org_id)
            site_ids = db.query(IncidentSite.id).filter_by(org_id=org_id)
            db.query(SiteLogEntry).filter(SiteLogEntry.incident_site_id.in_(site_ids)).delete(synchronize_session=False)
            db.query(LageJournalEntry).filter(LageJournalEntry.major_incident_id.in_(lage_ids)).delete(synchronize_session=False)
            db.query(EinheitSiteDispatch).filter(EinheitSiteDispatch.einheit_id.in_(
                db.query(LageEinheit.id).filter(LageEinheit.lage_id.in_(lage_ids)),
            )).delete(synchronize_session=False)
            db.query(LageEinheit).filter(LageEinheit.lage_id.in_(lage_ids)).delete(synchronize_session=False)
            for model in (IncidentSite, MajorIncident, Gateway, OrgStorageUsage):
                db.query(model).filter(model.org_id == org_id).delete(synchronize_session=False)
            db.query(FireDept).filter_by(id=org_id).delete()
            db.commit()
        engine.dispose()


def _race(session, load, action):
    ready = Barrier(2, timeout=15)
    locked, competing = Event(), Event()
    state = local()
    connections = []
    lock_connections = []

    def is_lock(statement):
        return bool(re.search(r"\bFOR UPDATE\b|^UPDATE\b", statement, re.IGNORECASE))

    def before(conn, cursor, statement, parameters, context, executemany):
        if getattr(state, "first_lock", False) and state.index and is_lock(statement):
            state.first_lock = False
            lock_connections.append(conn.scalar(text("SELECT CONNECTION_ID()")))
            assert locked.wait(15), "Erster Worker hat keine Zeile gesperrt"
            competing.set()

    def after(conn, cursor, statement, parameters, context, executemany):
        if getattr(state, "first_lock", False) and not state.index and is_lock(statement):
            state.first_lock = False
            lock_connections.append(conn.scalar(text("SELECT CONNECTION_ID()")))
            locked.set()
            assert competing.wait(15), "Zweiter Worker hat keinen Lock versucht"

    def worker(index):
        with session() as db:
            connections.append(db.scalar(text("SELECT CONNECTION_ID()")))
            obj = load(db)
            ready.wait()
            state.index, state.first_lock = index, True
            result = action(db, obj, index)
            db.commit()
            return result

    # Engine-Listener erfassen auch READ-COMMITTED-Ableger und neue Checkouts
    # innerhalb der echten Services (SMS database(), Dokument-Abschluss).
    engine = app_db.engine
    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, index) for index in range(2)]
            results = [future.result(timeout=30) for future in futures]
        assert len(set(connections)) == len(set(lock_connections)) == 2
        assert locked.is_set() and competing.is_set()
        return results
    finally:
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)


def test_sms_claim_has_one_attempt(database, monkeypatch):
    session, org_id = database
    moment = presence.utc().replace(microsecond=0)
    monkeypatch.setattr(presence, "utc", lambda now=None: now or moment)
    with session() as db:
        job_id = sms_outbox.enqueue_in(db, org_id, uuid4().hex, "+436641234567", "Test", ["eus"])
        db.commit()
    worker = local()
    # Echte database()/READ-COMMITTED-Konfiguration des SMS-Workers beibehalten.
    monkeypatch.setattr(sms_outbox, "SessionLocal", lambda: worker.db)

    def action(db, job, index):
        worker.db = db
        return sms_outbox.claim(org_id, job.id)

    tokens = _race(session, lambda db: db.get(SmsDispatchJob, job_id), action)
    assert tokens[0] and tokens[1] is None, tokens
    with session() as db:
        job = db.get(SmsDispatchJob, job_id)
        assert (job.status, job.generation, job.claim_token) == ("claimed", 1, tokens[0])
        assert db.query(SmsDispatchAttempt).filter_by(job_id=job_id).count() == 1


def test_presence_only_current_connection_can_heartbeat(database):
    session, org_id = database
    now, token = presence.utc().replace(microsecond=0), uuid4().hex
    with session() as db:
        gateway = Gateway(org_id=org_id, name="Race", kind="linux", device_token_hash=token)
        db.add(gateway)
        db.commit()
        gateway_id = gateway.id
        assert presence.begin(db, "linux", org_id, gateway_id, token, "old", now=now)

    def action(db, gateway, index):
        if not index:
            assert presence.begin(db, "linux", org_id, gateway_id, token, "new", now=now)
        connection = "new" if not index else "old"
        state = presence.state(db, "linux", org_id, gateway_id, token, connection, now=now, lock=True)
        changed = presence.touch(db, "linux", org_id, gateway_id, token, connection, now=now)
        return state, changed

    assert _race(session, lambda db: db.get(Gateway, gateway_id), action) == [
        ("connected", True), ("superseded", False),
    ]
    with session() as db:
        gateway = db.get(Gateway, gateway_id)
        assert gateway.agent_connection_id == "new"
        assert gateway.agent_heartbeat_at == now.replace(microsecond=0)


def test_repair_claim_has_one_winner(database):
    session, _ = database
    with session() as db:
        run = AdminRepairRun(aktion_id=uuid4().hex, risiko="low", status="simuliert",
                             simulation_hash="a" * 64, korrelations_id=str(uuid4()))
        db.add(run)
        db.commit()
        run_id = run.id
    try:
        assert _race(session, lambda db: db.get(AdminRepairRun, run_id),
                     lambda db, run, index: claim_run(db, run.id, str(index) * 64)) == [True, False]
        with session() as db:
            run = db.get(AdminRepairRun, run_id)
            assert (run.status, run.active_aktion, run.idempotency_key) == ("laufend", run.aktion_id, "0" * 64)
    finally:
        with session() as db:
            db.query(AdminRepairRun).filter_by(id=run_id).delete()
            db.commit()


def test_document_worker_renders_once(database, monkeypatch, tmp_path):
    from pypdf import PdfWriter

    session, org_id = database
    monkeypatch.setattr(objekt_dokument_service.settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    pdf = tmp_path / "original.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    with session() as db:
        obj = Objekt(org_id=org_id, name="Race")
        db.add(obj)
        db.flush()
        doc = ObjektDokument(org_id=org_id, objekt_id=obj.id, dateiname_original="original.pdf",
                             pfad="original.pdf", groesse_bytes=pdf.stat().st_size,
                             belegt_bytes=pdf.stat().st_size, seitenzahl=1)
        db.add(doc)
        db.commit()
        doc_id = doc.id
    worker, rendered = local(), []
    monkeypatch.setattr(app_db, "SessionLocal", lambda: worker.db)

    def render(*args):
        rendered.append(args)
        return None

    def action(db, ident, index):
        worker.db = db
        objekt_dokument_service.verarbeite_dokument(ident, render_func=render)

    _race(session, lambda db: db.query(ObjektDokument.id).filter_by(id=doc_id).scalar(), action)
    with session() as db:
        doc = db.get(ObjektDokument, doc_id)
        assert (doc.status, doc.fehler_text) == ("fertig", None)
        assert len(rendered) == 1
        assert db.query(ObjektDokumentSeite).filter_by(dokument_id=doc_id).count() == 1


def test_unit_takeover_has_one_active_dispatch(database):
    session, org_id = database
    with session() as db:
        lage = MajorIncident(org_id=org_id, name="Race")
        db.add(lage)
        db.flush()
        unit = LageEinheit(lage_id=lage.id, label="Race")
        sites = [IncidentSite(org_id=org_id, major_incident_id=lage.id, bezeichnung=str(i),
                              phase=SitePhase.disponiert) for i in range(2)]
        db.add_all([unit, *sites])
        db.flush()
        dispatches = [EinheitSiteDispatch(einheit_id=unit.id, site_id=site.id,
                                        dispatched_at=presence.utc(), auftragsart="abarbeitung") for site in sites]
        db.add_all(dispatches)
        db.commit()
        ids, unit_id, lage_id = [d.id for d in dispatches], unit.id, lage.id

    def load(db):
        return [db.get(EinheitSiteDispatch, ident) for ident in ids]

    def action(db, dispatches, index):
        ctx = einheit_service.EinheitKontext(None, None, db.get(LageEinheit, unit_id),
                                            db.get(MajorIncident, lage_id), org_id, "funk")
        try:
            return einheit_service.setze_einheit_status(
                db, ctx, dispatches[index], "anfahrt", user_id=None, author_name="Race",
            )["geaendert"]
        except einheit_service.EinheitKonflikt as exc:
            db.rollback()
            assert exc.code == "aktiver_auftrag"
            return False

    assert _race(session, load, action) == [True, False]
    with session() as db:
        assert [db.get(EinheitSiteDispatch, ident).einheit_status for ident in ids] == ["anfahrt", "zugewiesen"]
        assert db.get(LageEinheit, unit_id).incident_site_id == db.get(EinheitSiteDispatch, ids[0]).site_id
        assert db.query(LageJournalEntry).filter_by(major_incident_id=lage_id, ereignis_typ="begonnen").count() == 1
        # Audit ist global; nur die zu diesem Lauf gehoerige Statusmutation entfernen.
        for row in db.query(AuditLog).filter_by(action="gsl.einheit.status").all():
            if row.payload_json and json.loads(row.payload_json).get("lage_id") == lage_id:
                db.delete(row)
        db.commit()
