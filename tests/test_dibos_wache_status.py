"""Tests fuer Mapping, Persistenz und Verlauf des DIBOS-Wachenstatus."""
from datetime import datetime

import pytest

from app.core.audit import write_incident_change
from app.core.tenant import set_tenant_context
from app.models.incident import IncidentChange, IncidentWacheStatus
from app.models.master import FireDept
from app.services.dibos.dibos_enrich import _sync_wache_status
from app.services.dibos.dibos_mapping import map_wache_status
from app.services.incident_service import create_incident, set_wache_status
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


def test_set_wache_status_spiegelt_incident_felder(setup_db):
    """uebernommen/ausgefahren/am_einsatzort/einsatzbereit muessen live auf die
    gleichnamigen Incident-Felder durchschlagen, die sonst nur der Einsatzimporter
    befuellt (siehe einsatz_import_service.py); "alarmiert" bleibt bewusst
    unpsiegelt (started_at kommt bereits aus dem DIBOS-Event)."""
    db = TestingSession()
    set_tenant_context(db, ORG_ID)
    incident = _incident(db, "f-wache-felder")

    set_wache_status(db, incident, "fw_wolfu", "alarmiert", status_at=datetime(2026, 8, 8, 16, 56, 14))
    assert incident.taken_over_at is None
    assert incident.departed_at is None
    assert incident.on_scene_at is None
    assert incident.ready_again_at is None

    ts_ueb = datetime(2026, 8, 8, 16, 57, 55)
    set_wache_status(db, incident, "fw_wolfu", "übernommen", status_at=ts_ueb)
    assert incident.taken_over_at == ts_ueb

    ts_aus = datetime(2026, 8, 8, 17, 1, 0)
    set_wache_status(db, incident, "fw_wolfu", "ausgefahren", status_at=ts_aus)
    assert incident.departed_at == ts_aus

    ts_eo = datetime(2026, 8, 8, 17, 3, 0)
    set_wache_status(db, incident, "fw_wolfu", "am_einsatzort", status_at=ts_eo)
    assert incident.on_scene_at == ts_eo

    ts_bereit = datetime(2026, 8, 8, 18, 20, 17)
    set_wache_status(db, incident, "fw_wolfu", "einsatzbereit", status_at=ts_bereit)
    assert incident.ready_again_at == ts_bereit
    db.rollback()
    db.close()
