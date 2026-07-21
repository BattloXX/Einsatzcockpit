"""Tests für dibos_enrich.py: DIBOS→Einsatz-Anreicherung (Org-Opt-in).

Prüft die Zuordnung über die stabile Einsatznummer (eventNumber ==
Incident.lis_operation_number), das Nachtragen fehlender Adressfelder ohne
Überschreiben vorhandener Werte, die dibos_*-Zusatzfelder sowie den Import des
sichtbaren Meldungsprotokolls inkl. Dedup bei wiederholtem Poll.

enrich_events_for_org() öffnet intern eine eigene DB-Session (app.db.SessionLocal,
dieselbe SQLite-Testdatei über den Produktions-Engine) — spiegelt den echten
Hintergrund-Poll. Tests committen daher ihre eigene Vorbereitung und schließen
ihre Session VOR dem Aufruf; IDs werden vor dem Schließen eingesammelt (sonst
DetachedInstanceError beim späteren Zugriff auf das ORM-Objekt).

Nachrichten-/Sync-Zählungen werden bewusst als DELTA (vorher/nachher) geprüft,
nicht als absolute Zahl: create_incident() legt über _create_default_messages()
je nach Alarmstichwort-Konfiguration bereits eigene Standard-Meldungen an — wie
viele das sind, hängt vom AlarmType-Datenstand ab und ist hier nicht der
Test-Gegenstand.
"""
from datetime import UTC, datetime

from app.core.tenant import set_tenant_context
from app.models.incident import Incident, Message
from app.models.lis import LisSyncedObject
from app.services.dibos import dibos_enrich
from app.services.incident_service import create_incident
from tests.conftest import TestingSession

ORG_ID = 1  # FF Wolfurt (Home-Org, siehe seed_data.FIRE_DEPTS)


def _session() -> "TestingSession":
    db = TestingSession()
    set_tenant_context(db, ORG_ID)
    return db


def _event(seed: int) -> dict:
    """Baut ein vollständiges Event (Schema aus dem echten Mitschnitt 2026-07-21,
    Einsatz f26006436) mit pro Test EINDEUTIGER eventNumber und Kommentar-IDs.

    Wichtig für Testisolation: setup_db ist session-scoped (siehe conftest.py),
    die DB bleibt also über alle Tests dieser Datei hinweg bestehen. Sowohl die
    Einsatznummer-Zuordnung (_find_active_incident_by_event_number) als auch
    das Kommentar-Dedup (LisSyncedObject, Schlüssel org_id+obj_type+lis_id —
    NICHT zusätzlich nach incident_id!) würden sonst Daten aus einem früheren
    Test dieser Datei fälschlich wiederverwenden."""
    return {
        "id": 3294510 + seed, "eventNumber": f"f26006436-{seed}", "ag": "FW",
        "created": "2026-07-21T17:27:09", "dispatched": "2026-07-21T17:29:59", "closed": None,
        "tycod": "t2", "subTycod": "", "tycodDescription": "kleiner technischer Einsatz",
        "diagnose": "",
        "eventComment": "[Türöffnung] med. Notfall hinter verschlossener Türe",
        "bmaNo": None,
        "status": 1, "statusText": "AL", "statusTime": "2026-07-21T17:29:59",
        "locationCity": "WOLFURT", "locationCityPart": "WOLFURT-OT",
        "locationStreet": "UNTERLINDEN", "locationStreetNo": "23",
        "locationObject": "", "locationLongitude": 9.749971, "locationLatitude": 47.47214,
        "callerList": [{"callerName": "PI Wolfurt", "callerNumber": ""}],
        "targetList": [],
        "comments": [
            {
                "id": 11487516000 + seed, "messageType": 0, "isInternal": False,
                "comment": "PreSched", "creationDate": "2026-07-21T16:29:00", "creationPerson": "",
            },
            {
                "id": 11487518000 + seed, "messageType": 6, "isInternal": True,
                "comment": "###16:29:20 Alarmplan aufgerufen",
                "creationDate": "2026-07-21T16:29:20", "creationPerson": "Philipp Erhart",
            },
            {
                "id": 11487000000 + seed, "messageType": 0, "isInternal": False,
                "comment": "Einheit 7.211: Patient Verstorben",
                "creationDate": "2026-07-21T17:34:33", "creationPerson": "Aaron Höfler",
            },
        ],
    }


def _make_incident_id(db, *, lis_operation_number=None, status=None, **create_kwargs) -> int:
    """Legt einen Einsatz an, committet und gibt nur die ID zurück (nicht das
    ORM-Objekt — der Aufrufer schließt die Session i.d.R. direkt danach, ein
    späterer Attributzugriff auf ein committetes+geschlossenes Objekt würfe
    DetachedInstanceError).

    lis_operation_number/status sind keine create_incident()-Parameter (das
    entspricht dem echten Ablauf: create_incident() legt den Einsatz an,
    lis_operation_number wird erst danach vom LIS-Sync gesetzt, siehe
    lis_sync._get_or_link_incident) — daher separat nach dem Anlegen gesetzt."""
    defaults = dict(
        alarm_type_code="T1", primary_org_id=ORG_ID,
        started_at=datetime(2026, 7, 21, 17, 27, tzinfo=UTC),
    )
    defaults.update(create_kwargs)
    incident = create_incident(db, **defaults)
    if lis_operation_number is not None:
        incident.lis_operation_number = lis_operation_number
    if status is not None:
        incident.status = status
    db.commit()
    return incident.id


def test_enrich_finds_incident_by_stable_event_number():
    event = _event(1)
    db = _session()
    incident_id = _make_incident_id(db, lis_operation_number=event["eventNumber"])
    db.close()

    changed_ids = dibos_enrich.enrich_events_for_org(ORG_ID, [event])

    assert changed_ids == [incident_id]


def test_enrich_fills_missing_address_without_overwriting_existing():
    event = _event(2)
    db = _session()
    # Straße schon vorhanden (z.B. via LIS/IPR gesetzt) -> bleibt unangetastet,
    # Ort/Hausnummer/Koordinaten fehlen noch und werden ergänzt.
    incident_id = _make_incident_id(
        db, lis_operation_number=event["eventNumber"], address_street="Bereits gesetzte Strasse",
    )
    db.close()

    dibos_enrich.enrich_events_for_org(ORG_ID, [event])

    check_db = _session()
    refreshed = check_db.get(Incident, incident_id)
    assert refreshed.address_street == "Bereits gesetzte Strasse"  # unverändert
    assert refreshed.address_city == "WOLFURT"  # ergänzt
    assert refreshed.address_no == "23"  # ergänzt
    assert refreshed.lat == 47.47214
    assert refreshed.lng == 9.749971
    check_db.close()


def test_enrich_sets_dibos_metadata_fields():
    event = _event(3)
    db = _session()
    incident_id = _make_incident_id(db, lis_operation_number=event["eventNumber"])
    db.close()

    dibos_enrich.enrich_events_for_org(ORG_ID, [event])

    check_db = _session()
    refreshed = check_db.get(Incident, incident_id)
    assert refreshed.dibos_tycod == "t2"
    assert refreshed.dibos_event_comment == "[Türöffnung] med. Notfall hinter verschlossener Türe"
    assert refreshed.dibos_bma_no is None  # bmaNo war None im Event -> nicht gesetzt
    check_db.close()


def test_enrich_imports_only_non_internal_comments_as_messages():
    event = _event(4)
    db = _session()
    incident_id = _make_incident_id(db, lis_operation_number=event["eventNumber"])
    db.close()

    dibos_enrich.enrich_events_for_org(ORG_ID, [event])

    check_db = _session()
    messages = check_db.query(Message).filter(Message.incident_id == incident_id).all()
    details = {m.detail for m in messages}
    assert "PreSched" in details
    assert "Einheit 7.211: Patient Verstorben" in details
    assert not any((d or "").startswith("###") for d in details)  # interne Systemzeile ausgeschlossen
    check_db.close()


def test_enrich_is_idempotent_no_duplicate_messages_on_repeated_poll():
    event = _event(5)
    db = _session()
    incident_id = _make_incident_id(db, lis_operation_number=event["eventNumber"])
    before_count = (
        db.query(Message).filter(Message.incident_id == incident_id).count()
    )  # create_incident() kann bereits eigene Standard-Meldungen anlegen
    db.close()

    dibos_enrich.enrich_events_for_org(ORG_ID, [event])
    changed_second = dibos_enrich.enrich_events_for_org(ORG_ID, [event])

    check_db = _session()
    after_count = check_db.query(Message).filter(Message.incident_id == incident_id).count()
    synced_count = (
        check_db.query(LisSyncedObject)
        .filter(LisSyncedObject.obj_type == "dibos_comment", LisSyncedObject.incident_id == incident_id)
        .count()
    )
    check_db.close()

    assert after_count - before_count == 2  # nur die beiden nicht-internen Kommentare, kein Duplikat
    assert synced_count == 2
    assert changed_second == []  # zweiter Poll: nichts mehr neu -> kein Broadcast nötig


def test_enrich_ignores_closed_incidents():
    """Ein bereits geschlossener Einsatz wird NICHT mehr angereichert (DIBOS
    reichert nur laufende Einsätze an, siehe Modul-Docstring)."""
    event = _event(6)
    db = _session()
    _make_incident_id(db, lis_operation_number=event["eventNumber"], status="closed")
    db.close()

    changed_ids = dibos_enrich.enrich_events_for_org(ORG_ID, [event])

    assert changed_ids == []


def test_enrich_ignores_events_without_matching_incident():
    changed_ids = dibos_enrich.enrich_events_for_org(ORG_ID, [_event(7)])
    assert changed_ids == []
