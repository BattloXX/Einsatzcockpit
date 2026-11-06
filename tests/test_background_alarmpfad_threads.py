"""L3a: Alarm-DB im eigenen Pool, Transport und Warten auf dem aufrufenden Loop."""
from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.orm import sessionmaker

from app import db as datenbank
from app.core.tenant import set_tenant_context
from app.models.gateway import AlarmIngest, Gateway, Printer, PrintJob, PrintRule
from app.models.incident import Incident
from app.models.major_incident import EinheitSiteDispatch, IncidentSite, LageEinheitZugang
from app.models.master import OrgSettings, SystemSettings
from app.models.org_mail import OrgMailEingang, OrgMailEingangEreignis
from app.models.verleih import VerleihAusleihe
from app.services import linux_print_service
from app.services import print_dispatcher as service
from tests.conftest import TestingSession
from tests.test_ressourcenkarte_zugang_routes import _daten
from tests.test_objekt_kontakt_benachrichtigung import versand  # noqa: F401


@pytest.fixture(autouse=True)
def druckdaten_aufraeumen():
    # Bestandspruefungen zaehlen teilweise alle Jobs eines Dokumenttyps.
    # Unsere committed Thread-Testdaten duerfen deren Ergebnis nicht veraendern.
    with TestingSession() as db:
        set_tenant_context(db, None)
        vorhandene = {modell: [row.id for row in db.query(modell).all()] for modell in (PrintJob, PrintRule)}
    yield
    with TestingSession() as db:
        set_tenant_context(db, None)
        for modell, ids in vorhandene.items():
            for row in db.query(modell).filter(~modell.id.in_(ids)).all():
                db.delete(row)
        db.commit()


@contextmanager
def alarm_db_threads(monkeypatch, *module, pool_prefix="alarm-db", session_factory=TestingSession):
    loop_thread = threading.get_ident()
    aufrufe, fehler, offen = [], [], set()

    def session():
        thread = threading.get_ident()
        if thread == loop_thread or not threading.current_thread().name.startswith(pool_prefix):
            fehler.append("Session ausserhalb des Alarm-Pools")
        db = session_factory()
        offen.add(id(db))

        def pruefen(*args):
            aufrufe.append(threading.get_ident())
            if threading.get_ident() != thread or thread == loop_thread:
                fehler.append("DB-Zugriff auf falschem Thread")

        for name in ("do_orm_execute", "before_flush", "before_commit", "after_rollback"):
            event.listen(db, name, pruefen)
        close = db.close

        def schliessen():
            pruefen()
            close()
            offen.remove(id(db))

        db.close = schliessen
        return db

    with monkeypatch.context() as patch:
        patch.setattr(datenbank, "SessionLocal", session)
        patch.setattr(linux_print_service, "SessionLocal", session)
        for modul in module:
            patch.setattr(modul, "SessionLocal", session)
        yield offen
    assert aufrufe
    assert not fehler, fehler  # Auch von best-effort-Hooks abgefangene Fehler erkennen.
    assert not offen


_FAELLE = [
    ("incident", "einsatz_created", "einsatzinfo"),
    ("alarm", "alarm_serial_received", "alarm_rohtext"),
    ("mail_eingang", "mail_eingang", "mail_eingang"),
    ("gsl", "gsl_created", "gsl_lageblatt"),
    ("gsl_einheit", "gsl_einheit_angelegt", "gsl_einheit_qr"),
    ("gsl_stelle_disponiert", "gsl_stelle_disponiert", "site_karte"),
    ("verleih", "verleih_created", "verleih_schein"),
]


def _aufbau(art, trigger, dokument):
    _, lage_id, einheit_id, org_id = _daten()
    with TestingSession() as db:
        set_tenant_context(db, None)
        cfg = db.query(OrgSettings).filter_by(org_id=org_id).one()
        cfg.gateway_module_enabled, cfg.gk_qr_aktiv, cfg.mail_eingang_modus = True, True, "alle"
        flag = db.query(SystemSettings).filter_by(key="gateway_module_enabled").first()
        if flag is None:
            db.add(SystemSettings(key="gateway_module_enabled", value="true"))
        else:
            flag.value = "true"
        gw = Gateway(org_id=org_id, name="Alarm-Thread", device_token_hash=uuid4().hex)
        db.add(gw)
        db.flush()
        printer = Printer(org_id=org_id, gateway_id=gw.id, name="P", uri="ipp://p", aktiv=True)
        db.add(printer)
        db.flush()
        for sort_order in (1, 2):
            db.add(PrintRule(org_id=org_id, name=f"Regel {sort_order}", trigger=trigger,
                             documents=[dokument], printer_ids=[printer.id], sort_order=sort_order,
                             options={"copies": sort_order}))
        inc = Incident(primary_org_id=org_id, status="active", alarm_type_code="B2", reason="Brand")
        db.add(inc)
        db.flush()
        mail = OrgMailEingang(org_id=org_id, extern_id=uuid4().hex, incident_id=inc.id,
                             absender="alarm@example.invalid", empfangen_at=datetime.now(UTC).replace(tzinfo=None))
        alarm = AlarmIngest(org_id=org_id, gateway_id=gw.id, raw_hash=uuid4().hex, raw_text="ALARM")
        ausleihe = VerleihAusleihe(org_id=org_id, lage_id=lage_id, name="Verleih")
        site = IncidentSite(org_id=org_id, major_incident_id=lage_id, bezeichnung="Stelle")
        db.add_all([mail, alarm, ausleihe, site])
        db.flush()
        dispatch = EinheitSiteDispatch(einheit_id=einheit_id, site_id=site.id, auftragsart="abarbeitung",
                                       dispatched_at=datetime.now(UTC), status_at=datetime.now(UTC))
        db.add(dispatch)
        db.commit()
        ids = {"incident": inc.id, "alarm": alarm.id, "mail_eingang": mail.id, "gsl": lage_id,
               "gsl_einheit": einheit_id, "gsl_stelle_disponiert": dispatch.id, "verleih": ausleihe.id}
        args = (ids[art], org_id) if art == "gsl" else (ids[art],)
        return org_id, args, mail.id, einheit_id


@pytest.mark.parametrize(("art", "trigger", "dokument"), _FAELLE)
async def test_autodruck_db_im_alarm_pool_transport_auf_loop_und_idempotent(monkeypatch, art, trigger, dokument):
    import app.routers.ws as ws

    org_id, args, mail_id, einheit_id = _aufbau(art, trigger, dokument)
    loop = asyncio.get_running_loop()
    gesendet = []

    def pruefe_commit(job_id):
        with TestingSession() as db:
            set_tenant_context(db, None)
            job = db.get(PrintJob, job_id)
            assert job.org_id == org_id and job.document_type == dokument
            if art == "mail_eingang":
                assert db.query(OrgMailEingangEreignis).filter_by(eingang_id=mail_id, typ="gedruckt").count() == 1
            if art == "gsl_einheit":
                qr = db.query(LageEinheitZugang).filter_by(einheit_id=einheit_id, typ="qr").one()
                assert qr.generation == 1 and qr.qr_druck_job_id and qr.qr_druck_at
            return job.options["copies"]

    async def senden(org, job_id, payload):
        assert asyncio.get_running_loop() is loop
        assert not offen, "DB-Sessions muessen vor dem Transport geschlossen sein"
        assert org == org_id and payload["job_id"] == job_id
        gesendet.append((job_id, await asyncio.to_thread(pruefe_commit, job_id)))
        return {"job_id": job_id, "status": "queued", "error": None}

    monkeypatch.setattr(ws, "dispatch_print_job", senden)
    with alarm_db_threads(monkeypatch) as offen:
        await getattr(service, f"autoprint_{art}_background")(*args)
        await getattr(service, f"autoprint_{art}_background")(*args)
    assert [copies for _, copies in gesendet] == [1, 2]
    with TestingSession() as db:
        set_tenant_context(db, None)
        jobs = db.query(PrintJob).filter_by(org_id=org_id).order_by(PrintJob.id).all()
        assert [job.id for job in jobs] == [jid for jid, _ in gesendet]


@pytest.mark.parametrize(("funktion", "args"), [
    *((f"autoprint_{art}_background", (-1, 1) if art == "gsl" else (-1,)) for art, _, _ in _FAELLE),
    ("dispatch_fallback_for_failed_job", (-1,)),
])
async def test_fehlende_quelle_ohne_nebenwirkung_im_thread(monkeypatch, funktion, args):
    with alarm_db_threads(monkeypatch):
        assert await getattr(service, funktion)(*args) is None


async def test_fallback_spaltenwerte_und_dedupe_im_thread_transport_auf_loop(monkeypatch):
    import app.routers.ws as ws
    from tests.test_print_fallback import _setup

    job_id, org, printer, gateway = _setup(options={"copies": 2, "media": "A3"})
    loop = asyncio.get_running_loop()
    gesendet = []

    async def senden(org_id, jid, payload):
        assert asyncio.get_running_loop() is loop and not offen
        gesendet.append(jid)
        assert org_id == org and payload["printer_id"] == printer
        assert payload["options"] == {"copies": 2}
        return {"status": "queued"}

    monkeypatch.setattr(ws, "dispatch_print_job", senden)
    with alarm_db_threads(monkeypatch) as offen:
        ersatz = await service.dispatch_fallback_for_failed_job(job_id)
        assert await service.dispatch_fallback_for_failed_job(job_id) is None
    assert inspect(ersatz).detached
    assert ersatz.org_id == org and ersatz.gateway_id == gateway and ersatz.printer_id == printer
    assert ersatz.fallback_of_job_id == job_id and ersatz.options == {"copies": 2}
    assert gesendet == [ersatz.id]


async def test_agent_enqueue_im_thread_wakeup_auf_loop(monkeypatch):
    import app.routers.ws as ws
    from tests.test_print_agent_ws import _anlegen

    daten = _anlegen(1)
    with TestingSession() as db:
        set_tenant_context(db, None)
        job = PrintJob(org_id=1, gateway_id=daten["gateway_id"], printer_id=daten["printer_id"],
                       document_type="einsatzinfo", idempotency_key=uuid4().hex)
        db.add(job)
        db.commit()
        job_id = job.id
    loop = asyncio.get_running_loop()
    geweckt = []

    async def wecken(org, gateways, jobs):
        assert asyncio.get_running_loop() is loop and not offen
        geweckt.append((org, gateways, jobs))

    monkeypatch.setattr(ws, "notify_agents_job_available", wecken)
    with alarm_db_threads(monkeypatch) as offen:
        result = await service._dispatch_background_job(job_id)
    assert result == {"job_id": job_id, "status": "queued", "error": None}
    assert geweckt == [(1, [daten["gateway_id"]], [job_id])]
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.get(PrintJob, job_id).delivery == "lease"


async def test_linux_transportfehler_db_im_thread(monkeypatch):
    from app.services import gateway_presence, gateway_routing, ws_bus

    with TestingSession() as db:
        set_tenant_context(db, None)
        job = PrintJob(org_id=1, gateway_id=1, document_type="einsatzinfo", status="sent",
                       idempotency_key=uuid4().hex, push_route_id="thread-fehler")
        db.add(job)
        db.commit()
        job_id = job.id
    loop = asyncio.get_running_loop()
    connection = gateway_presence.Connection("linux", 1, 1, "connection", "worker", datetime.now(UTC))

    def prepare(*args):
        assert threading.current_thread().name.startswith("alarm-db")
        return "thread-fehler", connection

    async def io(*args):
        assert asyncio.get_running_loop() is loop

    monkeypatch.setattr(linux_print_service, "prepare", prepare)
    monkeypatch.setattr(gateway_routing, "deliver", io)
    monkeypatch.setattr(gateway_routing, "_result", lambda *args: ("failed", None))
    monkeypatch.setattr(ws_bus, "publish", io)
    with alarm_db_threads(monkeypatch):
        result = await linux_print_service.dispatch(1, job_id)
    assert result == {"job_id": job_id, "status": "uncertain", "error": "Socket-Send mit unklarem Ausgang"}


async def test_autodruck_fehler_warnt_und_stellt_naechsten_job_zu(monkeypatch, caplog):
    import app.routers.ws as ws

    org_id, args, _, _ = _aufbau("alarm", "alarm_serial_received", "alarm_rohtext")
    gesendet = []

    async def senden(org, job_id, payload):
        gesendet.append(job_id)
        if len(gesendet) == 1:
            raise RuntimeError("Gateway weg")
        return {"status": "queued"}

    monkeypatch.setattr(ws, "dispatch_print_job", senden)
    with alarm_db_threads(monkeypatch), caplog.at_level("WARNING"):
        await service.autoprint_alarm_background(*args)
    assert len(gesendet) == 2
    assert f"Alarm-Auto-Druck Job {gesendet[0]} nicht zustellbar: Gateway weg" in caplog.text
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.query(PrintJob).filter_by(org_id=org_id).count() == 2


async def test_wartende_druckauftraege_belegen_keine_alarm_threads(monkeypatch):
    """Mehr wartende Auftraege als Pool-Threads: Transport darf weitere DB-Arbeit starten."""
    import app.routers.ws as ws

    with TestingSession() as db:
        set_tenant_context(db, None)
        jobs = [PrintJob(org_id=1, gateway_id=1, document_type="einsatzinfo", idempotency_key=uuid4().hex)
                for _ in range(service.ALARM_POOL._max_workers + 1)]
        db.add_all(jobs)
        db.commit()
        ids = [job.id for job in jobs]
    alle_gestartet = asyncio.Event()
    gestartet = 0
    loop = asyncio.get_running_loop()

    async def senden(org, job_id, payload):
        nonlocal gestartet
        assert asyncio.get_running_loop() is loop
        gestartet += 1
        if gestartet == len(ids):
            alle_gestartet.set()
        await alle_gestartet.wait()
        # Verschachtelte DB-Aufrufe im Transport duerfen nicht hinter ihren
        # eigenen wartenden Auftraegen im gleichen Pool stecken bleiben.
        await service.im_db_thread(service._load_print_job_sync, job_id, pool=service.ALARM_POOL)
        return {"status": "queued"}

    monkeypatch.setattr(ws, "dispatch_print_job", senden)
    with alarm_db_threads(monkeypatch):
        await asyncio.wait_for(asyncio.gather(*(service._dispatch_background_job(jid) for jid in ids)), timeout=10)
    assert gestartet == len(ids)


@pytest.mark.parametrize("nur_geo", [False, True])
async def test_objekt_matching_db_im_alarm_pool_broadcast_nach_commit(monkeypatch, nur_geo):
    from app.models.master import FireDept
    from app.models.objekt import Objekt, ObjektBMA, ObjektEinsatz
    from app.services import broadcast, objekt_kontakt_notify, objekt_matching_service, objekt_service

    with TestingSession() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=uuid4().hex, name="Matching-Thread")
        db.add(org)
        db.flush()
        obj = Objekt(org_id=org.id, nummer=1, name="Werk", status="freigegeben", lat=47.4, lng=9.7)
        inc = Incident(primary_org_id=org.id, alarm_type_code="B2", report_text="BMA 1044",
                       lat=47.4, lng=9.7)
        db.add_all([obj, inc])
        db.flush()
        db.add(ObjektBMA(org_id=org.id, objekt_id=obj.id, bma_nummer="1044"))
        db.commit()
        org_id, objekt_id, incident_id = org.id, obj.id, inc.id
    monkeypatch.setattr(objekt_service, "objekt_effective_enabled", lambda *args: True)
    loop, reihenfolge = asyncio.get_running_loop(), []

    async def org_broadcast(org_id_arg, payload):
        assert asyncio.get_running_loop() is loop and not offen
        assert org_id_arg == org_id
        assert payload == {"type": "objekt_match", "incident_id": incident_id, "matches": [
            {"objekt_id": objekt_id, "name": "Werk", "quelle": "geo" if nur_geo else "bma",
             "status": "vorschlag" if nur_geo else "bestaetigt"},
        ]}
        with TestingSession() as db:
            set_tenant_context(db, None)
            assert db.query(ObjektEinsatz).filter_by(incident_id=incident_id).count() == 1
        reihenfolge.append("org")

    async def board_broadcast(iid, payload):
        assert asyncio.get_running_loop() is loop and not offen
        assert iid == incident_id and payload == {"type": "objektgefahren"}
        reihenfolge.append("board")

    async def kontakt(iid):
        assert asyncio.get_running_loop() is loop and not offen and iid == incident_id
        reihenfolge.append("kontakt")

    monkeypatch.setattr(broadcast, "broadcast_org", org_broadcast)
    monkeypatch.setattr(broadcast.manager, "broadcast", board_broadcast)
    monkeypatch.setattr(objekt_kontakt_notify, "dispatch_objekt_einsatzinfo", kontakt)
    with alarm_db_threads(monkeypatch) as offen:
        await objekt_matching_service.match_incident_background(incident_id, nur_geo=nur_geo)
        await objekt_matching_service.match_incident_background(incident_id, nur_geo=nur_geo)
    assert reihenfolge == ["org", "board", "kontakt", "kontakt"]


async def test_objekt_matching_fehlender_einsatz_im_thread(monkeypatch):
    from app.services.objekt_matching_service import match_incident_background

    with alarm_db_threads(monkeypatch):
        assert await match_incident_background(-1) is None


@pytest.mark.parametrize("adresse", [("Testgasse", "7a", "Wolfurt"), (None, None, "Wolfurt"),
                                    (None, None, None), "fehlend"])
async def test_adressaenderung_db_im_alarm_pool_geocoding_auf_loop(monkeypatch, adresse):
    from app.routers import api_v1
    from app.services.alarm_apply_service import _geocode_adressaenderung

    incident_id = -1
    if adresse != "fehlend":
        with TestingSession() as db:
            set_tenant_context(db, None)
            inc = Incident(alarm_type_code="B2", address_street=adresse[0],
                           address_no=adresse[1], address_city=adresse[2])
            db.add(inc)
            db.commit()
            incident_id = inc.id
    loop, aufrufe = asyncio.get_running_loop(), []

    async def geocode(*args):
        assert asyncio.get_running_loop() is loop and not offen
        aufrufe.append(args)

    monkeypatch.setattr(api_v1, "_geocode_incident", geocode)
    with alarm_db_threads(monkeypatch) as offen:
        assert await _geocode_adressaenderung(incident_id) is None
    assert aufrufe == ([(incident_id, *adresse)] if adresse != "fehlend" and any(adresse) else [])


async def test_objekt_matching_rollback_und_fehlerprotokoll_im_thread(monkeypatch, caplog):
    from app.services import objekt_matching_service, objekt_service

    with TestingSession() as db:
        set_tenant_context(db, None)
        inc = Incident(alarm_type_code="B2", reason="unveraendert")
        db.add(inc)
        db.commit()
        incident_id = inc.id
    monkeypatch.setattr(objekt_service, "objekt_effective_enabled", lambda *args: True)

    def fehler(db, incident, *, nur_geo):
        incident.reason = "darf nicht gespeichert werden"
        db.flush()
        raise RuntimeError("Matching-Testfehler")

    monkeypatch.setattr(objekt_matching_service, "match_incident", fehler)
    with alarm_db_threads(monkeypatch):
        assert await objekt_matching_service.match_incident_background(incident_id) is None
    assert f"Objekt-Matching fehlgeschlagen (Einsatz {incident_id})" in caplog.text
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.get(Incident, incident_id).reason == "unveraendert"


async def test_einsatzinfo_mail_db_im_alarm_pool_bcc_retry_und_skip(monkeypatch, caplog):
    from app.models.einsatzinfo_mail import MailEinsatzinfoLog, MailEinsatzinfoLogRecipient, MailEinsatzinfoRecipient
    from app.models.master import FireDept
    from app.services import einsatzinfo_mail_service as mail, mail_service

    with TestingSession() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=uuid4().hex, name="Mail-Thread")
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id, einsatzinfo_mail_enabled=True))
        inc = Incident(primary_org_id=org.id, alarm_type_code="B2", reason="Brand")
        db.add(inc)
        db.add_all([MailEinsatzinfoRecipient(org_id=org.id, email=f"thread{i}@example.invalid") for i in range(51)])
        db.commit()
        org_id, incident_id = org.id, inc.id
    monkeypatch.setattr(mail_service, "mail_available", lambda *args: True)
    monkeypatch.setattr(mail_service, "versandweg", lambda *args: {"absender": "ff@example.invalid"})
    loop, nachrichten = asyncio.get_running_loop(), []

    async def senden(msg, smtp):
        assert asyncio.get_running_loop() is loop and not offen
        nachrichten.append(msg)
        if len(nachrichten) == 2:
            raise RuntimeError("SMTP-Testfehler")

    monkeypatch.setattr(mail_service, "_send", senden)
    with alarm_db_threads(monkeypatch, mail) as offen:
        with pytest.raises(RuntimeError, match="SMTP-Testfehler"):
            await mail.dispatch_einsatzinfo_mail(org_id, incident_id, variant="test", text_prefix="Nachalarm: ")
        result = await mail.dispatch_einsatzinfo_mail(org_id, incident_id, variant="test", text_prefix="Nachalarm: ")
        assert result == mail.EinsatzinfoMailResult(True, recipient_count=51)
        assert await mail.dispatch_einsatzinfo_mail(org_id, incident_id, variant="test") == (
            mail.EinsatzinfoMailResult(True, "bereits versendet", 51, True))
        with caplog.at_level("INFO"):
            assert await mail.dispatch_einsatzinfo_mail(org_id, -1) == mail.EinsatzinfoMailResult(
                False, "Einsatz nicht gefunden")
    assert "Einsatz -1 nicht gefunden" in caplog.text
    assert [len(msg["Bcc"].addresses) for msg in nachrichten] == [50, 1, 1]
    assert all(msg["To"] == "ff@example.invalid" and str(msg["Subject"]).startswith("Nachalarm: ")
               for msg in nachrichten)
    with TestingSession() as db:
        set_tenant_context(db, None)
        log = db.query(MailEinsatzinfoLog).filter_by(incident_id=incident_id).one()
        assert log.success_count == log.recipient_count == 51 and log.completed_at
        assert log.error == "SMTP-Testfehler" and log.variant == "test"
        assert db.query(MailEinsatzinfoLogRecipient).filter_by(log_id=log.id, success=True).count() == 51


async def test_objekt_kontakt_db_im_alarm_pool_transporte_ohne_session_und_retry(monkeypatch, versand):
    from app.models.user import AuditLog
    from app.models.objekt import ObjektKontaktBenachrichtigung

    db, org, _, objekt, _, incident, _, mails, sms, notify = versand
    incident_id, org_id, objekt_id = incident.id, org.id, objekt.id
    factory = sessionmaker(bind=db.get_bind())
    loop, versuche = asyncio.get_running_loop(), []

    async def mail(config, oid, msg, smtp):
        assert asyncio.get_running_loop() is loop and not offen and oid == org_id
        assert msg["To"] == "kontakt@example.at"
        versuche.append("mail")
        if len(versuche) == 1:
            raise RuntimeError("Kontakt-SMTP-Testfehler")
        mails.append(msg)

    async def sms_senden(oid, nummer, text, ctx):
        assert asyncio.get_running_loop() is loop and not offen and oid == org_id
        sms.append((nummer, text))
        return True

    monkeypatch.setattr(notify, "deliver", mail)
    monkeypatch.setattr(notify, "send_sms", sms_senden)
    with alarm_db_threads(monkeypatch, notify, session_factory=factory) as offen:
        assert await notify.dispatch_objekt_einsatzinfo(incident_id, objekt_ids=[objekt_id]) == {
            "gesendet": 1, "fehler": 1, "uebersprungen": 0}
        assert await notify.dispatch_objekt_einsatzinfo(incident_id, force=True) == {
            "gesendet": 1, "fehler": 0, "uebersprungen": 1}
        assert await notify.dispatch_objekt_einsatzinfo(incident_id) == {
            "gesendet": 0, "fehler": 0, "uebersprungen": 2}
    db.expire_all()
    assert len(mails) == len(sms) == 1 and versuche == ["mail", "mail"]
    assert {row.status for row in db.query(ObjektKontaktBenachrichtigung)} == {"gesendet"}
    assert db.query(AuditLog).filter_by(action="objekt.kontakt_info_gesendet").count() == 2


@pytest.mark.parametrize("art", ["lis", "dibos"])
async def test_capture_start_db_im_standardpool_client_und_lebenszyklus_auf_loop(monkeypatch, tmp_path, art):
    from types import SimpleNamespace

    from app.models.dibos import OrgDibosConfig
    from app.models.lis import OrgLisConfig
    from app.models.master import FireDept
    from app.services import org_job_state
    from app.services.dibos import dibos_capture
    from app.services.lis import lis_capture

    modul = lis_capture if art == "lis" else dibos_capture
    with TestingSession() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=uuid4().hex, name="Capture-Thread")
        db.add(org)
        db.flush()
        config = (OrgLisConfig(org_id=org.id, base_url="https://lis.invalid", organization_id="guid",
                               username="user", password_enc="enc", poll_interval_seconds=17)
                  if art == "lis" else OrgDibosConfig(
                      org_id=org.id, base_url="https://dibos.invalid", gateway_user="gateway",
                      gateway_password_enc="gw-enc", service_user="service", service_password_enc="svc-enc",
                      poll_interval_seconds=17))
        db.add(config)
        db.commit()
        org_id = org.id
        site = config.site if art == "lis" else None
    loop, aufrufe = asyncio.get_running_loop(), []
    token = "capture-token"

    async def nicht_aktiv(oid):
        return False

    async def purge():
        return 0

    async def claim(*args, **kw):
        assert asyncio.get_running_loop() is loop and not offen
        aufrufe.append(("claim", args, kw))
        return token

    async def finish(*args):
        assert asyncio.get_running_loop() is loop and not offen
        aufrufe.append(("finish", args))

    async def run(kind, oid, claim_token, fn):
        assert kind == art and oid == org_id and claim_token == token
        await fn()

    async def capture(client, recorder, *args):
        assert asyncio.get_running_loop() is loop and not offen
        assert args == (("guid", 3, 17, token) if art == "lis" else (3, 17, token))
        aufrufe.append(("capture",))

    class Client:
        def __init__(self, *args, **kw):
            assert asyncio.get_running_loop() is loop and not offen
            assert args == (("https://lis.invalid", site, "user", "secret-enc") if art == "lis" else (
                "https://dibos.invalid", "gateway", "secret-gw-enc", "service", "secret-svc-enc"))
            aufrufe.append(("client",))

        async def select_operation(self, organization_id):
            assert asyncio.get_running_loop() is loop and not offen and organization_id == "guid"
            aufrufe.append(("select",))

    monkeypatch.setattr("app.core.crypto.decrypt_secret", lambda value: "secret-" + value)
    monkeypatch.setattr(modul, "LisClient" if art == "lis" else "DibosClient", Client)
    monkeypatch.setattr(modul, "CAPTURE_ROOT" if art == "lis" else "TRACE_ROOT", tmp_path)
    monkeypatch.setattr(modul, "is_capture_running" if art == "lis" else "is_trace_running", nicht_aktiv)
    monkeypatch.setattr(modul, "purge_old_captures" if art == "lis" else "purge_old_traces", purge)
    monkeypatch.setattr(modul, "capture_traffic", capture)
    monkeypatch.setattr(org_job_state, "try_start", claim)
    monkeypatch.setattr(org_job_state, "finish", finish)
    monkeypatch.setattr(org_job_state, "backend", SimpleNamespace(run=run))
    with alarm_db_threads(monkeypatch, pool_prefix="hintergrund-db") as offen:
        run_id = await (modul.start_capture_for_org(org_id, 3) if art == "lis" else modul.start_trace_for_org(org_id, 3))
        assert datetime.strptime(run_id, "%Y%m%dT%H%M%SZ")
        tasks = modul._active_captures if art == "lis" else modul._active_traces
        await tasks[org_id]
        for _ in range(3):
            await asyncio.sleep(0)
        assert org_id not in tasks
    assert [entry[0] for entry in aufrufe] == (["claim", "client", "select", "capture", "finish"] if art == "lis"
                                              else ["claim", "client", "capture", "finish"])


async def test_lis_status_db_im_standardpool_http_ohne_session_und_audit(monkeypatch):
    from app.models.incident import IncidentChange, IncidentVehicle
    from app.services.lis import lis_sync
    from app.models.master import FireDept
    from tests import test_lis_sync as lis_tests

    with TestingSession() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=uuid4().hex, name="Status-Thread")
        db.add(org)
        db.commit()
        org_id = org.id
    monkeypatch.setattr(lis_tests, "ORG_ID", org_id)
    iv_id, calls = lis_tests._push_setup(monkeypatch)
    loop = asyncio.get_running_loop()
    original = lis_sync.LisClient

    class Client(original):
        async def get_operation_unit_status_types(self):
            assert asyncio.get_running_loop() is loop and not offen
            return await super().get_operation_unit_status_types()

        async def set_operation_unit_status(self, *args):
            assert asyncio.get_running_loop() is loop and not offen
            await super().set_operation_unit_status(*args)

    monkeypatch.setattr(lis_sync, "LisClient", Client)
    with alarm_db_threads(monkeypatch, pool_prefix="hintergrund-db") as offen:
        await lis_sync.push_vehicle_status_to_lis(iv_id, "Am Einsatzort")
        await lis_sync.push_vehicle_status_to_lis(iv_id, "Am Einsatzort")
        await lis_sync.push_vehicle_status_to_lis(-1, "Am Einsatzort")
    assert calls == [("unit-ecp05", "s5")]
    with TestingSession() as db:
        set_tenant_context(db, None)
        iv = db.get(IncidentVehicle, iv_id)
        assert iv.lis_pushed_status == "S5" and iv.lis_pushed_at
        changes = db.query(IncidentChange).filter_by(incident_id=iv.incident_id, action="lis.status_pushed").all()
        assert len(changes) == 1


async def test_objekt_kontakt_vorbereitungsfehler_zaehlt_und_sms_laeuft_weiter(monkeypatch, versand):
    from app.models.objekt import ObjektKontaktBenachrichtigung

    db, _, _, _, _, incident, _, _, sms, notify = versand
    incident_id = incident.id
    factory = sessionmaker(bind=db.get_bind())

    def kaputt(**kwargs):
        raise ValueError("Mailvorbereitung fehlgeschlagen")

    monkeypatch.setattr(notify, "_build_message", kaputt)
    with alarm_db_threads(monkeypatch, notify, session_factory=factory):
        assert await notify.dispatch_objekt_einsatzinfo(incident_id) == {
            "gesendet": 1, "fehler": 1, "uebersprungen": 0}
    db.expire_all()
    mail = db.query(ObjektKontaktBenachrichtigung).filter_by(kanal="mail").one()
    assert mail.status == "fehler" and mail.fehlertext == "Mailvorbereitung fehlgeschlagen"
    assert len(sms) == 1


async def test_objekt_kontakt_abbruch_bleibt_wiederholbar(monkeypatch, versand):
    from app.models.objekt import ObjektKontaktBenachrichtigung

    db, _, _, _, kontakt, incident, _, mails, _, notify = versand
    for freigabe in kontakt.freigaben:
        freigabe.aktiv = freigabe.kanal == "mail"
    db.commit()
    incident_id = incident.id
    factory = sessionmaker(bind=db.get_bind())
    senden = notify.deliver

    async def abbrechen(*args):
        assert not offen
        raise asyncio.CancelledError

    monkeypatch.setattr(notify, "deliver", abbrechen)
    with alarm_db_threads(monkeypatch, notify, session_factory=factory) as offen:
        with pytest.raises(asyncio.CancelledError):
            await notify.dispatch_objekt_einsatzinfo(incident_id)
        monkeypatch.setattr(notify, "deliver", senden)
        assert await notify.dispatch_objekt_einsatzinfo(incident_id) == {
            "gesendet": 1, "fehler": 0, "uebersprungen": 0}
    db.expire_all()
    assert db.query(ObjektKontaktBenachrichtigung).one().status == "gesendet"
    assert len(mails) == 1


@pytest.mark.parametrize("fehler", [None, "http", "matching"])
async def test_dibos_geocode_alarm_thread_commit_vor_matching_und_rollback(monkeypatch, caplog, fehler):
    from types import SimpleNamespace

    from app.services.dibos import dibos_enrich

    with TestingSession() as db:
        set_tenant_context(db, None)
        inc = Incident(primary_org_id=1, alarm_type_code="B2", address_street="Threadstrasse")
        db.add(inc)
        db.commit()
        incident_id = inc.id
    loop, schritte = asyncio.get_running_loop(), []

    async def geocode(*args):
        assert asyncio.get_running_loop() is loop
        assert args == ("Threadstrasse", None, None)
        schritte.append("http")
        if fehler == "http":
            raise RuntimeError("Geocode-Testfehler")
        return SimpleNamespace(lat=47.5, lng=9.7)

    async def match(iid):
        assert asyncio.get_running_loop() is loop and iid == incident_id
        with TestingSession() as db:
            set_tenant_context(db, None)
            assert (db.get(Incident, iid).lat, db.get(Incident, iid).lng) == (47.5, 9.7)
        schritte.append("matching")
        if fehler == "matching":
            raise RuntimeError("Matching-Testfehler")

    monkeypatch.setattr("app.services.geocoding.geocode_address", geocode)
    monkeypatch.setattr("app.services.objekt_matching_service.match_incident_background", match)
    with alarm_db_threads(monkeypatch):
        assert await dibos_enrich._geocode_and_match_dibos_incident(incident_id, 1) is None
        await dibos_enrich._geocode_and_match_dibos_incident(-1, 1)
    assert schritte == (["http"] if fehler == "http" else ["http", "matching"])
    assert ("Geocoding/Objekt-Matching nach DIBOS-Anlage fehlgeschlagen" in caplog.text) == bool(fehler)
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.get(Incident, incident_id).lat == (None if fehler == "http" else 47.5)


@pytest.mark.parametrize("fehler", [False, True])
async def test_dibos_report_alarm_thread_http_commit_und_fehler(monkeypatch, caplog, fehler):
    from app.models.master import FireDept
    from app.models.wordpress_report import WordPressReportConfig
    from app.services import wordpress_report_service as report
    from app.services.dibos import dibos_enrich

    with TestingSession() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=uuid4().hex, name="DIBOS-Bericht")
        db.add(org)
        db.flush()
        db.add(WordPressReportConfig(org_id=org.id, enabled=True, webhook_url="https://report.invalid",
                                     webhook_token="secret"))
        inc = Incident(primary_org_id=org.id, alarm_type_code="B2", status="closed")
        db.add(inc)
        db.commit()
        incident_id = inc.id
    loop, aufrufe = asyncio.get_running_loop(), []

    async def webhook(url, token, payload):
        assert asyncio.get_running_loop() is loop
        assert (url, token, payload["incident_id"]) == ("https://report.invalid", "secret", incident_id)
        aufrufe.append(payload)
        if fehler:
            raise RuntimeError("Report-Testfehler")
        return {"post_id": 123, "edit_url": "https://report.invalid/edit"}

    monkeypatch.setattr(report, "_sende_webhook", webhook)
    with alarm_db_threads(monkeypatch):
        await dibos_enrich._post_incident_report(incident_id)
        await dibos_enrich._post_incident_report(-1)
    assert len(aufrufe) == 1
    assert ("WordPress-Bericht: Webhook-Fehler" in caplog.text) == fehler
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.get(Incident, incident_id).wp_report_post_id == (None if fehler else 123)


@pytest.mark.parametrize("fehler", [False, True])
async def test_dibos_enrich_alarm_thread_write_broadcast_live_reihenfolge(monkeypatch, caplog, fehler):
    from app.services import incident_live_notify
    from app.services.broadcast import manager
    from app.services.dibos import dibos_enrich
    from app.services.loop_thread_io import auf_event_loop

    with TestingSession() as db:
        set_tenant_context(db, None)
        inc = Incident(primary_org_id=1, alarm_type_code="B2")
        db.add(inc)
        db.commit()
        incident_id = inc.id
    loop, schritte = asyncio.get_running_loop(), []

    def enrich(*args, **kwargs):
        assert threading.current_thread().name.startswith("alarm-db")
        with datenbank.SessionLocal() as db:
            set_tenant_context(db, None)
            db.get(Incident, incident_id).reason = "committed"
            db.commit()
        schritte.append("commit")
        return {"ok": True, "created_ids": [incident_id], "closed_ids": [incident_id, -1],
                "changed_ids": [incident_id], "vehicle_changed_ids": [incident_id],
                "rsvp_changed_ids": [incident_id]}

    def gestartet(db, iid):
        assert not threading.get_ident() == loop_thread
        assert db.get(Incident, iid).reason == "committed"
        schritte.append("alarm_check")
        return True

    def background(coro, label, iid):
        assert asyncio.get_running_loop() is loop
        coro.close()
        schritte.append("report_start" if "WordPress" in label else "print_start")

    async def broadcast(iid, payload):
        assert asyncio.get_running_loop() is loop and iid == incident_id
        with TestingSession() as db:
            set_tenant_context(db, None)
            assert db.get(Incident, iid).reason == "committed"
        schritte.append(payload["type"])
        if fehler:
            raise RuntimeError("Broadcast-Testfehler")

    async def live(db, incident, **kwargs):
        assert threading.current_thread().name.startswith("alarm-db")
        assert db.get(Incident, incident.id).reason == "committed"
        await auf_event_loop(live_transport, kwargs["reason"])
        if fehler:
            raise RuntimeError("Live-Testfehler")

    async def live_transport(reason):
        assert asyncio.get_running_loop() is loop
        schritte.append(reason)

    loop_thread = threading.get_ident()
    monkeypatch.setattr(dibos_enrich, "enrich_events_for_org", enrich)
    monkeypatch.setattr(dibos_enrich, "_start_background", background)
    monkeypatch.setattr("app.services.incident_notify.incident_alarm_started", gestartet)
    monkeypatch.setattr(manager, "broadcast", broadcast)
    monkeypatch.setattr(incident_live_notify, "notify_incident_live", live)
    with alarm_db_threads(monkeypatch):
        assert await dibos_enrich.enrich_and_broadcast(1, []) is True
    assert schritte == ["commit", "alarm_check", "print_start", "report_start", "incident_closed",
                        "closed", "dibos_sync", "unit_status", "rsvp:changed"]
    assert ("DIBOS-Auto-Close: Broadcast fehlgeschlagen" in caplog.text) == fehler
    assert ("DIBOS-Live-Push (Fahrzeugstatus) fehlgeschlagen" in caplog.text) == fehler


async def test_dibos_enrich_worker_fehler_bleibt_false(monkeypatch, caplog):
    from app.services.dibos import dibos_enrich

    def fehler(*args, **kwargs):
        assert threading.current_thread().name.startswith("alarm-db")
        raise RuntimeError("Enrichment-Testfehler")

    monkeypatch.setattr(dibos_enrich, "enrich_events_for_org", fehler)
    assert await dibos_enrich.enrich_and_broadcast(1, []) is False
    assert "DIBOS-Einsatzanreicherung fehlgeschlagen (Org 1)" in caplog.text


def test_alarm_abschluss_wird_bei_parallelen_kanaelen_nur_einmal_geschrieben(monkeypatch, tmp_path):
    """Zwei gleichzeitig fertige Kanaele duerfen incident.alarm_finished nicht doppelt schreiben."""
    import threading
    from datetime import UTC, datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.tenant import set_tenant_context
    from app.db import Base
    from app.models.incident import Incident, IncidentAlarmJob
    from app.models.master import FireDept
    from app.models.user import AuditLog
    from app.services import alarm_outbox

    engine = create_engine(f"sqlite:///{tmp_path / 'abschluss.db'}",
                           connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(alarm_outbox, "SessionLocal", factory)
    with factory() as db:
        set_tenant_context(db, None)
        org = FireDept(slug="abschluss", name="Abschluss")
        db.add(org)
        db.flush()
        incident = Incident(primary_org_id=org.id, alarm_type_code="B2", status="active",
                            started_at=datetime(2026, 9, 4, 8, 0), address_city="Testort")
        db.add(incident)
        db.flush()
        for kanal in ("sms", "push", "teams", "mail"):
            db.add(IncidentAlarmJob(
                incident_id=incident.id, org_id=org.id, channel=kanal, status="sent",
                next_attempt_at=datetime.now(UTC).replace(tzinfo=None), context={},
            ))
        db.commit()
        incident_id, org_id = incident.id, org.id

    start = threading.Barrier(4)

    def fertig():
        with factory() as db:
            set_tenant_context(db, None)
            vorfall = db.get(Incident, incident_id)
            start.wait()
            alarm_outbox._finish_if_done(db, vorfall, org_id, None)

    threads = [threading.Thread(target=fertig) for _ in range(4)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    with factory() as db:
        set_tenant_context(db, None)
        assert db.query(AuditLog).filter_by(action="incident.alarm_finished", incident_id=incident_id).count() == 1
    engine.dispose()
