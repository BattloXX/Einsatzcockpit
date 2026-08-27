from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.config import settings
from app.core.multi_account import (
    MAX_ACCOUNTS,
    add_account,
    load_accounts,
    sign_accounts,
)
from app.core.security import hash_api_key, hash_password, sign_session, unsign_session
from app.core.tenant import set_tenant_context
from app.models.master import FireDept
from app.models.user import DeviceToken, FcmToken, User
from tests.conftest import TestingSession


@pytest.fixture
def konten(setup_db):
    db = TestingSession()
    db.expire_on_commit = False
    set_tenant_context(db, None)
    suffix = uuid4().hex[:10]
    org = FireDept(slug=f"mehrfach-{suffix}", name=f"Mehrfachlogin {suffix}")
    db.add(org)
    db.flush()
    users = []
    for index in range(7):
        user = User(
            username=f"mehrfach-{suffix}-{index}",
            display_name=f"Benutzer {index}",
            password_hash=hash_password("Test1234!"),
            active=True,
            org_id=org.id,
        )
        db.add(user)
        users.append(user)
    db.commit()
    result = {"org": org, "users": users}
    db.close()
    return result


def _csrf(client) -> str:
    client.get("/login?add=1")
    return client.cookies.get("ec_csrf") or ""


def _login(client, user: User, *, remember: bool = False):
    data = {
        "username": user.username,
        "password": "Test1234!",
        "_csrf": _csrf(client),
    }
    if remember:
        data["remember"] = "1"
    return client.post("/login", data=data, follow_redirects=False)


def _aktiver_user(client) -> int | None:
    session = unsign_session(client.cookies.get("session") or "")
    return session[0] if session else None


def test_zwei_anmeldungen_stehen_im_signierten_cookie(client, konten):
    first, second = konten["users"][:2]
    assert _login(client, first).status_code == 302
    assert _login(client, second, remember=True).status_code == 302

    accounts = load_accounts(client.cookies.get("ec_accounts"))
    assert {account["u"] for account in accounts} == {first.id, second.id}
    assert next(account for account in accounts if account["u"] == second.id)["r"] is True


def test_wechsel_ausserhalb_der_liste_und_ohne_csrf_wird_abgelehnt(client, konten):
    first, second = konten["users"][:2]
    _login(client, first)

    ohne_csrf = client.post(
        "/benutzer/wechseln", data={"user_id": second.id}, follow_redirects=False,
    )
    assert ohne_csrf.status_code == 403

    response = client.post(
        "/benutzer/wechseln",
        data={"user_id": second.id, "_csrf": _csrf(client)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert _aktiver_user(client) == first.id


@pytest.mark.parametrize("zustand", ["inaktiv", "gesperrt"])
def test_inaktives_oder_gesperrtes_konto_wird_abgelehnt(client, konten, zustand):
    first, second = konten["users"][:2]
    _login(client, first)
    _login(client, second)
    db = TestingSession()
    set_tenant_context(db, None)
    target = db.get(User, first.id)
    if zustand == "inaktiv":
        target.active = False
    else:
        target.locked_until = datetime.now(UTC) + timedelta(hours=1)
    db.commit()
    db.close()

    response = client.post(
        "/benutzer/wechseln",
        data={"user_id": first.id, "_csrf": _csrf(client)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert _aktiver_user(client) == second.id


def test_manipuliertes_cookie_wird_ignoriert(client, konten):
    first = konten["users"][0]
    _login(client, first)
    client.cookies.set(
        "ec_accounts", "kein.gueltiges.cookie", domain="testserver.local", path="/",
    )

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    accounts = load_accounts(client.cookies.get("ec_accounts"))
    assert [account["u"] for account in accounts] == [first.id]


def test_logout_wechselt_zum_zuletzt_genutzten_konto(client, konten):
    first, second = konten["users"][:2]
    _login(client, first)
    _login(client, second)

    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/"
    assert _aktiver_user(client) == first.id


def test_alle_abmelden_loescht_beide_cookies(client, konten):
    _login(client, konten["users"][0])
    response = client.post(
        "/logout/alle", data={"_csrf": _csrf(client)}, follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.cookies.get("session") is None
    assert client.cookies.get("ec_accounts") is None


def test_device_session_bekommt_keine_kontoliste(client, konten):
    user = konten["users"][0]
    db = TestingSession()
    set_tenant_context(db, None)
    device = DeviceToken(label="Mehrfachlogin-Test", token_hash=hash_api_key(uuid4().hex), user_id=user.id)
    db.add(device)
    db.commit()
    db.refresh(device)
    db.close()
    client.cookies.set(
        "session", sign_session(user.id, device=True, device_token_id=device.id),
        domain="testserver.local", path="/",
    )
    client.cookies.set(
        "ec_accounts", sign_accounts(add_account([], user.id, False)),
        domain="testserver.local", path="/",
    )

    response = client.get("/")
    assert response.status_code == 200
    assert "Benutzer wechseln" not in response.text


def test_kontoliste_hat_deckel_bei_fuenf(konten):
    accounts = []
    for index, user in enumerate(konten["users"]):
        accounts = add_account(accounts, user.id, False, now=1_000 + index)
    assert len(accounts) == MAX_ACCOUNTS
    assert {account["u"] for account in accounts} == {
        user.id for user in konten["users"][-MAX_ACCOUNTS:]
    }


def test_kontoeintraege_laufen_passend_zur_sessionart_ab(konten):
    first, second = konten["users"][:2]
    now = 50_000_000
    token = sign_accounts([
        {"u": first.id, "ts": now - settings.SESSION_INACTIVITY_SECONDS - 1, "r": False},
        {
            "u": second.id,
            "ts": now - settings.SESSION_INACTIVITY_SECONDS - 1,
            "r": True,
        },
    ])
    assert [account["u"] for account in load_accounts(token, now=now)] == [second.id]


def test_wechsel_schleppt_org_parameter_nicht_mit_und_loescht_pin_cookies(client, konten):
    first, second = konten["users"][:2]
    _login(client, first)
    _login(client, second)
    client.cookies.set("board_pin", "incident-nachweis", domain="testserver.local", path="/")
    client.cookies.set("board_pin_lage", "lage-nachweis", domain="testserver.local", path="/")

    response = client.post(
        "/benutzer/wechseln?org=999999",
        data={"user_id": first.id, "_csrf": _csrf(client)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert client.cookies.get("board_pin") is None
    assert client.cookies.get("board_pin_lage") is None


def test_fcm_token_wird_nur_mit_konkretem_token_atomar_umgehaengt(client, konten):
    first, second = konten["users"][:2]
    _login(client, first)
    _login(client, second)
    db = TestingSession()
    set_tenant_context(db, None)
    db.add(FcmToken(user_id=first.id, token="fcm-mehrfachlogin-test", platform="android"))
    db.commit()
    db.close()

    client.post(
        "/benutzer/wechseln",
        data={"user_id": first.id, "_csrf": _csrf(client)},
        follow_redirects=False,
    )
    db = TestingSession()
    set_tenant_context(db, None)
    assert db.query(FcmToken).filter_by(token="fcm-mehrfachlogin-test").one().user_id == first.id
    db.close()

    client.post(
        "/benutzer/wechseln?fcm_token=fcm-mehrfachlogin-test",
        data={
            "user_id": second.id,
            "_csrf": _csrf(client),
        },
        follow_redirects=False,
    )
    db = TestingSession()
    set_tenant_context(db, None)
    assert db.query(FcmToken).filter_by(token="fcm-mehrfachlogin-test").one().user_id == second.id
    db.close()
