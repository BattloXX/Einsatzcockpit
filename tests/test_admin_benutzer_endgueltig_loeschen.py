"""Endgültiges Löschen eines Benutzers mit Verweisen aus beliebigen Tabellen.

Regression 2026-10-11: /admin/benutzer/{id}/endgueltig-loeschen endete mit
"Internal Server Error", weil nur eine handgepflegte Auswahl der Fremdschlüssel
auf user.id geleert wurde.
"""
from __future__ import annotations

from app.core.security import hash_api_key
from app.models.major_incident import IncidentSite, LageToken, MajorIncident, MajorIncidentStatus, SiteLogEntry
from app.models.master import FireDept
from app.models.user import User, UserRole
from tests.test_admin_rollenrechte import _login, _session, _user


def test_endgueltig_loeschen_leert_und_entfernt_alle_verweise(client):
    db = _session()
    try:
        org = db.query(FireDept).first()
        assert org is not None
        _user(db, "hard_delete_actor", org.id, "admin")
        target = _user(db, "hard_delete_target", org.id, "readonly")
        db.flush()
        # Verweise aus Tabellen, die die alte, handgepflegte Liste nicht kannte.
        lage = MajorIncident(org_id=org.id, name="Lage", status=MajorIncidentStatus.active,
                             started_by_user_id=target.id)
        db.add(lage)
        db.flush()
        site = IncidentSite(major_incident_id=lage.id, org_id=org.id, bezeichnung="Stelle", created_by=target.id)
        db.add(site)
        db.flush()
        db.add(SiteLogEntry(incident_site_id=site.id, kind="note", text="Notiz", user_id=target.id))
        # Pflicht-Verweis ohne ON DELETE: vom User ausgestellter Lage-Token.
        db.add(LageToken(lage_id=lage.id, token_hash=hash_api_key("hard-delete-token"),
                         issued_by_user_id=target.id))
        db.commit()
        target_id, lage_id, site_id = target.id, lage.id, site.id
    finally:
        db.close()

    _login(client, "hard_delete_actor")
    response = client.post(f"/admin/benutzer/{target_id}/endgueltig-loeschen",
                           data={"_csrf": client.cookies.get("ec_csrf")}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/benutzer?deleted=1"

    db = _session()
    try:
        assert db.get(User, target_id) is None
        assert db.query(UserRole).filter(UserRole.user_id == target_id).count() == 0
        lage = db.get(MajorIncident, lage_id)
        assert lage is not None and lage.started_by_user_id is None
        site = db.get(IncidentSite, site_id)
        assert site is not None and site.created_by is None
        log = db.query(SiteLogEntry).filter(SiteLogEntry.incident_site_id == site_id).one()
        assert log.user_id is None and log.text == "Notiz"
        assert db.query(LageToken).filter(LageToken.lage_id == lage_id).count() == 0
    finally:
        db.close()
