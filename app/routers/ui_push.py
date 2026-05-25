"""Web Push – subscribe / unsubscribe."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.user import PushSubscription

router = APIRouter(prefix="/push")


@router.get("/vapid-public-key")
def vapid_public_key():
    return {"publicKey": settings.VAPID_PUBLIC_KEY}


@router.post("/subscribe")
async def subscribe(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return Response(status_code=401)
    data = await request.json()
    endpoint = data.get("endpoint", "")
    p256dh = data.get("keys", {}).get("p256dh", "")
    auth = data.get("keys", {}).get("auth", "")
    # Upsert by endpoint
    existing = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).first()
    if not existing:
        db.add(PushSubscription(user_id=user.id, endpoint=endpoint, p256dh=p256dh, auth=auth))
        db.commit()
    return JSONResponse({"ok": True})


@router.post("/unsubscribe")
async def unsubscribe(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    endpoint = data.get("endpoint", "")
    db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).delete()
    db.commit()
    return JSONResponse({"ok": True})
