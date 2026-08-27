"""Benutzerwechsel fuer mehrere gleichzeitig angemeldete Konten."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.multi_account import (
    ACCOUNTS_COOKIE,
    add_account,
    delete_accounts_cookie,
    load_accounts,
    remove_account,
    set_accounts_cookie,
)
from app.core.security import sign_session
from app.core.tenant import set_tenant_context
from app.db import get_db
from app.models.user import User
from app.routers.auth import _set_session_cookie

router = APIRouter()


def _ist_gesperrt(user: User) -> bool:
    if not user.locked_until:
        return False
    locked_until = user.locked_until
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=UTC)
    return locked_until > datetime.now(UTC)


def _wechsel_response(
    request: Request,
    db: Session,
    user: User,
    accounts: list,
    remember: bool,
    fcm_token: str = "",
    status_code: int = 303,
) -> RedirectResponse:
    updated = add_account(accounts, user.id, remember)
    response = RedirectResponse("/", status_code=status_code)
    _set_session_cookie(
        response,
        sign_session(user.id, remember=remember),
        max_age=settings.SESSION_REMEMBER_MAX_AGE_SECONDS if remember else None,
    )
    set_accounts_cookie(response, updated)
    response.delete_cookie("board_pin", path="/")
    response.delete_cookie("board_pin_lage", path="/")
    if fcm_token:
        from app.services.push_service import upsert_fcm_token

        upsert_fcm_token(db, user_id=user.id, token=fcm_token)
    write_audit(
        db,
        "auth.switch_user",
        org_id=user.org_id,
        user_id=user.id,
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return response


@router.post("/benutzer/wechseln")
async def benutzer_wechseln(
    request: Request,
    user_id: int = Form(...),
    fcm_token: str = Form(""),
    db: Session = Depends(get_db),
):
    set_tenant_context(db, None)
    accounts = load_accounts(request.cookies.get(ACCOUNTS_COOKIE))
    entry = next((account for account in accounts if account["u"] == user_id), None)
    if entry is None:
        return RedirectResponse("/", status_code=303)
    user = db.get(User, user_id)
    if not user or not user.active or user.is_device or _ist_gesperrt(user):
        return RedirectResponse("/", status_code=303)
    # Ein bereits ueber Entra angemeldetes Konto bleibt wechselbar. enforce_sso
    # verbietet den lokalen Passwort-Login, nicht den Wechsel mit diesem signierten Nachweis.
    return _wechsel_response(
        request,
        db,
        user,
        accounts,
        entry["r"],
        fcm_token or request.query_params.get("fcm_token", ""),
    )


@router.get("/benutzer/hinzufuegen")
async def benutzer_hinzufuegen():
    return RedirectResponse("/login?add=1", status_code=302)


@router.post("/logout/alle")
async def alle_abmelden(request: Request, db: Session = Depends(get_db)):
    set_tenant_context(db, None)
    user = getattr(request.state, "user", None)
    if user:
        write_audit(db, "auth.logout_all", org_id=user.org_id, user_id=user.id)
        db.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("session", path="/")
    delete_accounts_cookie(response)
    return response


def logout_oder_wechseln(request: Request, db: Session) -> RedirectResponse:
    set_tenant_context(db, None)
    user = getattr(request.state, "user", None)
    accounts = load_accounts(request.cookies.get(ACCOUNTS_COOKIE))
    if user:
        write_audit(db, "auth.logout", org_id=user.org_id, user_id=user.id)
        accounts = remove_account(accounts, user.id)
    gueltige_accounts = []
    users_by_id = {}
    for account in accounts:
        account_user = db.get(User, account["u"])
        if account_user and account_user.active and not account_user.is_device and not _ist_gesperrt(account_user):
            gueltige_accounts.append(account)
            users_by_id[account_user.id] = account_user
    if gueltige_accounts:
        next_entry = max(gueltige_accounts, key=lambda account: account["ts"])
        return _wechsel_response(
            request,
            db,
            users_by_id[next_entry["u"]],
            gueltige_accounts,
            next_entry["r"],
            status_code=302,
        )
    db.commit()
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("session", path="/")
    delete_accounts_cookie(response)
    return response
