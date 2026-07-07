"""ECPG PR1: Feature-Flag, Pairing, Artifact-Signatur, Idempotenz, Job-Anlage,
serieller Ingest, printer_report, Tenant-Isolation."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.security import (
    hash_api_key,
    sign_artifact_token,
    unsign_artifact_token,
)
from app.core.tenant import set_tenant_context
from app.models.gateway import (
    GATEWAY_STATUS_OFFLINE,
    GATEWAY_STATUS_UNPAIRED,
    Gateway,
    PrintJob,
    Printer,
)
from app.services import gateway_service as gw_svc
from app.services import print_dispatcher as disp


@pytest.fixture
def db(setup_db):
    eng = create_engine("sqlite:///./test.db", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=eng)
    s = Session()
    set_tenant_context(s, None)
    try:
        yield s
    finally:
        s.rollback()
        s.close()


_ORG_A = 991001
_ORG_B = 991002


# ── Feature-Flag ────────────────────────────────────────────────────────────────

class _Sys:
    def __init__(self, value=None):
        self.key = "gateway_module_enabled"
        self.value = value


class _OrgS:
    def __init__(self, enabled=False):
        self.gateway_module_enabled = enabled


def _flag_db(sys_value, org_enabled):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _Sys(sys_value)
    db.query.return_value.filter.return_value.execution_options.return_value.first.return_value = _OrgS(org_enabled)
    return db


def test_system_flag_missing_false():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    assert gw_svc.gateway_system_enabled(db) is False


def test_effective_false_when_no_org():
    assert gw_svc.gateway_effective_enabled(None, MagicMock()) is False


def test_effective_false_when_system_off():
    assert gw_svc.gateway_effective_enabled(1, _flag_db("false", True)) is False


def test_effective_false_when_org_off():
    assert gw_svc.gateway_effective_enabled(1, _flag_db("true", False)) is False


def test_effective_true_when_both_on():
    assert gw_svc.gateway_effective_enabled(1, _flag_db("true", True)) is True


# ── Artifact-Signatur ────────────────────────────────────────────────────────────

def test_artifact_token_roundtrip():
    tok = sign_artifact_token(55, 7)
    assert unsign_artifact_token(tok) == (55, 7)


def test_artifact_token_tampered_rejected():
    tok = sign_artifact_token(55, 7)
    assert unsign_artifact_token(tok + "x") is None


def test_artifact_token_garbage_rejected():
    assert unsign_artifact_token("not-a-token") is None


def test_verify_artifact_wrong_job_rejected():
    from app.services.print_artifact_service import verify_artifact_token
    tok = sign_artifact_token(55, 7)
    assert verify_artifact_token(99, tok) is None
    assert verify_artifact_token(55, tok) == 7


# ── Pairing ──────────────────────────────────────────────────────────────────────

def _make_gateway(db, org_id=_ORG_A, name="GW"):
    gw = Gateway(org_id=org_id, name=name)
    db.add(gw)
    db.flush()
    return gw


def test_pairing_success_sets_token_and_clears_code(db):
    gw = _make_gateway(db)
    code = gw_svc.erzeuge_pairing_code(db, gw)
    db.flush()
    assert gw.pairing_code_hash and gw.pairing_expires_at

    result = gw_svc.pair_gateway(db, code)
    assert result is not None
    paired, raw_token = result
    assert paired.id == gw.id
    assert paired.device_token_hash == hash_api_key(raw_token)
    assert paired.pairing_code_hash is None
    assert paired.status == GATEWAY_STATUS_OFFLINE


def test_pairing_wrong_code_fails(db):
    gw = _make_gateway(db)
    gw_svc.erzeuge_pairing_code(db, gw)
    db.flush()
    assert gw_svc.pair_gateway(db, "WRONGCOD") is None


def test_pairing_expired_code_fails(db):
    gw = _make_gateway(db)
    code = gw_svc.erzeuge_pairing_code(db, gw)
    gw.pairing_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    db.flush()
    assert gw_svc.pair_gateway(db, code) is None


def test_rotate_and_revoke(db):
    gw = _make_gateway(db)
    code = gw_svc.erzeuge_pairing_code(db, gw)
    _, first_token = gw_svc.pair_gateway(db, code)
    db.flush()
    new_token = gw_svc.rotate_token(db, gw)
    assert new_token != first_token
    assert gw.device_token_hash == hash_api_key(new_token)

    gw_svc.revoke_token(gw)
    assert gw.device_token_hash is None
    assert gw.status == GATEWAY_STATUS_UNPAIRED


# ── Idempotenz + Job-Anlage ──────────────────────────────────────────────────────

def test_idempotency_manual_always_unique():
    k1 = disp.build_idempotency_key(source="manual", rule_id=None, incident_id=1,
                                    gsl_id=None, objekt_id=None, document_type="einsatzinfo",
                                    artifact_ref=None, printer_id=2)
    k2 = disp.build_idempotency_key(source="manual", rule_id=None, incident_id=1,
                                    gsl_id=None, objekt_id=None, document_type="einsatzinfo",
                                    artifact_ref=None, printer_id=2)
    assert k1 != k2 and k1.startswith("manual:")


def test_idempotency_rule_deterministic():
    kw = dict(source="rule", rule_id=3, incident_id=1, gsl_id=None, objekt_id=None,
              document_type="einsatzinfo", artifact_ref=None, printer_id=2)
    assert disp.build_idempotency_key(**kw) == disp.build_idempotency_key(**kw)


def test_create_print_job_rule_dedup(db):
    gw = _make_gateway(db)
    job1, created1 = disp.create_print_job(
        db, org_id=_ORG_A, gateway_id=gw.id, printer_id=1, document_type="einsatzinfo",
        source="rule", rule_id=5, incident_id=100,
    )
    job2, created2 = disp.create_print_job(
        db, org_id=_ORG_A, gateway_id=gw.id, printer_id=1, document_type="einsatzinfo",
        source="rule", rule_id=5, incident_id=100,
    )
    assert created1 is True and created2 is False
    assert job1.id == job2.id


def test_create_print_job_manual_new_each_time(db):
    gw = _make_gateway(db)
    j1, c1 = disp.create_print_job(db, org_id=_ORG_A, gateway_id=gw.id, printer_id=1,
                                   document_type="einsatzinfo", source="manual", incident_id=100)
    j2, c2 = disp.create_print_job(db, org_id=_ORG_A, gateway_id=gw.id, printer_id=1,
                                   document_type="einsatzinfo", source="manual", incident_id=100)
    assert c1 and c2 and j1.id != j2.id


# ── Druckregel-Filter (on_event) ─────────────────────────────────────────────────

def test_filter_min_alarmstufe():
    rule = MagicMock()
    rule.filters = {"min_alarmstufe": 3}
    assert disp._filter_matches(rule, {"alarmstufe": 5}) is True
    assert disp._filter_matches(rule, {"alarmstufe": 1}) is False


def test_filter_stichwort():
    rule = MagicMock()
    rule.filters = {"stichwort": ["Brand"]}
    assert disp._filter_matches(rule, {"stichwort": "B3 Brand groß"}) is True
    assert disp._filter_matches(rule, {"stichwort": "T1 technisch"}) is False


def test_filter_zeitfenster_tag():
    """Fenster innerhalb eines Tages (08:00–18:00)."""
    rule = MagicMock()
    rule.filters = {"zeitfenster": {"von": "08:00", "bis": "18:00"}}
    assert disp._filter_matches(rule, {"now_hhmm": "12:00"}) is True
    assert disp._filter_matches(rule, {"now_hhmm": "06:00"}) is False
    # Ohne bekannte Uhrzeit greift das Fenster nicht (kein Ausschluss)
    assert disp._filter_matches(rule, {"now_hhmm": None}) is True


def test_filter_zeitfenster_ueber_mitternacht():
    """Fenster über Mitternacht (22:00–06:00)."""
    rule = MagicMock()
    rule.filters = {"zeitfenster": {"von": "22:00", "bis": "06:00"}}
    assert disp._filter_matches(rule, {"now_hhmm": "23:30"}) is True
    assert disp._filter_matches(rule, {"now_hhmm": "05:00"}) is True
    assert disp._filter_matches(rule, {"now_hhmm": "12:00"}) is False


def test_on_event_creates_jobs_and_dedups(db):
    """einsatz_created mit aktiver Regel → Job je Dokument×Drucker, idempotent."""
    from app.models.gateway import PrintRule
    from app.models.master import OrgSettings, SystemSettings

    org = 991500
    # Modul systemweit + org-seitig aktiv
    if not db.query(SystemSettings).filter(SystemSettings.key == "gateway_module_enabled").first():
        db.add(SystemSettings(key="gateway_module_enabled", value="true"))
    db.add(OrgSettings(org_id=org, gateway_module_enabled=True))
    gw = Gateway(org_id=org, name="GW", device_token_hash=hash_api_key("tok-" + str(org)))
    db.add(gw)
    db.flush()
    rule = PrintRule(org_id=org, name="Einsatzinfo bei Alarm", aktiv=True,
                     trigger="einsatz_created", documents=["einsatzinfo"], printer_ids=[gw.id])
    db.add(rule)
    db.flush()

    jobs1 = disp.on_event(db, org, "einsatz_created", {"incident_id": 555})
    assert len(jobs1) == 1
    jobs2 = disp.on_event(db, org, "einsatz_created", {"incident_id": 555})
    assert len(jobs2) == 0  # dedupliziert (gleicher Einsatz/Regel/Dokument/Drucker)


def test_on_event_empty_when_module_off(db):
    # Org ohne Flag → keine Jobs
    jobs = disp.on_event(db, 991600, "einsatz_created", {"incident_id": 1})
    assert jobs == []


# ── Serieller Ingest (Idempotenz via raw_hash) ───────────────────────────────────

def test_serial_ingest_idempotent(db, monkeypatch):
    import app.services.serial_alarm_service as sas
    # Einsatz-Anlage isolieren – wir testen nur die raw_hash-Idempotenz.
    monkeypatch.setattr(sas, "_create_or_link_incident", lambda *a, **k: (None, None))

    raw = "ALARM 1234\nB3 Brand\nWolfurt Kirchstrasse 1"
    ing1, created1 = sas.ingest_alarm(db, org_id=_ORG_A, gateway_id=1, raw_text=raw,
                                       charset="cp850", parsed=None, parse_status="parse_failed")
    ing2, created2 = sas.ingest_alarm(db, org_id=_ORG_A, gateway_id=1, raw_text=raw,
                                       charset="cp850", parsed=None, parse_status="parse_failed")
    assert created1 is True and created2 is False
    assert ing1.id == ing2.id


# ── printer_report ───────────────────────────────────────────────────────────────

def test_printer_report_creates_suggestion_and_updates(db):
    from app.services.printer_report_service import apply_printer_report
    gw = _make_gateway(db)
    db.commit()

    apply_printer_report(gw.id, _ORG_A, {"printers": [
        {"name": "Bürodrucker", "uri": "ipp://10.0.0.5/ipp/print",
         "identity": {"serial": "ABC123"}, "capabilities": {"duplex": True}},
    ]})
    p = db.query(Printer).filter(Printer.gateway_id == gw.id).first()
    assert p is not None and p.aktiv is False and p.uri.endswith("/ipp/print")

    # gleiche Identität, neue IP → Update statt Duplikat
    apply_printer_report(gw.id, _ORG_A, {"printers": [
        {"name": "Bürodrucker", "uri": "ipp://10.0.0.9/ipp/print",
         "identity": {"serial": "ABC123"}},
    ]})
    db.expire_all()
    printers = db.query(Printer).filter(Printer.gateway_id == gw.id).all()
    assert len(printers) == 1
    assert printers[0].uri == "ipp://10.0.0.9/ipp/print"


# ── Tenant-Isolation ─────────────────────────────────────────────────────────────

def test_tenant_isolation_gateway(setup_db):
    eng = create_engine("sqlite:///./test.db", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=eng)
    s = Session()
    set_tenant_context(s, None)
    gw_a = Gateway(org_id=_ORG_A, name="A-Gateway")
    gw_b = Gateway(org_id=_ORG_B, name="B-Gateway")
    s.add_all([gw_a, gw_b])
    s.commit()

    # Kontext Org B → sieht nur eigene Gateways
    set_tenant_context(s, _ORG_B)
    visible = s.query(Gateway).filter(Gateway.name.in_(["A-Gateway", "B-Gateway"])).all()
    names = {g.name for g in visible}
    s.close()
    assert "B-Gateway" in names
    assert "A-Gateway" not in names
