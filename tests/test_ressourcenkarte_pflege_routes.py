"""HTTP-Abdeckung fuer die Personal- und Ausstattungstabs der Ressourcenkarte."""

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import LageEinheit
from tests.test_ressourcenkarte_routes import _daten, _login


def _zweite_einheit(lage_id: int) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        einheit = LageEinheit(lage_id=lage_id, label="TLF", status="bereitgestellt")
        db.add(einheit)
        db.commit()
        return einheit.id
    finally:
        db.close()


def _basis(lage_id: int, einheit_id: int) -> str:
    return f"/lage/{lage_id}/einheiten/{einheit_id}"


def test_tabs_sind_readonly_lesbar_und_enthalten_keine_post_formulare(client, setup_db):
    username, lage_id, einheit_id, _ = _daten("readonly")
    _login(client, username)
    for tab in ("personal", "ausstattung"):
        response = client.get(_basis(lage_id, einheit_id) + f"/karte/{tab}")
        assert response.status_code == 200
        assert "hx-post=" not in response.text


def test_personal_setzen_modus_verstaerken_und_umbuchen_broadcastet_beide(client, setup_db, monkeypatch):
    import app.routers.ui_ressourcenkarte as router

    username, lage_id, einheit_id, member_id = _daten()
    ziel_id = _zweite_einheit(lage_id)
    _login(client, username)
    events = []

    async def fake_broadcast(lage, event):
        events.append((lage, event))

    monkeypatch.setattr(router, "broadcast_lage", fake_broadcast)
    csrf = client.cookies.get("ec_csrf")
    basis = _basis(lage_id, einheit_id)
    assert (
        client.post(
            basis + "/personal/setzen", data={"_csrf": csrf, "gesamt": 4, "fuehrung": 1, "agt": 1, "sanitaeter": 1}
        ).status_code
        == 200
    )
    assert client.post(basis + "/personal/verstaerken", data={"_csrf": csrf, "anzahl": 2}).status_code == 200
    assert (
        client.post(
            basis + "/personal/umbuchen", data={"_csrf": csrf, "nach_einheit_id": ziel_id, "anzahl": 1}
        ).status_code
        == 200
    )
    assert {event["einheit_id"] for _, event in events} == {einheit_id, ziel_id}
    assert client.post(basis + "/personal/modus", data={"_csrf": csrf, "modus": "liste"}).status_code == 200
    assert (
        client.post(
            basis + "/personal/hinzufuegen", data={"_csrf": csrf, "member_id": member_id, "funktion": "mannschaft"}
        ).status_code
        == 200
    )
    duplicate = client.post(
        basis + "/personal/hinzufuegen", data={"_csrf": csrf, "member_id": member_id, "funktion": "mannschaft"}
    )
    assert duplicate.status_code == 422 and duplicate.headers["HX-Retarget"] == "#personalFehler"


def test_ausstattung_hinzufuegen_aendern_umbuchen_entfernen(client, setup_db, monkeypatch):
    import app.routers.ui_ressourcenkarte as router
    from app.models.major_incident import LageEinheitAusstattung

    username, lage_id, einheit_id, _ = _daten()
    ziel_id = _zweite_einheit(lage_id)
    _login(client, username)
    events = []

    async def fake_broadcast(lage, event):
        events.append(event)

    monkeypatch.setattr(router, "broadcast_lage", fake_broadcast)
    csrf = client.cookies.get("ec_csrf")
    basis = _basis(lage_id, einheit_id)
    assert (
        client.post(
            basis + "/ausstattung/hinzufuegen", data={"_csrf": csrf, "kategorie": "tauchpumpe", "menge": 2}
        ).status_code
        == 200
    )
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        zeile_id = db.query(LageEinheitAusstattung.id).filter_by(einheit_id=einheit_id).scalar()
    finally:
        db.close()
    assert (
        client.post(
            basis + f"/ausstattung/{zeile_id}/aendern",
            data={"_csrf": csrf, "menge": 2, "status": "defekt", "bemerkung": "Test"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            basis + f"/ausstattung/{zeile_id}/umbuchen", data={"_csrf": csrf, "nach_einheit_id": ziel_id, "menge": 1}
        ).status_code
        == 200
    )
    assert client.post(basis + f"/ausstattung/{zeile_id}/entfernen", data={"_csrf": csrf}).status_code == 200
    assert {event["einheit_id"] for event in events} == {einheit_id, ziel_id}


def test_pflege_post_ist_fuer_readonly_verboten(client, setup_db):
    username, lage_id, einheit_id, _ = _daten("readonly")
    _login(client, username)
    response = client.post(
        _basis(lage_id, einheit_id) + "/personal/setzen", data={"_csrf": client.cookies.get("ec_csrf"), "gesamt": 1}
    )
    assert response.status_code == 403
