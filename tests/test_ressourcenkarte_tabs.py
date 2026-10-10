"""HTTP-Abdeckung der Einsatz- und Journal-Tabs der Ressourcenkarte."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import (
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    LageJournalEntry,
    MajorIncident,
    SitePriority,
)
from app.models.master import FireDept
from app.models.user import Role, User, UserRole


def _daten(role_code: str = "recorder") -> tuple[str, int, int, int, int]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        suffix = uuid4().hex[:8]
        org = FireDept(slug="tabs-org-" + suffix, name="Tabs", color="#f00", bos="FW")
        db.add(org)
        db.flush()
        user = User(
            username="tabs-" + suffix, password_hash=hash_password("Test1234!"), display_name="Test",
            org_id=org.id, active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter(Role.code == role_code).one().id))
        lage = MajorIncident(org_id=org.id, name="Lage")
        db.add(lage)
        db.flush()
        einheit = LageEinheit(lage_id=lage.id, label="RLF", status="bereitgestellt")
        andere = LageEinheit(lage_id=lage.id, label="TLF", status="bereitgestellt")
        site = IncidentSite(
            major_incident_id=lage.id, org_id=org.id, bezeichnung="Hauptstraße", priority=SitePriority.dringend
        )
        db.add_all((einheit, andere, site))
        db.flush()
        now = datetime.now(UTC).replace(tzinfo=None)
        db.add_all((
            EinheitSiteDispatch(einheit_id=einheit.id, site_id=site.id, dispatched_at=now - timedelta(hours=2)),
            EinheitSiteDispatch(
                einheit_id=einheit.id, site_id=site.id, dispatched_at=now - timedelta(hours=3),
                withdrawn_at=now - timedelta(hours=1), withdrawn_grund="Andere Aufgabe", withdrawn_author="EL",
            ),
            LageJournalEntry(
                major_incident_id=lage.id, einheit_id=einheit.id, site_id=site.id, category="ressource_manuell",
                ereignis_typ="manuell", quelle="manuell", text="Eintrag", ts=now,
            ),
        ))
        db.commit()
        return user.username, lage.id, einheit.id, andere.id, site.id
    finally:
        db.close()


def _login(client, username: str) -> None:
    client.get("/login")
    response = client.post(
        "/login", data={"username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")}
    )
    assert response.status_code == 200


def test_einsaetze_und_journal_filter_paging(client, setup_db):
    username, lage_id, einheit_id, _, _ = _daten()
    _login(client, username)
    einsaetze = client.get(f"/lage/{lage_id}/einheiten/{einheit_id}/karte/einsaetze")
    assert einsaetze.status_code == 200
    assert all(
        text in einsaetze.text
        for text in ("Aktueller Einsatz", "Weitere Aufträge", "Abgeschlossene", "Zurückgezogene", "Andere Aufgabe")
    )
    journal = client.get(f"/lage/{lage_id}/einheiten/{einheit_id}/karte/journal?typen=manuell&limit=1")
    assert journal.status_code == 200
    assert "Eintrag" in journal.text and "Manuell" in journal.text


def test_journal_post_storno_und_broadcast(client, setup_db, monkeypatch):
    import app.routers.ui_ressourcenkarte as router

    username, lage_id, einheit_id, _, site_id = _daten()
    _login(client, username)
    events = []

    async def fake_broadcast(lage, event):
        events.append((lage, event))

    monkeypatch.setattr(router, "broadcast_lage", fake_broadcast)
    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        f"/lage/{lage_id}/einheiten/{einheit_id}/journal",
        data={"_csrf": csrf, "text": "Neu", "site_id": site_id},
    )
    assert response.status_code == 200
    assert events[-1] == (lage_id, {"type": "ressource:changed", "einheit_id": einheit_id})
    db = SessionLocal()
    try:
        entry = (
            db.query(LageJournalEntry)
            .filter(LageJournalEntry.einheit_id == einheit_id, LageJournalEntry.text == "Neu")
            .one()
        )
        entry_id = entry.id
    finally:
        db.close()
    missing = client.post(
        f"/lage/{lage_id}/einheiten/{einheit_id}/journal/{entry_id}/storno", data={"_csrf": csrf, "grund": ""}
    )
    assert missing.status_code == 422
    ok = client.post(
        f"/lage/{lage_id}/einheiten/{einheit_id}/journal/{entry_id}/storno", data={"_csrf": csrf, "grund": "Korrektur"}
    )
    assert ok.status_code == 200
    duplicate = client.post(
        f"/lage/{lage_id}/einheiten/{einheit_id}/journal/{entry_id}/storno", data={"_csrf": csrf, "grund": "Nochmals"}
    )
    assert duplicate.status_code == 422


def test_journal_readonly(client, setup_db):
    username, lage_id, einheit_id, _, _ = _daten("readonly")
    _login(client, username)
    csrf = client.cookies.get("ec_csrf")
    denied = client.post(f"/lage/{lage_id}/einheiten/{einheit_id}/journal", data={"_csrf": csrf, "text": "Nein"})
    assert denied.status_code == 403


def test_journal_andere_einheit_nicht_stornierbar(client, setup_db):
    username, lage_id, einheit_id, andere_id, _ = _daten()
    _login(client, username)
    db = SessionLocal()
    try:
        entry_id = db.query(LageJournalEntry.id).filter(LageJournalEntry.einheit_id == einheit_id).scalar()
    finally:
        db.close()
    foreign = client.post(
        f"/lage/{lage_id}/einheiten/{andere_id}/journal/{entry_id}/storno",
        data={"_csrf": client.cookies.get("ec_csrf"), "grund": "Nein"},
    )
    assert foreign.status_code == 422


def test_journal_fremde_org_ist_404(client, setup_db):
    username, _, _, _, _ = _daten()
    _, lage_id, einheit_id, _, _ = _daten()
    _login(client, username)
    response = client.get(f"/lage/{lage_id}/einheiten/{einheit_id}/karte/journal")
    assert response.status_code == 404
