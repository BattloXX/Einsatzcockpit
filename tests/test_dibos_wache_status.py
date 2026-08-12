"""Tests fuer Mapping, Persistenz und Verlauf des DIBOS-Wachenstatus."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.core.audit import write_incident_change
from app.core.tenant import set_tenant_context
from app.models.incident import IncidentChange, IncidentWacheStatus
from app.models.master import FireDept
from app.services.dibos.dibos_enrich import _sync_wache_status
from app.services.dibos.dibos_mapping import map_wache_status
from app.services.incident_service import combined_verlauf, create_incident, set_wache_status
from tests.conftest import TestingSession

ORG_ID = 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("AL", "alarmiert"), ("UEB", "übernommen"), ("S2", "einsatzbereit"),
     ("S4", "ausgefahren"), ("S5", "am_einsatzort"), ("S3", None), ("S8", None)],
)
def test_map_wache_status(raw, expected):
    assert map_wache_status(raw) == expected


def _incident(db, event_number: str):
    incident, _ = create_incident(db, alarm_type_code="T1", primary_org_id=ORG_ID)
    incident.lis_operation_number = event_number
    db.flush()
    return incident


def _unit(unid: str, status: str = "AL") -> dict:
    return {
        "unitType": "wache", "eventNumber": "f-wache-test", "unid": unid,
        "unidRfl": f"Wache {unid}", "currentStatusText": status,
        "currentStatusTime": "2026-08-08T18:56:14",
    }


def test_sync_wache_status_mit_und_ohne_filter(setup_db):
    db = TestingSession()
    set_tenant_context(db, ORG_ID)
    org = db.get(FireDept, ORG_ID)
    incident = _incident(db, "f-wache-test")
    units = [_unit("fw_wolfu"), _unit("fw_andere", "S4")]

    assert _sync_wache_status(db, org, incident, "f-wache-test", units, "fw_wolfu")
    assert db.query(IncidentWacheStatus).filter_by(incident_id=incident.id).count() == 1
    assert _sync_wache_status(db, org, incident, "f-wache-test", units, None)
    assert db.query(IncidentWacheStatus).filter_by(incident_id=incident.id).count() == 2
    db.rollback()
    db.close()


def test_set_wache_status_noop_und_audit_timestamp(setup_db):
    db = TestingSession()
    set_tenant_context(db, ORG_ID)
    incident = _incident(db, "f-wache-noop")
    ts = datetime(2026, 8, 8, 16, 56, 14)
    assert set_wache_status(db, incident, "fw_wolfu", "alarmiert", status_at=ts) is not None
    assert set_wache_status(db, incident, "fw_wolfu", "alarmiert", status_at=ts) is None
    db.flush()
    changes = db.query(IncidentChange).filter_by(
        incident_id=incident.id, action="wache.status_set"
    ).all()
    assert len(changes) == 1
    assert changes[0].ts == ts

    override = datetime(2025, 1, 2, 3, 4, 5)
    write_incident_change(db, incident.id, "test.ts", "incident", incident.id, None, None, ts=override)
    db.flush()
    assert db.query(IncidentChange).filter_by(action="test.ts", incident_id=incident.id).one().ts == override
    db.rollback()
    db.close()


def test_echter_trace_wachenstatus_chronologisch(setup_db):
    trace_dir = Path("/tmp/fw_log_extract")
    if not trace_dir.is_dir():
        pytest.skip("Echter DIBOS-Trace /tmp/fw_log_extract nicht vorhanden")

    db = TestingSession()
    set_tenant_context(db, ORG_ID)
    org = db.get(FireDept, ORG_ID)
    incident = _incident(db, "f26006968")
    for path in sorted(trace_dir.glob("*GetCurrentUnits_response.json")):
        _sync_wache_status(
            db, org, incident, "f26006968", json.loads(path.read_text(encoding="utf-8")), "fw_wolfu"
        )
    db.flush()

    changes = db.query(IncidentChange).filter_by(
        incident_id=incident.id, action="wache.status_set"
    ).order_by(IncidentChange.ts).all()
    assert [(json.loads(c.after_json)["status"], c.ts) for c in changes] == [
        ("alarmiert", datetime(2026, 8, 8, 16, 56, 14)),
        ("übernommen", datetime(2026, 8, 8, 16, 57, 55)),
        ("einsatzbereit", datetime(2026, 8, 8, 18, 20, 17)),
    ]
    assert [e["summary"] for e in reversed(combined_verlauf(db, incident.id))] == [
        "FW Wolfurt: alarmiert", "FW Wolfurt: übernommen", "FW Wolfurt: einsatzbereit",
    ]
    db.rollback()
    db.close()
