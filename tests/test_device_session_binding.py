"""Device-Session-Bindung an device_token_id (Audit A4 / SEC-5-Rest).

Neue Device-Cookies tragen die device_token_id: Der Widerruf GENAU dieses
Geräts beendet die Session sofort — auch wenn der User weitere aktive Geräte
hat. Bestandscookies ohne Token-Bezug fallen auf die grobkörnige Prüfung
("hat noch irgendein aktives Gerät") zurück.
"""
from datetime import UTC, datetime

from app.core.security import hash_api_key, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import DeviceToken, User

ORG_ID = 1  # FF Wolfurt (seeded)


def _make_user_with_devices(username: str) -> tuple[int, dict[str, int], dict[str, str]]:
    """Legt einen aktiven User mit zwei Geräte-Tokens an.

    Returns (user_id, {label: token_id}, {label: raw_token}).
    """
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            from app.core.security import hash_password
            user = User(username=username, password_hash=hash_password("Test1234!"),
                        display_name="Geräte-Tester", org_id=ORG_ID, active=True)
            db.add(user)
            db.flush()
        raw = {}
        ids = {}
        for label in ("Tablet A", "Tablet B"):
            raw_token = f"devtok-{username}-{label.replace(' ', '')}"
            dt = DeviceToken(label=label, token_hash=hash_api_key(raw_token), user_id=user.id)
            db.add(dt)
            db.flush()
            raw[label] = raw_token
            ids[label] = dt.id
        db.commit()
        return user.id, ids, raw
    finally:
        db.close()


def _revoke(token_id: int) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dt = db.get(DeviceToken, token_id)
        dt.revoked_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()


def _ist_eingeloggt(client, cookie: str) -> bool:
    client.cookies.clear()
    client.cookies.set("session", cookie)
    # /profil erfordert Login ("/" ist die öffentliche Landing-Page!):
    # 200 = Session gültig, 302→/login = Session ungültig.
    r = client.get("/profil", follow_redirects=False)
    return r.status_code == 200


def test_widerruf_trifft_nur_das_eigene_geraet(client):
    _user_id, ids, raw = _make_user_with_devices("device_bind_user")

    ra = client.get(f"/geraet-login?token={raw['Tablet A']}", follow_redirects=False)
    assert ra.status_code == 302
    cookie_a = ra.cookies.get("session") or client.cookies.get("session")
    assert cookie_a

    client.cookies.clear()
    rb = client.get(f"/geraet-login?token={raw['Tablet B']}", follow_redirects=False)
    cookie_b = rb.cookies.get("session") or client.cookies.get("session")
    assert cookie_b and cookie_b != cookie_a

    assert _ist_eingeloggt(client, cookie_a)
    assert _ist_eingeloggt(client, cookie_b)

    # Tablet A verloren → Token widerrufen: NUR Cookie A wird ungültig,
    # obwohl Tablet B weiterhin aktiv ist (vor PR 6 blieb A gültig).
    _revoke(ids["Tablet A"])
    assert not _ist_eingeloggt(client, cookie_a)
    assert _ist_eingeloggt(client, cookie_b)


def test_bestandscookie_ohne_tokenbezug_faellt_auf_grobe_pruefung_zurueck(client):
    user_id, ids, _raw = _make_user_with_devices("device_legacy_user")

    # Cookie im Alt-Format (vor PR 6): device=True ohne device_token_id
    legacy_cookie = sign_session(user_id, device=True)
    assert _ist_eingeloggt(client, legacy_cookie)

    # Ein Gerät widerrufen → Alt-Cookie bleibt gültig (irgendein Gerät aktiv)
    _revoke(ids["Tablet A"])
    assert _ist_eingeloggt(client, legacy_cookie)

    # Alle Geräte widerrufen → Alt-Cookie wird ungültig (bisheriges SEC-5-Verhalten)
    _revoke(ids["Tablet B"])
    assert not _ist_eingeloggt(client, legacy_cookie)
