"""Öffentliche Einlösung des Gruppenkommandanten-Zugangs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.rate_limit import limiter as _limiter
from app.core.security import generate_numeric_pin, hash_api_key
from app.core.telefon import telefon_maske
from app.core.templating import templates
from app.db import get_db
from app.models.major_incident import (
    LageEinheit,
    LageEinheitZugang,
    LageEinheitZugangVersand,
    MajorIncident,
)
from app.services import gk_zugang_service

router = APIRouter()
_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"}
_BEENDET = "Der Zugang wurde beendet oder ersetzt. Bitte neuen Link bei der Einsatzleitung anfordern."


class TokenDaten(BaseModel):
    token: str


class EinloesenDaten(TokenDaten):
    pin: str | None = None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _antwort(data: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status_code, headers=_NO_STORE)


def _meldung(pruefung: gk_zugang_service.TokenPruefung) -> str:
    return "Link ungültig" if pruefung.zustand == "unbekannt" else _BEENDET


def _gebundene_daten(db: Session, zugang: LageEinheitZugang) -> tuple[MajorIncident, LageEinheit] | None:
    # Public routes have no tenant context. The token-derived org_id is therefore
    # an explicit part of every follow-up lookup.
    if zugang.org_id is None:
        return None
    lage = (
        db.query(MajorIncident)
        .filter(MajorIncident.id == zugang.lage_id, MajorIncident.org_id == zugang.org_id)
        .first()
    )
    einheit = (
        db.query(LageEinheit).filter(LageEinheit.id == zugang.einheit_id, LageEinheit.lage_id == zugang.lage_id).first()
    )
    return (lage, einheit) if lage and einheit else None


@router.get("/gk", response_class=HTMLResponse)
async def gk_start(request: Request):
    """Deliberately static: a fragment never reaches this endpoint."""
    response = templates.TemplateResponse(request, "gk/einloesen.html", {})
    response.headers.update(_NO_STORE)
    return response


@router.post("/gk/pruefen")
@(_limiter.limit("10/15minutes") if _limiter else lambda f: f)
async def gk_pruefen(request: Request, daten: TokenDaten, db: Session = Depends(get_db)):
    pruefung = gk_zugang_service.token_pruefen(db, daten.token)
    if pruefung.zustand != "ok" or not pruefung.zugang:
        return _antwort({"ok": False, "meldung": _meldung(pruefung)}, 404)
    gebunden = _gebundene_daten(db, pruefung.zugang)
    if not gebunden:
        return _antwort({"ok": False, "meldung": _BEENDET}, 404)
    lage, einheit = gebunden
    return _antwort(
        {"ok": True, "lage": lage.name, "einheit": einheit.label, "pin_noetig": pruefung.zugang.pin_pflicht}
    )


@router.post("/gk/pin")
@(_limiter.limit("5/15minutes") if _limiter else lambda f: f)
async def gk_pin(request: Request, daten: TokenDaten, db: Session = Depends(get_db)):
    pruefung = gk_zugang_service.token_pruefen(db, daten.token)
    zugang = pruefung.zugang
    if pruefung.zustand != "ok" or not zugang:
        return _antwort({"ok": False, "meldung": _meldung(pruefung)}, 404)
    if not zugang.pin_pflicht or not _gebundene_daten(db, zugang):
        return _antwort({"ok": False, "meldung": _BEENDET}, 404)
    now = _now()
    anfragen = (
        db.query(LageEinheitZugangVersand)
        .filter(
            LageEinheitZugangVersand.zugang_id == zugang.id,
            LageEinheitZugangVersand.org_id == zugang.org_id,
            LageEinheitZugangVersand.kanal == "pin_sms",
            LageEinheitZugangVersand.created_at >= now - timedelta(minutes=10),
        )
        .count()
    )
    if anfragen >= 3:
        return _antwort({"ok": False, "meldung": "Zu viele Code-Anforderungen. Bitte später erneut versuchen."}, 429)
    if zugang.pin_gesperrt_bis and zugang.pin_gesperrt_bis > now:
        return _antwort({"ok": False, "meldung": "Zu viele Fehlversuche. Bitte später erneut versuchen."}, 429)
    pin = generate_numeric_pin()
    zugang.pin_hash, zugang.pin_gueltig_bis, zugang.pin_versuche = hash_api_key(pin), now + timedelta(minutes=10), 0
    versand = LageEinheitZugangVersand(
        org_id=zugang.org_id,
        zugang_id=zugang.id,
        einheit_id=zugang.einheit_id,
        leader_id=zugang.leader_id,
        generation=zugang.generation,
        kanal="pin_sms",
        ausloeser="manuell",
        status="uebersprungen",
        ziel_maske=telefon_maske(zugang.phone_e164),
        created_at=now,
        abgeschlossen_at=now,
    )
    db.add(versand)
    from app.services.exercise_guard import darf_extern
    from app.services.sms_service import send_sms, sms_available

    gebunden = _gebundene_daten(db, zugang)
    assert gebunden is not None
    org_id = zugang.org_id
    assert org_id is not None
    lage, _ = gebunden
    if (
        zugang.phone_e164
        and sms_available(org_id, db)
        and darf_extern("sms", is_exercise=lage.is_exercise, org_id=org_id, db=db)
    ):
        try:
            result = await send_sms(org_id, zugang.phone_e164, f"GSL-Bestätigungscode: {pin}")
            versand.status = "gesendet" if result.success else "fehlgeschlagen"
        except Exception:
            # Do not include provider exceptions: they may contain message text.
            versand.status, versand.fehler = "fehlgeschlagen", "SMS-Versand fehlgeschlagen"
    write_audit(
        db,
        "gsl.zugang.pin_angefordert",
        org_id=zugang.org_id,
        entity_type="lage_einheit",
        entity_id=zugang.einheit_id,
        payload={"lage_id": zugang.lage_id, "einheit_id": zugang.einheit_id, "generation": zugang.generation},
    )
    db.commit()
    if versand.status != "gesendet":
        return _antwort(
            {"ok": False, "meldung": "Der Code konnte nicht gesendet werden. Bitte die Einsatzleitung informieren."},
            503,
        )
    return _antwort({"ok": True})


@router.post("/gk/einloesen")
@(_limiter.limit("10/15minutes") if _limiter else lambda f: f)
async def gk_einloesen(request: Request, daten: EinloesenDaten, db: Session = Depends(get_db)):
    pruefung = gk_zugang_service.token_pruefen(db, daten.token)
    zugang = pruefung.zugang
    if pruefung.zustand != "ok" or not zugang or not _gebundene_daten(db, zugang):
        return _antwort({"ok": False, "meldung": _meldung(pruefung)}, 404)
    now = _now()
    if zugang.pin_pflicht:
        pin_ok = bool(
            daten.pin
            and zugang.pin_hash
            and zugang.pin_gueltig_bis
            and zugang.pin_gueltig_bis > now
            and (zugang.pin_gesperrt_bis is None or zugang.pin_gesperrt_bis <= now)
        )
        pin_ok = pin_ok and hash_api_key(daten.pin or "") == zugang.pin_hash
        if not pin_ok:
            zugang.pin_versuche += 1
            if zugang.pin_versuche >= 5:
                zugang.pin_gesperrt_bis = now + timedelta(minutes=15)
            write_audit(
                db,
                "gsl.zugang.pin_fehlgeschlagen",
                org_id=zugang.org_id,
                entity_type="lage_einheit",
                entity_id=zugang.einheit_id,
                payload={"lage_id": zugang.lage_id, "einheit_id": zugang.einheit_id, "generation": zugang.generation},
            )
            db.commit()
            return _antwort({"ok": False, "meldung": "Code ungültig oder abgelaufen."}, 403)
        zugang.pin_hash = None
        zugang.pin_gueltig_bis = None
        zugang.pin_versuche = 0
    raw, session = gk_zugang_service.sitzung_anlegen(
        db,
        zugang,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
        verifiziert=zugang.pin_pflicht,
    )
    zugang.einloesungen += 1
    zugang.erste_einloesung_at = zugang.erste_einloesung_at or now
    write_audit(
        db,
        "gsl.zugang.eingeloest",
        org_id=zugang.org_id,
        entity_type="lage_einheit",
        entity_id=zugang.einheit_id,
        payload={
            "lage_id": zugang.lage_id,
            "einheit_id": zugang.einheit_id,
            "generation": zugang.generation,
            "client_kurz": session.client_kurz,
        },
    )
    db.commit()
    response = _antwort({"ok": True, "redirect": "/einheit"})
    response.set_cookie(
        gk_zugang_service.COOKIE,
        raw,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
        max_age=max(0, int((session.laeuft_ab_at - now).total_seconds())),
    )
    return response


@router.post("/gk/abmelden")
async def gk_abmelden(request: Request, db: Session = Depends(get_db)):
    cookie = request.cookies.get(gk_zugang_service.COOKIE)
    if cookie:
        principal = gk_zugang_service.sitzung_pruefen(db, cookie)
        if principal:
            principal.session.revoked_at, principal.session.revoke_grund = _now(), "abmeldung"
            write_audit(
                db,
                "gsl.zugang.abgemeldet",
                org_id=principal.org_id,
                entity_type="lage_einheit",
                entity_id=principal.einheit.id,
                payload={
                    "lage_id": principal.lage.id,
                    "einheit_id": principal.einheit.id,
                    "generation": principal.zugang.generation,
                },
            )
            db.commit()
    response = _antwort({"ok": True})
    response.delete_cookie(gk_zugang_service.COOKIE, path="/")
    return response
