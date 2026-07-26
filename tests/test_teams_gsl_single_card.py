"""Tests für die Teams-Alarmkarte bei laufender Großschadenslage (Org-Opt-in
TeamsAlarmConfig.suppress_card_in_major_incident, siehe
teams_alarm_service.py::post_incident_card()).

Bei einer laufenden Lage soll — sofern aktiviert — nur EINE Karte für die
gesamte Lage verschickt werden, nicht eine je zugeordnetem Einsatz
(IncidentSite.major_incident_id). Muster/Konventionen: test_teams_alarm.py
(geteilte Home-Org ORG_ID=1, alles in einer einzigen, nie committeten
Transaktion je Test — rollback() am Ende räumt zuverlässig auf, ohne die
session-scoped Test-DB dauerhaft zu verändern)."""
from datetime import UTC, datetime

from app.core.tenant import set_tenant_context
from app.models.incident import Incident
from app.models.teams_bot import TeamsAlarmConfig, TeamsCardPost
from app.services import teams_alarm_service
from app.services.major_incident_service import create_lage, create_site
from tests.conftest import TestingSession

ORG_ID = 1


def _session():
    db = TestingSession()
    set_tenant_context(db, ORG_ID)
    return db


def _incident(db, **overrides) -> Incident:
    defaults = dict(
        primary_org_id=ORG_ID, alarm_type_code="T4", status="active",
        reason="Verkehrsunfall", address_street="Bundesstraße", address_no="1",
        address_city="Wolfurt", started_at=datetime(2026, 7, 26, 10, 0, tzinfo=UTC),
        lat=47.47, lng=9.73, alarm_token="tok_abc123",
    )
    defaults.update(overrides)
    incident = Incident(**defaults)
    db.add(incident)
    db.flush()
    return incident


def _cfg(**overrides) -> TeamsAlarmConfig:
    defaults = dict(
        org_id=ORG_ID, enabled=True, send_exercise=False,
        suppress_card_in_major_incident=False,
        include_map=True, include_gmaps_link=True, include_qr_link=True,
        include_board_link=True, webhook_url_alarm="https://outlook.office.com/webhook/alarm",
    )
    defaults.update(overrides)
    return TeamsAlarmConfig(**defaults)


def _fake_webhook(calls):
    async def _inner(webhook_url, incident, cfg, *, base_url, org):
        calls.append(incident.id)
        return True
    return _inner


def test_zweiter_einsatz_derselben_lage_wird_uebersprungen(monkeypatch):
    calls = []
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook", _fake_webhook(calls))

    db = _session()
    try:
        cfg = _cfg(suppress_card_in_major_incident=True)
        db.add(cfg)
        lage = create_lage(db, ORG_ID, "Sturmlage Test")
        einsatz_1 = _incident(db)
        einsatz_2 = _incident(db)
        create_site(db, lage, "Stelle 1", org_id=ORG_ID, incident_id=einsatz_1.id)
        create_site(db, lage, "Stelle 2", org_id=ORG_ID, incident_id=einsatz_2.id)
        db.flush()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, einsatz_1, base_url="https://example.com"))
        asyncio.run(teams_alarm_service.post_incident_card(db, einsatz_2, base_url="https://example.com"))

        assert calls == [einsatz_1.id]  # nur der erste Einsatz hat tatsächlich gesendet
        protokoll = db.query(TeamsCardPost).filter(
            TeamsCardPost.org_id == ORG_ID, TeamsCardPost.major_incident_id == lage.id,
        ).all()
        assert len(protokoll) == 1
        assert protokoll[0].incident_id == einsatz_1.id
    finally:
        db.rollback()
        db.close()


def test_ohne_suppress_flag_senden_beide_regressionstest(monkeypatch):
    """Default aus — unverändertes bisheriges Verhalten: jeder Einsatz sendet einzeln."""
    calls = []
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook", _fake_webhook(calls))

    db = _session()
    try:
        cfg = _cfg(suppress_card_in_major_incident=False)
        db.add(cfg)
        lage = create_lage(db, ORG_ID, "Sturmlage Test 2")
        einsatz_1 = _incident(db)
        einsatz_2 = _incident(db)
        create_site(db, lage, "Stelle 1", org_id=ORG_ID, incident_id=einsatz_1.id)
        create_site(db, lage, "Stelle 2", org_id=ORG_ID, incident_id=einsatz_2.id)
        db.flush()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, einsatz_1, base_url="https://example.com"))
        asyncio.run(teams_alarm_service.post_incident_card(db, einsatz_2, base_url="https://example.com"))

        assert calls == [einsatz_1.id, einsatz_2.id]
        assert db.query(TeamsCardPost).filter(TeamsCardPost.org_id == ORG_ID).count() == 0
    finally:
        db.rollback()
        db.close()


def test_einsatz_ohne_lage_sendet_normal_trotz_aktivem_schalter(monkeypatch):
    calls = []
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook", _fake_webhook(calls))

    db = _session()
    try:
        cfg = _cfg(suppress_card_in_major_incident=True)
        db.add(cfg)
        einsatz = _incident(db)  # keine IncidentSite-Zuordnung

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, einsatz, base_url="https://example.com"))

        assert calls == [einsatz.id]
    finally:
        db.rollback()
        db.close()


def test_zwei_verschiedene_lagen_senden_je_eine_karte(monkeypatch):
    """Die Sperre gilt pro Lage, nicht global — zwei unabhängige Lagen dürfen
    beide je eine Karte senden."""
    calls = []
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook", _fake_webhook(calls))

    db = _session()
    try:
        cfg = _cfg(suppress_card_in_major_incident=True)
        db.add(cfg)
        lage_a = create_lage(db, ORG_ID, "Lage A")
        lage_b = create_lage(db, ORG_ID, "Lage B")
        einsatz_a = _incident(db)
        einsatz_b = _incident(db)
        create_site(db, lage_a, "Stelle A", org_id=ORG_ID, incident_id=einsatz_a.id)
        create_site(db, lage_b, "Stelle B", org_id=ORG_ID, incident_id=einsatz_b.id)
        db.flush()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, einsatz_a, base_url="https://example.com"))
        asyncio.run(teams_alarm_service.post_incident_card(db, einsatz_b, base_url="https://example.com"))

        assert calls == [einsatz_a.id, einsatz_b.id]
        assert db.query(TeamsCardPost).filter(TeamsCardPost.org_id == ORG_ID).count() == 2
    finally:
        db.rollback()
        db.close()
