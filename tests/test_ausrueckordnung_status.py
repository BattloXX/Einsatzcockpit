"""ECP-04: Ausrückordnung der Einsatzinformation zeigt den Fahrzeugstatus farbig und mit Bezeichnung."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentVehicle
from app.models.master import AlarmDispatchVehicle, AlarmType, FireDept, VehicleMaster
from app.models.user import Role, User, UserRole
from app.services.incident_service import _create_fixed_columns, set_unit_status


def _login(client, username, password="Test1234!"):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _daten():
    with SessionLocal() as db:
        set_tenant_context(db, None)
        kennung = uuid4().hex[:8]
        org = FireDept(slug=f"auso-{kennung}", name="Ausrueck", color="#123456", bos="Feuerwehr")
        db.add(org)
        db.flush()
        rolle = db.query(Role).filter(Role.code == "readonly").first() or Role(code="readonly", name="readonly")
        db.add(rolle)
        db.flush()
        user = User(username=f"auso-{kennung}", password_hash=hash_password("Test1234!"),
                    display_name="Lese", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=rolle.id))
        alarm = AlarmType(org_id=org.id, code="B2", label="Brand", category="B")
        db.add(alarm)
        db.flush()
        fahrzeuge = {}
        for i, code in enumerate(("TLF", "RLF", "KDO", "MTF")):
            fz = VehicleMaster(dept_id=org.id, code=code, name=f"{code} Name", type=code)
            db.add(fz)
            db.flush()
            db.add(AlarmDispatchVehicle(alarm_type_id=alarm.id, vehicle_master_id=fz.id, display_order=i))
            fahrzeuge[code] = fz.id
        incident = Incident(primary_org_id=org.id, alarm_type_code="B2", status="active",
                            started_at=datetime.now(UTC))
        db.add(incident)
        db.flush()
        spalten = _create_fixed_columns(db, incident)
        aktiv = next(c for c in incident.columns if c.code == "active")
        zuordnung = {}
        for code, status in (("TLF", "Einsatzbereit"), ("RLF", "Einsatz übernommen"), ("KDO", "Am Einsatzort")):
            iv = IncidentVehicle(incident_id=incident.id, column_id=aktiv.id, vehicle_master_id=fahrzeuge[code],
                                 unit_status="Einsatz übernommen")
            db.add(iv)
            db.flush()
            set_unit_status(db, iv, status)
            zuordnung[code] = iv.id
        db.commit()
        del spalten
        return user.username, incident.id, zuordnung


def test_ausrueckordnung_zeigt_status_farbe_und_bezeichnung(client):
    username, incident_id, _ = _daten()
    _login(client, username)
    html = client.get(f"/einsatz/{incident_id}/info").text
    assert 'id="ausrueckordnung"' in html
    for code, klasse, label in (
        ("TLF", "unit-status-einsatzbereit", "Einsatzbereit"),
        ("RLF", "unit-status-einsatz-uebernommen", "Auf Anfahrt"),
        ("KDO", "unit-status-am-einsatzort", "Am Einsatzort"),
    ):
        chip = html.split(f"<strong>{code}</strong>")[0].rsplit("<span", 1)[1]
        assert klasse in chip, code
        rest = html.split(f"<strong>{code}</strong>")[1].split("</span>\n  ")[0]
        assert label in rest, code
    # nicht im Einsatz: neutral ohne Statusfarbe, mit Text
    chip_mtf = html.split("<strong>MTF</strong>")[0].rsplit("<span", 1)[1]
    assert "badge-pill--gray" in chip_mtf and "unit-status-" not in chip_mtf
    assert "Kein Status" in html


def test_partial_aktualisiert_sich_nach_statuswechsel_und_zeigt_zeit(client):
    username, incident_id, zuordnung = _daten()
    _login(client, username)
    vorher = client.get(f"/einsatz/{incident_id}/ausrueckordnung").text
    assert "seit " in vorher  # Zeitpunkt der letzten Statusaenderung
    with SessionLocal() as db:
        set_tenant_context(db, None)
        iv = db.get(IncidentVehicle, zuordnung["TLF"])
        set_unit_status(db, iv, "Am Einsatzort")
        db.commit()
    nachher = client.get(f"/einsatz/{incident_id}/ausrueckordnung").text
    chip = nachher.split("<strong>TLF</strong>")[0].rsplit("<span", 1)[1]
    assert "unit-status-am-einsatzort" in chip and "unit-status-einsatzbereit" not in chip


def test_unbekannter_status_bleibt_neutral(client):
    username, incident_id, zuordnung = _daten()
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.get(IncidentVehicle, zuordnung["TLF"]).unit_status = "Unbekannt-X"
        db.commit()
    _login(client, username)
    html = client.get(f"/einsatz/{incident_id}/ausrueckordnung").text
    chip = html.split("<strong>TLF</strong>")[0].rsplit("<span", 1)[1]
    assert "badge-pill--gray" in chip and "unit-status-" not in chip
    assert "Unbekannt-X" in html


def test_ausrueckordnung_partial_braucht_login_und_zugriff(client):
    _, incident_id, _ = _daten()
    client.cookies.clear()
    assert client.get(f"/einsatz/{incident_id}/ausrueckordnung").status_code in (401, 302, 303)
    username2, _, _ = _daten()  # andere Org
    _login(client, username2)
    assert client.get(f"/einsatz/{incident_id}/ausrueckordnung").status_code in (403, 404)
