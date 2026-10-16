"""Lease-Service der Windows-Druckagents: Redundanz, Gleichzeitigkeit, Offline, Neustart, Fehler.

Zeit wird über den now-Parameter gesteuert. Jede Testfunktion nutzt eine eigene Org, weil
die Service-Funktionen committen (Daten bleiben in der Worker-DB) und der Sweep org-übergreifend
arbeitet – Assertions prüfen deshalb nur die eigenen Job-IDs.
"""
import itertools
import threading
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.models.gateway import (
    DELIVERY_LEASE,
    GATEWAY_KIND_WINDOWS_AGENT,
    GATEWAY_STATUS_OFFLINE,
    GATEWAY_STATUS_ONLINE,
    JOB_CANCELED,
    JOB_FAILED,
    JOB_PRINTING,
    JOB_QUEUED,
    JOB_SENT,
    JOB_UNCERTAIN,
    PHASE_SUBMITTING,
    Gateway,
    Printer,
    PrinterAgentRoute,
    PrintJob,
)
from app.services import print_agent_service as svc
from tests.conftest import TEST_DB_URL

NOW = datetime(2026, 1, 1, 12, 0)
_org_ids = itertools.count(992000 + (uuid4().int % 5000) * 10)
_engine = None


def _engine_():
    global _engine
    if _engine is None:
        _engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False, "timeout": 30})
    return _engine


def _session():
    session = sessionmaker(bind=_engine_())()
    set_tenant_context(session, None)
    return session


@pytest.fixture
def db(setup_db):
    session = _session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _agent(db, org_id, name, *, online=True, seen=NOW):
    gateway = Gateway(
        org_id=org_id, name=name, kind=GATEWAY_KIND_WINDOWS_AGENT,
        device_token_hash=f"{name}-{uuid4().hex}",
        status=GATEWAY_STATUS_ONLINE if online else GATEWAY_STATUS_OFFLINE, last_seen_at=seen,
    )
    db.add(gateway)
    db.flush()
    return gateway


def _route(db, printer, gateway, priority, *, ready=True, identity=None, checked=NOW):
    route = PrinterAgentRoute(
        org_id=printer.org_id, printer_id=printer.id, gateway_id=gateway.id, priority=priority,
        local_queue=f"Queue {gateway.name}", ready=ready,
        readiness={"state": "ready" if ready else "offline", "checked_at": checked.isoformat()},
        identity=identity if identity is not None else {"serial": "abc"},
    )
    db.add(route)
    db.flush()
    return route


def _kommandofunk(db, **route_kwargs):
    """Druckziel mit Agent A (Prio 10) und Agent B (Prio 20)."""
    org_id = next(_org_ids)
    a = _agent(db, org_id, "A", **route_kwargs.pop("a", {}))
    b = _agent(db, org_id, "B", **route_kwargs.pop("b", {}))
    printer = Printer(org_id=org_id, gateway_id=a.id, name="Kommandofunk", uri="agent://kommandofunk",
                      identity={"serial": "ABC"}, aktiv=True)
    db.add(printer)
    db.flush()
    ra = _route(db, printer, a, 10, **route_kwargs.pop("ra", {}))
    rb = _route(db, printer, b, 20, **route_kwargs.pop("rb", {}))
    db.commit()
    return a, b, printer, ra, rb


def _job(db, printer, *, queued_at=NOW, rule_id=None):
    job = PrintJob(
        org_id=printer.org_id, gateway_id=printer.gateway_id, printer_id=printer.id,
        document_type="einsatzinfo", idempotency_key=f"agent-{uuid4().hex}",
        delivery=DELIVERY_LEASE, status=JOB_QUEUED, queued_at=queued_at, rule_id=rule_id,
    )
    db.add(job)
    db.commit()
    return job


def _reload(db, job):
    db.expire_all()
    return db.get(PrintJob, job.id)


def _bis_gespoolt(db, agent, job, now=NOW):
    grant = svc.lease_jobs(db, agent, 1, now)[0]
    assert svc.print_intent(db, agent, job.id, grant["lease_token"], now) == (True, None)
    svc.apply_agent_status(db, agent, job.id, grant["lease_token"], JOB_PRINTING, "spooled",
                           "win:7", None, now)
    return grant


# ── Redundanz ────────────────────────────────────────────────────────────────

def test_primaer_bereit_bekommt_den_job_und_sekundaer_nicht(db):
    a, b, printer, ra, rb = _kommandofunk(db)
    job = _job(db, printer)
    assert svc.route_kandidaten(db, printer, NOW) == [ra, rb]
    assert svc.lease_jobs(db, b, 5, NOW) == []
    grants = svc.lease_jobs(db, a, 5, NOW)
    assert [g["job_id"] for g in grants] == [job.id]
    assert grants[0]["route"]["local_queue"] == "Queue A"
    geladen = _reload(db, job)
    assert (geladen.status, geladen.lease_gateway_id, geladen.gateway_id) == (JOB_SENT, a.id, a.id)


def test_primaer_nicht_bereit_sekundaer_sofort(db):
    _a, b, printer, _ra, rb = _kommandofunk(db, ra={"ready": False})
    job = _job(db, printer)
    assert svc.route_kandidaten(db, printer, NOW) == [rb]
    assert [g["job_id"] for g in svc.lease_jobs(db, b, 5, NOW)] == [job.id]


def test_primaer_offline_sekundaer_sofort(db):
    _a, b, printer, _ra, _rb = _kommandofunk(db, a={"seen": NOW - timedelta(seconds=svc.AGENT_ONLINE_S + 1)})
    job = _job(db, printer)
    assert [g["job_id"] for g in svc.lease_jobs(db, b, 5, NOW)] == [job.id]


def test_veraltete_bereitschaft_zaehlt_nicht(db):
    _a, b, printer, _ra, _rb = _kommandofunk(
        db, ra={"checked": NOW - timedelta(seconds=svc.READINESS_FRESH_S + 1)})
    job = _job(db, printer)
    assert [g["job_id"] for g in svc.lease_jobs(db, b, 5, NOW)] == [job.id]


def test_primaer_bereit_greift_nicht_zu_sekundaer_erst_nach_grace(db):
    _a, b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    knapp_davor = NOW + timedelta(seconds=svc.FALLBACK_GRACE_S - 1)
    assert svc.lease_jobs(db, b, 5, knapp_davor) == []
    danach = NOW + timedelta(seconds=svc.FALLBACK_GRACE_S)
    assert [g["job_id"] for g in svc.lease_jobs(db, b, 5, danach)] == [job.id]


def test_identitaet_abweichend_route_ausgeschlossen(db):
    a, b, printer, ra, rb = _kommandofunk(db)
    svc.route_readiness_aktualisieren(db, a, [{
        "route_id": ra.id, "ready": True, "state": "ready", "identity": {"serial": "FALSCH"},
    }], NOW)
    db.refresh(ra)
    assert ra.ready is False
    assert ra.readiness["state"] == "identity_mismatch"
    assert svc.route_kandidaten(db, printer, NOW) == [rb]


def test_identitaet_wird_beim_ersten_binden_uebernommen(db):
    a, _b, printer, ra, _rb = _kommandofunk(db)
    printer.identity = None
    db.commit()
    svc.route_readiness_aktualisieren(db, a, [{
        "route_id": ra.id, "ready": True, "state": "ready", "identity": {"serial": "XYZ"},
        "capabilities": {"media": ["A4", "A3"], "duplex": True, "color": True},
    }], NOW)
    db.refresh(printer)
    assert printer.identity == {"serial": "XYZ"}
    assert printer.capabilities["media"] == ["A4", "A3"]


def test_readiness_fremder_routen_wird_ignoriert(db):
    a, _b, _printer, _ra, rb = _kommandofunk(db)
    svc.route_readiness_aktualisieren(db, a, [{"route_id": rb.id, "ready": False, "state": "offline"}], NOW)
    db.refresh(rb)
    assert rb.ready is True


@pytest.mark.parametrize(("soll", "ist", "erwartet"), [
    ({"mac": "AA:BB"}, {"mac": "aabb"}, True),
    ({"serial": "1"}, {"serial": "2", "ip": "10.0.0.1"}, False),
    ({"ip": "10.0.0.1"}, {"ip": "10.0.0.1"}, True),
    ({}, {"serial": "x"}, True),
])
def test_identitaet_passt(soll, ist, erwartet):
    assert svc.identitaet_passt(soll, ist) is erwartet


# ── Gleichzeitigkeit / mehrere Worker ────────────────────────────────────────

def test_parallel_leasen_jeder_job_genau_einmal(db):
    a, b, printer, _ra, _rb = _kommandofunk(db)
    jobs = [_job(db, printer) for _ in range(30)]
    spaeter = NOW + timedelta(seconds=svc.FALLBACK_GRACE_S)  # beide Agents dürfen
    ergebnisse: list[tuple[int, int]] = []
    sperre = threading.Lock()

    def arbeiter(gateway_id):
        session = _session()
        try:
            gateway = session.get(Gateway, gateway_id)
            for _ in range(10):
                for grant in svc.lease_jobs(session, gateway, 5, spaeter):
                    with sperre:
                        ergebnisse.append((grant["job_id"], gateway_id))
        finally:
            session.close()

    threads = [threading.Thread(target=arbeiter, args=(gid,)) for gid in (a.id, a.id, b.id, b.id)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    job_ids = [job_id for job_id, _ in ergebnisse]
    assert sorted(job_ids) == sorted(job.id for job in jobs)
    assert len(job_ids) == len(set(job_ids))
    db.expire_all()
    for job_id, gateway_id in ergebnisse:
        assert db.get(PrintJob, job_id).lease_gateway_id == gateway_id


def test_parallele_intents_genau_einer_gewinnt(db):
    a, _b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    grant = svc.lease_jobs(db, a, 1, NOW)[0]
    tokens = [grant["lease_token"], "falsch-" + uuid4().hex]
    ergebnisse = []

    def intent(token):
        session = _session()
        try:
            ergebnisse.append(svc.print_intent(session, session.get(Gateway, a.id), job.id, token, NOW))
        finally:
            session.close()

    threads = [threading.Thread(target=intent, args=(token,)) for token in tokens]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(ok for ok, _ in ergebnisse) == [False, True]


def test_sweep_doppelt_nur_ein_uebergang(db):
    a, _b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    svc.lease_jobs(db, a, 1, NOW)
    spaeter = NOW + timedelta(seconds=svc.LEASE_TTL_S + 1)
    ergebnisse = []

    def lauf():
        session = _session()
        try:
            ergebnisse.append(svc.sweep(session, spaeter))
        finally:
            session.close()

    threads = [threading.Thread(target=lauf) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    treffer = [eintrag for ergebnis in ergebnisse for eintrag in ergebnis["requeued"] if eintrag[0] == job.id]
    assert treffer == [(job.id, printer.org_id)]


# ── Offline-Fälle ────────────────────────────────────────────────────────────

def test_kein_agent_bereit_nach_wartezeit_failed(db):
    _a, _b, printer, _ra, _rb = _kommandofunk(db, ra={"ready": False}, rb={"ready": False})
    job = _job(db, printer)
    zu_frueh = svc.sweep(db, NOW + timedelta(seconds=svc.QUEUE_MAX_WAIT_S - 1))
    assert (job.id, printer.org_id) not in zu_frueh["failed"]
    ergebnis = svc.sweep(db, NOW + timedelta(seconds=svc.QUEUE_MAX_WAIT_S + 1))
    assert (job.id, printer.org_id) in ergebnis["failed"]
    geladen = _reload(db, job)
    assert (geladen.status, geladen.error) == (JOB_FAILED, svc.FEHLER_KEIN_AGENT)


def test_lease_abgelaufen_vor_intent_geht_an_b(db):
    a, b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    grant_a = svc.lease_jobs(db, a, 1, NOW)[0]
    # A fällt aus (kein Heartbeat mehr), Lease läuft ab.
    a.last_seen_at = NOW - timedelta(seconds=svc.AGENT_ONLINE_S)
    db.commit()
    spaeter = NOW + timedelta(seconds=svc.LEASE_TTL_S + 1)
    assert (job.id, printer.org_id) in svc.sweep(db, spaeter)["requeued"]
    grant_b = svc.lease_jobs(db, b, 1, spaeter)[0]
    assert grant_b["job_id"] == job.id
    assert grant_b["lease_generation"] == grant_a["lease_generation"] + 1
    # A kommt zurück und will mit altem Token drucken → abgelehnt.
    assert svc.print_intent(db, a, job.id, grant_a["lease_token"], spaeter) == (False, "not_owner")
    assert svc.print_intent(db, b, job.id, grant_b["lease_token"], spaeter) == (True, None)


def test_lease_ablauf_beim_dritten_versuch_failed(db):
    a, _b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    zeit = NOW
    for versuch in range(svc.MAX_AGENT_ATTEMPTS):
        assert svc.lease_jobs(db, a, 1, zeit)
        zeit += timedelta(seconds=svc.LEASE_TTL_S + 1)
        a.last_seen_at = zeit
        db.commit()
        ergebnis = svc.sweep(db, zeit)
        art = "failed" if versuch == svc.MAX_AGENT_ATTEMPTS - 1 else "requeued"
        assert (job.id, printer.org_id) in ergebnis[art]
    geladen = _reload(db, job)
    assert (geladen.status, geladen.error) == (JOB_FAILED, svc.FEHLER_MEHRFACH)


def test_nach_intent_kein_kontakt_wird_uncertain_und_nie_neu_verliehen(db):
    a, b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    grant = svc.lease_jobs(db, a, 1, NOW)[0]
    assert svc.print_intent(db, a, job.id, grant["lease_token"], NOW) == (True, None)
    spaeter = NOW + timedelta(seconds=svc.LEASE_TTL_S + 1)
    assert (job.id, printer.org_id) not in svc.sweep(db, spaeter)["requeued"]
    assert svc.lease_jobs(db, b, 5, spaeter) == []
    viel_spaeter = NOW + timedelta(seconds=svc.UNCERTAIN_AFTER_S + 1)
    assert (job.id, printer.org_id) in svc.sweep(db, viel_spaeter)["uncertain"]
    assert _reload(db, job).status == JOB_UNCERTAIN
    nochmal = svc.sweep(db, viel_spaeter + timedelta(hours=1))
    assert all(eintrag[0] != job.id for liste in nochmal.values() for eintrag in liste)
    assert svc.lease_jobs(db, b, 5, viel_spaeter) == []


def test_renew_haelt_druckenden_job_am_leben(db):
    a, _b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    grant = _bis_gespoolt(db, a, job)
    spaeter = NOW + timedelta(seconds=svc.UNCERTAIN_AFTER_S - 10)
    assert svc.renew(db, a, [{"job_id": job.id, "lease_token": grant["lease_token"]}], spaeter) == []
    assert (job.id, printer.org_id) not in svc.sweep(db, spaeter + timedelta(seconds=30))["uncertain"]
    assert svc.renew(db, a, [{"job_id": job.id, "lease_token": "falsch"}], spaeter) == [job.id]


# ── Neustart / resume ────────────────────────────────────────────────────────

def test_resume_gueltiger_token_verlaengert(db):
    a, _b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    grant = svc.lease_jobs(db, a, 1, NOW)[0]
    svc.print_intent(db, a, job.id, grant["lease_token"], NOW)
    spaeter = NOW + timedelta(seconds=300)
    abandon = svc.resume(db, a, [{"job_id": job.id, "lease_token": grant["lease_token"],
                                  "local_state": "submitting"}], spaeter)
    assert abandon == []
    geladen = _reload(db, job)
    assert geladen.agent_contact_at == spaeter
    assert geladen.phase == PHASE_SUBMITTING


def test_resume_falscher_oder_fremder_token_abandon(db):
    a, b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    grant = svc.lease_jobs(db, a, 1, NOW)[0]
    abandon = svc.resume(db, b, [{"job_id": job.id, "lease_token": grant["lease_token"]},
                                 {"job_id": 987654321, "lease_token": "x"}], NOW)
    assert [eintrag["job_id"] for eintrag in abandon] == [job.id, 987654321]


# ── Druckfehler ──────────────────────────────────────────────────────────────

def test_abruffehler_reiht_neu_ein_bis_zum_dritten_versuch(db):
    a, b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    zeit = NOW
    for versuch in range(1, svc.MAX_AGENT_ATTEMPTS + 1):
        grants = svc.lease_jobs(db, a, 1, zeit)
        assert grants, versuch
        ergebnis = svc.apply_agent_status(db, a, job.id, grants[0]["lease_token"], JOB_FAILED,
                                          "fetching", None, "Download fehlgeschlagen", zeit)
        if versuch < svc.MAX_AGENT_ATTEMPTS:
            assert ergebnis["requeued"] is True
            assert ergebnis["fallback"] is False
            assert set(ergebnis["notify_gateway_ids"]) == {a.id, b.id}
        else:
            assert ergebnis["requeued"] is False
            assert ergebnis["fallback"] is True
            assert ergebnis["status"] == JOB_FAILED
        zeit += timedelta(seconds=1)


def test_spoolerfehler_nach_uebergabe_failed_mit_ersatzdruck(db):
    a, _b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    grant = _bis_gespoolt(db, a, job)
    ergebnis = svc.apply_agent_status(db, a, job.id, grant["lease_token"], JOB_FAILED, "spooled",
                                      None, "Druckauftrag wurde abgebrochen", NOW)
    assert (ergebnis["status"], ergebnis["fallback"], ergebnis["requeued"]) == (JOB_FAILED, True, False)


def test_abgelehnte_uebergabe_failed_mit_ersatzdruck(db):
    a, _b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    grant = svc.lease_jobs(db, a, 1, NOW)[0]
    svc.print_intent(db, a, job.id, grant["lease_token"], NOW)
    ergebnis = svc.apply_agent_status(db, a, job.id, grant["lease_token"], JOB_FAILED, "submitting",
                                      None, "Queue fehlt", NOW)
    assert (ergebnis["status"], ergebnis["fallback"]) == (JOB_FAILED, True)


def test_uncertain_ohne_ersatzdruck_und_endgueltig_fuer_agent(db):
    a, _b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    grant = _bis_gespoolt(db, a, job)
    ergebnis = svc.apply_agent_status(db, a, job.id, grant["lease_token"], JOB_UNCERTAIN, "submitting",
                                      None, "Neustart während Übergabe", NOW)
    assert (ergebnis["status"], ergebnis["fallback"]) == (JOB_UNCERTAIN, False)
    nochmal = svc.apply_agent_status(db, a, job.id, grant["lease_token"], "done", "spooled", None, None, NOW)
    assert nochmal["result"] == "ignored"
    assert _reload(db, job).status == JOB_UNCERTAIN


def test_veralteter_token_wird_ignoriert(db):
    a, _b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    svc.lease_jobs(db, a, 1, NOW)
    ergebnis = svc.apply_agent_status(db, a, job.id, "alt", "done", "spooled", None, None, NOW)
    assert ergebnis["result"] == "stale"
    geladen = _reload(db, job)
    assert (geladen.status, geladen.phase) == (JOB_SENT, "leased")


def test_status_done_nach_spoolen(db):
    a, _b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    grant = _bis_gespoolt(db, a, job)
    assert _reload(db, job).spool_ref == "win:7"
    ergebnis = svc.apply_agent_status(db, a, job.id, grant["lease_token"], "done", "spooled", None, None, NOW)
    assert ergebnis["status"] == "done"


def test_agent_fremder_org_least_keine_jobs(db):
    _a, _b, printer, _ra, _rb = _kommandofunk(db)
    _job(db, printer)
    fremd_a, _fremd_b, fremd_printer, _fra, _frb = _kommandofunk(db)
    # Route des fremden Agents zeigt (manipuliert) auf das Druckziel der ersten Org.
    db.add(PrinterAgentRoute(org_id=fremd_printer.org_id, printer_id=printer.id, gateway_id=fremd_a.id,
                             priority=1, local_queue="X", ready=True,
                             readiness={"state": "ready", "checked_at": NOW.isoformat()}))
    db.commit()
    assert svc.lease_jobs(db, fremd_a, 5, NOW + timedelta(minutes=1)) == []


# ── print_intent-Gründe ──────────────────────────────────────────────────────

def test_intent_nach_lease_ablauf_abgelehnt(db):
    a, _b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    grant = svc.lease_jobs(db, a, 1, NOW)[0]
    spaeter = NOW + timedelta(seconds=svc.LEASE_TTL_S + 1)
    assert svc.print_intent(db, a, job.id, grant["lease_token"], spaeter) == (False, "lease_expired")


def test_intent_nach_abbruch_abgelehnt(db):
    a, _b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    grant = svc.lease_jobs(db, a, 1, NOW)[0]
    geladen = _reload(db, job)
    geladen.status = JOB_CANCELED
    db.commit()
    assert svc.print_intent(db, a, job.id, grant["lease_token"], NOW) == (False, "canceled")


def test_intent_wiederholung_mit_gleichem_token_ok(db):
    a, _b, printer, _ra, _rb = _kommandofunk(db)
    job = _job(db, printer)
    grant = svc.lease_jobs(db, a, 1, NOW)[0]
    assert svc.print_intent(db, a, job.id, grant["lease_token"], NOW) == (True, None)
    assert svc.print_intent(db, a, job.id, grant["lease_token"], NOW) == (True, None)


def test_enqueue_und_config_sync(db):
    a, b, printer, ra, _rb = _kommandofunk(db)
    job = PrintJob(org_id=printer.org_id, gateway_id=a.id, printer_id=printer.id,
                   document_type="einsatzinfo", idempotency_key=f"agent-{uuid4().hex}")
    db.add(job)
    db.commit()
    assert svc.hat_agent_routen(db, printer.id)
    assert svc.enqueue_for_agents(db, job, NOW) == sorted([a.id, b.id])
    assert (job.delivery, job.status, job.queued_at) == (DELIVERY_LEASE, JOB_QUEUED, NOW)
    sync = svc.agent_config_sync(db, a)
    assert [route["route_id"] for route in sync["routes"]] == [ra.id]
    assert sync["routes"][0]["printer_name"] == "Kommandofunk"
