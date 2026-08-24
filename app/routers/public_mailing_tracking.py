"""Anonymous, signed mailing tracking endpoints (SEC-11 explicitly scoped)."""

import base64
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import unsign_mailing_track_token
from app.db import get_db
from app.models.mailing import MailingCampaign, MailingLinkClick, MailingQueueItem, MailingSuppressionEntry

router = APIRouter(tags=["mailing-tracking"])
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+XJqWAAAAAElFTkSuQmCC")


def _item(db, token):
    decoded = unsign_mailing_track_token(token)
    if not decoded:
        return None
    qid, oid = decoded
    return (
        db.query(MailingQueueItem)
        .execution_options(include_all_tenants=True)
        .filter(MailingQueueItem.id == qid, MailingQueueItem.org_id == oid)
        .first()
    )


def _unsubscribe(token: str, db: Session) -> HTMLResponse:
    item = _item(db, token)
    if item:
        email = item.email.lower()
        existing = (
            db.query(MailingSuppressionEntry)
            .execution_options(include_all_tenants=True)
            .filter(MailingSuppressionEntry.org_id == item.org_id, MailingSuppressionEntry.email == email)
            .first()
        )
        if existing is None:
            db.add(MailingSuppressionEntry(org_id=item.org_id, email=email, reason="unsubscribe"))
            try:
                db.commit()
            except IntegrityError:
                # Ein paralleler GET/POST kann denselben eindeutigen Eintrag
                # bereits angelegt haben; die Abmeldung bleibt erfolgreich.
                db.rollback()
    return HTMLResponse(
        "<!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Abmeldung bestätigt</title></head><body>"
        "<main><h1>Abmeldung bestätigt</h1><p>Sie wurden erfolgreich abgemeldet.</p></main>"
        "</body></html>"
    )


@router.get("/mailing/u/{token}", response_class=HTMLResponse)
def unsubscribe_get(token: str, db: Session = Depends(get_db)):
    return _unsubscribe(token, db)


@router.post("/mailing/u/{token}", response_class=HTMLResponse)
def unsubscribe_post(token: str, db: Session = Depends(get_db)):
    return _unsubscribe(token, db)


@router.get("/mailing/t/{token}.png")
def pixel(token: str, db: Session = Depends(get_db)):
    item = _item(db, token)
    if item:
        now = datetime.now(UTC).replace(tzinfo=None)
        item.opened_at = item.opened_at or now
        item.open_count += 1
        campaign = (
            db.query(MailingCampaign)
            .execution_options(include_all_tenants=True)
            .filter(MailingCampaign.id == item.campaign_id, MailingCampaign.org_id == item.org_id)
            .first()
        )
        if campaign:
            campaign.open_count += 1
        db.commit()
    return Response(PNG, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/mailing/c/{token}")
def click(token: str, request: Request, u: str = Query(...), db: Session = Depends(get_db)):
    item = _item(db, token)
    if item:
        now = datetime.now(UTC).replace(tzinfo=None)
        item.first_clicked_at = item.first_clicked_at or now
        item.click_count += 1
        db.add(
            MailingLinkClick(
                org_id=item.org_id,
                queue_item_id=item.id,
                url=u,
                clicked_at=now,
                ip=request.client.host if request.client else None,
            )
        )
        campaign = (
            db.query(MailingCampaign)
            .execution_options(include_all_tenants=True)
            .filter(MailingCampaign.id == item.campaign_id, MailingCampaign.org_id == item.org_id)
            .first()
        )
        if campaign:
            campaign.click_count += 1
        db.commit()
    return RedirectResponse(u, status_code=302)
