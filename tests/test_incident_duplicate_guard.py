"""Duplikat-Sperre bei fast zeitgleicher Einsatzanlage mit gleichem Stichwort
(app/services/incident_service.py::create_incident(), Parameter
reject_near_duplicates). Ausnahme: Stichworte mit
AlarmType.triggers_major_incident=True (z. B. T9) — siehe Modul-Docstring dort.

Jeder Test bekommt eine eigene, frische Org (statt der geteilten Home-Org) -
sonst wuerden aktive Einsaetze anderer Tests/Testdateien mit gleichem
Stichwort auf derselben Org faelschlich als Duplikat-Kandidat gefunden
(dieselbe Klasse Cross-Test-Kollision wie bei anderen Testdateien in diesem
Projekt, siehe z. B. tests/test_dibos_enrich.py-Kommentar dort).
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.tenant import set_tenant_context
from app.models.incident import Incident
from app.models.master import AlarmType, FireDept
from app.services.incident_service import create_incident
from tests.conftest import TestingSession


@pytest.fixture
def org_id():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = FireDept(
            slug=f"dup-guard-{uuid.uuid4().hex[:8]}", name="Duplikat-Test-Org",
            color="#123456", bos="Feuerwehr",
        )
        db.add(org)
        db.flush()
        db.add_all([
            AlarmType(org_id=org.id, code="T2", category="T", label="Technisch Mittel",
                      triggers_major_incident=False),
            AlarmType(org_id=org.id, code="T9", category="T", label="Großschadenslage",
                      triggers_major_incident=True),
        ])
        db.commit()
        return org.id
    finally:
        db.close()


def _session(org_id):
    db = TestingSession()
    set_tenant_context(db, org_id)
    return db


def test_zweiter_aufruf_binnen_fenster_gibt_ersten_incident_zurueck(org_id):
    db = _session(org_id)
    try:
        erster, war_neu_1 = create_incident(
            db, "T2", primary_org_id=org_id, reject_near_duplicates=True,
        )
        db.commit()
        assert war_neu_1 is True

        zweiter, war_neu_2 = create_incident(
            db, "T2", primary_org_id=org_id, reject_near_duplicates=True,
        )
        db.commit()

        assert war_neu_2 is False
        assert zweiter.id == erster.id
        anzahl = db.query(Incident).filter(
            Incident.primary_org_id == org_id, Incident.alarm_type_code == "T2",
        ).count()
        assert anzahl == 1
    finally:
        db.close()


def test_t9_ausnahme_beide_werden_angelegt(org_id):
    """Regressionstest: Sturmlagen koennen mehrere echte T9-Einsaetze binnen
    Minuten erzeugen - die duerfen nicht faelschlich zusammengelegt werden."""
    db = _session(org_id)
    try:
        erster, _ = create_incident(
            db, "T9", primary_org_id=org_id, reject_near_duplicates=True,
        )
        db.commit()

        zweiter, war_neu = create_incident(
            db, "T9", primary_org_id=org_id, reject_near_duplicates=True,
        )
        db.commit()

        assert war_neu is True
        assert zweiter.id != erster.id
        anzahl = db.query(Incident).filter(
            Incident.primary_org_id == org_id, Incident.alarm_type_code == "T9",
        ).count()
        assert anzahl == 2
    finally:
        db.close()


def test_ausserhalb_des_fensters_wird_nicht_blockiert(org_id):
    db = _session(org_id)
    try:
        erster, _ = create_incident(
            db, "T2", primary_org_id=org_id, reject_near_duplicates=True,
        )
        db.commit()
        # Zeitlich weit zurueckdatieren, um "ausserhalb des 90s-Fensters" zu simulieren.
        erster.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)
        db.commit()

        zweiter, war_neu = create_incident(
            db, "T2", primary_org_id=org_id, reject_near_duplicates=True,
        )
        db.commit()

        assert war_neu is True
        assert zweiter.id != erster.id
    finally:
        db.close()


def test_reject_near_duplicates_false_blockiert_nie(org_id):
    """Muster lis_sync.py/serial_alarm_service.py: die dortige, bereits sorgfaeltige
    Pruefung darf von dieser groeberen Sperre nicht ueberstimmt werden."""
    db = _session(org_id)
    try:
        erster, _ = create_incident(db, "T2", primary_org_id=org_id)
        db.commit()

        zweiter, war_neu = create_incident(db, "T2", primary_org_id=org_id)
        db.commit()

        assert war_neu is True
        assert zweiter.id != erster.id
    finally:
        db.close()


def test_globaler_kill_switch_deaktiviert_die_sperre(org_id, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "INCIDENT_DUPLICATE_GUARD_ENABLED", False)

    db = _session(org_id)
    try:
        erster, _ = create_incident(
            db, "T2", primary_org_id=org_id, reject_near_duplicates=True,
        )
        db.commit()

        zweiter, war_neu = create_incident(
            db, "T2", primary_org_id=org_id, reject_near_duplicates=True,
        )
        db.commit()

        assert war_neu is True
        assert zweiter.id != erster.id
    finally:
        db.close()


def test_unterschiedliches_stichwort_blockiert_nicht(org_id):
    db = _session(org_id)
    try:
        erster, _ = create_incident(
            db, "T9", primary_org_id=org_id, reject_near_duplicates=True,
        )
        db.commit()

        zweiter, war_neu = create_incident(
            db, "T2", primary_org_id=org_id, reject_near_duplicates=True,
        )
        db.commit()

        assert war_neu is True
        assert zweiter.id != erster.id
    finally:
        db.close()


def test_geschlossener_incident_blockiert_nicht(org_id):
    """Nur AKTIVE Einsaetze zaehlen als Duplikat-Kandidat - ein bereits
    geschlossener Einsatz mit gleichem Stichwort darf keinen neuen blockieren."""
    db = _session(org_id)
    try:
        erster, _ = create_incident(
            db, "T2", primary_org_id=org_id, reject_near_duplicates=True,
        )
        erster.status = "closed"
        db.commit()

        zweiter, war_neu = create_incident(
            db, "T2", primary_org_id=org_id, reject_near_duplicates=True,
        )
        db.commit()

        assert war_neu is True
        assert zweiter.id != erster.id
    finally:
        db.close()
