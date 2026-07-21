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


def _event(seed: int, bma_no: str | None = None, person_responses: list[dict] | None = None) -> dict:
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
        "bmaNo": bma_no,
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
        "personResponseList": person_responses or [],
    }


def _person_response(
    response_id: int, person: str, status: str, change_date: str,
    *, id_sybos: str | None = None, department: str = "fw_wolfu",
) -> dict:
    return {
        "id": response_id, "responseTime": change_date, "person": person, "function": "",
        "status": status, "department": department, "departmentSybos": None,
        "idSybos": id_sybos, "idDibos": str(response_id + 90000),
        "elsEventId": None, "changeDate": change_date,
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

    result = dibos_enrich.enrich_events_for_org(ORG_ID, [event])

    assert result["changed_ids"] == [incident_id]


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
    assert changed_second["changed_ids"] == []  # zweiter Poll: nichts mehr neu -> kein Broadcast nötig


def test_enrich_ignores_closed_incidents():
    """Ein bereits geschlossener Einsatz wird NICHT mehr angereichert (DIBOS
    reichert nur laufende Einsätze an, siehe Modul-Docstring)."""
    event = _event(6)
    db = _session()
    _make_incident_id(db, lis_operation_number=event["eventNumber"], status="closed")
    db.close()

    result = dibos_enrich.enrich_events_for_org(ORG_ID, [event])

    assert result["changed_ids"] == []


def test_enrich_ignores_events_without_matching_incident():
    result = dibos_enrich.enrich_events_for_org(ORG_ID, [_event(7)])
    assert result["changed_ids"] == []


# ── BMA-Nr. -> Objektverwaltung: Objekt automatisch mit dem Einsatz verknüpfen ──
#
# Eigene, ISOLIERTE Test-Org je Test (statt der gemeinsam genutzten ORG_ID=1):
# Objekt/ObjektBMA-Zeilen für org_id=1 würden über die gesamte (session-scoped)
# Testdatenbank hinweg sichtbar bleiben und andere Testdateien verwirren, die
# für ORG_ID=1 eine bestimmte, "saubere" Objekt-Liste erwarten (Regression
# beobachtet: test_einsatzinfo_pr11.py::test_objekt_kandidaten_nach_entfernung…
# schlug fehl, weil hier zuvor Objekte unter org_id=1 angelegt wurden). Der
# systemweite Schalter objekt_module_enabled (SystemSettings, kein Org-Bezug)
# wird dagegen bewusst zurückgesetzt, weil er kein Org-Feld hat.

def _make_test_org(db, slug: str) -> int:
    from app.models.master import FireDept
    org = FireDept(slug=slug, name=f"DIBOS-BMA-Test {slug}", color="#336699", bos="Feuerwehr")
    db.add(org)
    db.flush()
    db.commit()
    return org.id


def _enable_objekt_module(db, org_id: int) -> None:
    """Aktiviert die Objektverwaltung system- (get-or-update, da system_settings.key
    Primary Key ist und andere Testdateien denselben Key evtl. schon gesetzt haben)
    und für die übergebene Org."""
    from app.models.master import OrgSettings, SystemSettings

    sys_row = db.query(SystemSettings).filter(SystemSettings.key == "objekt_module_enabled").first()
    if sys_row is None:
        db.add(SystemSettings(key="objekt_module_enabled", value="true"))
    else:
        sys_row.value = "true"
    db.add(OrgSettings(org_id=org_id, objekt_module_enabled=True))
    db.commit()


def _make_objekt_with_bma(db, org_id: int, bma_nummer: str, name: str, nummer: int) -> int:
    from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt, ObjektBMA

    objekt = Objekt(
        org_id=org_id, nummer=nummer, name=name, status=OBJEKT_STATUS_FREIGEGEBEN,
        strasse="Teststrasse", hausnummer="1", ort="Wolfurt",
    )
    db.add(objekt)
    db.flush()
    db.add(ObjektBMA(org_id=org_id, objekt_id=objekt.id, bma_nummer=bma_nummer))
    db.commit()
    return objekt.id


def _make_bare_incident(db, org_id: int, *, lis_operation_number: str, status: str = "active") -> int:
    """Legt einen minimalen Einsatz direkt an (ohne create_incident()), da eine
    frisch angelegte Test-Org keine Stammdaten (AlarmType, Fahrzeuge) hat, auf
    die create_incident() angewiesen ist — Muster: test_objekt_einsatz_verknuepfung.py."""
    inc = Incident(
        primary_org_id=org_id, alarm_type_code="T1", status=status,
        started_at=datetime(2026, 7, 21, 17, 27, tzinfo=UTC),
        lis_operation_number=lis_operation_number,
    )
    db.add(inc)
    db.commit()
    return inc.id


def test_enrich_links_objekt_via_dibos_bma_number_when_not_already_linked():
    event = _event(8, bma_no="0074001")
    db = TestingSession()
    org_id = _make_test_org(db, "dibos-bma-1")
    set_tenant_context(db, org_id)
    _enable_objekt_module(db, org_id)
    objekt_id = _make_objekt_with_bma(db, org_id, "74001", "Alten- und Pflegeheim", nummer=1)
    incident_id = _make_bare_incident(db, org_id, lis_operation_number=event["eventNumber"])
    db.close()

    result = dibos_enrich.enrich_events_for_org(org_id, [event])

    assert result["changed_ids"] == [incident_id]
    check_db = TestingSession()
    set_tenant_context(check_db, org_id)
    from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT, ObjektEinsatz
    link = (
        check_db.query(ObjektEinsatz)
        .filter(ObjektEinsatz.incident_id == incident_id)
        .first()
    )
    assert link is not None
    assert link.objekt_id == objekt_id
    assert link.status == OBJEKT_EINSATZ_BESTAETIGT
    assert link.quelle == "bma"
    check_db.close()


def test_enrich_does_not_add_second_objekt_when_already_confirmed_linked():
    """Ein Mensch (oder eine andere Quelle) hat bereits ein ANDERES Objekt
    bestätigt — ein neu über DIBOS bekannter BMA-Treffer darf dieses nicht um
    ein zweites Objekt ergänzen (siehe _has_confirmed_objekt_link)."""
    from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT, ObjektEinsatz

    event = _event(9, bma_no="74002")
    db = TestingSession()
    org_id = _make_test_org(db, "dibos-bma-2")
    set_tenant_context(db, org_id)
    _enable_objekt_module(db, org_id)
    _make_objekt_with_bma(db, org_id, "74002", "Werkhalle", nummer=1)
    anderes_objekt_id = _make_objekt_with_bma(db, org_id, "99999", "Bereits bestätigtes Objekt", nummer=2)
    incident_id = _make_bare_incident(db, org_id, lis_operation_number=event["eventNumber"])
    db.add(ObjektEinsatz(
        org_id=org_id, objekt_id=anderes_objekt_id, incident_id=incident_id,
        quelle="manuell", status=OBJEKT_EINSATZ_BESTAETIGT,
    ))
    db.commit()
    db.close()

    dibos_enrich.enrich_events_for_org(org_id, [event])

    check_db = TestingSession()
    set_tenant_context(check_db, org_id)
    links = check_db.query(ObjektEinsatz).filter(ObjektEinsatz.incident_id == incident_id).all()
    assert [link.objekt_id for link in links] == [anderes_objekt_id]  # unverändert, kein zweiter Link
    check_db.close()


def test_enrich_skips_objekt_matching_when_module_disabled():
    from app.models.master import SystemSettings

    event = _event(10, bma_no="74003")
    db = TestingSession()
    org_id = _make_test_org(db, "dibos-bma-3")
    set_tenant_context(db, org_id)
    _enable_objekt_module(db, org_id)
    _make_objekt_with_bma(db, org_id, "74003", "Sollte nicht verknüpft werden", nummer=1)
    # Modul wieder deaktivieren, NACH dem gemeinsamen Setup, damit dieser Test
    # unabhängig von der Ausführungsreihenfolge anderer Tests in dieser Datei ist.
    # (Systemweiter Schalter ohne Org-Bezug — daher am Ende wieder aktiviert.)
    db.query(SystemSettings).filter(SystemSettings.key == "objekt_module_enabled").update({"value": "false"})
    incident_id = _make_bare_incident(db, org_id, lis_operation_number=event["eventNumber"])
    db.commit()
    db.close()

    try:
        dibos_enrich.enrich_events_for_org(org_id, [event])

        check_db = TestingSession()
        set_tenant_context(check_db, org_id)
        from app.models.objekt import ObjektEinsatz
        count = check_db.query(ObjektEinsatz).filter(ObjektEinsatz.incident_id == incident_id).count()
        check_db.close()
        assert count == 0
    finally:
        # Systemweiten Schalter für nachfolgende Tests wieder aktivieren.
        cleanup_db = TestingSession()
        cleanup_db.query(SystemSettings).filter(
            SystemSettings.key == "objekt_module_enabled",
        ).update({"value": "true"})
        cleanup_db.commit()
        cleanup_db.close()


# ── Personenrückmeldungen (Zu-/Absagen) -> Teilnahme ────────────────────────
#
# Eigene, isolierte Test-Org je Test (siehe Begründung im BMA-Abschnitt oben).

def _make_member(db, org_id: int, firstname: str, lastname: str, sybos_id: str | None = None) -> int:
    from app.models.master import Member
    member = Member(org_id=org_id, firstname=firstname, lastname=lastname, sybos_id=sybos_id)
    db.add(member)
    db.commit()
    return member.id


def test_enrich_creates_teilnahme_for_matched_member_via_sybos_id():
    event = _event(11, person_responses=[
        _person_response(51662, "Jesse Rohner", "Zugesagt", "2026-07-21T17:47:23", id_sybos="31359"),
    ])
    db = TestingSession()
    org_id = _make_test_org(db, "dibos-rsvp-1")
    set_tenant_context(db, org_id)
    member_id = _make_member(db, org_id, "Jesse", "Rohner", sybos_id="31359")
    incident_id = _make_bare_incident(db, org_id, lis_operation_number=event["eventNumber"])
    db.close()

    result = dibos_enrich.enrich_events_for_org(org_id, [event])

    assert result["rsvp_changed_ids"] == [incident_id]
    check_db = TestingSession()
    set_tenant_context(check_db, org_id)
    from app.models.teilnahme import Teilnahme
    row = check_db.query(Teilnahme).filter(Teilnahme.bezug_id == incident_id).first()
    assert row is not None
    assert row.mitglied_id == member_id
    assert row.rsvp_status == "zugesagt"
    assert row.rsvp_source == "dibos"
    assert row.dibos_response_id == 51662
    check_db.close()


def test_enrich_creates_freitext_teilnahme_when_no_member_match():
    event = _event(12, person_responses=[
        _person_response(51700, "Fremde Person", "Abgesagt", "2026-07-21T18:00:00", id_sybos=None),
    ])
    db = TestingSession()
    org_id = _make_test_org(db, "dibos-rsvp-2")
    set_tenant_context(db, org_id)
    incident_id = _make_bare_incident(db, org_id, lis_operation_number=event["eventNumber"])
    db.close()

    dibos_enrich.enrich_events_for_org(org_id, [event])

    check_db = TestingSession()
    set_tenant_context(check_db, org_id)
    from app.models.teilnahme import Teilnahme
    row = check_db.query(Teilnahme).filter(Teilnahme.bezug_id == incident_id).first()
    assert row is not None
    assert row.mitglied_id is None
    assert row.freitext_name == "Fremde Person"
    assert row.rsvp_status == "abgesagt"
    check_db.close()


def test_enrich_maps_delayed_minutes_status_to_zugesagt():
    event = _event(13, person_responses=[
        _person_response(51670, "Verspätete Person", "10 Min", "2026-07-21T19:18:59", id_sybos=None),
    ])
    db = TestingSession()
    org_id = _make_test_org(db, "dibos-rsvp-3")
    set_tenant_context(db, org_id)
    incident_id = _make_bare_incident(db, org_id, lis_operation_number=event["eventNumber"])
    db.close()

    dibos_enrich.enrich_events_for_org(org_id, [event])

    check_db = TestingSession()
    set_tenant_context(check_db, org_id)
    from app.models.teilnahme import Teilnahme
    row = check_db.query(Teilnahme).filter(Teilnahme.bezug_id == incident_id).first()
    assert row is not None
    assert row.rsvp_status == "zugesagt"
    check_db.close()


def test_enrich_ignores_unknown_rsvp_status():
    event = _event(14, person_responses=[
        _person_response(51701, "Unklare Person", "Irgendwas", "2026-07-21T18:00:00", id_sybos=None),
    ])
    db = TestingSession()
    org_id = _make_test_org(db, "dibos-rsvp-4")
    set_tenant_context(db, org_id)
    incident_id = _make_bare_incident(db, org_id, lis_operation_number=event["eventNumber"])
    db.close()

    result = dibos_enrich.enrich_events_for_org(org_id, [event])

    assert result["rsvp_changed_ids"] == []
    check_db = TestingSession()
    set_tenant_context(check_db, org_id)
    from app.models.teilnahme import Teilnahme
    count = check_db.query(Teilnahme).filter(Teilnahme.bezug_id == incident_id).count()
    check_db.close()
    assert count == 0


def test_enrich_upserts_by_dibos_response_id_on_status_change():
    """Dieselbe Rückmelde-ID (id 51670, siehe MD-Beispiel) ändert bei einem
    späteren Poll ihren Status — muss dieselbe Zeile aktualisieren, nicht eine
    zweite anlegen (Upsert-Schlüssel: dibos_response_id)."""
    db = TestingSession()
    org_id = _make_test_org(db, "dibos-rsvp-5")
    set_tenant_context(db, org_id)
    incident_id = _make_bare_incident(db, org_id, lis_operation_number="f26006438-15")
    db.close()

    event_v1 = {**_event(15, person_responses=[
        _person_response(51670, "Wechselnde Person", "10 Min", "2026-07-21T19:10:16", id_sybos=None),
    ]), "eventNumber": "f26006438-15"}
    dibos_enrich.enrich_events_for_org(org_id, [event_v1])

    event_v2 = {**_event(15, person_responses=[
        _person_response(51670, "Wechselnde Person", "Abgesagt", "2026-07-21T19:17:53", id_sybos=None),
    ]), "eventNumber": "f26006438-15"}
    dibos_enrich.enrich_events_for_org(org_id, [event_v2])

    check_db = TestingSession()
    set_tenant_context(check_db, org_id)
    from app.models.teilnahme import Teilnahme
    rows = check_db.query(Teilnahme).filter(Teilnahme.bezug_id == incident_id).all()
    assert len(rows) == 1  # kein Duplikat
    assert rows[0].rsvp_status == "abgesagt"  # neuester Stand übernommen
    check_db.close()


def test_enrich_ignores_response_older_than_stored_change_date():
    """Ein nachträglich eintreffendes, aber ÄLTERES changeDate darf einen
    bereits gespeicherten neueren Stand nicht zurücksetzen (Versionsanker)."""
    db = TestingSession()
    org_id = _make_test_org(db, "dibos-rsvp-6")
    set_tenant_context(db, org_id)
    incident_id = _make_bare_incident(db, org_id, lis_operation_number="f26006438-16")
    db.close()

    newer = {**_event(16, person_responses=[
        _person_response(51671, "Zeitreisende Person", "Abgesagt", "2026-07-21T19:17:53", id_sybos=None),
    ]), "eventNumber": "f26006438-16"}
    dibos_enrich.enrich_events_for_org(org_id, [newer])

    older = {**_event(16, person_responses=[
        _person_response(51671, "Zeitreisende Person", "Zugesagt", "2026-07-21T19:10:16", id_sybos=None),
    ]), "eventNumber": "f26006438-16"}
    result = dibos_enrich.enrich_events_for_org(org_id, [older])

    assert result["rsvp_changed_ids"] == []  # älterer Stand wurde übersprungen
    check_db = TestingSession()
    set_tenant_context(check_db, org_id)
    from app.models.teilnahme import Teilnahme
    row = check_db.query(Teilnahme).filter(Teilnahme.bezug_id == incident_id).first()
    assert row.rsvp_status == "abgesagt"  # unverändert, der neuere Stand bleibt
    check_db.close()
