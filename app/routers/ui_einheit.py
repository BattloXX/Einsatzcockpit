"""JSON-API fuer den GSL-Einheitenmodus."""

# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import has_role
from app.core.security import get_author_name
from app.core.templating import templates
from app.db import get_db
from app.models.major_incident import (
    EINHEIT_STATUS_LABEL,
    SITE_LOG_KIND_LABEL,
    CrossSiteMarker,
    EinheitAktion,
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheitAusstattung,
    LageEinheitPerson,
    SiteLogEntry,
    SiteMedia,
    VehiclePosition,
)
from app.models.master import FireDept, VehicleMaster
from app.models.user import AuditLog, DeviceToken
from app.services import gk_zugang_service, ressource_pflege_service
from app.services.broadcast import broadcast_lage
from app.services.einheit_service import (
    EinheitKonflikt,
    EinheitKontext,
    EinheitKontextFehler,
    _auftragsdaten,
    _iso_z,
    auftraege_fuer_einheit,
    auftrag_laden,
    kontext_fuer_einheit,
    kontext_fuer_geraet,
    kontext_fuer_zugang,
    setze_einheit_status,
)
from app.services.gk_zugang_service import ZugangFehlergrund
from app.services.gsl_live_notify import notify_gsl_live
from app.services.lage_media_service import site_media_path, site_thumb_path, speichere_site_foto
from app.services.site_log_service import add_site_log, format_lagemeldung, normalisiere_user_kind

router = APIRouter(prefix="/einheit", tags=["einheit"])

ZUGANG_AKTIONEN = frozenset({
    "status", "meldung", "foto", "anforderung", "antwort", "quittierung",
    "ressource_personal", "ressource_ausstattung",
})


def _simulationskontext(request: Request, db: Session) -> EinheitKontext | None:
    """Löst den Simulationskontext einmalig für HTML-Hülle und JSON-API auf."""
    user = getattr(request.state, "user", None)
    sim = request.headers.get("X-EC-Einheit-Sim") or request.query_params.get("sim")
    if not sim:
        return None
    if user is None or (
        getattr(request.state, "qr_incident_id", None) is not None
        or getattr(request.state, "qr_lage_id", None) is not None
    ):
        raise EinheitKontextFehler(403, "kein_einheitenkontext", "Keine Berechtigung für die Simulation")
    if not has_role(user, "admin"):
        write_audit(db, "gsl.einheit.simulation_abgelehnt", user_id=user.id, payload={"einheit_id": sim})
        db.commit()
        raise EinheitKontextFehler(403, "kein_einheitenkontext", "Keine Berechtigung für die Simulation")
    try:
        return kontext_fuer_einheit(db, user, int(sim), quelle="simulation")
    except ValueError as exc:
        raise EinheitKontextFehler(404, "einheit_nicht_gefunden", "Einheit nicht gefunden") from exc


def _optionaler_kontext(request: Request, db: Session) -> EinheitKontext | None:
    """Löst den optionalen Geräte- oder Simulationskontext der HTML-Hülle auf."""
    user = getattr(request.state, "user", None)
    if user is not None and getattr(request.state, "is_device", False):
        token_id = getattr(request.state, "device_token_id", None)
        query = db.query(DeviceToken).filter(DeviceToken.user_id == user.id, DeviceToken.revoked_at.is_(None))
        token = query.filter(DeviceToken.id == token_id).first() if token_id else query.order_by(DeviceToken.created_at.desc()).first()
        return kontext_fuer_geraet(db, user, token) if token else None
    if user is not None and (request.headers.get("X-EC-Einheit-Sim") or request.query_params.get("sim")):
        return _simulationskontext(request, db)
    cookie = request.cookies.get(gk_zugang_service.COOKIE)
    if not cookie:
        return None
    principal, _ = gk_zugang_service.sitzung_pruefen_mit_grund(db, cookie)
    if principal:
        db.commit()
        return kontext_fuer_zugang(db, principal)
    db.rollback()
    return None


def _einheit_seite(request: Request, db: Session, start_dispatch_id: int | None = None):
    user = getattr(request.state, "user", None)
    simulation_fehler: EinheitKontextFehler | None = None
    try:
        ctx = _optionaler_kontext(request, db)
    except EinheitKontextFehler as exc:
        ctx = None
        simulation_fehler = exc
    if ctx is None and user is None:
        return RedirectResponse("/login", status_code=302)
    if ctx is None and not getattr(request.state, "is_device", False):
        if simulation_fehler:
            return templates.TemplateResponse(request, "einheit/einheit.html", {
                "org": None, "lage_id": None, "einheit_id": None, "einheit_label": "Fahrzeug/Gerät",
                "fahrzeug": "Fahrzeug/Gerät", "is_exercise": False, "simulation": False,
                "sim_einheit_id": None, "schreibbar": False, "admin_name": "",
                "start_dispatch_id": start_dispatch_id, "kein_kontext": False,
                "simulation_fehler": simulation_fehler.nachricht,
            }, status_code=simulation_fehler.status_code)
        return RedirectResponse("/", status_code=302)
    vehicle_label = "Fahrzeug/Gerät"
    if getattr(request.state, "is_device", False) and user:
        token_id = getattr(request.state, "device_token_id", None)
        token = db.query(DeviceToken).filter(DeviceToken.id == token_id).first() if token_id else None
        if token and token.vehicle_master_id:
            vehicle_label = getattr(db.get(VehicleMaster, token.vehicle_master_id), "code", vehicle_label)
    org = None
    org_id = ctx.lage.org_id if ctx else getattr(user, "org_id", None)
    if org_id:
        org = db.query(FireDept).filter(FireDept.id == org_id).execution_options(include_all_tenants=True).first()
    return templates.TemplateResponse(request, "einheit/einheit.html", {
        "org": org, "lage_id": ctx.lage.id if ctx else None, "einheit_id": ctx.einheit.id if ctx else None,
        "einheit_label": ctx.einheit.label if ctx else vehicle_label,
        "fahrzeug": (ctx.vehicle.code or ctx.vehicle.name) if ctx and ctx.vehicle else (ctx.einheit.label if ctx else vehicle_label),
        "is_exercise": bool(ctx and ctx.lage.is_exercise), "simulation": bool(ctx and ctx.simulation),
        "sim_einheit_id": ctx.einheit.id if ctx and ctx.simulation else None,
        "schreibbar": bool(ctx and not (ctx.simulation and not ctx.lage.is_exercise)),
        "admin_name": getattr(user, "display_name", "") if ctx and ctx.simulation else "",
        "gk_zugang": bool(ctx and ctx.quelle == "zugang"),
        "gk_name": ctx.leader.display_name if ctx and ctx.leader else "",
        "start_dispatch_id": start_dispatch_id, "kein_kontext": ctx is None, "simulation_fehler": None,
    })


@router.get("", response_class=HTMLResponse)
def einheit_seite(request: Request, db: Session = Depends(get_db)):
    return _einheit_seite(request, db)


@router.get("/auftrag/{dispatch_id}", response_class=HTMLResponse)
def einheit_auftrag_seite(dispatch_id: int, request: Request, db: Session = Depends(get_db)):
    return _einheit_seite(request, db, dispatch_id)


def _fehler(status: int, code: str, details: dict | None = None) -> NoReturn:
    body: dict[str, Any] = {"code": code}
    if details:
        body["details"] = details
    raise HTTPException(status_code=status, detail=body)


def _antwort_exc(exc: HTTPException) -> JSONResponse:
    return JSONResponse(exc.detail, status_code=exc.status_code)


def einheit_kontext(request: Request, db: Session = Depends(get_db)) -> EinheitKontext:
    """Loest Tablet- bzw. explizit erlaubte Admin-Simulationen auf."""
    user = getattr(request.state, "user", None)
    # Simulation deliberately precedes all other principals. An anonymous GK
    # cookie cannot activate simulation because it has no admin user.
    sim = request.headers.get("X-EC-Einheit-Sim") or request.query_params.get("sim")
    if sim and not getattr(request.state, "is_device", False):
        if user is None:
            _fehler(403, "kein_einheitenkontext")
        try:
            ctx = _simulationskontext(request, db)
            assert ctx is not None
        except EinheitKontextFehler as exc:
            _fehler(exc.status_code, exc.code)
        einheit_id = ctx.einheit.id
        heute = datetime.now(UTC).date()
        schon = (
            db.query(AuditLog.id)
            .filter(
                AuditLog.action == "gsl.einheit.simulation_gestartet",
                AuditLog.user_id == user.id,
                AuditLog.created_at >= datetime.combine(heute, datetime.min.time()),
                AuditLog.created_at < datetime.combine(heute + timedelta(days=1), datetime.min.time()),
            )
            .first()
        )
        if not schon:
            write_audit(db, "gsl.einheit.simulation_gestartet", user_id=user.id, payload={"einheit_id": einheit_id})
            db.commit()
        return ctx
    if user is None:
        cookie = request.cookies.get(gk_zugang_service.COOKIE)
        if cookie:
            principal, grund = gk_zugang_service.sitzung_pruefen_mit_grund(db, cookie)
            if principal:
                # Only the validator's activity timestamp is committed here;
                # no endpoint mutation has run at this point.
                db.commit()
                return kontext_fuer_zugang(db, principal)
            db.rollback()
            codes = {
                ZugangFehlergrund.WIDERRUFEN: "zugang_widerrufen",
                ZugangFehlergrund.ABGELAUFEN: "zugang_abgelaufen",
                ZugangFehlergrund.UNGUELTIG: "zugang_ungueltig",
            }
            _fehler(401, codes.get(grund or ZugangFehlergrund.UNGUELTIG, "zugang_ungueltig"))
        _fehler(403, "kein_einheitenkontext")
    if getattr(request.state, "is_device", False):
        token_id = getattr(request.state, "device_token_id", None)
        query = db.query(DeviceToken).filter(DeviceToken.user_id == user.id, DeviceToken.revoked_at.is_(None))
        token = (
            query.filter(DeviceToken.id == token_id).first()
            if token_id
            else query.order_by(DeviceToken.created_at.desc()).first()
        )
        try:
            lage_id = int(request.query_params["lage"]) if "lage" in request.query_params else None
        except ValueError:
            lage_id = None
        ctx = kontext_fuer_geraet(db, user, token, lage_id) if token else None
        if ctx is None:
            _fehler(403, "kein_einheitenkontext")
        return ctx

    cookie = request.cookies.get(gk_zugang_service.COOKIE)
    if cookie:
        principal, grund = gk_zugang_service.sitzung_pruefen_mit_grund(db, cookie)
        if principal:
            db.commit()
            return kontext_fuer_zugang(db, principal)
        db.rollback()
        codes = {
            ZugangFehlergrund.WIDERRUFEN: "zugang_widerrufen",
            ZugangFehlergrund.ABGELAUFEN: "zugang_abgelaufen",
            ZugangFehlergrund.UNGUELTIG: "zugang_ungueltig",
        }
        _fehler(401, codes.get(grund or ZugangFehlergrund.UNGUELTIG, "zugang_ungueltig"))

    _fehler(403, "kein_einheitenkontext")


def _schreibbar(ctx: EinheitKontext) -> None:
    if ctx.simulation and not ctx.lage.is_exercise:
        _fehler(403, "simulation_nur_lesend")


def einheit_darf(aktion: str, ctx: EinheitKontext, db: Session) -> None:
    if ctx.quelle != "zugang":
        return
    if aktion not in ZUGANG_AKTIONEN:
        _fehler(403, "zugang_aktion_nicht_erlaubt")
    if aktion.startswith("ressource_") and not gk_zugang_service.org_einstellungen(db, ctx.org_id).gk_zugang_ressource_pflegen:
        _fehler(403, "zugang_aktion_nicht_erlaubt")


def _zeit(value: str | None) -> tuple[datetime | None, str | None]:
    if not value:
        return None, None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fehler(422, "erfasst_at_ungueltig")
    parsed = parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed
    now = datetime.now(UTC).replace(tzinfo=None)
    if parsed < now - timedelta(hours=24) or parsed > now + timedelta(minutes=5):
        return now, "erfasst_at_korrigiert"
    return parsed, None


def fuehre_aktion_aus(
    db: Session,
    ctx: EinheitKontext,
    *,
    client_uuid: str,
    aktion: str,
    erfasst_at: datetime | None,
    dispatch_id: int | None,
    ausfuehren: Callable[[], dict],
) -> tuple[dict, int]:
    try:
        UUID(client_uuid)
    except ValueError, AttributeError, TypeError:
        _fehler(422, "client_uuid_ungueltig")
    vorhanden = db.query(EinheitAktion).filter(EinheitAktion.client_uuid == client_uuid).first()
    if vorhanden:
        gleich = (
            vorhanden.device_token_id == (ctx.device_token.id if ctx.device_token else None)
            and vorhanden.zugang_id == (ctx.zugang.id if ctx.zugang else None)
        )
        if ctx.simulation:
            gleich = vorhanden.quelle == ctx.quelle and vorhanden.einheit_id == ctx.einheit.id
        if not gleich:
            return {"code": "client_uuid_vergeben"}, 409
        return json.loads(vorhanden.antwort_json or "{}"), 409 if vorhanden.ergebnis == "konflikt" else 200
    try:
        antwort = ausfuehren()
        status, ergebnis = 200, "ok"
    except EinheitKonflikt as exc:
        antwort = {"code": exc.code}
        if exc.details:
            antwort["details"] = exc.details
        status, ergebnis = 409, "konflikt"
    row = EinheitAktion(
        client_uuid=client_uuid,
        org_id=ctx.org_id,
        quelle=ctx.quelle,
        device_token_id=ctx.device_token.id if ctx.device_token else None,
        zugang_id=ctx.zugang.id if ctx.zugang else None,
        einheit_id=ctx.einheit.id,
        dispatch_id=dispatch_id,
        aktion=aktion,
        ergebnis=ergebnis,
        entity_type=antwort.get("entity_type"),
        entity_id=antwort.get("entity_id"),
        antwort_json=json.dumps(antwort, ensure_ascii=False),
        erfasst_at=erfasst_at,
        empfangen_at=datetime.now(UTC),
    )
    db.add(row)
    try:
        db.flush()
        antwort["aktion_id"] = row.id
        row.antwort_json = json.dumps(antwort, ensure_ascii=False)
        db.commit()
    except IntegrityError:
        db.rollback()
        replay = db.query(EinheitAktion).filter(EinheitAktion.client_uuid == client_uuid).first()
        if replay:
            return json.loads(replay.antwort_json or "{}"), 409 if replay.ergebnis == "konflikt" else 200
        raise
    return antwort, status


def _autor(request: Request, ctx: EinheitKontext) -> str | None:
    if ctx.quelle == "zugang":
        return ctx.akteur_name
    if ctx.simulation:
        return f"{request.state.user.display_name} (Simulation {ctx.einheit.label})"
    return get_author_name(request)


async def _senden(lage_id: int, site_id: int, einheit_id: int, dispatch_id: int, phase: str | None = None) -> None:
    await broadcast_lage(lage_id, {"type": "site:card_changed", "site_id": site_id})
    await broadcast_lage(
        lage_id, {"type": "einheit:changed", "einheit_id": einheit_id, "dispatch_id": dispatch_id, "site_id": site_id}
    )
    if phase:
        await broadcast_lage(lage_id, {"type": "site_phase_changed", "site_id": site_id, "phase": phase})


@router.get("/api/zustand")
def zustand(request: Request, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)):
    data = auftraege_fuer_einheit(db, ctx)
    data["simulation"] = ctx.simulation
    data["schreibbar"] = not (ctx.simulation and not ctx.lage.is_exercise)
    data["kann_ressource_pflegen"] = (
        data["schreibbar"]
        and ctx.quelle == "zugang"
        and gk_zugang_service.org_einstellungen(db, ctx.org_id).gk_zugang_ressource_pflegen
    )
    data["ressource"] = _ressourcen_daten(db, ctx)
    raw = json.dumps({k: v for k, v in data.items() if k != "server_time"}, sort_keys=True, default=str)
    etag = hashlib.sha256(raw.encode()).hexdigest()
    if request.headers.get("if-none-match", "").strip('"') == etag:
        return Response(status_code=304, headers={"ETag": f'"{etag}"'})
    data["etag"] = etag
    return JSONResponse(data, headers={"ETag": f'"{etag}"'})


def _ressourcen_daten(db: Session, ctx: EinheitKontext) -> dict[str, Any]:
    """Der GK erhält ausschließlich die Ressourcen seiner eigenen Einheit."""
    personen = (
        db.query(LageEinheitPerson)
        .filter(
            LageEinheitPerson.lage_id == ctx.lage.id,
            LageEinheitPerson.einheit_id == ctx.einheit.id,
            LageEinheitPerson.bis_at.is_(None),
        )
        .order_by(LageEinheitPerson.id)
        .all()
    )
    ausstattung = (
        db.query(LageEinheitAusstattung)
        .filter(
            LageEinheitAusstattung.lage_id == ctx.lage.id,
            LageEinheitAusstattung.einheit_id == ctx.einheit.id,
        )
        .order_by(LageEinheitAusstattung.ist_faehigkeit, LageEinheitAusstattung.bezeichnung, LageEinheitAusstattung.id)
        .all()
    )
    return {
        "personal_modus": ctx.einheit.personal_modus,
        "personal": {
            "gesamt": ctx.einheit.staerke_gesamt or 0,
            "fuehrung": ctx.einheit.staerke_fuehrung or 0,
            "agt": ctx.einheit.staerke_agt or 0,
            "sanitaeter": ctx.einheit.staerke_sanitaeter or 0,
            "bemerkung": ctx.einheit.personal_bemerkung,
            "personen": [
                {
                    "id": person.id,
                    "name": person.name,
                    "funktion": person.funktion,
                    "qualifikationen": person.qualifikationen,
                }
                for person in personen
            ],
        },
        "ausstattung": [
            {
                "id": zeile.id,
                "kategorie": zeile.kategorie,
                "bezeichnung": zeile.bezeichnung,
                "menge": zeile.menge,
                "status": zeile.status,
                "bemerkung": zeile.bemerkung,
            }
            for zeile in ausstattung
        ],
    }


@router.post("/api/ressource/personal")
async def ressource_personal(
    request: Request, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)
):
    _schreibbar(ctx)
    einheit_darf("ressource_personal", ctx, db)
    body = await request.json()
    operation = body.get("operation", body.get("aktion", "setzen"))
    autor = _autor(request, ctx)

    def run() -> dict[str, Any]:
        try:
            if operation == "setzen":
                result = ressource_pflege_service.personal_setzen(
                    db, ctx.lage, ctx.einheit,
                    gesamt=body.get("gesamt"), fuehrung=body.get("fuehrung"), agt=body.get("agt"),
                    sanitaeter=body.get("sanitaeter"), bemerkung=body.get("bemerkung"),
                    user_id=None, author_name=autor,
                )
                return {"ok": True, "entity_type": "lage_einheit", "entity_id": ctx.einheit.id, **result}
            if operation == "hinzufuegen":
                person = ressource_pflege_service.person_hinzufuegen(
                    db, ctx.lage, ctx.einheit,
                    member_id=body.get("member_id"), name=body.get("name"), funktion=body.get("funktion", "mannschaft"),
                    qualifikationen=body.get("qualifikationen"), bemerkung=body.get("bemerkung"),
                    user_id=None, author_name=autor,
                )
                return {"ok": True, "entity_type": "lage_einheit_person", "entity_id": person.id}
            if operation == "entfernen":
                ressource_pflege_service.person_entfernen(
                    db, ctx.lage, ctx.einheit, body.get("person_id"), grund=body.get("grund"),
                    user_id=None, author_name=autor,
                )
                return {"ok": True, "entity_type": "lage_einheit_person"}
        except ValueError as exc:
            _fehler(422, "ressource_ungueltig", {"nachricht": str(exc)})
        _fehler(404 if operation in {"umbuchen", "verstaerken", "vorlage"} else 422, "ressource_aktion_ungueltig")

    try:
        answer, code = fuehre_aktion_aus(
            db, ctx, client_uuid=body.get("client_uuid"), aktion="ressource_personal", erfasst_at=None,
            dispatch_id=None, ausfuehren=run,
        )
    except HTTPException as exc:
        return _antwort_exc(exc)
    if code == 200 and answer.get("ok"):
        answer["ressource"] = _ressourcen_daten(db, ctx)
        await broadcast_lage(ctx.lage.id, {"type": "ressource:changed", "einheit_id": ctx.einheit.id})
    return JSONResponse(answer, status_code=code)


@router.post("/api/ressource/ausstattung")
async def ressource_ausstattung(
    request: Request, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)
):
    _schreibbar(ctx)
    einheit_darf("ressource_ausstattung", ctx, db)
    body = await request.json()
    operation = body.get("operation", body.get("aktion", "hinzufuegen"))
    autor = _autor(request, ctx)

    def run() -> dict[str, Any]:
        try:
            if operation == "hinzufuegen":
                zeile = ressource_pflege_service.ausstattung_hinzufuegen(
                    db, ctx.lage, ctx.einheit,
                    kategorie=body.get("kategorie", "sonstiges"), bezeichnung=body.get("bezeichnung"),
                    menge=body.get("menge", 1), status=body.get("status", "einsatzbereit"),
                    bemerkung=body.get("bemerkung"), user_id=None, author_name=autor,
                )
                return {"ok": True, "entity_type": "lage_einheit_ausstattung", "entity_id": zeile.id}
            if operation == "aendern":
                zeile = ressource_pflege_service.ausstattung_aendern(
                    db, ctx.lage, ctx.einheit, body.get("zeile_id"), menge=body.get("menge"),
                    status=body.get("status"), bemerkung=body.get("bemerkung"), user_id=None, author_name=autor,
                )
                return {"ok": True, "entity_type": "lage_einheit_ausstattung", "entity_id": zeile.id}
            if operation == "entfernen":
                ressource_pflege_service.ausstattung_entfernen(
                    db, ctx.lage, ctx.einheit, body.get("zeile_id"), user_id=None, author_name=autor,
                )
                return {"ok": True, "entity_type": "lage_einheit_ausstattung"}
        except ValueError as exc:
            _fehler(422, "ressource_ungueltig", {"nachricht": str(exc)})
        _fehler(404 if operation in {"umbuchen", "vorlage"} else 422, "ressource_aktion_ungueltig")

    try:
        answer, code = fuehre_aktion_aus(
            db, ctx, client_uuid=body.get("client_uuid"), aktion="ressource_ausstattung", erfasst_at=None,
            dispatch_id=None, ausfuehren=run,
        )
    except HTTPException as exc:
        return _antwort_exc(exc)
    if code == 200 and answer.get("ok"):
        answer["ressource"] = _ressourcen_daten(db, ctx)
        await broadcast_lage(ctx.lage.id, {"type": "ressource:changed", "einheit_id": ctx.einheit.id})
    return JSONResponse(answer, status_code=code)


@router.get("/api/auftrag/{dispatch_id}")
def auftrag_detail(dispatch_id: int, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)):
    try:
        dispatch = auftrag_laden(db, ctx, dispatch_id)
    except LookupError:
        raise HTTPException(status_code=404)
    site = dispatch.site
    out: dict[str, Any] = {
        "auftrag": _auftragsdaten(dispatch),
        "stelle": {
            k: _auftragsdaten(dispatch)[k]
            for k in ("site_id", "bezeichnung", "einsatzgrund", "adresse", "lat", "lng", "priority", "phase")
        },
    }
    if dispatch.withdrawn_at is not None:
        return out
    other = (
        db.query(EinheitSiteDispatch)
        .filter(
            EinheitSiteDispatch.site_id == site.id,
            EinheitSiteDispatch.withdrawn_at.is_(None),
            EinheitSiteDispatch.einheit_id != ctx.einheit.id,
        )
        .all()
    )
    out["andere_einheiten"] = [
        {
            "label": d.einheit.label,
            "einheit_status": d.einheit_status,
            "einheit_status_label": EINHEIT_STATUS_LABEL.get(d.einheit_status, d.einheit_status),
        }
        for d in other
    ]
    if ctx.quelle == "zugang":
        # GK sees only their own dispatch; labels and status of other units are
        # operationally sensitive and not needed to execute that dispatch.
        out["andere_einheiten"] = []
    logs = (
        db.query(SiteLogEntry)
        .filter(SiteLogEntry.incident_site_id == site.id)
        .order_by(SiteLogEntry.ts.desc())
        .limit(50)
        .all()
    )
    out["chronik"] = [
        {
            "ts": _iso_z(x.ts),
            "kind": x.kind,
            "kind_label": SITE_LOG_KIND_LABEL.get(x.kind, x.kind),
            "text": x.text,
            "author_name": x.author_name,
            "eigene": x.einheit_id == ctx.einheit.id,
        }
        for x in logs
        if ctx.quelle != "zugang" or x.einheit_id == ctx.einheit.id
    ]
    media = (
        db.query(SiteMedia)
        .filter(
            SiteMedia.incident_site_id == site.id,
            *([SiteMedia.einheit_id == ctx.einheit.id] if ctx.quelle == "zugang" else []),
        )
        .order_by(SiteMedia.uploaded_at.desc())
        .all()
    )
    out["fotos"] = [
        {
            "id": x.id,
            "thumb_url": f"/einheit/medien/thumb/{x.id}",
            "url": f"/einheit/medien/{x.id}",
            "kommentar": x.kommentar,
            "author_name": x.author_name,
            "uploaded_at": _iso_z(x.uploaded_at),
        }
        for x in media
    ]
    out["objekte"] = []
    if site.incident_id:
        from app.models.incident import Incident

        incident = db.get(Incident, site.incident_id)
        if incident:
            out["objekte"] = [
                {
                    "id": link.objekt.id,
                    "bezeichnung": link.objekt.name,
                    "adresse": link.objekt.adresse_zeile,
                    "status": link.status,  # bestaetigt | vorschlag
                    "url": f"/objekte/{link.objekt.id}",
                }
                for link in incident.objekt_links
            ]
    out["gefahren"] = _gefahren(db, ctx, site)
    out["strassensperren"] = _sperren(db, ctx, site)
    out["navigation_hinweis"] = "Externe Navigation berücksichtigt keine ECP-Straßensperren."
    return out


def _gefahren(db: Session, ctx: EinheitKontext, site: IncidentSite) -> list[dict]:
    if site.lat is None or site.lng is None:
        return []
    result = []
    for marker in (
        db.query(CrossSiteMarker)
        .filter(CrossSiteMarker.major_incident_id == ctx.lage.id, CrossSiteMarker.status != "behoben")
        .all()
    ):
        if marker.lat is None or marker.lng is None:
            continue
        abstand = math.hypot(
            (marker.lat - site.lat) * 111_320, (marker.lng - site.lng) * 111_320 * math.cos(math.radians(site.lat))
        )
        if abstand <= 300:
            result.append(
                {
                    "title": marker.title,
                    "type_label": marker.type_label,
                    "status_label": marker.status_label,
                    "abstand_m": round(abstand),
                }
            )
    return result


def _sperren(db: Session, ctx: EinheitKontext, site: IncidentSite) -> list[dict]:
    if site.lat is None or site.lng is None:
        return []
    try:
        from app.services.road_closure_flags import strassensperren_effective_enabled

        if not strassensperren_effective_enabled(ctx.org_id, db):
            return []
        from app.services.road_closure_incident_service import relevant_closures

        rows, _ = relevant_closures(db, ctx.org_id, None, (site.lat, site.lng))
        return [{"title": x.title, "restriction_label": x.restriction_label} for x in rows]
    except Exception:
        return []


@router.post("/api/auftrag/{dispatch_id}/status")
async def status(
    dispatch_id: int, request: Request, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)
):
    _schreibbar(ctx)
    einheit_darf("status", ctx, db)
    body = await request.json()
    try:
        dispatch = auftrag_laden(db, ctx, dispatch_id)
    except LookupError:
        raise HTTPException(status_code=404)
    erfasst, hinweis = _zeit(body.get("erfasst_at"))

    def run() -> dict:
        result = setze_einheit_status(
            db,
            ctx,
            dispatch,
            body.get("status", ""),
            user_id=getattr(getattr(request.state, "user", None), "id", None),
            author_name=_autor(request, ctx),
            grund=body.get("grund"),
            unterbrechen=bool(body.get("unterbrechen")),
            erfasst_at=erfasst,
        )
        answer = {
            "ok": True,
            "entity_id": dispatch.id,
            "entity_type": "dispatch",
            "server_time": _iso_z(datetime.now(UTC)),
            "auftrag_version": dispatch.version,
            **result,
        }
        if hinweis:
            answer["hinweis"] = hinweis
        return answer

    try:
        answer, code = fuehre_aktion_aus(
            db,
            ctx,
            client_uuid=body.get("client_uuid"),
            aktion="status",
            erfasst_at=erfasst,
            dispatch_id=dispatch.id,
            ausfuehren=run,
        )
    except HTTPException as exc:
        return _antwort_exc(exc)
    if code == 200 and answer.get("ok"):
        await _senden(
            ctx.lage.id,
            dispatch.site_id,
            ctx.einheit.id,
            dispatch.id,
            dispatch.site.phase.value if answer.get("phase_geaendert") else None,
        )
        if answer.get("phase_geaendert"):
            await notify_gsl_live(db, ctx.lage, org_id=ctx.lage.org_id, reason="counts")
    return JSONResponse(answer, status_code=code)


@router.post("/api/auftrag/{dispatch_id}/meldung")
async def meldung(
    dispatch_id: int, request: Request, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)
):
    _schreibbar(ctx)
    einheit_darf("meldung", ctx, db)
    body = await request.json()
    try:
        dispatch = auftrag_laden(db, ctx, dispatch_id)
    except LookupError:
        raise HTTPException(status_code=404)
    felder = body.get("felder") or {}
    if not isinstance(felder, dict):
        return JSONResponse({"code": "felder_ungueltig"}, status_code=400)
    text = (body.get("text") or format_lagemeldung(felder)).strip()
    if not text or len(text) > 4000:
        return JSONResponse({"code": "text_ungueltig"}, status_code=400)
    if dispatch.withdrawn_at is not None:
        text += " (nach Rückzug eingegangen)"
    erfasst, korr = _zeit(body.get("erfasst_at"))

    def run() -> dict:
        entry = add_site_log(
            db,
            dispatch.site,
            normalisiere_user_kind(body.get("art", "")),
            text,
            user_id=getattr(getattr(request.state, "user", None), "id", None),
            author_name=_autor(request, ctx),
            einheit_id=ctx.einheit.id,
            erfasst_at=erfasst,
        )
        dispatch.letzte_rueckmeldung_at = datetime.now(UTC)
        write_audit(
            db,
            "gsl.einheit.meldung",
            user_id=getattr(getattr(request.state, "user", None), "id", None),
            payload={
                "dispatch_id": dispatch.id, "quelle": ctx.quelle,
                "via": "zugang" if ctx.quelle == "zugang" else None,
                "zugang_id": ctx.zugang.id if ctx.zugang else None,
                "generation": ctx.zugang.generation if ctx.zugang else None,
            },
        )
        hint = korr or (
            "auftrag_geaendert"
            if body.get("auftrag_version") is not None and body["auftrag_version"] != dispatch.version
            else None
        )
        out = {
            "ok": True,
            "entity_id": entry.id,
            "entity_type": "site_log",
            "server_time": _iso_z(datetime.now(UTC)),
            "auftrag_version": dispatch.version,
        }
        if hint:
            out["hinweis"] = hint
        return out

    try:
        answer, code = fuehre_aktion_aus(
            db,
            ctx,
            client_uuid=body.get("client_uuid"),
            aktion=body.get("art", "meldung"),
            erfasst_at=erfasst,
            dispatch_id=dispatch.id,
            ausfuehren=run,
        )
    except HTTPException as exc:
        return _antwort_exc(exc)
    if code == 200 and answer.get("ok"):
        await _senden(ctx.lage.id, dispatch.site_id, ctx.einheit.id, dispatch.id)
        if body.get("art") == "lagemeldung":
            await broadcast_lage(ctx.lage.id, {"type": "funkjournal:changed"})
    return JSONResponse(answer, status_code=code)


@router.post("/api/auftrag/{dispatch_id}/foto")
async def foto(
    dispatch_id: int,
    request: Request,
    file: UploadFile = File(...),
    client_uuid: str = Form(...),
    erfasst_at: str | None = Form(None),
    kommentar: str | None = Form(None),
    ctx: EinheitKontext = Depends(einheit_kontext),
    db: Session = Depends(get_db),
):
    _schreibbar(ctx)
    einheit_darf("foto", ctx, db)
    try:
        dispatch = auftrag_laden(db, ctx, dispatch_id)
    except LookupError:
        raise HTTPException(status_code=404)
    if kommentar and len(kommentar) > 500:
        return JSONResponse({"code": "kommentar_ungueltig"}, status_code=400)
    parsed, korr = _zeit(erfasst_at)

    try:
        UUID(client_uuid)
    except ValueError, TypeError:
        return JSONResponse({"code": "client_uuid_ungueltig"}, status_code=422)
    existing = db.query(EinheitAktion).filter(EinheitAktion.client_uuid == client_uuid).first()
    if existing:
        same = (
            existing.device_token_id == (ctx.device_token.id if ctx.device_token else None)
            and existing.zugang_id == (ctx.zugang.id if ctx.zugang else None)
        )
        if ctx.simulation:
            same = existing.quelle == ctx.quelle and existing.einheit_id == ctx.einheit.id
        return JSONResponse(
            json.loads(existing.antwort_json or "{}") if same else {"code": "client_uuid_vergeben"},
            status_code=(409 if not same or existing.ergebnis == "konflikt" else 200),
        )
    media = await speichere_site_foto(
        db,
        dispatch.site,
        file,
        org_id=ctx.org_id,
        user_id=getattr(getattr(request.state, "user", None), "id", None),
        author_name=_autor(request, ctx),
        einheit_id=ctx.einheit.id,
        kommentar=kommentar,
        erfasst_at=parsed,
    )
    if dispatch.withdrawn_at is not None:
        # Die von speichere_site_foto erzeugte Chronikzeile markieren.
        db.flush()
        log = (
            db.query(SiteLogEntry)
            .filter(SiteLogEntry.incident_site_id == dispatch.site_id)
            .order_by(SiteLogEntry.id.desc())
            .first()
        )
        if log:
            log.text += " (nach Rückzug eingegangen)"
    dispatch.letzte_rueckmeldung_at = datetime.now(UTC)
    action = EinheitAktion(
        client_uuid=client_uuid,
        org_id=ctx.org_id,
        quelle=ctx.quelle,
        device_token_id=ctx.device_token.id if ctx.device_token else None,
        zugang_id=ctx.zugang.id if ctx.zugang else None,
        einheit_id=ctx.einheit.id,
        dispatch_id=dispatch.id,
        aktion="foto",
        ergebnis="ok",
        entity_type="site_media",
        entity_id=media.id,
        erfasst_at=parsed,
        empfangen_at=datetime.now(UTC),
    )
    db.add(action)
    db.flush()
    answer = {
        "ok": True,
        "aktion_id": action.id,
        "entity_id": media.id,
        "entity_type": "site_media",
        "server_time": _iso_z(datetime.now(UTC)),
        "auftrag_version": dispatch.version,
    }
    if korr:
        answer["hinweis"] = korr
    action.antwort_json = json.dumps(answer, ensure_ascii=False)
    write_audit(
        db,
        "gsl.einheit.foto",
        user_id=getattr(getattr(request.state, "user", None), "id", None),
        payload={
            "dispatch_id": dispatch.id, "quelle": ctx.quelle,
            "via": "zugang" if ctx.quelle == "zugang" else None,
            "zugang_id": ctx.zugang.id if ctx.zugang else None,
            "generation": ctx.zugang.generation if ctx.zugang else None,
        },
    )
    db.commit()
    await _senden(ctx.lage.id, dispatch.site_id, ctx.einheit.id, dispatch.id)
    return answer


@router.get("/api/karte")
def karte(ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)):
    rows = (
        db.query(EinheitSiteDispatch)
        .join(IncidentSite)
        .filter(
            EinheitSiteDispatch.einheit_id == ctx.einheit.id,
            IncidentSite.major_incident_id == ctx.lage.id,
            EinheitSiteDispatch.withdrawn_at.is_(None),
            IncidentSite.lat.is_not(None),
            IncidentSite.lng.is_not(None),
        )
        .all()
    )
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [d.site.lng, d.site.lat]},
            "properties": {
                "dispatch_id": d.id,
                "site_id": d.site_id,
                "bezeichnung": d.site.bezeichnung,
                "einheit_status": d.einheit_status,
                "priority": d.site.priority.name if d.site.priority else None,
                "reihenfolge": d.reihenfolge,
                "aktuell": d.site_id == ctx.einheit.incident_site_id,
            },
        }
        for d in rows
    ]
    if ctx.vehicle:
        pos = (
            db.query(VehiclePosition)
            .filter(VehiclePosition.incident_id == ctx.lage.id, VehiclePosition.vehicle_id == ctx.vehicle.id)
            .order_by(VehiclePosition.received_at.desc())
            .first()
        )
        if pos:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [pos.lon, pos.lat]},
                    "properties": {"typ": "eigene_position"},
                }
            )
    return {"type": "FeatureCollection", "features": features}


def _medium(media_id: int, thumb: bool, ctx: EinheitKontext, db: Session):
    media = db.get(SiteMedia, media_id)
    if not media:
        raise HTTPException(status_code=404)
    allowed = (
        db.query(EinheitSiteDispatch.id)
        .join(IncidentSite)
        .filter(
            EinheitSiteDispatch.einheit_id == ctx.einheit.id,
            EinheitSiteDispatch.site_id == media.incident_site_id,
            IncidentSite.major_incident_id == ctx.lage.id,
        )
        .first()
    )
    if not allowed:
        raise HTTPException(status_code=404)
    path = site_thumb_path(media) if thumb and site_thumb_path(media).exists() else site_media_path(media)
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(path), media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.get("/medien/{media_id}")
def medium(media_id: int, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)):
    return _medium(media_id, False, ctx, db)


@router.get("/medien/thumb/{media_id}")
def medium_thumb(media_id: int, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)):
    return _medium(media_id, True, ctx, db)
