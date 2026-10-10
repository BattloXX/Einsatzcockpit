"""Gezielte Regressionstests für GK-Zugangseinstellungen."""

import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import LageEinheit, LageEinheitLeader, LageEinheitZugang
from app.models.master import FireDept, OrgSettings
from app.models.user import Role, User, UserRole
from app.services import gk_zugang_service as service


def _anmelden(client, username: str) -> None:
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        "/login", data={"username": username, "password": "Test1234!", "_csrf": csrf}, follow_redirects=False
    )
    assert response.status_code == 302


def _org_mit_admin(suffix: str, rolle: str = "admin") -> tuple[int, str]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"gk-settings-{suffix}", name="GK Settings", color="#123456", bos="Feuerwehr")
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id))
        username = f"gk-settings-{suffix}"
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name=username,
            org_id=org.id,
            active=True,
        )
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == rolle).one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        return org.id, username
    finally:
        db.close()


def _daten(**extra) -> dict[str, str]:
    data = {
        "gk_zugang_gueltigkeit_stunden": "48",
        "gk_sitzung_stunden": "12",
        "gk_zugang_max_sitzungen": "2",
        "gk_zugang_nachricht": "GSL {lage} {einheit} {gruppenkommandant} {link}",
    }
    data.update(extra)
    return data


def _zugang(db, org_id: int, label: str):
    from app.models.major_incident import MajorIncident

    lage = MajorIncident(org_id=org_id, name=f"Lage {label}", status="active")
    db.add(lage)
    db.flush()
    einheit = LageEinheit(lage_id=lage.id, label=label, status="bereitgestellt", resource_type="fahrzeug")
    db.add(einheit)
    db.flush()
    leader = LageEinheitLeader(
        einheit_id=einheit.id,
        person_name="Max Muster",
        phone_e164="+436641234567",
        phone_version=1,
        start_at=service._now(),
        rolle="fuehrer",
    )
    db.add(leader)
    db.flush()
    einheit.leader_assignment_id = leader.id
    db.query(OrgSettings).filter_by(org_id=org_id).one().gk_zugang_aktiv = True
    access = service.stelle_zugang_aus(db, lage, einheit, user_id=None, grund="test")
    return einheit.id, access


def test_speichern_lesen_defaults_und_template(client, setup_db):
    suffix = uuid4().hex[:10]
    org_id, username = _org_mit_admin(suffix)
    _anmelden(client, username)
    page = client.get("/admin/gsl-einstellungen")
    assert page.status_code == 200
    assert "Gruppenkommandanten-Zugang" in page.text
    assert "{gruppenkommandant}" in page.text
    assert "Standard wiederherstellen" in page.text
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        defaults = db.query(OrgSettings).filter_by(org_id=org_id).one()
        assert not defaults.gk_zugang_aktiv
        assert not defaults.gk_zugang_auto_sms
        assert not defaults.gk_zugang_sms_pin
        assert not defaults.gk_zugang_ressource_pflegen
    finally:
        db.close()
    csrf = client.cookies.get("ec_csrf")
    data = _daten(
        _csrf=csrf,
        gk_zugang_aktiv="1",
        gk_zugang_auto_sms="1",
        gk_zugang_gueltigkeit_stunden="72",
        gk_sitzung_stunden="24",
        gk_zugang_max_sitzungen="3",
        gk_zugang_sms_pin="1",
        gk_zugang_ressource_pflegen="1",
    )
    assert client.post("/admin/gsl-einstellungen", data=data).status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cfg = db.query(OrgSettings).filter_by(org_id=org_id).one()
        assert cfg.gk_zugang_aktiv and cfg.gk_zugang_auto_sms and cfg.gk_zugang_sms_pin
        assert cfg.gk_zugang_ressource_pflegen and cfg.gk_zugang_gueltigkeit_stunden == 72
        assert (cfg.gk_sitzung_stunden, cfg.gk_zugang_max_sitzungen) == (24, 3)
        assert cfg.gk_zugang_nachricht == data["gk_zugang_nachricht"]
    finally:
        db.close()


@pytest.mark.parametrize(
    "daten",
    [
        {"gk_zugang_gueltigkeit_stunden": "0"},
        {"gk_sitzung_stunden": "73"},
        {"gk_zugang_max_sitzungen": "6"},
        {"gk_sitzung_stunden": "49"},
        {"gk_zugang_nachricht": "{lage} {link} {fremd}"},
        {"gk_zugang_nachricht": "{lage}"},
        {"gk_zugang_nachricht": "x" * 481 + "{link}"},
    ],
)
def test_validierung(client, setup_db, daten):
    suffix = uuid4().hex[:10]
    _, username = _org_mit_admin(suffix)
    _anmelden(client, username)
    payload = _daten(_csrf=client.cookies.get("ec_csrf"), **daten)
    assert client.post("/admin/gsl-einstellungen", data=payload).status_code == 422


def test_ausschalten_und_notbremse_sind_organisationsweit_aber_gescopt(client, setup_db):
    suffix = uuid4().hex[:10]
    org_a, username = _org_mit_admin(f"a-{suffix}")
    org_b, _ = _org_mit_admin(f"b-{suffix}")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        _, issued_a = _zugang(db, org_a, "A")
        _, issued_b = _zugang(db, org_b, "B")
        cookie, _ = service.sitzung_anlegen(
            db, db.get(LageEinheitZugang, issued_a.zugang_id), user_agent=None, ip=None, verifiziert=True
        )
        db.commit()
    finally:
        db.close()
    _anmelden(client, username)
    csrf = client.cookies.get("ec_csrf")
    data = _daten(_csrf=csrf, gk_zugang_aktiv="")
    assert client.post("/admin/gsl-einstellungen", data=data).status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert service.token_pruefen(db, issued_a.link.rsplit("#", 1)[1]).zustand == "beendet"
        assert service.sitzung_pruefen(db, cookie) is None
        db.query(OrgSettings).filter_by(org_id=org_a).one().gk_zugang_aktiv = True
        db.commit()
    finally:
        db.close()
    response = client.post(
        "/admin/gsl-einstellungen/zugaenge-widerrufen", data={"_csrf": csrf}, follow_redirects=False
    )
    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(LageEinheitZugang, issued_a.zugang_id).status == "widerrufen"
        assert service.token_pruefen(db, issued_b.link.rsplit("#", 1)[1]).zustand == "ok"
    finally:
        db.close()


def test_nicht_admin_erhaelt_403(client, setup_db):
    _, username = _org_mit_admin(uuid4().hex[:10], rolle="recorder")
    _anmelden(client, username)
    assert client.post(
        "/admin/gsl-einstellungen/zugaenge-widerrufen", data={"_csrf": client.cookies.get("ec_csrf")}
    ).status_code == 403


def test_gk_einstellungen_javascript_ist_pruefbar():
    path = Path("app/static/js/gk_einstellungen.js")
    assert all(len(line) <= 160 for line in path.read_text().splitlines())
    if shutil.which("node") is None:
        pytest.skip("node nicht verfügbar")
    result = subprocess.run(["node", "--check", str(path)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
