"""Phase 10: Nachbereitung, Abschluss und Startseiten-Kachel."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import Member, OrgSettings
from app.models.probenplanung import ProbeCheckliste, ProbeChecklistItem, ProbeErkenntnis, Probeart
from app.models.teilnahme import Termin
from tests.test_probenplanung_checkliste import ORG_ID, _flags, _login, _probe_anlegen, _probeart, _user


def _setup(client, name: str, *, required: bool = True) -> tuple[str, int]:
    _user(name, "probenverwalter")
    _flags()
    csrf = _login(client, name)
    probeart_id = _probeart(0, f"Phase10 {name}")
    termin_id = _probe_anlegen(client, csrf, probeart_id, f"Probe {name}")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(Probeart, probeart_id).nachbereitung_erforderlich = required
        db.get(Termin, termin_id).status = "durchgefuehrt"
        db.commit()
    finally:
        db.close()
    return csrf, termin_id


def _save(client, csrf: str, termin_id: int, *, text: str = "Auswertung", complete: bool = True):
    return client.post(f"/probenplanung/{termin_id}/nachbereitung", data={
        "_csrf": csrf, "bemerkungen": text,
        "teilnehmer_vollstaendig": "1" if complete else "",
    })


def test_abschlussbedingungen_und_erlaubter_abschluss(client):
    csrf, termin_id = _setup(client, "phase10_abschluss")
    assert client.post(f"/probenplanung/{termin_id}/abschliessen", data={"_csrf": csrf}).status_code == 409
    _save(client, csrf, termin_id, text="", complete=True)
    assert client.post(f"/probenplanung/{termin_id}/abschliessen", data={"_csrf": csrf}).status_code == 409
    _save(client, csrf, termin_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = Incident(primary_org_id=ORG_ID, alarm_type_code="T1", status="active", is_exercise=True)
        db.add(incident)
        db.flush()
        db.get(Termin, termin_id).exercise_incident_id = incident.id
        db.commit()
        incident_id = incident.id
    finally:
        db.close()
    assert client.post(f"/probenplanung/{termin_id}/abschliessen", data={"_csrf": csrf}).status_code == 409
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(Incident, incident_id).status = "closed"
        db.commit()
    finally:
        db.close()
    assert client.post(
        f"/probenplanung/{termin_id}/abschliessen", data={"_csrf": csrf}, follow_redirects=False,
    ).status_code == 303


def test_abschluss_respektiert_statusmatrix_bei_absage(client):
    csrf, termin_id = _setup(client, "phase10_absage")
    _save(client, csrf, termin_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(Termin, termin_id).status = "abgesagt"
        db.commit()
    finally:
        db.close()
    assert client.post(f"/probenplanung/{termin_id}/abschliessen", data={"_csrf": csrf}).status_code == 409
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(Termin, termin_id).status == "abgesagt"
    finally:
        db.close()


def test_erkenntnisse_anlegen_aendern_loeschen(client):
    csrf, termin_id = _setup(client, "phase10_erkenntnis")
    url = f"/probenplanung/{termin_id}/nachbereitung/erkenntnis"
    assert client.post(url, data={"_csrf": csrf, "text": "Alt", "kategorie": "Taktik"}).status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(ProbeErkenntnis).filter_by(termin_id=termin_id).one()
        row_id = row.id
    finally:
        db.close()
    assert client.patch(f"{url}/{row_id}", data={
        "_csrf": csrf, "text": "Neu", "kategorie": "Ausbildung", "massnahme_text": "Üben",
    }).status_code == 200
    assert client.request("DELETE", f"{url}/{row_id}", data={"_csrf": csrf}).status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(ProbeErkenntnis, row_id) is None
    finally:
        db.close()


def test_dashboard_aggregiert_fortschritt_ohne_item_laden(client):
    csrf, termin_id = _setup(client, "phase10_dashboard", required=False)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        termin = db.get(Termin, termin_id)
        termin.probeart.name = "Vollprobe"
        termin.beginn = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=2)
        checklist = ProbeCheckliste(org_id=ORG_ID, termin_id=termin.id, template_name="Test", template_version=1)
        db.add(checklist)
        db.flush()
        db.add_all([
            ProbeChecklistItem(org_id=ORG_ID, checkliste_id=checklist.id, titel="Erledigt", typ="checkbox", zustand="erledigt"),
            ProbeChecklistItem(org_id=ORG_ID, checkliste_id=checklist.id, titel="Überfällig", typ="checkbox", zustand="offen", faellig_am=datetime.now(UTC).date() - timedelta(days=1)),
        ])
        member = Member(org_id=ORG_ID, firstname="Ver", lastname="Antwortlich", active=True)
        db.add(member)
        db.flush()
        termin.verantwortlich_member_id = member.id
        db.commit()
    finally:
        db.close()
    statements: list[str] = []
    def count_items(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "probe_checklist_item" in statement.lower():
            statements.append(statement)
    event.listen(Engine, "before_cursor_execute", count_items)
    try:
        response = client.get("/probenplanung/uebersicht")
    finally:
        event.remove(Engine, "before_cursor_execute", count_items)
    assert response.status_code == 200
    assert "1/2" in response.text and "Überfällige Vorbereitung: 1 Punkte" in response.text
    assert len(statements) == 1 and "sum(" in statements[0].lower()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(Termin, termin_id).probeart.name = "Phase10 dashboard"
        db.commit()
    finally:
        db.close()


def test_dashboard_und_bearbeitung_haben_modul_und_rollenguards(client):
    csrf, termin_id = _setup(client, "phase10_guard")
    _user("phase10_leser", "readonly")
    reader_csrf = _login(client, "phase10_leser")
    assert client.get(f"/probenplanung/{termin_id}/nachbereitung").status_code == 200
    assert client.post(f"/probenplanung/{termin_id}/nachbereitung", data={"_csrf": reader_csrf}).status_code == 403
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(OrgSettings).filter_by(org_id=ORG_ID).one().probenplanung_modul_aktiv = False
        db.commit()
    finally:
        db.close()
    assert client.get("/probenplanung/uebersicht").status_code == 404
