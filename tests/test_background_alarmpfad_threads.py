"""L3a: Alarm-DB im eigenen Pool, Transport und Warten auf dem aufrufenden Loop."""
from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect

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
def alarm_db_threads(monkeypatch):
    loop_thread = threading.get_ident()
    aufrufe, fehler, offen = [], [], set()

    def session():
        thread = threading.get_ident()
        if thread == loop_thread or not threading.current_thread().name.startswith("alarm-db"):
            fehler.append("Session ausserhalb des Alarm-Pools")
        db = TestingSession()
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
