"""Route-Tests fuer POST /gateway/rules/{id}/test."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.models.gateway import Gateway, PrintRule
from app.routers.ui_gateway import rule_test
from app.services.print_dispatcher import TestBezug as Bezug
from tests.conftest import TestingSession


@pytest.fixture
def db(setup_db):
    session = TestingSession()
    set_tenant_context(session, None)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _rule(db, *, trigger="einsatz_created", printers=True, paired=True):
    org = 970000 + uuid.uuid4().int % 20000
    gw = Gateway(
        org_id=org, name="Route-GW-" + uuid.uuid4().hex,
        device_token_hash=hash_api_key(uuid.uuid4().hex) if paired else None,
    )
    db.add(gw)
    db.flush()
    rule = PrintRule(
        org_id=org, name="Route-Regel-" + uuid.uuid4().hex, trigger=trigger,
        printer_ids=[999] if printers else [], documents=["einsatzinfo"],
    )
    db.add(rule)
    db.commit()
    return rule, SimpleNamespace(org_id=org)


async def test_rule_test_ohne_drucker(db):
    rule, user = _rule(db, printers=False)
    response = await rule_test(rule.id, MagicMock(), None, db, user, None)
    assert "test_err=printer" in response.headers["location"]


async def test_rule_test_ohne_gekoppeltes_gateway(db):
    rule, user = _rule(db, paired=False)
    response = await rule_test(rule.id, MagicMock(), None, db, user, None)
    assert "test_err=gateway" in response.headers["location"]


@pytest.mark.parametrize(
    ("trigger", "art"),
    [
        ("einsatz_created", "einsatz"),
        ("einsatz_updated", "einsatz"),
        ("manual_only", "einsatz"),
        ("gsl_created", "gsl"),
        ("gsl_lage_updated", "gsl"),
        ("verleih_created", "verleih"),
        ("alarm_serial_received", "alarm"),
    ],
)
async def test_rule_test_ohne_bezugsobjekt(db, trigger, art):
    rule, user = _rule(db, trigger=trigger)
    response = await rule_test(rule.id, MagicMock(), None, db, user, None)
    assert f"test_err={art}" in response.headers["location"]


async def test_rule_test_leeres_ergebnis(db, monkeypatch):
    rule, user = _rule(db)
    monkeypatch.setattr(
        "app.services.print_dispatcher.resolve_test_context",
        lambda db, rule: Bezug({"incident_id": 123}, "einsatz", 123),
    )
    monkeypatch.setattr("app.services.print_dispatcher.build_test_jobs", lambda db, rule, context: [])
    response = await rule_test(rule.id, MagicMock(), None, db, user, None)
    location = response.headers["location"]
    assert "test_err=leer" in location and "test_art=einsatz" in location and "test_ref=123" in location


@pytest.mark.parametrize("art", ["einsatz", "gsl", "verleih", "alarm"])
async def test_rule_test_erfolg_meldet_art_und_id(db, monkeypatch, art):
    rule, user = _rule(db)
    monkeypatch.setattr(
        "app.services.print_dispatcher.resolve_test_context",
        lambda db, rule: Bezug({"incident_id": 321}, art, 321),
    )
    job = SimpleNamespace(id=77)
    monkeypatch.setattr("app.services.print_dispatcher.build_test_jobs", lambda db, rule, context: [job])

    async def dispatch(db, job):
        return {"status": "sent"}

    monkeypatch.setattr("app.services.print_dispatcher.dispatch_job", dispatch)
    response = await rule_test(rule.id, MagicMock(), None, db, user, None)
    location = response.headers["location"]
    assert "test_ok=1" in location and f"test_art={art}" in location and "test_ref=321" in location
