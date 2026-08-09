"""Tests für die DIBOS-Einsatzanlage (Org-Opt-in OrgDibosConfig.create_incidents,
siehe app/services/dibos/dibos_enrich.py::_get_or_create_incident_for_event()).

Analog zur LIS/IPR-Anbindung (lis_sync.py::_get_or_link_incident()), Matching
über die Leitstellennummer (eventNumber == Incident.lis_operation_number).

Eigene, frische Org je Test (statt der geteilten Home-Org): setup_db ist
session-scoped (siehe conftest.py) — die DB wird NICHT zwischen Tests
zurückgesetzt, und primary_org_id+lis_operation_number ist inzwischen ein
echter DB-Unique-Constraint (siehe models/incident.py) — dieselbe Begründung
wie in test_incident_duplicate_guard.py.
"""
import uuid
from datetime import UTC, datetime

import pytest

from app.core.tenant import set_tenant_context
from app.models.incident import Incident
from app.models.master import AlarmType, FireDept
from app.services.dibos import dibos_enrich
from app.services.incident_service import create_incident
from tests.conftest import TestingSession


@pytest.fixture
def org_id():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = FireDept(
            slug=f"dibos-create-{uuid.uuid4().hex[:8]}", name="DIBOS-Anlage-Test-Org",
            color="#abcdef", bos="Feuerwehr",
        )
        db.add(org)
        db.flush()
        # Ohne einen passenden AlarmType faellt create_incident() auf "T1" zurueck
        # (siehe app/services/incident_service.py::create_incident()) - fuer den
        # Adress-Match-Test (Tier 2) muessen beide Seiten tatsaechlich "T2" fuehren,
        # nicht durch den Fallback beide zufaellig auf "T1" zusammenlaufen.
        db.add(AlarmType(org_id=org.id, code="T2", category="T", label="Technisch Mittel"))
        db.commit()
        return org.id
    finally:
        db.close()


def _session(org_id):
    db = TestingSession()
    set_tenant_context(db, org_id)
    return db


def _event(
    event_number: str, *, tycod: str = "t2", closed: str | None = None,
    street: str = "Teststrasse", street_no: str = "5", city: str = "Wolfurt",
    lat: float = 47.5, lng: float = 9.7, created: str = "2026-07-26T10:00:00",
    comment: str = "Testmeldung",
) -> dict:
    """Baut ein rohes DIBOS-Event (Schema aus dibos_client.py::parse_events()'s
    Eingabeformat, VOR dem Parsen) mit den fürs Matching relevanten Feldern."""
    return {
        "eventNumber": event_number, "tycod": tycod, "tycodDescription": "Technischer Einsatz",
        "diagnose": "", "eventComment": comment, "bmaNo": None,
        "created": created, "dispatched": created, "closed": closed,
        "callerList": [], "targetList": [], "comments": [], "personResponseList": [],
        "locationStreet": street, "locationStreetNo": street_no, "locationCity": city,
        "locationLongitude": lng, "locationLatitude": lat,
    }


def test_event_ohne_passenden_incident_legt_neuen_an(org_id):
    event = _event("f-neu-001")
    result = dibos_enrich.enrich_events_for_org(org_id, [event], create_incidents=True)
    assert len(result["created_ids"]) == 1

    db = _session(org_id)
    try:
        incident = db.get(Incident, result["created_ids"][0])
        assert incident is not None
        assert incident.lis_operation_number == "f-neu-001"
        assert incident.lis_operation_id is None  # bewusst NICHT gesetzt (siehe Modul-Docstring)
        assert incident.alarm_type_code == "T2"
        assert incident.address_street == "Teststrasse"
        assert incident.status == "active"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_neuer_incident_loest_benachrichtigung_aus(org_id, monkeypatch):
    calls = []

    async def fake_notify(db, incident, **kwargs):
        calls.append((incident.id, kwargs["org_id"], kwargs["background_tasks"]))

    async def fake_broadcast(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.services.incident_notify.notify_incident_created", fake_notify,
    )
    monkeypatch.setattr("app.services.broadcast.broadcast_org", fake_broadcast)

    await dibos_enrich.enrich_and_broadcast(
        org_id, [_event("f-neu-notify-001")], create_incidents=True,
    )

    assert len(calls) == 1
    incident_id, notified_org_id, background_tasks = calls[0]
    assert incident_id > 0
    assert notified_org_id == org_id
    assert background_tasks is None


def test_zweiter_poll_gleiches_event_legt_nicht_zweimal_an(org_id):
    """Tier 1 (bereits per Einsatznummer aktiv verknüpft) greift beim zweiten Poll."""
    event = _event("f-neu-002")
    r1 = dibos_enrich.enrich_events_for_org(org_id, [event], create_incidents=True)
    assert len(r1["created_ids"]) == 1

    r2 = dibos_enrich.enrich_events_for_org(org_id, [event], create_incidents=True)
    assert r2["created_ids"] == []

    db = _session(org_id)
    try:
        anzahl = db.query(Incident).filter(
            Incident.primary_org_id == org_id, Incident.lis_operation_number == "f-neu-002",
        ).count()
        assert anzahl == 1
    finally:
        db.close()


def test_event_passt_zu_bestehendem_incident_ueber_adresse_verknuepft(org_id):
    """Tier 2 in find_matching_incident() (Alarmstichwort+Adresse im Zeitfenster,
    da noch keine Leitstellennummer bekannt ist) verknüpft statt neu anzulegen."""
    db = _session(org_id)
    try:
        vorhandener, _ = create_incident(
            db, "T2", primary_org_id=org_id,
            address_street="Teststrasse", address_no="5", address_city="Wolfurt",
            started_at=datetime(2026, 7, 26, 10, 0, tzinfo=UTC),
        )
        db.commit()
        vorhandener_id = vorhandener.id
    finally:
        db.close()

    event = _event("f-match-003")
    result = dibos_enrich.enrich_events_for_org(org_id, [event], create_incidents=True)
    assert result["created_ids"] == []  # kein neuer Einsatz - nur Verknüpfung

    db = _session(org_id)
    try:
        incident = db.get(Incident, vorhandener_id)
        assert incident.lis_operation_number == "f-match-003"
        anzahl = db.query(Incident).filter(Incident.primary_org_id == org_id).count()
        assert anzahl == 1
    finally:
        db.close()


def test_geschlossenes_event_wird_direkt_geschlossen_angelegt(org_id):
    """War das Event bei Anlage bereits abgeschlossen, wird der Einsatz zur
    Dokumentation angelegt, aber sofort geschlossen — keine Benachrichtigung
    (kein Eintrag in created_ids), analog lis_sync.py."""
    event = _event("f-closed-004", closed="2026-07-26T11:00:00")
    result = dibos_enrich.enrich_events_for_org(org_id, [event], create_incidents=True)
    assert result["created_ids"] == []

    db = _session(org_id)
    try:
        incident = db.query(Incident).filter(
            Incident.primary_org_id == org_id, Incident.lis_operation_number == "f-closed-004",
        ).first()
        assert incident is not None
        assert incident.status == "closed"
        assert incident.closed_via_lis_auto is True
    finally:
        db.close()


def test_create_incidents_false_legt_nichts_an(org_id):
    """Regressionstest: ohne das Org-Opt-in bleibt das Verhalten exakt wie
    bisher — reine Anreicherung, kein Anlegen (Default aus)."""
    event = _event("f-off-005")
    result = dibos_enrich.enrich_events_for_org(org_id, [event])  # create_incidents Default False
    assert result["created_ids"] == []

    db = _session(org_id)
    try:
        anzahl = db.query(Incident).filter(
            Incident.primary_org_id == org_id, Incident.lis_operation_number == "f-off-005",
        ).count()
        assert anzahl == 0
    finally:
        db.close()


def test_race_beim_gleichzeitigen_anlegen_wird_abgefangen(org_id):
    """Simuliert eine echte Race-Situation zwischen zwei 'gleichzeitigen' Polls
    (z.B. leichter Erkennungs-Loop und laufendes Voll-Tracing): Session A prüft
    (noch nichts vorhanden), bevor Session B fertig anlegt und committet.
    Session A weiß davon nichts und versucht trotzdem, denselben Einsatz frisch
    anzulegen — der DB-Unique-Constraint (uq_incident_org_lis_operation_number)
    muss dabei einen IntegrityError auslösen, den
    _get_or_create_incident_for_event() abfängt, statt einen doppelten Einsatz
    zu committen (Muster: test_lis_sync.py::
    test_get_or_link_incident_recovers_from_integrity_error_race)."""
    import app.services.lis.lis_matching as lis_matching_module

    db_a = _session(org_id)
    db_b = _session(org_id)
    try:
        org_a = db_a.get(FireDept, org_id)
        org_b = db_b.get(FireDept, org_id)
        event = _event("f-race-006")

        # Session B gewinnt das Rennen zuerst und committet vollständig.
        winner, created_b = dibos_enrich._get_or_create_incident_for_event(db_b, org_b, org_id, event)
        db_b.commit()
        assert created_b is True

        # Session A hat ihre eigene Prüfung bereits VOR B's Commit durchgeführt
        # (kein Treffer) — simuliert durch Patchen von find_matching_incident auf
        # 'None' für den regulären (ersten) Aufruf.
        real_matcher = lis_matching_module.find_matching_incident
        lis_matching_module.find_matching_incident = lambda *a, **kw: None
        try:
            result_a = dibos_enrich._get_or_create_incident_for_event(db_a, org_a, org_id, event)
        finally:
            lis_matching_module.find_matching_incident = real_matcher

        assert result_a is not None
        incident_a, created_a = result_a
        assert created_a is False
        assert incident_a.id == winner.id

        anzahl = db_a.query(Incident).filter(
            Incident.primary_org_id == org_id, Incident.lis_operation_number == "f-race-006",
        ).count()
        assert anzahl == 1
    finally:
        db_a.rollback()
        db_a.close()
        db_b.close()
