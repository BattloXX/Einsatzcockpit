"""GSL-04: Buergerportal-Link nur nach Bestaetigung erneuern, protokolliert."""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import LageJournalEntry, MajorIncident
from app.models.master import FireDept
from app.models.user import AuditLog, Role, User, UserRole


def _login(client, username, password="Test1234!"):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


@pytest.fixture()
def lage_mit_link():
    with SessionLocal() as db:
        set_tenant_context(db, None)
        kennung = uuid4().hex[:8]
        org = FireDept(slug=f"portal-{kennung}", name="Portal", color="#123456", bos="Feuerwehr")
        db.add(org)
        db.flush()
        rolle = db.query(Role).filter(Role.code == "incident_leader").first() or Role(
            code="incident_leader", name="incident_leader")
        db.add(rolle)
        db.flush()
        leiter = User(username=f"pl-{kennung}", password_hash=hash_password("Test1234!"),
                      display_name="Leiter", org_id=org.id, active=True)
        db.add(leiter)
        db.flush()
        db.add(UserRole(user_id=leiter.id, role_id=rolle.id))
        lage = MajorIncident(org_id=org.id, name="Lage Portal", public_token="alter-token-" + kennung)
        ohne = MajorIncident(org_id=org.id, name="Lage ohne Link")
        db.add_all([lage, ohne])
        db.commit()
        return {"user": leiter.username, "lage": lage.id, "ohne": ohne.id, "alt": lage.public_token, "org": org.id}


def _token(lage_id):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        return db.get(MajorIncident, lage_id).public_token


def _audit(aktion, lage_id):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        return [a for a in db.query(AuditLog).filter(AuditLog.action == aktion).all()
                if (a.payload_json or "").find(f'"lage_id": {lage_id}') >= 0]


def test_erneuern_ohne_bestaetigung_aendert_nichts(lage_mit_link, client):
    d = lage_mit_link
    _login(client, d["user"])
    csrf = client.cookies.get("ec_csrf")
    antwort = client.post(f"/lage/{d['lage']}/meldungen/token", data={"_csrf": csrf}, follow_redirects=False)
    assert antwort.status_code == 303 and "token=bestaetigung" in antwort.headers["location"]
    assert _token(d["lage"]) == d["alt"]
    assert _audit("major_incident.token_rotated", d["lage"]) == []


def test_erneuern_mit_bestaetigung_ersetzt_link_protokolliert_und_zeigt_neuen_link(lage_mit_link, client):
    d = lage_mit_link
    _login(client, d["user"])
    csrf = client.cookies.get("ec_csrf")
    antwort = client.post(f"/lage/{d['lage']}/meldungen/token", data={"_csrf": csrf, "bestaetigt": "1"},
                          follow_redirects=False)
    assert antwort.status_code == 303 and antwort.headers["location"].endswith("?token=erneuert")
    neu = _token(d["lage"])
    assert neu and neu != d["alt"] and len(neu) >= 40
    (eintrag,) = _audit("major_incident.token_rotated", d["lage"])
    assert eintrag.org_id == d["org"]
    with SessionLocal() as db:
        set_tenant_context(db, None)
        journal = db.query(LageJournalEntry).filter(LageJournalEntry.major_incident_id == d["lage"]).all()
        assert any("Bürgerportal-Link erneuert" in e.text for e in journal)
    seite = client.get(antwort.headers["location"])
    assert f"/melden/{neu}" in seite.text and "wurde erneuert" in seite.text
    assert d["alt"] not in seite.text


def test_seite_zeigt_bestaetigungsdialog_statt_direktem_erneuern(lage_mit_link, client):
    d = lage_mit_link
    _login(client, d["user"])
    html = client.get(f"/lage/{d['lage']}/meldungen").text
    assert 'id="token-erneuern-modal"' in html and "sofort ungültig" in html
    # der Knopf auf der Seite oeffnet nur den Dialog; abgeschickt wird erst im Dialog mit bestaetigt=1
    assert 'data-ec-dialog="token-erneuern-modal"' in html
    assert 'name="bestaetigt" value="1"' in html and "data-token-bestaetigen" in html


def test_erstes_erstellen_braucht_keine_bestaetigung(lage_mit_link, client):
    d = lage_mit_link
    _login(client, d["user"])
    csrf = client.cookies.get("ec_csrf")
    antwort = client.post(f"/lage/{d['ohne']}/meldungen/token", data={"_csrf": csrf}, follow_redirects=False)
    assert antwort.headers["location"].endswith("?token=erstellt")
    assert _token(d["ohne"])
    assert len(_audit("major_incident.token_created", d["ohne"])) == 1
