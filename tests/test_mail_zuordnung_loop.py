"""Regressionen fuer den Rueckblick-Worker des Resend-Posteingangs."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Message, MessageMedia
from app.models.master import OrgSettings
from app.models.org_mail import OrgMailEingang, OrgResendConfig
from app.services import mail_zuordnung_loop as loop
from app.services.resend_inbound_service import loesche_alte_eingaenge
from tests.test_mail_zuordnung_integration import (
    _einsatz,
    _lauf,
    _mail,
    _org,
    _settings,
    _zustand,
)
from tests.test_mail_zuordnung_integration import (
    anhaenge as _integration_anhaenge,
)


@pytest.fixture
def anhaenge(monkeypatch, tmp_path):
    """Die Mail→Meldung-Anhangsfixture auch fuer die Retention wiederverwenden."""
    return _integration_anhaenge.__wrapped__(monkeypatch, tmp_path)


@pytest.mark.asyncio
async def test_waiting_mail_is_assigned_after_incident_starts_within_lookback(setup_db):
    org_id, _ = _org()
    _settings(org_id, rueckblick=30)
    mail_id = _mail(org_id)
    assert await _lauf(mail_id) is False
    incident_id = _einsatz(org_id)
    assert await loop.run_mail_zuordnung_once() == 1
    status, assignment, assigned_incident, message_id, _ = _zustand(mail_id)
    assert (status, assignment, assigned_incident) == ("zugeordnet", "auto", incident_id)
    assert message_id is not None


@pytest.mark.asyncio
async def test_waiting_mail_with_leitstellennummer_is_assigned_after_incident_starts(setup_db):
    org_id, _ = _org()
    _settings(org_id, rueckblick=30)
    mail_id = _mail(org_id, betreff="f26009038 T1 WOLFURT")
    assert await _lauf(mail_id) is False
    incident_id = _einsatz(org_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(OrgMailEingang, mail_id)
        from app.models.incident import Incident
        db.get(Incident, incident_id).lis_operation_number = "F26009038"
        db.commit()
    finally:
        db.close()
    assert await loop.run_mail_zuordnung_once() == 1
    assert _zustand(mail_id)[:3] == ("zugeordnet", "auto", incident_id)


@pytest.mark.asyncio
async def test_outside_lookback_moves_mail_to_posteingang(setup_db):
    org_id, _ = _org()
    _settings(org_id, rueckblick=30)
    mail_id = _mail(org_id)
    assert await _lauf(mail_id) is False
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(OrgMailEingang, mail_id).empfangen_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=31)
        db.commit()
    finally:
        db.close()
    _einsatz(org_id)
    assert await loop.run_mail_zuordnung_once() == 0
    assert _zustand(mail_id)[:2] == ("posteingang", "wartend")
    assert _zustand(mail_id)[4] == "Rueckblickfenster abgelaufen"


@pytest.mark.asyncio
async def test_exercise_and_other_org_are_never_used(setup_db):
    org_a, _ = _org()
    org_b, _ = _org()
    _settings(org_a)
    mail_id = _mail(org_a)
    await _lauf(mail_id)
    _einsatz(org_a, exercise=True)
    _einsatz(org_b)
    assert await loop.run_mail_zuordnung_once() == 0
    assert _zustand(mail_id)[:4] == ("wartend", "wartend", None, None)


@pytest.mark.asyncio
async def test_two_parallel_runs_do_not_create_a_second_message(setup_db):
    org_id, _ = _org()
    _settings(org_id)
    mail_id = _mail(org_id)
    await _lauf(mail_id)
    incident_id = _einsatz(org_id)
    await asyncio.gather(loop.run_mail_zuordnung_once(), loop.run_mail_zuordnung_once())
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(Message).filter(Message.incident_id == incident_id).count() == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_disabled_mode_is_not_claimed(setup_db):
    org_id, _ = _org()
    _settings(org_id)
    mail_id = _mail(org_id)
    await _lauf(mail_id)
    _settings(org_id, modus="aus")
    _einsatz(org_id)
    assert await loop.run_mail_zuordnung_once() == 0
    assert _zustand(mail_id)[:2] == ("wartend", "wartend")


@pytest.mark.asyncio
async def test_error_for_one_mail_does_not_stop_batch(setup_db, monkeypatch):
    org_id, _ = _org()
    _settings(org_id)
    first, second = _mail(org_id, email_id="a"), _mail(org_id, email_id="b")
    await _lauf(first)
    await _lauf(second)
    incident_id = _einsatz(org_id)
    original = loop.ordne_zu

    async def broken_once(db, row_id, **kwargs):
        if row_id == first:
            raise RuntimeError("simulated")
        return await original(db, row_id, **kwargs)

    monkeypatch.setattr(loop, "ordne_zu", broken_once)
    assert await loop.run_mail_zuordnung_once() == 1
    assert _zustand(first)[:2] == ("wartend", "wartend")
    assert _zustand(second)[2] == incident_id


@pytest.mark.asyncio
async def test_retention_deletes_inbox_row_but_keeps_assigned_message(setup_db, anhaenge):
    org_id, _ = _org()
    _settings(org_id)
    incident_id = _einsatz(org_id)
    mail_id = _mail(org_id, anhaenge=[("a_png", "bild.png")])
    assert await _lauf(mail_id)
    _, _, _, message_id, _ = _zustand(mail_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cfg = db.query(OrgResendConfig).filter_by(org_id=org_id).first()
        if cfg is None:
            cfg = OrgResendConfig(org_id=org_id, inbound_retention_days=1)
            db.add(cfg)
        else:
            cfg.inbound_retention_days = 1
        db.query(OrgSettings).filter_by(org_id=org_id).one().mail_eingang_aufbewahrung_tage = 1
        db.get(OrgMailEingang, mail_id).empfangen_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
        db.commit()
        assert loesche_alte_eingaenge(db) == 1
        assert db.get(OrgMailEingang, mail_id) is None
        assert db.get(Message, message_id).incident_id == incident_id
        assert db.query(MessageMedia).filter(MessageMedia.message_id == message_id).count() == 1
    finally:
        db.close()


def test_main_registers_mail_zuordnung_loop():
    source = (Path(__file__).parents[1] / "app/main.py").read_text()
    assert "from app.services.mail_zuordnung_loop import mail_zuordnung_loop" in source
    assert "mail_zuordnung_loop()," in source
