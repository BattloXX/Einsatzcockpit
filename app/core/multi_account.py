"""Signiertes Cookie fuer mehrere interaktive Benutzerkonten."""
from __future__ import annotations

import time
from typing import TypedDict

from fastapi import Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import settings

ACCOUNTS_COOKIE = "ec_accounts"
MAX_ACCOUNTS = 5
_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="accounts")


class AccountEntry(TypedDict):
    u: int
    ts: int
    r: bool


def load_accounts(token: str | None, *, now: int | None = None) -> list[AccountEntry]:
    """Liest gueltige, noch nicht abgelaufene Kontoeintraege aus dem Cookie."""
    if not token:
        return []
    try:
        data = _signer.loads(token, max_age=None)
    except BadSignature:
        return []
    if not isinstance(data, list):
        return []

    jetzt = int(time.time()) if now is None else now
    accounts: dict[int, AccountEntry] = {}
    for raw in data:
        if not isinstance(raw, dict):
            continue
        user_id = raw.get("u")
        timestamp = raw.get("ts")
        remember = raw.get("r")
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            continue
        if not isinstance(timestamp, int) or isinstance(timestamp, bool):
            continue
        if not isinstance(remember, bool):
            continue
        inactivity = (
            settings.SESSION_REMEMBER_INACTIVITY_SECONDS
            if remember else settings.SESSION_INACTIVITY_SECONDS
        )
        if timestamp > jetzt or jetzt - timestamp > inactivity:
            continue
        entry: AccountEntry = {"u": user_id, "ts": timestamp, "r": remember}
        previous = accounts.get(user_id)
        if previous is None or timestamp > previous["ts"]:
            accounts[user_id] = entry
    return sorted(accounts.values(), key=lambda account: account["ts"], reverse=True)[:MAX_ACCOUNTS]


def add_account(
    accounts: list[AccountEntry], user_id: int, remember: bool, *, now: int | None = None,
) -> list[AccountEntry]:
    """Setzt ein Konto an die Spitze der LRU-Liste und begrenzt auf fuenf."""
    timestamp = int(time.time()) if now is None else now
    result = [account for account in accounts if account["u"] != user_id]
    result.append({"u": user_id, "ts": timestamp, "r": remember})
    return sorted(result, key=lambda account: account["ts"], reverse=True)[:MAX_ACCOUNTS]


def remove_account(accounts: list[AccountEntry], user_id: int) -> list[AccountEntry]:
    return [account for account in accounts if account["u"] != user_id]


def sign_accounts(accounts: list[AccountEntry]) -> str:
    payload = [{"u": account["u"], "ts": account["ts"], "r": account["r"]} for account in accounts]
    return _signer.dumps(payload)


def set_accounts_cookie(response: Response, accounts: list[AccountEntry]) -> None:
    response.set_cookie(
        ACCOUNTS_COOKIE,
        sign_accounts(accounts),
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=settings.SESSION_REMEMBER_MAX_AGE_SECONDS,
        path="/",
    )


def delete_accounts_cookie(response: Response) -> None:
    response.delete_cookie(ACCOUNTS_COOKIE, path="/")
