"""Web Push – subscribe / unsubscribe / diagnose."""
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.user import PushSubscription

log = logging.getLogger(__name__)
router = APIRouter(prefix="/push")


@router.get("/vapid-public-key")
def vapid_public_key(db: Session = Depends(get_db)):
    from app.services.push_service import _push_cfg
    cfg = _push_cfg(db)
    return {"publicKey": cfg["public_key"] or ""}


@router.post("/subscribe")
async def subscribe(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return Response(status_code=401)
    data = await request.json()
    endpoint = data.get("endpoint", "")
    p256dh = data.get("keys", {}).get("p256dh", "")
    auth = data.get("keys", {}).get("auth", "")
    # Upsert by endpoint – immer user_id/keys aktualisieren (z.B. nach Device-Login-Wechsel)
    existing = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).first()
    if existing:
        existing.user_id = user.id
        existing.p256dh = p256dh
        existing.auth = auth
    else:
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


@router.post("/test")
async def test_push(request: Request, db: Session = Depends(get_db)):
    """Sendet einen Test-Push an die Subscription des anfragenden Geräts.
    Gibt JSON mit ok/error zurück – für die Admin-Push-Seite."""
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse({"ok": False, "error": "Nicht eingeloggt"}, status_code=401)

    data = await request.json()
    endpoint = data.get("endpoint", "")
    if not endpoint:
        return JSONResponse({"ok": False, "error": "Kein Endpoint übermittelt"})

    sub = (
        db.query(PushSubscription)
        .filter(PushSubscription.endpoint == endpoint, PushSubscription.user_id == user.id)
        .first()
    )
    if not sub:
        return JSONResponse({"ok": False, "error": "Subscription nicht in Datenbank gefunden"})

    from app.services.push_service import _push_cfg
    cfg = _push_cfg(db)
    if not cfg["enabled"]:
        return JSONResponse({"ok": False, "error": "Push ist deaktiviert (enable_push=false)"})
    if not cfg["private_key"]:
        return JSONResponse({"ok": False, "error": "VAPID Private Key fehlt"})
    if not cfg["public_key"]:
        return JSONResponse({"ok": False, "error": "VAPID Public Key fehlt"})
    if not cfg["claim_email"]:
        return JSONResponse({"ok": False, "error": "VAPID E-Mail fehlt"})

    try:
        from pywebpush import webpush
        payload = json.dumps({
            "title": "Test-Push",
            "body": "Wenn du das siehst, funktioniert Web Push!",
            "url": "/admin/push-nachrichten",
        })
        webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            },
            data=payload,
            vapid_private_key=cfg["private_key"],
            vapid_claims={"sub": f"mailto:{cfg['claim_email']}"},
            timeout=10,
        )
        return JSONResponse({"ok": True})
    except Exception as exc:
        status = None
        try:
            from pywebpush import WebPushException as _WPE
            if isinstance(exc, _WPE) and exc.response is not None:
                status = exc.response.status_code
        except Exception:
            pass
        log.exception("Test-Push fehlgeschlagen für User %s Subscription %s", user.id, sub.id)
        if status in (403, 404, 410):
            # Subscription ist ungültig (VAPID-Key-Wechsel oder Ablauf) – aus DB entfernen
            try:
                db.delete(sub)
                db.commit()
            except Exception:
                db.rollback()
        # Nur Statuscode + Exception-Typ zurückgeben – keine str(exc)-Details im Response
        err_type = type(exc).__name__
        if status:
            detail = f"HTTP {status} vom Push-Server ({err_type}) – Details im Server-Log"
        else:
            detail = f"{err_type} – Details im Server-Log"
        return JSONResponse({"ok": False, "error": detail})
