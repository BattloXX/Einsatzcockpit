"""Einsatzbezogene ASUEW-Hinweise und ihre persistierten Zustellungen."""
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.models.breathing import BreathingTroop, BreathingWarnung
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings
from app.models.user import User
from app.services import breathing_service as svc
from tests.conftest import TestingSession


def troop(troop_id, status="im_einsatz", safety=False):
    return BreathingTroop(id=troop_id, status=status, is_sicherheitstrupp=safety)


@pytest.mark.parametrize("count", [0, 2, 3, 4])
def test_assp_threshold(count):
    troops = [troop(index + 1) for index in range(count)]
    troops.append(troop(100, "bereit", safety=True))
    expected = {"assp": troops[0]} if count >= 3 else {}
    assert svc.incident_hinweise(iter(troops)) == expected


@pytest.mark.parametrize("safety_status", [None, "bereit", "im_einsatz", "rueckzug", "zurueck", "erholt"])
def test_safety_troop_must_be_ready(safety_status):
    active = troop(5)
    troops = [active]
    if safety_status is not None:
        troops.append(troop(8, safety_status, safety=True))
    expected = {} if safety_status == "bereit" else {"kein_sicherheitstrupp": active}
    assert svc.incident_hinweise(troops) == expected


@pytest.mark.parametrize("status", ["im_einsatz", "rueckzug"])
def test_only_safety_troop_deployed_has_no_missing_safety_hint(status):
    assert svc.incident_hinweise([troop(1, status, safety=True)]) == {}


@pytest.mark.parametrize("status", ["bereit", "zurueck", "erholt"])
def test_inactive_troops_do_not_count(status):
    assert svc.incident_hinweise([troop(1, status)]) == {}
    active = troop(5)
    assert svc.incident_hinweise([
        troop(1, status), troop(2, status), active, troop(7), troop(9, "bereit", safety=True),
    ]) == {}


def test_anchor_is_lowest_deployed_id_including_withdrawing_safety_troop():
    anchor = troop(2, "rueckzug", safety=True)
    troops = [troop(8), troop(1, "zurueck"), troop(5), anchor, troop(0, "erholt")]
    assert svc.incident_hinweise(troops) == {"assp": anchor, "kein_sicherheitstrupp": anchor}
    for active in (anchor, troops[0], troops[2]):
        assert svc.check_troop_warnings(active) == []
    assert svc.incident_hinweise(reversed(troops)) == {
        "assp": anchor, "kein_sicherheitstrupp": anchor,
    }


@pytest.fixture
def ctx(monkeypatch):
    db = TestingSession()
    set_tenant_context(db, None)
    suffix = uuid4().hex
    org = FireDept(slug=f"hinweise-{suffix}", name="ASUEW Hinweise")
    db.add(org)
    db.flush()
    users = [User(username=f"hinweise-{suffix}-{index}", display_name=f"Empfaenger {index}",
                  org_id=org.id, active=True, phone=f"+43664000000{index}") for index in range(3)]
    db.add_all(users)
    db.flush()
    leader, escalation, supervisor = users
    incident = Incident(primary_org_id=org.id, incident_leader_user_id=leader.id,
                        status="active", is_exercise=False)
    db.add_all([incident, OrgSettings(org_id=org.id, asuew_eskalation_sms=True,
                                     asuew_eskalation_user_ids=str(escalation.id))])
    db.commit()
    enabled = svc.breathing_effective_enabled
    monkeypatch.setattr(svc, "breathing_effective_enabled",
                        lambda org_id, session: org_id == org.id and enabled(org_id, session))
    now = datetime.now(UTC).replace(tzinfo=None)
    monkeypatch.setattr(svc, "_now", lambda: now.replace(tzinfo=UTC))
    context = SimpleNamespace(db=db, incident=incident, leader=leader, escalation=escalation,
                              supervisor=supervisor, now=now)
    try:
        yield context
    finally:
        db.rollback()
        incident.status = "closed"
        for warning in db.query(BreathingWarnung).filter_by(incident_id=incident.id):
            for row in warning.zustellungen:
                if row.status in {"pending", "claimed"}:
                    row.status = "suppressed"
        db.commit()
        db.close()


def add_troop(c, status="im_einsatz", safety=False):
    row = BreathingTroop(incident_id=c.incident.id, name="Testtrupp", status=status,
                         is_sicherheitstrupp=safety, entry_at=c.now if status != "bereit" else None,
                         ueberwacher_user_id=c.supervisor.id)
    c.db.add(row)
    c.db.commit()
    return row


def warnings(c):
    c.db.expire_all()
    return c.db.query(BreathingWarnung).filter_by(incident_id=c.incident.id).order_by(
        BreathingWarnung.id,
    ).all()


def assert_pending_leadership_push(c, warning):
    assert len(warning.zustellungen) == 2
    assert {(row.user_id, row.kanal, row.stufe, row.status) for row in warning.zustellungen} == {
        (user.id, "push", 0, "pending") for user in (c.leader, c.escalation)
    }


def test_scan_assp_once_then_closes_and_suppresses_pending(ctx):
    c = ctx
    anchor = add_troop(c)
    add_troop(c, "rueckzug")
    returning = add_troop(c)
    add_troop(c, "bereit", safety=True)
    assert svc._collect_new_breathing_warnings(c.db) == [(c.incident.id, anchor.id, "assp")]
    warning, = warnings(c)
    assert warning.troop_id == anchor.id and warning.kind == "assp"
    assert_pending_leadership_push(c, warning)
    row_ids = [row.id for row in warning.zustellungen]
    with TestingSession() as second:
        set_tenant_context(second, None)
        assert svc._collect_new_breathing_warnings(second) == []
    warning, = warnings(c)
    assert [row.id for row in warning.zustellungen] == row_ids
    returning.status = "zurueck"
    c.db.commit()
    assert svc._collect_new_breathing_warnings(c.db) == []
    warning, = warnings(c)
    assert warning.erledigt_at == c.now
    assert all(row.status == "suppressed" for row in warning.zustellungen)


def test_scan_missing_safety_closes_when_safety_ready(ctx):
    c = ctx
    anchor = add_troop(c)
    safety = add_troop(c, safety=True)
    assert svc._collect_new_breathing_warnings(c.db) == [
        (c.incident.id, anchor.id, "kein_sicherheitstrupp"),
    ]
    warning, = warnings(c)
    assert_pending_leadership_push(c, warning)
    assert svc._collect_new_breathing_warnings(c.db) == []
    safety.status = "bereit"
    c.db.commit()
    assert svc._collect_new_breathing_warnings(c.db) == []
    warning, = warnings(c)
    assert warning.erledigt_at == c.now
    assert all(row.status == "suppressed" for row in warning.zustellungen)


def test_scan_anchor_change_closes_old_warning_and_creates_new_one(ctx):
    c = ctx
    anchor = add_troop(c)
    successor = add_troop(c)
    svc._collect_new_breathing_warnings(c.db)
    anchor.status = "zurueck"
    c.db.commit()
    assert svc._collect_new_breathing_warnings(c.db) == [
        (c.incident.id, successor.id, "kein_sicherheitstrupp"),
    ]
    old, new = warnings(c)
    assert old.troop_id == anchor.id and old.erledigt_at == c.now
    assert all(row.status == "suppressed" for row in old.zustellungen)
    assert new.troop_id == successor.id and new.erledigt_at is None
    assert_pending_leadership_push(c, new)
