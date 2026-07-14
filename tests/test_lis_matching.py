"""Tests für die LIS/IPR-Anbindung: reine Mapping-Funktionen + Matching-Heuristik.

Netzwerk (lis_client) wird hier bewusst nicht getestet — nur die isoliert
testbaren Bausteine: Parsing (lis_mapping) und Verknüpfungs-Logik (lis_matching).
"""
from datetime import UTC, datetime, timedelta

from app.core.tenant import set_tenant_context
from app.models.incident import Incident
from app.services.lis import lis_mapping, lis_matching
from tests.conftest import TestingSession

ORG_ID = 1  # FF Wolfurt (Home-Org, siehe seed_data.FIRE_DEPTS)


def _session() -> "TestingSession":
    db = TestingSession()
    set_tenant_context(db, ORG_ID)
    return db


def _make_incident(db, **overrides) -> Incident:
    defaults = dict(
        primary_org_id=ORG_ID,
        alarm_type_code="T1",
        status="active",
        reason="Brandmeldeanlage",
        address_street="Achstraße",
        address_no="8",
        address_city="Wolfurt",
        started_at=datetime(2026, 7, 3, 20, 0, tzinfo=UTC),
    )
    defaults.update(overrides)
    incident = Incident(**defaults)
    db.add(incident)
    db.flush()
    return incident


# ── lis_mapping: Fahrzeugstatus (S4/S5) ──────────────────────────────────────

def test_map_unit_status_s4():
    assert lis_mapping.map_unit_status("S4 - zum Einsatzort") == "Einsatz übernommen"


def test_map_unit_status_s5():
    assert lis_mapping.map_unit_status("S5 - am Einsatzort") == "Am Einsatzort"


def test_map_unit_status_unmapped_returns_none():
    assert lis_mapping.map_unit_status("S2 - Einsatzbereit Stützpkt") is None
    assert lis_mapping.map_unit_status(None) is None
    assert lis_mapping.map_unit_status("") is None


# ── lis_mapping: Alarmstichwort ──────────────────────────────────────────────

def test_map_stichwort_known_and_fallback():
    assert lis_mapping.map_stichwort("f1") == "F1"
    assert lis_mapping.map_stichwort("T4") == "T4"
    assert lis_mapping.map_stichwort("unknown-code") == "T1"
    assert lis_mapping.map_stichwort(None) == "T1"


def test_map_stichwort_handles_real_prefixed_code():
    """Echter Mitschnitt (Capture 2026-07-04, Testeinsatz LIS): Type.Code kommt als
    't_t3' (mit Präfix), nicht als 't3' — muss trotzdem auf T3 gemappt werden."""
    assert lis_mapping.map_stichwort("t_t3") == "T3"
    assert lis_mapping.map_stichwort("f_f1") == "F1"


def test_map_stichwort_f11():
    """Regression (2026-07-11, echter Mitschnitt): LIS Type.Code 'f11' ('f11 -
    Sondereinsatzmittel') fehlte in _STUFE_MAP und fiel auf den 'T1'-Fallback
    zurück — ein Einsatz wurde dadurch fälschlich als T1 statt F11 dargestellt."""
    assert lis_mapping.map_stichwort("f11") == "F11"
    assert lis_mapping.map_stichwort("F11") == "F11"


def test_map_stichwort_vollstaendiger_vorarlberger_katalog():
    """Vollständiger Alarmstichwort-Katalog der Vorarlberger Feuerwehren (siehe
    feuerwehr-riezlern.at/sonstiges/wissenswertes/alarmstichworte) muss 1:1 auf
    den jeweils gleichnamigen internen Code gemappt werden."""
    erwartet = ["F1", "F2", "F3", "F4", "F5", "F10", "F11", "F14", "F21", "F30",
                "T1", "T2", "T3", "T4", "T5", "T6", "T7", "T9", "T21"]
    for code in erwartet:
        assert lis_mapping.map_stichwort(code.lower()) == code


# ── lis_mapping: Übungserkennung (Doku hat keinen Boolean/Enum dafür) ────────

def test_is_exercise_operation_detects_real_schulungseinsatz():
    """Echter Mitschnitt: Type.Type = 'Schulungseinsatz (ohne RFL) - Feuerwehr'."""
    assert lis_mapping.is_exercise_operation({"Type": "Schulungseinsatz (ohne RFL) - Feuerwehr"}) is True


def test_is_exercise_operation_false_for_normal_type():
    assert lis_mapping.is_exercise_operation({"Type": "t3 - mittlerer technischer Einsatz"}) is False


def test_is_exercise_operation_false_when_missing():
    assert lis_mapping.is_exercise_operation(None) is False
    assert lis_mapping.is_exercise_operation({}) is False


# ── lis_mapping: Personen-Zu-/Absagen (Doku Abschnitt 8.2 Beispiele) ─────────

def test_parse_person_response_zusage_mit_rolle_und_ankunft():
    parsed = lis_mapping.parse_person_response(
        "andreas.schneider4 (Mannschaft): Zugesagt Ankunftszeit 20:01", "UNITSTATUSHISTORY",
    )
    assert parsed == {
        "person": "andreas.schneider4", "role": "Mannschaft",
        "status": "Zugesagt", "arrival_time": "20:01",
    }


def test_parse_person_response_absage_ohne_rolle():
    parsed = lis_mapping.parse_person_response("michael.pfattner: Abgesagt", "UNITSTATUSHISTORY")
    assert parsed["person"] == "michael.pfattner"
    assert parsed["status"] == "Abgesagt"
    assert parsed["role"] is None
    assert parsed["arrival_time"] is None


def test_parse_person_response_ignores_vehicle_status_changes():
    assert lis_mapping.parse_person_response("Wolfurt KDOF: S4 - zum Einsatzort", "UNITSTATUSHISTORY") is None
    assert lis_mapping.parse_person_response("fwel_wolfu: S4 - zum Einsatzort", "UNITSTATUSHISTORY") is None
    assert lis_mapping.parse_person_response("Wolfurt TMB 27: entlassen", "UNITSTATUSHISTORY") is None


def test_parse_person_response_ignores_other_task_types():
    assert lis_mapping.parse_person_response("andreas.schneider4: Zugesagt", "TASK") is None


# ── lis_matching: Verknüpfungs-Heuristik ─────────────────────────────────────

def test_find_matching_incident_matches_within_window():
    db = _session()
    try:
        incident = _make_incident(db)

        match = lis_matching.find_matching_incident(
            db, ORG_ID,
            alarm_type_code="T1",
            street="Achstraße",
            city="Wolfurt",
            started_at=incident.started_at + timedelta(hours=1),
        )
        assert match is not None
        assert match.id == incident.id
    finally:
        db.rollback()
        db.close()


def test_find_matching_incident_rejects_different_alarm_type():
    db = _session()
    try:
        incident = _make_incident(db, alarm_type_code="T4")

        match = lis_matching.find_matching_incident(
            db, ORG_ID,
            alarm_type_code="T1",
            street="Achstraße",
            city="Wolfurt",
            started_at=incident.started_at,
        )
        assert match is None
    finally:
        db.rollback()
        db.close()


def test_find_matching_incident_rejects_outside_window():
    db = _session()
    try:
        incident = _make_incident(db)

        match = lis_matching.find_matching_incident(
            db, ORG_ID,
            alarm_type_code="T1",
            street="Achstraße",
            city="Wolfurt",
            started_at=incident.started_at + timedelta(hours=4),
            window_hours=3,
        )
        assert match is None
    finally:
        db.rollback()
        db.close()


def test_find_matching_incident_rejects_closed_incident():
    db = _session()
    try:
        incident = _make_incident(db, status="closed")

        match = lis_matching.find_matching_incident(
            db, ORG_ID,
            alarm_type_code="T1",
            street="Achstraße",
            city="Wolfurt",
            started_at=incident.started_at,
        )
        assert match is None
    finally:
        db.rollback()
        db.close()


def test_find_matching_incident_direct_lis_operation_id_hit():
    db = _session()
    try:
        incident = _make_incident(db, reason="Andere Sache", address_street="Andere Straße",
                                   lis_operation_id="op-guid-123")

        match = lis_matching.find_matching_incident(
            db, ORG_ID,
            alarm_type_code="T9",  # irrelevant für den direkten ID-Match
            street=None, city=None, started_at=None,
            lis_operation_id="op-guid-123",
        )
        assert match is not None
        assert match.id == incident.id
    finally:
        db.rollback()
        db.close()


def test_find_matching_incident_none_without_address():
    db = _session()
    try:
        _make_incident(db)

        match = lis_matching.find_matching_incident(
            db, ORG_ID,
            alarm_type_code="T1",
            street=None,
            city=None,
            started_at=datetime(2026, 7, 3, 20, 0, tzinfo=UTC),
        )
        assert match is None
    finally:
        db.rollback()
        db.close()


def test_find_matching_incident_matches_despite_different_reason_text():
    """Reproduziert Prod-Vorfall #200/#201: gleiches Stichwort an gleicher Adresse,
    aber völlig unterschiedlicher Meldungstext (einmal rohes Pager-Markup '[...] >',
    einmal bereinigt) — laut Nutzer-Entscheidung genügt Stichwort + Adresse, der
    Meldungstext wird gar nicht mehr verglichen."""
    db = _session()
    try:
        incident = _make_incident(
            db, reason="3.OG - [PATIENTENRETTUNG] > RTW/NEF vor Ort",
            address_street="Kellhofstraße", address_no="10", address_city="Wolfurt",
        )

        match = lis_matching.find_matching_incident(
            db, ORG_ID,
            alarm_type_code="T1",
            street="KELLHOFSTRAßE",
            city="WOLFURT",
            started_at=incident.started_at,
        )
        assert match is not None
        assert match.id == incident.id
    finally:
        db.rollback()
        db.close()


# ── Fallback: Teilstring-Match, wenn strukturierte Adresse auf einer Seite fehlt ──

def test_find_matching_incident_fallback_street_in_report_text():
    """Reproduziert Prod-Vorfall 2026-07-14 (Bregenz F4): der Pager-Text-Parser des
    seriellen Gateways lieferte wegen des Ortsteil-Präfixes 'vorkloster' leere
    Adressfelder — die LIS-Operation kam mit korrekter Adresse, matchte über den
    Adress-Weg (2) aber nicht. Der Straßenname aus der LIS-Adresse muss den
    bereits vorhandenen, adresslosen seriellen Einsatz über dessen report_text
    finden."""
    db = _session()
    try:
        seriell = _make_incident(
            db, alarm_type_code="F4", address_street="", address_no="", address_city="",
            report_text="bregenz vorkloster untere burggräflergasse 14 kellerbrand personen eingeschlossen",
            started_at=datetime(2026, 7, 14, 11, 47, 22, tzinfo=UTC),
        )

        match = lis_matching.find_matching_incident(
            db, ORG_ID,
            alarm_type_code="F4",
            street="UNTERE BURGGRÄFLERGASSE",
            city="BREGENZ",
            started_at=datetime(2026, 7, 14, 11, 25, 28, tzinfo=UTC),
            report_text="[Kellerbrand] Personen eingeschlossen",
        )
        assert match is not None
        assert match.id == seriell.id
    finally:
        db.rollback()
        db.close()


def test_find_matching_incident_fallback_reverse_direction():
    """Umgekehrte Reihenfolge (Lauterach F11, wie im selben Vorfall beobachtet): der
    serielle Alarm kommt zuerst (ohne Adresse), das LIS-Sync danach — muss den
    bereits über report_text bekannten Straßennamen im eigenen freien Meldungstext
    des seriellen Einsatzes wiederfinden."""
    db = _session()
    try:
        lis_incident = _make_incident(
            db, alarm_type_code="F11", address_street="FELLENTORSTRASSE", address_no="20",
            address_city="LAUTERACH", reason="[Patientenbergung] [Bewusstlos] RTW und Notarzt vor Ort",
            started_at=datetime(2026, 7, 14, 13, 2, 49, tzinfo=UTC),
        )

        match = lis_matching.find_matching_incident(
            db, ORG_ID,
            alarm_type_code="F11",
            street="",
            city="",
            started_at=datetime(2026, 7, 14, 13, 3, 55, tzinfo=UTC),
            report_text=(
                "_drehleiter r2 lauterach fellentorstrasse 20 patientenbergung "
                "bewusstlos rtw und notarzt vor ort"
            ),
        )
        assert match is not None
        assert match.id == lis_incident.id
    finally:
        db.rollback()
        db.close()


def test_find_matching_incident_fallback_ignores_short_street_names():
    """Sehr kurze Straßenfragmente dürfen nicht als Fallback-Treffer zählen (sonst
    steigt das Risiko generischer Fehltreffer in freiem Text)."""
    db = _session()
    try:
        _make_incident(
            db, alarm_type_code="F4", address_street="", address_city="",
            reason="irgendein rohtext ohne verwertbare adresse",
        )

        match = lis_matching.find_matching_incident(
            db, ORG_ID,
            alarm_type_code="F4",
            street="Au",  # < _MIN_FALLBACK_STREET_LEN
            city="",
            started_at=None,
            report_text="irgendein rohtext ohne verwertbare adresse",
        )
        assert match is None
    finally:
        db.rollback()
        db.close()


def test_find_matching_incident_keeps_simultaneous_same_type_incidents_separate():
    """Sturmtag-Regression: mehrere echte, unterschiedliche Einsätze mit demselben
    Stichwort (T9) innerhalb weniger Minuten dürfen NICHT zusammengeführt werden,
    nur weil beide denselben Alarmtyp haben — sie haben beidseitig eine
    strukturierte Adresse und dürfen daher nie im Fallback landen, selbst wenn
    (rein hypothetisch) ein Straßenname im Meldungstext des anderen vorkäme
    (reproduziert 2026-07-14: 8 echte T9-Einsätze in Wolfurt binnen 11 Minuten
    blieben zu Recht getrennt)."""
    db = _session()
    try:
        _make_incident(
            db, alarm_type_code="T9", address_street="Bahnweg", address_no="16",
            address_city="Wolfurt", report_text="wolfurt bahnweg 16 strasse überflutet",
            started_at=datetime(2026, 7, 14, 11, 28, 0, tzinfo=UTC),
        )

        match = lis_matching.find_matching_incident(
            db, ORG_ID,
            alarm_type_code="T9",
            street="Bucher Straße",
            city="Wolfurt",
            started_at=datetime(2026, 7, 14, 11, 26, 35, tzinfo=UTC),
            report_text="wolfurt bucher straße 9a baum auf strasse",
        )
        assert match is None
    finally:
        db.rollback()
        db.close()


def test_find_matching_incident_fallback_never_fires_when_both_sides_have_address():
    """Härtet die neue Sicherheitsschranke direkt: selbst wenn der Straßenname der
    einen Seite (konstruiert, adversarial) im Meldungstext der anderen vorkäme,
    darf kein Match entstehen, solange beide Seiten eine strukturierte Adresse
    haben — der Fallback ist strikt auf 'mindestens eine Seite ohne Adresse'
    beschränkt."""
    db = _session()
    try:
        _make_incident(
            db, alarm_type_code="T9", address_street="Achstraße", address_city="Wolfurt",
            report_text="wolfurt achstrasse: baum auf bahnweg gefallen",  # enthaelt "bahnweg"
            started_at=datetime(2026, 7, 14, 11, 28, 0, tzinfo=UTC),
        )

        match = lis_matching.find_matching_incident(
            db, ORG_ID,
            alarm_type_code="T9",
            street="Bahnweg",
            city="Wolfurt",
            started_at=datetime(2026, 7, 14, 11, 28, 0, tzinfo=UTC),
            report_text="wolfurt bahnweg 16 strasse überflutet",
        )
        assert match is None
    finally:
        db.rollback()
        db.close()


# ── LIS-first: Einsatz kommt zuerst über LIS, API liefert später "denselben" ──

def test_lis_created_incident_can_later_be_linked_via_api_matching():
    """Simuliert: LIS legt Einsatz an (kein external_key, aber lis_operation_id gesetzt).
    Später liefert die API denselben Einsatz — find_matching_incident (wie in
    api_v1.create_incident_api verwendet) muss ihn finden, damit KEIN Duplikat entsteht."""
    db = _session()
    try:
        lis_incident = _make_incident(
            db, external_key=None, lis_operation_id="lis-op-999",
            reason="Verkehrsunfall", address_street="Bundesstraße", address_no="1",
            address_city="Wolfurt", alarm_type_code="T4",
        )

        match = lis_matching.find_matching_incident(
            db, ORG_ID,
            alarm_type_code="T4",
            street="Bundesstraße",
            city="Wolfurt",
            started_at=lis_incident.started_at,
        )
        assert match is not None
        assert match.id == lis_incident.id
        assert match.lis_operation_id == "lis-op-999"
        assert match.external_key is None  # noch nicht verknüpft — das übernimmt der Aufrufer
    finally:
        db.rollback()
        db.close()
