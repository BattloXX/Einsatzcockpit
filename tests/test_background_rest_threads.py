"""L3b: DB-Transaktionen im Thread, Nutzdaten/Transporte auf dem aufrufenden Loop."""
from __future__ import annotations

import asyncio
import importlib
import threading
import time
from contextlib import contextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event

from app import db as datenbank
from app.core.tenant import set_tenant_context
from app.models.incident import Incident, Message
from app.models.master import FireDept, OrgSettings
from tests.conftest import TestingSession

_MODULE = (
    "ai_service", "atemschutz_pruefung_service", "autoclose", "gk_zugang_service",
    "katplan_ki_service", "objekt_ki_kontext", "objekt_ki_service", "planspiel_engine",
    "resend_inbound_service", "schaden_service", "sms_inbox_service", "teams_alarm_service",
    "verleih_service", "incident_live_notify", "gsl_live_notify",
)


@contextmanager
def session():
    with TestingSession() as db:
        set_tenant_context(db, None)
        yield db


@contextmanager
def db_threads(monkeypatch):
    """Auch abgefangene Thread-Verstoesse und noch offene Sessions erkennen."""
    loop_thread = threading.get_ident()
    aufrufe = []
    offen = set()

    def pruefen(*args):
        aufrufe.append(threading.get_ident())
        assert aufrufe[-1] != loop_thread

    def factory():
        pruefen()
        db = TestingSession()
        offen.add(id(db))
        for name in ("do_orm_execute", "before_flush", "before_commit", "after_rollback"):
            event.listen(db, name, pruefen)
        schliessen = db.close

        def close():
            pruefen()
            try:
                schliessen()
            finally:
                offen.remove(id(db))

        db.close = close
        return db

    with monkeypatch.context() as patch:
        patch.setattr(datenbank, "SessionLocal", factory)
        for name in _MODULE:
            modul = importlib.import_module(f"app.services.{name}")
            if hasattr(modul, "SessionLocal"):
                patch.setattr(modul, "SessionLocal", factory)
        yield offen
    assert aufrufe and loop_thread not in aufrufe
    assert not offen


@pytest.fixture
def org_id():
    with session() as db:
        org = FireDept(slug=f"rest-threads-{uuid4().hex}", name="Thread-Org", color="#123456", bos="Feuerwehr")
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id))
        db.commit()
        return org.id


async def test_ai_protokolle_und_monatswechsel_im_thread(monkeypatch, org_id):
    from app.models.master import AIRequestLog
    from app.services import ai_service as service

    with session() as db:
        cfg = db.query(OrgSettings).filter_by(org_id=org_id).one()
        cfg.ai_tokens_month_key, cfg.ai_tokens_used_month = "2020-01", 999
        db.commit()
    with db_threads(monkeypatch):
        await service._record_token_usage(org_id, 23, "thread-model")
        await service._record_ai_request(
            feature="thread", model="thread-model", org_id=org_id, started_at=time.perf_counter(),
            success=False, input_tokens=12, output_tokens=11, error_message="x" * 600,
        )
    with session() as db:
        cfg = db.query(OrgSettings).filter_by(org_id=org_id).one()
        assert (cfg.ai_tokens_month_key, cfg.ai_tokens_used_month) == (datetime.now(UTC).strftime("%Y-%m"), 23)
        log = db.query(AIRequestLog).filter_by(org_id=org_id).one()
        assert (log.feature, log.model, log.total_tokens, log.success, log.error_message) == (
            "thread", "thread-model", 23, False, "x" * 500,
        )


async def test_defekt_mail_teams_und_protokoll(monkeypatch, org_id):
    from app.models.atemschutz_pruefung import AtemschutzGeraet, AtemschutzPruefung, AtemschutzPruefBenachrichtigung
    from app.services import atemschutz_pruefung_service as service, mail_service, teams_service

    with session() as db:
        cfg = db.query(OrgSettings).filter_by(org_id=org_id).one()
        cfg.atemschutz_wart_mail = "wart@example.invalid"
        cfg.atemschutz_wart_teams_webhook_url = "https://teams.invalid/defekt"
        geraet = AtemschutzGeraet(org_id=org_id, nummer="PA-7")
        db.add(geraet)
        db.flush()
        pruefung = AtemschutzPruefung(org_id=org_id, geraet_id=geraet.id, eingesetzt_am=date(2026, 11, 6),
                                     traeger_free_text="Tester", sichtpruefung_ok=False)
        db.add(pruefung)
        db.commit()
        ident = pruefung.id
    loop = asyncio.get_running_loop()
    gesendet = []

    async def mail(msg, cfg):
        assert asyncio.get_running_loop() is loop
        assert msg["To"] == "wart@example.invalid"
        assert "PA-7" in msg["Subject"]
        gesendet.append("mail")

    async def teams(webhook, titel, text, **kwargs):
        assert asyncio.get_running_loop() is loop
        assert "Sichtprüfung" in text and "Tester" in text
        assert kwargs["url"] == f"https://ecp.invalid/atemschutz-pruefung/{ident}"
        gesendet.append("teams")
        return False

    monkeypatch.setattr(mail_service, "_org_smtp_cfg", lambda *args: {"from_addr": "ecp@example.invalid"})
    monkeypatch.setattr(mail_service, "_send", mail)
    monkeypatch.setattr(teams_service, "post_teams_karte", teams)
    with db_threads(monkeypatch):
        await service.melde_defekt_background(ident, "https://ecp.invalid")
    assert gesendet == ["mail", "teams"]
    with session() as db:
        logs = db.query(AtemschutzPruefBenachrichtigung).filter_by(pruefung_id=ident).order_by(
            AtemschutzPruefBenachrichtigung.id).all()
        assert [(r.kanal, r.status) for r in logs] == [("mail", "gesendet"), ("teams", "fehler")]


async def test_schaden_teams_und_verbuchung(monkeypatch, org_id):
    from app.models.fahrtenbuch import Fahrt, FahrtBenachrichtigung, FahrtKategorie, Fahrtzweck
    from app.models.master import VehicleMaster
    from app.services import schaden_service as service, teams_service

    with session() as db:
        fahrzeug = VehicleMaster(dept_id=org_id, code="RLF", name="RLF", type="RLF",
                                 schaden_teams_webhook_override="https://teams.invalid/schaden")
        zweck = Fahrtzweck(org_id=org_id, name="Test", kategorie=FahrtKategorie.uebung)
        db.add_all([fahrzeug, zweck])
        db.flush()
        fahrt = Fahrt(org_id=org_id, fahrzeug_id=fahrzeug.id, zweck_id=zweck.id,
                      fahrttyp=FahrtKategorie.uebung, maschinist_name="Tester", zeitpunkt=datetime(2026, 11, 6),
                      schaden_beschreibung="Spiegel", schaden_betriebsfaehig=True)
        db.add(fahrt)
        db.commit()
        ident = fahrt.id
    loop = asyncio.get_running_loop()
    gesendet = []

    async def teams(url, payload):
        assert asyncio.get_running_loop() is loop
        assert url == "https://teams.invalid/schaden" and "Spiegel" in str(payload)
        gesendet.append(payload)
        return True

    monkeypatch.setattr(teams_service, "post_teams_adaptive_card", teams)
    with db_threads(monkeypatch):
        await service.melde_schaden_background(ident, "https://ecp.invalid")
    assert len(gesendet) == 1
    with session() as db:
        log = db.query(FahrtBenachrichtigung).filter_by(fahrt_id=ident).one()
        assert (log.kanal, log.status) == ("teams", "gesendet")


async def test_autoclose_nachrichten_auf_loop_db_im_thread(monkeypatch, org_id):
    from app.services import autoclose as service, incident_live_notify

    with session() as db:
        incident = Incident(primary_org_id=org_id, alarm_type_code="T1", status="closed")
        db.add(incident)
        db.commit()
        ident = incident.id
    loop = asyncio.get_running_loop()
    events = []

    async def broadcast(key, payload):
        assert asyncio.get_running_loop() is loop
        events.append((key, payload))

    def push(*args):
        assert threading.get_ident() != loop_thread

    loop_thread = threading.get_ident()
    monkeypatch.setattr(service.manager, "broadcast", broadcast)
    monkeypatch.setattr(incident_live_notify, "_dispatch_live_push", push)
    with db_threads(monkeypatch):
        await service._broadcast_events([(ident, 30)], [(ident, org_id)])
    assert events[0] == (ident, {"type": "autoclose_warning", "grace_minutes": 30})
    assert events[1] == (ident, {"type": "incident_closed"})
    assert any(payload["type"] == "einsatz_live" for _, payload in events)


async def test_gk_auto_sms_status_und_loop(monkeypatch):
    from app.models.major_incident import LageEinheitZugangVersand
    from app.services import gk_zugang_service as service
    from tests.test_gk_zugang_service import _daten

    monkeypatch.setattr(service, "sms_available", lambda *args: True)
    monkeypatch.setattr(service, "darf_extern", lambda *args, **kwargs: True)
    with session() as db:
        org, lage, einheit, leader = _daten(db)
        db.query(OrgSettings).filter_by(org_id=org.id).one().gk_zugang_auto_sms = True
        auftrag = service.plane_auto_sms(db, lage, einheit, leader, "neu")
        assert auftrag
        db.commit()
    loop = asyncio.get_running_loop()
    aufrufe = []

    async def send(org, phone, text, **kwargs):
        assert asyncio.get_running_loop() is loop
        assert org == auftrag.org_id and "gkz_" in text
        aufrufe.append("sms")
        return SimpleNamespace(success=True, provider="test")

    async def broadcast(lage_id, payload):
        assert asyncio.get_running_loop() is loop
        assert lage_id == auftrag.lage_id and payload["type"] == "ressource:changed"
        aufrufe.append("broadcast")

    monkeypatch.setattr(service, "send_sms", send)
    monkeypatch.setattr(service, "broadcast_lage", broadcast)
    with db_threads(monkeypatch):
        await service.sende_auto_sms(auftrag)
    assert aufrufe == ["sms", "broadcast"]
    with session() as db:
        row = db.get(LageEinheitZugangVersand, auftrag.versand_id)
        assert row.status == "gesendet" and row.sms_log_id is not None


async def test_objektkontext_unveraendert_im_thread(monkeypatch):
    from app.services import objekt_ki_kontext as service
    from tests.test_objekt_ki_kontext import _setup

    db, incident_id = _setup()
    try:
        erwartet = service.objekt_ki_kontext(db, incident_id)
    finally:
        db.close()
    with db_threads(monkeypatch):
        assert await service.lade_objekt_kontext(incident_id) == erwartet


async def test_katplan_api_ohne_offene_session(monkeypatch, org_id):
    from app.models.katplan import KatplanPlan, KatplanDokument, KatplanDokumentSeite, KatplanImportVorschlag
    from app.services import ai_service, katplan_ki_service as service

    monkeypatch.setattr(ai_service, "is_enabled", lambda: True)
    with session() as db:
        db.query(OrgSettings).filter_by(org_id=org_id).one().katplan_ki_import_enabled = True
        plan = KatplanPlan(org_id=org_id, titel="Hochwasser")
        db.add(plan)
        db.flush()
        dokument = KatplanDokument(org_id=org_id, plan_id=plan.id, dateiname_original="Plan.pdf",
                                   pfad="plan.pdf", titel="Plan", groesse_bytes=1, seitenzahl=2, status="fertig")
        db.add(dokument)
        db.flush()
        for nr in (1, 2):
            db.add(KatplanDokumentSeite(org_id=org_id, plan_id=plan.id, dokument_id=dokument.id,
                                       seiten_nr=nr, einzel_pdf_pfad="seite.pdf", volltext=f"Massnahmen {nr}"))
        db.commit()
        ident = dokument.id
    loop = asyncio.get_running_loop()
    aufrufe = []

    async def complete(system, prompt, **kwargs):
        assert asyncio.get_running_loop() is loop and not offen
        assert kwargs == {"feature": "katplan_import", "org_id": org_id}
        aufrufe.append(prompt)
        if len(aufrufe) == 1:
            return "ungueltig"
        return '{"elemente": [{"typ": "info", "titel": "Sperre"}]}'

    monkeypatch.setattr(ai_service, "complete", complete)
    with db_threads(monkeypatch) as offen:
        assert await service.analysiere_quelle(ident) == 1
    assert len(aufrufe) == 2
    with session() as db:
        dokument = db.get(KatplanDokument, ident)
        assert dokument.ki_status == "fertig" and dokument.ki_fehler == "1 Seite(n) konnten nicht analysiert werden."
        v = db.query(KatplanImportVorschlag).filter_by(dokument_id=ident).one()
        assert (v.titel, v.seiten_nr, v.status) == ("Sperre", 2, "offen")


@pytest.mark.parametrize("render_fehler", [False, True])
async def test_objekt_ki_api_ohne_session_klassifikation_und_nachholen(monkeypatch, org_id, render_fehler):
    from app.models.objekt import Objekt, ObjektDokument, ObjektDokumentSeite, ObjektSeiteKiVorschlag, ObjektStammdatenVorschlag
    from app.services import ai_service, objekt_ki_service as service

    monkeypatch.setattr(ai_service, "is_enabled", lambda: True)
    with session() as db:
        db.query(OrgSettings).filter_by(org_id=org_id).one().objekt_ki_klassifikation_enabled = True
        objekt = Objekt(org_id=org_id, name="Lager", status="freigegeben")
        db.add(objekt)
        db.flush()
        dokument = ObjektDokument(org_id=org_id, objekt_id=objekt.id, dateiname_original="Plan.pdf", pfad="p.pdf")
        db.add(dokument)
        db.flush()
        for nr, art in ((1, None), (2, "objektinformation")):
            db.add(ObjektDokumentSeite(org_id=org_id, objekt_id=objekt.id, dokument_id=dokument.id,
                                      seiten_nr=nr, dokumentart=art, volltext="Objektbeschreibung"))
        db.commit()
        ident = objekt.id
    loop = asyncio.get_running_loop()
    aufrufe = []

    def bild(seite):
        assert threading.get_ident() != loop_thread
        if render_fehler and seite.seiten_nr == 2:
            raise RuntimeError("Rendering nicht lesbar")
        return b"png"

    async def vision(system, prompt, images, **kwargs):
        assert asyncio.get_running_loop() is loop and not offen
        assert images == [b"png"] and kwargs["org_id"] == org_id
        aufrufe.append(kwargs["feature"])
        if kwargs["feature"] == "objekt_dokumentklassifizierung":
            return '{"dokumentart": "objektinformation", "titel": "Objektbeschreibung"}'
        return '{"bmz_standort": "Eingang Nord"}'

    loop_thread = threading.get_ident()
    monkeypatch.setattr(service, "_lade_bild_fuer_ki", bild)
    monkeypatch.setattr(ai_service, "complete_vision", vision)
    with db_threads(monkeypatch) as offen:
        assert await service.analysiere_unklassifizierte_seiten(ident) == (1 if render_fehler else 2)
    assert aufrufe == (["objekt_dokumentklassifizierung", "objekt_stammdaten_extraktion"]
                       + ([] if render_fehler else ["objekt_stammdaten_extraktion"]))
    with session() as db:
        if render_fehler:
            # Der fruehere Ablauf gab die bisherige Anzahl zurueck, rollte aber alles zurueck.
            assert not db.query(ObjektSeiteKiVorschlag).filter_by(org_id=org_id).count()
            assert not db.query(ObjektStammdatenVorschlag).filter_by(objekt_id=ident).count()
            return
        assert db.query(ObjektSeiteKiVorschlag).filter_by(org_id=org_id).one().titel == "Objektbeschreibung"
        assert [v.bmz_standort for v in db.query(ObjektStammdatenVorschlag).filter_by(objekt_id=ident)] == [
            "Eingang Nord", "Eingang Nord",
        ]


async def test_planspiel_benachrichtigungen_auf_loop(monkeypatch, org_id):
    from app.models.major_incident import MajorIncident, IncidentSite
    from app.services import broadcast, gsl_live_notify, planspiel_engine as service

    with session() as db:
        incident = Incident(primary_org_id=org_id, alarm_type_code="T1", status="active")
        lage = MajorIncident(org_id=org_id, name="Testlage", status="active")
        db.add_all([incident, lage])
        db.flush()
        message = Message(incident_id=incident.id, title="Testmeldung")
        site = IncidentSite(org_id=org_id, major_incident_id=lage.id, bezeichnung="Stelle")
        db.add_all([message, site])
        db.commit()
        ausgefuehrt = service.AusfuehrungsErgebnis(incident.id, message.id, lage.id, site.id)
        generiert = service.GeneriertErgebnis(lage.id, site.id, 1)
        message_event = {"type": "message_created", "column_id": message.column_id, "vehicle_uid": None}
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    events = []

    async def senden(key, payload):
        assert asyncio.get_running_loop() is loop
        events.append((key, payload))

    def push(*args):
        assert threading.get_ident() != loop_thread

    monkeypatch.setattr(broadcast.manager, "broadcast", senden)
    monkeypatch.setattr(gsl_live_notify, "_dispatch_gsl_push", push)
    with db_threads(monkeypatch):
        await service.nach_ausfuehrung_benachrichtigen(org_id, 17, ausgefuehrt)
        await service.nach_generierung_benachrichtigen(org_id, 17, [generiert])
    assert events[0] == (ausgefuehrt.incident_id, message_event)
    assert [payload["type"] for _, payload in events] == [
        "message_created", "funkjournal:changed", "site:card_changed", "planspiel_changed",
        "site_created", "gsl_live", "planspiel_changed",
    ]


@pytest.mark.parametrize("fehler", [False, True])
async def test_resend_http_auf_loop_zuordnung_und_commit_im_thread(monkeypatch, org_id, fehler):
    import httpx

    from app.models.org_mail import OrgMailEingang
    from app.services import mail_zuordnung_service, print_dispatcher, resend_inbound_service as service

    with session() as db:
        row = OrgMailEingang(org_id=org_id, quelle="resend", extern_id="thread-mail", absender="alt@example.invalid",
                            empfaenger="[]", betreff="Alt", empfangen_at=datetime(2026, 11, 6))
        db.add(row)
        db.commit()
        ident = row.id
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    aufrufe = []

    async def request(client, url, **kwargs):
        assert asyncio.get_running_loop() is loop
        assert url == service.RESEND_RECEIVING_URL + "thread-mail"
        assert kwargs["headers"]["Authorization"] == "Bearer test-key"
        aufrufe.append("http")
        return httpx.Response(500 if fehler else 200, json={"subject": "Neu", "from": "neu@example.invalid",
                              "to": ["ecp@example.invalid"], "text": "Inhalt"})

    async def zuordnen(db, message_id):
        assert threading.get_ident() != loop_thread
        assert db.get(OrgMailEingang, message_id).text_body == "Inhalt"
        aufrufe.append("zuordnung")

    async def drucken(message_id):
        assert asyncio.get_running_loop() is loop
        with session() as db:
            assert db.get(OrgMailEingang, message_id).betreff == "Neu"
        aufrufe.append("druck")

    monkeypatch.setattr(service, "resend_api_key_fuer_org", lambda *args: "test-key")
    monkeypatch.setattr(httpx.AsyncClient, "get", request)
    monkeypatch.setattr(mail_zuordnung_service, "ordne_zu", zuordnen)
    monkeypatch.setattr(print_dispatcher, "autoprint_mail_eingang_background", drucken)
    with db_threads(monkeypatch):
        await service.fetch_inbound_background(org_id, ident)
    assert aufrufe == (["http"] if fehler else ["http", "zuordnung", "druck"])
    with session() as db:
        row = db.get(OrgMailEingang, ident)
        assert row.status == ("abruf_fehler" if fehler else "neu")
        assert row.betreff == ("Alt" if fehler else "Neu")


async def test_sms_inbox_regel_und_ergebnis_thread_transport_loop(monkeypatch, org_id):
    from app.models.sms import SmsForwardRule, SmsInbox
    from app.services import sms_dispatch_service, sms_inbox_service as service, teams_service

    with session() as db:
        db.query(OrgSettings).filter_by(org_id=org_id).one().sms_receive_enabled = True
        rule = SmsForwardRule(org_id=org_id, name="Weiterleitung", enabled=True, match_type="prefix",
                              match_number="+4366", forward_teams=True, teams_webhook_url="https://teams.invalid/sms",
                              forward_adhoc_numbers="+43664123,+43664123,+43664567", prepend_sender=True)
        row = SmsInbox(org_id=org_id, from_number="+43660111", text="Testtext")
        db.add_all([rule, row])
        db.commit()
        ident, rule_id = row.id, rule.id
    loop = asyncio.get_running_loop()
    gesendet = []

    async def teams(url, titel, text):
        assert asyncio.get_running_loop() is loop and not offen
        assert (url, titel, text) == ("https://teams.invalid/sms", "SMS von +43660111", "Testtext")
        gesendet.append("teams")
        return True

    async def sms(org, jobs):
        assert asyncio.get_running_loop() is loop and not offen
        assert org == org_id
        assert jobs == [("+43664123", "SMS von +43660111: Testtext"), ("+43664567", "SMS von +43660111: Testtext")]
        gesendet.append("sms")
        return 2, 1

    monkeypatch.setattr(teams_service, "post_teams_karte", teams)
    monkeypatch.setattr(sms_dispatch_service, "send_bulk", sms)
    with db_threads(monkeypatch) as offen:
        await service.process_inbound_sms(ident)
    assert gesendet == ["teams", "sms"]
    with session() as db:
        row = db.get(SmsInbox, ident)
        assert (row.processed, row.matched_rule_id, row.forward_summary) == (True, rule_id, "Teams ✓, 1/2 SMS")


async def test_teams_gsl_payload_und_loop(monkeypatch, org_id):
    from app.models.teams_bot import TeamsAlarmConfig
    from app.services import teams_alarm_service as service
    from app.services.teams_card import build_gsl_alarm_card

    with session() as db:
        cfg = db.query(OrgSettings).filter_by(org_id=org_id).one()
        cfg.gsl_alarm_enabled, cfg.gsl_alarm_text = True, "Alarm {lage}"
        db.add(TeamsAlarmConfig(org_id=org_id, enabled=True, webhook_url_alarm="https://teams.invalid/gsl"))
        db.commit()
    loop = asyncio.get_running_loop()
    gesendet = []

    async def post(url, payload, **kwargs):
        assert asyncio.get_running_loop() is loop and not offen
        assert url == "https://teams.invalid/gsl" and kwargs == {"log_label": "Lage 23"}
        assert payload == build_gsl_alarm_card(23, "Hochwasser", "Alarm Hochwasser",
                                              is_exercise=False, base_url="https://ecp.invalid")
        gesendet.append(1)
        return True, False, None, False

    monkeypatch.setattr(service, "_post_payload", post)
    with db_threads(monkeypatch) as offen:
        await service.post_gsl_alarm_card(org_id, 23, "Hochwasser", False, base_url="https://ecp.invalid")
    assert gesendet == [1]


@pytest.mark.parametrize("erfolg", [True, False, "exception", "text_exception"])
async def test_material_sms_text_status_und_chronik(monkeypatch, erfolg):
    from app.models.major_incident import SiteLogEntry
    from app.models.verleih import VerleihAusleihe
    from app.services import sms_service, verleih_service as service
    from tests.test_verleih_material_service import _context

    db, lage, site = _context(f"thread-{uuid4().hex[:8]}")
    try:
        db.add(OrgSettings(org_id=lage.org_id, gsl_verleih_sms_ausleih_text="{stelle}: {name}"))
        db.commit()
        ausleihe = service.material_an_einsatzstelle(
            db, lage=lage, site=site, positionen=[{"bezeichnung": "Pumpe"}], name="Mia", telefon="0664123",
            adresse=None, notizen=None, einheit=None, quelle="board", user_id=None, author_name=None, pin="123456",
        )
        ident, org_id, site_id = ausleihe.id, lage.org_id, site.id
    finally:
        db.close()
    loop = asyncio.get_running_loop()
    aufrufe = []

    async def sms(org, phone, text):
        assert asyncio.get_running_loop() is loop and not offen
        assert (org, phone, text) == (org_id, "0664123", "Keller Musterhaus: Mia PIN: 123456")
        aufrufe.append(1)
        if erfolg == "exception":
            raise RuntimeError("SMS-Ausfall")
        return erfolg

    monkeypatch.setattr(sms_service, "send_sms", sms)
    if erfolg == "text_exception":
        def text_fehler(*args):
            raise RuntimeError("Text nicht lesbar")
        monkeypatch.setattr(service, "get_sms_ausleih_text", text_fehler)
    with db_threads(monkeypatch) as offen:
        assert await service.sende_material_sms(ident, org_id, True) is (erfolg is True)
    assert aufrufe == ([] if erfolg == "text_exception" else [1])
    with session() as db:
        assert db.get(VerleihAusleihe, ident).sms_ausleih_gesendet is (erfolg is True)
        log = db.query(SiteLogEntry).filter_by(incident_site_id=site_id).order_by(SiteLogEntry.id.desc()).first()
        assert log.text == ("SMS an Empfänger gesendet" if erfolg is True else "SMS an Empfänger fehlgeschlagen")


@pytest.mark.parametrize("vision", [False, True])
async def test_ai_konfiguration_im_thread_provider_ohne_session(monkeypatch, org_id, vision):
    import anthropic

    from app.config import settings
    from app.services import ai_service as service

    loop = asyncio.get_running_loop()
    aufrufe = []

    async def create(**kwargs):
        assert asyncio.get_running_loop() is loop and not offen
        assert kwargs["system"] == "System"
        aufrufe.append(1)
        return SimpleNamespace(content=[SimpleNamespace(text="Antwort")],
                               usage=SimpleNamespace(input_tokens=2, output_tokens=3))

    monkeypatch.setattr(settings, "AI_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kwargs: SimpleNamespace(
        messages=SimpleNamespace(create=create)))
    with db_threads(monkeypatch) as offen:
        if vision:
            assert await service.complete_vision("System", "Frage", [b"png"], feature="thread", org_id=org_id) == "Antwort"
        else:
            assert await service.complete("System", "Frage", feature="thread", org_id=org_id) == "Antwort"
        # Die Token-Verbuchung wird wie bisher separat gestartet; vor Patch-Ende abwarten.
        token_tasks = [task for task in asyncio.all_tasks() if task.get_coro().__name__ == "_record_token_usage"]
        await asyncio.gather(*token_tasks)
    assert aufrufe == [1]
    with session() as db:
        assert db.query(OrgSettings).filter_by(org_id=org_id).one().ai_tokens_used_month == 5
