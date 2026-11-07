"""ELVis bleibt sichtbar, aber alarmiert auch bei langer Stille niemals."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.dienst_monitor import DienstStatus
from app.models.gateway import AlarmIngest, Gateway
from app.models.master import FireDept, OrgSettings
from app.services.dienst_monitor_service import dienst_zustand, entscheide, pruefe_dienste
from app.services.system_diagnose import service_summary


@pytest.fixture
def elvis_monitor():
    with SessionLocal() as db:
        set_tenant_context(db, None)
        orgs = [FireDept(slug=uuid4().hex, name="ELVis-Monitor", timezone="Europe/Vienna") for _ in range(2)]
        db.add_all(orgs)
        db.flush()
        agents = [Gateway(org_id=org.id, name="ELVis-PC", kind="windows_agent", device_token_hash=uuid4().hex,
                          offline_alert_enabled=False, wut_config={"elvis": {"mode": "protokollieren"}})
                  for org in orgs]
        db.add_all(agents)
        db.add_all([OrgSettings(org_id=org.id) for org in orgs])
        db.commit()
        try:
            yield db, orgs, agents
        finally:
            set_tenant_context(db, None)
            for model in (AlarmIngest, DienstStatus, Gateway, OrgSettings):
                for row in db.query(model).filter(model.org_id.in_([org.id for org in orgs])).all():
                    db.delete(row)
                db.flush()
            for org in orgs:
                db.delete(org)
            db.commit()


def elvis_check(db, org, now=None):
    return next(c for c in pruefe_dienste(db, org.id, now) if c.key == "elvis_weiterleitung")


def event(db, org, agent, now, *, test=False, source="elvis"):
    db.add(AlarmIngest(org_id=org.id, gateway_id=agent.id, raw_hash=uuid4().hex, raw_text="ELVis",
                       received_at=now, source=source, parsed={"elvis_test": test}))
    db.commit()


@pytest.mark.parametrize("test", [False, True])
@pytest.mark.parametrize("mode", ["protokollieren", "alarmquelle"])
def test_event_zeit_modus_und_lange_stille(elvis_monitor, test, mode):
    db, (org, _), (agent, _) = elvis_monitor
    agent.wut_config = {"elvis": {"mode": mode}}
    now = datetime(2026, 11, 7, 12, tzinfo=UTC).replace(tzinfo=None)
    event(db, org, agent, now - timedelta(days=90), test=not test)
    event(db, org, agent, now, test=test)
    check = elvis_check(db, org, now + timedelta(days=180))
    assert check.state == "ok" and check.relevant and check.alert is False
    assert mode in check.detail
    assert f"07.11.2026 13:00 ({'Test' if test else 'echt'})" in check.detail
    assert dienst_zustand(check, None, 5, now) == "ok"
    row = DienstStatus(org_id=org.id, key=check.key, state="unknown", fail_cycles=0, ok_cycles=0)
    for days in (0, 180, 365):
        assert entscheide(check, row, 0, 1, now + timedelta(days=days)).art is None
    assert row.down_since is None and row.fail_cycles == 0


def test_ohne_event_unknown_und_org_isolation(elvis_monitor):
    db, (org_a, org_b), (agent_a, agent_b) = elvis_monitor
    now = datetime.now(UTC).replace(tzinfo=None)
    event(db, org_b, agent_b, now)
    event(db, org_a, agent_a, now, source="wut")
    # Selbst ein fremdes Event mit der Agent-ID von A darf nicht sichtbar werden.
    event(db, org_b, agent_a, now)
    set_tenant_context(db, org_b.id)
    check = elvis_check(db, org_a, now + timedelta(days=365))
    assert check.state == "unknown" and check.relevant and not check.alert
    assert "noch keines" in check.detail
    assert dienst_zustand(check, None, 5, now) == "unknown"
    row = DienstStatus(org_id=org_a.id, key=check.key, state="unknown", fail_cycles=0)
    assert entscheide(check, row, 0, 1, now).art is None
    assert row.down_since is None
    assert elvis_check(db, org_b).state == "ok"
    set_tenant_context(db, None)


@pytest.mark.parametrize("grund", ["ohne_agent", "aus", "ungekoppelt", "linux"])
def test_nicht_konfiguriert(elvis_monitor, grund):
    db, (org, _), (agent, _) = elvis_monitor
    if grund == "ohne_agent":
        db.delete(agent)
    elif grund == "aus":
        agent.wut_config = {"elvis": {"mode": "aus"}}
    elif grund == "ungekoppelt":
        agent.device_token_hash = None
    else:
        agent.kind = "linux"
    db.commit()
    check = elvis_check(db, org)
    assert check.state == "nicht_konfiguriert" and not check.relevant and not check.alert


def test_schalter_und_admin_mcp_liste(elvis_monitor):
    db, (org, _), _ = elvis_monitor
    settings = db.query(OrgSettings).filter_by(org_id=org.id).one()
    settings.dienst_monitor_ausgeschaltet = "elvis_weiterleitung"
    db.commit()
    check = elvis_check(db, org)
    assert (check.state, check.relevant, check.alert, check.teile) == ("nicht_konfiguriert", False, False, ())
    assert check.detail == "Ueberwachung ausgeschaltet."
    assert service_summary(db)["evidence"]["dienste"][check.key]["ausgeschaltet"] == 1
    settings.dienst_monitor_ausgeschaltet = ""
    db.commit()
    assert elvis_check(db, org).state == "unknown"
