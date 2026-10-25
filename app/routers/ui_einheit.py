"""JSON-API fuer den GSL-Einheitenmodus."""

# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.async_bridge import run_async
from app.core.audit import write_audit
from app.core.permissions import has_role
from app.core.rate_limit import limiter as _limiter
from app.core.security import get_author_name
from app.core.telefon import telefon_maske
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
    LageEinheitLeader,
    LageEinheitPerson,
    SiteLogEntry,
    SiteMedia,
    VehiclePosition,
)
from app.models.master import FireDept, VehicleMaster
from app.models.objekt import ObjektDokumentSeite
from app.models.user import AuditLog, DeviceToken
from app.models.verleih import VerleihAusleihe, VerleihFoto
from app.services import gk_nummer_service, gk_zugang_service, resource_service, ressource_pflege_service
from app.services.broadcast import broadcast_lage
from app.services.einheit_service import (
    EinheitKonflikt,
    EinheitKontext,
    EinheitKontextFehler,
    _auftragsdaten,
    _iso_z,
    auftraege_fuer_einheit,
    auftrag_laden,
    einheiten_fuer_benutzer,
    kontext_fuer_benutzer,
    kontext_fuer_einheit,
    kontext_fuer_geraet,
    kontext_fuer_zugang,
    setze_einheit_status,
)
from app.services.gk_zugang_service import ZugangFehlergrund
from app.services.gsl_live_notify import notify_gsl_live
from app.services.lage_media_service import site_media_path, site_thumb_path, speichere_site_foto
from app.services.site_log_service import (
    add_site_log,
    format_lagemeldung,
    format_schnellmeldung,
    meldung_hervorhebung,
    normalisiere_user_kind,
)

router = APIRouter(prefix="/einheit", tags=["einheit"])

ZUGANG_AKTIONEN = frozenset({
    "status", "meldung", "foto", "anforderung", "antwort", "quittierung",
    "ressource_personal", "ressource_ausstattung",
    "nummer", "material", "material_zurueck",
})


class NummerAnfordernDaten(BaseModel):
    telefon: str


class NummerBestaetigenDaten(BaseModel):
    code: str


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
    from app.services.planspiel_zugriff import simulation_erlaubt

    try:
        sim_id = int(sim)
    except ValueError as exc:
        raise EinheitKontextFehler(404, "einheit_nicht_gefunden", "Einheit nicht gefunden") from exc
    # Planspiel-Allein-Betrieb: Einsatzleiter darf Einheiten seiner laufenden Uebungslage simulieren.
    if not has_role(user, "admin") and not simulation_erlaubt(db, user, sim_id):
        write_audit(db, "gsl.einheit.simulation_abgelehnt", user_id=user.id, payload={"einheit_id": sim})
        db.commit()
        raise EinheitKontextFehler(403, "kein_einheitenkontext", "Keine Berechtigung für die Simulation")
    try:
        return kontext_fuer_einheit(db, user, sim_id, quelle="simulation")
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
    if user is not None and not getattr(request.state, "is_device", False):
        return kontext_fuer_benutzer(db, user, _benutzer_einheit_id(request))
    for typ, cookie_name in (("personal", gk_zugang_service.COOKIE), ("qr", gk_zugang_service.QR_COOKIE)):
        cookie = request.cookies.get(cookie_name)
        if not cookie:
            continue
        principal, _ = gk_zugang_service.sitzung_pruefen_mit_grund(db, cookie, typ)
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
    benutzer_ohne_einheit = bool(user and not getattr(request.state, "is_device", False) and user.member_id)
    if ctx is None and not getattr(request.state, "is_device", False):
        if simulation_fehler:
            return templates.TemplateResponse(request, "einheit/einheit.html", {
                "org": None, "lage_id": None, "einheit_id": None, "einheit_label": "Fahrzeug/Gerät",
                "fahrzeug": "Fahrzeug/Gerät", "is_exercise": False, "simulation": False,
                "sim_einheit_id": None, "schreibbar": False, "admin_name": "",
                "start_dispatch_id": start_dispatch_id, "kein_kontext": False,
                "simulation_fehler": simulation_fehler.nachricht,
            }, status_code=simulation_fehler.status_code)
        if not benutzer_ohne_einheit:
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
    benutzer_einheiten = []
    if ctx and ctx.quelle == "benutzer":
        benutzer_einheiten = [
            {"id": einheit.id, "label": einheit.label, "lage_name": lage.name}
            for einheit, lage, _leader in einheiten_fuer_benutzer(db, user)
        ]
    return templates.TemplateResponse(request, "einheit/einheit.html", {
        "org": org, "lage_id": ctx.lage.id if ctx else None, "einheit_id": ctx.einheit.id if ctx else None,
        "einheit_label": ctx.einheit.label if ctx else vehicle_label,
        "fahrzeug": (ctx.vehicle.code or ctx.vehicle.name) if ctx and ctx.vehicle else (ctx.einheit.label if ctx else vehicle_label),
        "is_exercise": bool(ctx and ctx.lage.is_exercise), "simulation": bool(ctx and ctx.simulation),
        "sim_einheit_id": ctx.einheit.id if ctx and ctx.simulation else None,
        "planspiel_run_id": ctx.lage.planspiel_run_id if ctx and ctx.simulation else None,
        "schreibbar": bool(ctx and not (ctx.simulation and not ctx.lage.is_exercise)),
        "admin_name": getattr(user, "display_name", "") if ctx and ctx.simulation else "",
        "gk_zugang": bool(ctx and ctx.quelle == "zugang"),
        "gk_name": ctx.leader.display_name if ctx and ctx.leader else "",
        "zugang_qr": bool(ctx and ctx.zugang and ctx.zugang.typ == "qr"),
        "benutzer_einheit_id": ctx.einheit.id if ctx and ctx.quelle == "benutzer" else None,
        "benutzer_einheiten": benutzer_einheiten if len(benutzer_einheiten) > 1 else [],
        "benutzer_ohne_einheit": benutzer_ohne_einheit,
        "start_dispatch_id": start_dispatch_id, "kein_kontext": ctx is None, "simulation_fehler": None,
    })


def _einheit_start(antwort):
    """Zurueck in der Einheitenansicht: die Wahl "Gesamtansicht" wird zurueckgesetzt."""
    from app.services.einheit_service import GESAMTANSICHT_COOKIE

    antwort.delete_cookie(GESAMTANSICHT_COOKIE)
    return antwort


@router.get("", response_class=HTMLResponse)
def einheit_seite(request: Request, db: Session = Depends(get_db)):
    return _einheit_start(_einheit_seite(request, db))


@router.get("/auftrag/{dispatch_id}", response_class=HTMLResponse)
def einheit_auftrag_seite(dispatch_id: int, request: Request, db: Session = Depends(get_db)):
    return _einheit_start(_einheit_seite(request, db, dispatch_id))


def _fehler(status: int, code: str, details: dict | None = None) -> NoReturn:
    body: dict[str, Any] = {"code": code}
    if details:
        body["details"] = details
    raise HTTPException(status_code=status, detail=body)


def _benutzer_einheit_id(request: Request) -> int | None:
    wert = request.headers.get("X-EC-Einheit") or request.query_params.get("einheit")
    if wert is None:
        return None
    try:
        return int(wert)
    except ValueError as exc:
        raise EinheitKontextFehler(404, "einheit_nicht_gefunden", "Einheit nicht gefunden") from exc


def _antwort_exc(exc: HTTPException) -> JSONResponse:
    return JSONResponse(exc.detail, status_code=exc.status_code)


def _einheit_kontext_aufloesen(request: Request, db: Session) -> EinheitKontext:
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
        principal = None
        grund = None
        for typ, cookie in (("personal", request.cookies.get(gk_zugang_service.COOKIE)), ("qr", request.cookies.get(gk_zugang_service.QR_COOKIE))):
            if not cookie:
                continue
            principal, grund = gk_zugang_service.sitzung_pruefen_mit_grund(db, cookie, typ)
            if principal:
                # Only the validator's activity timestamp is committed here;
                # no endpoint mutation has run at this point.
                db.commit()
                return kontext_fuer_zugang(db, principal)
            db.rollback()
        if request.cookies.get(gk_zugang_service.COOKIE) or request.cookies.get(gk_zugang_service.QR_COOKIE):
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

    try:
        benutzer_einheit_id = _benutzer_einheit_id(request)
    except EinheitKontextFehler as exc:
        _fehler(exc.status_code, exc.code)
    ctx = kontext_fuer_benutzer(db, user, benutzer_einheit_id)
    if ctx:
        return ctx
    if request.headers.get("X-EC-Einheit") is not None:
        _fehler(403, "kein_einheitenkontext")

    principal = None
    grund = None
    for typ, cookie in (("personal", request.cookies.get(gk_zugang_service.COOKIE)), ("qr", request.cookies.get(gk_zugang_service.QR_COOKIE))):
        if not cookie:
            continue
        principal, grund = gk_zugang_service.sitzung_pruefen_mit_grund(db, cookie, typ)
        if principal:
            db.commit()
            return kontext_fuer_zugang(db, principal)
        db.rollback()
    if request.cookies.get(gk_zugang_service.COOKIE) or request.cookies.get(gk_zugang_service.QR_COOKIE):
        codes = {
            ZugangFehlergrund.WIDERRUFEN: "zugang_widerrufen",
            ZugangFehlergrund.ABGELAUFEN: "zugang_abgelaufen",
            ZugangFehlergrund.UNGUELTIG: "zugang_ungueltig",
        }
        _fehler(401, codes.get(grund or ZugangFehlergrund.UNGUELTIG, "zugang_ungueltig"))

    _fehler(403, "kein_einheitenkontext")


def einheit_kontext(request: Request, db: Session = Depends(get_db)) -> EinheitKontext:
    ctx = _einheit_kontext_aufloesen(request, db)
    # Optionale Vorbedingung; Berechtigungen kommen weiterhin allein aus dem geprüften Kontext.
    erwartet = request.headers.get("X-EC-Einheit-Kontext")
    if erwartet is not None and erwartet != str(ctx.einheit.id):
        _fehler(409, "einheit_gewechselt")
    return ctx


@router.get("/api/dokumente", response_class=HTMLResponse)
def einheit_dokumente(request: Request, ctx: EinheitKontext = Depends(einheit_kontext),
                     db: Session = Depends(get_db)):
    # Dieselben geprüften Kontextparameter für Viewer-/Druckabrufe beibehalten.
    from urllib.parse import urlencode

    from app.services.einsatz_dokument_service import fuer_einheit
    parameter = {k: request.query_params[k] for k in ("sim", "einheit") if k in request.query_params}
    return templates.TemplateResponse(request, "einsatz_dokumente/_einheit.html", {
        "dokumente": fuer_einheit(db, ctx).order_by("id").all(),
        "kontext_query": "?" + urlencode(parameter) if parameter else "",
    }, headers={"Cache-Control": "no-store"})


@router.get("/api/dokumente/{dokument_id}/pdf")
def einheit_dokument_pdf(dokument_id: int, request: Request, ctx: EinheitKontext = Depends(einheit_kontext),
                        db: Session = Depends(get_db)):
    from app.models.einsatz_dokument import EinsatzDokument
    from app.services.dokumentviewer_service import viewer_auswahl
    from app.services.einsatz_dokument_service import fuer_einheit, pdf_daten

    dokument = fuer_einheit(db, ctx).filter(EinsatzDokument.id == dokument_id).first()
    if dokument is None:
        raise HTTPException(404, "Dokument nicht freigegeben.")
    return viewer_auswahl(pdf_daten(db, dokument), request.query_params)


def _schreibbar(ctx: EinheitKontext) -> None:
    if ctx.simulation and not ctx.lage.is_exercise:
        _fehler(403, "simulation_nur_lesend")


def _verleih_aktiv(db: Session, ctx: EinheitKontext) -> None:
    from app.services.mi_feature_service import get_mi_features

    if not get_mi_features(db, ctx.lage.org_id)["geraeteverleih"]:
        _fehler(403, "verleih_deaktiviert")


def einheit_darf(aktion: str, ctx: EinheitKontext, db: Session) -> None:
    if ctx.quelle not in {"zugang", "benutzer"}:
        return
    if aktion not in ZUGANG_AKTIONEN:
        _fehler(403, "zugang_aktion_nicht_erlaubt")
    if ctx.quelle == "zugang" and ctx.zugang and ctx.zugang.typ == "qr" and aktion.startswith("ressource_"):
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


def _aktion_passt(row: EinheitAktion, ctx: EinheitKontext, dispatch_id: int | None, aktion: str) -> bool:
    if (row.org_id != ctx.org_id or row.einheit_id != ctx.einheit.id or row.dispatch_id != dispatch_id
            or row.aktion != aktion or row.quelle != ctx.quelle):
        return False
    if ctx.simulation or ctx.quelle == "benutzer":
        return True
    return (row.device_token_id == (ctx.device_token.id if ctx.device_token else None)
            and row.zugang_id == (ctx.zugang.id if ctx.zugang else None))


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
        if not _aktion_passt(vorhanden, ctx, dispatch_id, aktion):
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
            if not _aktion_passt(replay, ctx, dispatch_id, aktion):
                return {"code": "client_uuid_vergeben"}, 409
            return json.loads(replay.antwort_json or "{}"), 409 if replay.ergebnis == "konflikt" else 200
        raise
    return antwort, status


def _autor(request: Request, ctx: EinheitKontext) -> str | None:
    if ctx.quelle in {"zugang", "benutzer"}:
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
    # Gleiche Gefahrenquelle wie im Detail, gebündelt für die eigenen offenen Aufträge.
    offene = [data["kategorien"]["aktuell"], *data["kategorien"]["weitere"]]
    site_ids = {auftrag["site_id"] for auftrag in offene if auftrag}
    if site_ids:
        stellen = {
            site.id: site for site in db.query(IncidentSite).filter(
                IncidentSite.major_incident_id == ctx.lage.id, IncidentSite.id.in_(site_ids),
            ).all()
        }
        marker = _gefahren_marker(db, ctx)
        for auftrag in offene:
            if auftrag:
                site = stellen[auftrag["site_id"]]
                auftrag["gefahren"] = _gefahren_an_stelle(site, marker)
                auftrag["gefahren_verfuegbar"] = auftrag["hat_koordinaten"]
    from app.services import verleih_service as verleih_svc
    from app.services.mi_feature_service import get_mi_features

    verleih_aktiv = bool(get_mi_features(db, ctx.lage.org_id)["geraeteverleih"])
    data["simulation"] = ctx.simulation
    data["schreibbar"] = not (ctx.simulation and not ctx.lage.is_exercise)
    data["kann_ressource_pflegen"] = (
        data["schreibbar"]
        and ctx.quelle in {"zugang", "benutzer"}
        and not (ctx.zugang and ctx.zugang.typ == "qr")
        and gk_zugang_service.org_einstellungen(db, ctx.org_id).gk_zugang_ressource_pflegen
    )
    data["ressource"] = _ressourcen_daten(db, ctx)
    data["verleih_aktiv"] = verleih_aktiv
    data["material_katalog"] = (
        [
            {
                "id": artikel.id,
                "artikel_nr": artikel.artikel_nr,
                "bezeichnung": artikel.bezeichnung,
                "verfuegbar": artikel.verfuegbarkeit == "verfuegbar",
            }
            for artikel in verleih_svc.get_artikel_aktiv(db)
        ]
        if verleih_aktiv and ctx.einheit.is_from_org
        else []
    )
    leader = db.get(LageEinheitLeader, ctx.einheit.leader_assignment_id) if ctx.einheit.leader_assignment_id else None
    if leader and (leader.einheit_id != ctx.einheit.id or leader.end_at is not None):
        leader = None
    data["nummer"] = {
        "hinweis": bool(ctx.quelle == "zugang" and (not leader or not leader.phone_e164)),
        "maske": telefon_maske(leader.phone_e164) if leader and leader.phone_e164 else None,
        "verifiziert": bool(leader and leader.phone_verifiziert_at),
    }
    raw = json.dumps({k: v for k, v in data.items() if k != "server_time"}, sort_keys=True, default=str)
    etag = hashlib.sha256(raw.encode()).hexdigest()
    if request.headers.get("if-none-match", "").strip('"') == etag:
        return Response(status_code=304, headers={"ETag": f'"{etag}"', "Cache-Control": "no-store"})
    data["etag"] = etag
    return JSONResponse(data, headers={"ETag": f'"{etag}"', "Cache-Control": "no-store"})


@router.post("/api/nummer/anfordern")
@(_limiter.limit("5/15minutes") if _limiter else lambda f: f)
async def nummer_anfordern(request: Request, daten: NummerAnfordernDaten, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)):
    einheit_darf("nummer", ctx, db)
    if ctx.quelle != "zugang":
        _fehler(403, "zugang_aktion_nicht_erlaubt")
    try:
        result = await gk_nummer_service.starte_verifikation(db, ctx, daten.telefon)
    except ValueError as exc:
        status = 429 if "Zu viele" in str(exc) else 503 if "SMS" in str(exc) or "Anbieter" in str(exc) else 422
        return JSONResponse({"ok": False, "meldung": str(exc)}, status_code=status, headers={"Cache-Control": "no-store"})
    return JSONResponse({"ok": True, "maske": result.maske}, headers={"Cache-Control": "no-store"})


@router.post("/api/nummer/bestaetigen")
@(_limiter.limit("10/15minutes") if _limiter else lambda f: f)
def nummer_bestaetigen(request: Request, daten: NummerBestaetigenDaten, background_tasks: BackgroundTasks,
                             ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)):
    einheit_darf("nummer", ctx, db)
    if ctx.quelle != "zugang":
        _fehler(403, "zugang_aktion_nicht_erlaubt")
    try:
        result = gk_nummer_service.bestaetige_verifikation(db, ctx, daten.code)
    except ValueError as exc:
        status = 429 if "Zu viele" in str(exc) else 409 if "Führung" in str(exc) else 422
        return JSONResponse({"ok": False, "meldung": str(exc)}, status_code=status, headers={"Cache-Control": "no-store"})
    if result.auto_sms:
        background_tasks.add_task(gk_zugang_service.sende_auto_sms, result.auto_sms)
    return JSONResponse({"ok": True, "maske": result.maske, "sitzung_beendet": result.sitzung_beendet},
                        headers={"Cache-Control": "no-store"})


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
def ressource_personal(
    request: Request, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)
):
    _schreibbar(ctx)
    einheit_darf("ressource_personal", ctx, db)
    body = run_async(request.json)
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
        run_async(broadcast_lage, ctx.lage.id, {"type": "ressource:changed", "einheit_id": ctx.einheit.id})
    return JSONResponse(answer, status_code=code)


@router.post("/api/ressource/ausstattung")
def ressource_ausstattung(
    request: Request, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)
):
    _schreibbar(ctx)
    einheit_darf("ressource_ausstattung", ctx, db)
    body = run_async(request.json)
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
        run_async(broadcast_lage, ctx.lage.id, {"type": "ressource:changed", "einheit_id": ctx.einheit.id})
    return JSONResponse(answer, status_code=code)


@router.get("/api/meldungen")
def meldungen_verlauf(
    dispatch_id: int | None = None, cursor: str | None = None, mit_status: bool = False,
    limit: int = Query(30, ge=1, le=100),
    ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db),
):
    from app.services.einheit_verlauf_service import meldungsverlauf

    try:
        dispatch = auftrag_laden(db, ctx, dispatch_id) if dispatch_id is not None else None
        data = meldungsverlauf(db, ctx, dispatch=dispatch, cursor=cursor, limit=limit, mit_status=mit_status)
    except LookupError:
        raise HTTPException(status_code=404)
    except ValueError:
        raise HTTPException(status_code=422, detail="Ungültige Verlaufsauswahl")
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.get("/api/auftrag/{dispatch_id}")
def auftrag_detail(dispatch_id: int, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)):
    try:
        dispatch = auftrag_laden(db, ctx, dispatch_id)
    except LookupError:
        raise HTTPException(status_code=404)
    site = dispatch.site
    auftrag = _auftragsdaten(dispatch)
    out: dict[str, Any] = {
        "auftrag": auftrag,
        "stelle": {
            k: _auftragsdaten(dispatch)[k]
            for k in ("site_id", "bezeichnung", "einsatzgrund", "adresse", "lat", "lng", "priority", "phase")
        },
    }
    from app.services.mi_feature_service import get_mi_features

    if get_mi_features(db, ctx.lage.org_id)["geraeteverleih"]:
        material_query = db.query(VerleihAusleihe).filter(
            VerleihAusleihe.site_id == site.id,
            VerleihAusleihe.org_id == ctx.org_id,
            VerleihAusleihe.lage_id == ctx.lage.id,
        )
        if ctx.quelle == "zugang":
            material_query = material_query.filter(VerleihAusleihe.einheit_id == ctx.einheit.id)
        out["material"] = [
            {
                "id": ausleihe.id,
                "status": ausleihe.status,
                "name": ausleihe.name,
                "adresse": ausleihe.adresse,
                "ausgeliehen_at": _iso_z(ausleihe.ausgeliehen_at),
                "zurueckgegeben_at": _iso_z(ausleihe.zurueckgegeben_at) if ausleihe.zurueckgegeben_at else None,
                "einheit_label": ausleihe.einheit.label if ausleihe.einheit else None,
                "eigene": ausleihe.einheit_id == ctx.einheit.id,
                "positionen": [
                    {"bezeichnung": position.bezeichnung, "menge": position.menge, "status": position.status}
                    for position in ausleihe.positionen
                ],
                "fotos": [
                    {
                        "id": foto.id,
                        "thumb_url": f"/einheit/medien/verleih/thumb/{foto.id}",
                        "url": f"/einheit/medien/verleih/{foto.id}",
                    }
                    for foto in ausleihe.fotos
                ],
            }
            for ausleihe in material_query.order_by(VerleihAusleihe.ausgeliehen_at.desc()).all()
        ]
    else:
        out["material"] = []
    if dispatch.withdrawn_at is not None:
        return out
    other = (
        db.query(EinheitSiteDispatch)
        .filter(
            EinheitSiteDispatch.site_id == site.id,
            EinheitSiteDispatch.withdrawn_at.is_(None),
            EinheitSiteDispatch.abgezogen_at.is_(None),
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
    out["andere_einheiten_eingeschraenkt"] = ctx.quelle == "zugang"
    logs = (
        db.query(SiteLogEntry)
        .filter(
            SiteLogEntry.incident_site_id == site.id,
            *([SiteLogEntry.einheit_id == ctx.einheit.id] if ctx.quelle == "zugang" else []),
        )
        .order_by(SiteLogEntry.ts.desc())
        .limit(50)
        .all()
    )
    out["chronik"] = [
        {
            "ts": _iso_z(x.ts),
            "kind": x.kind,
            "kind_label": SITE_LOG_KIND_LABEL.get(x.kind, x.kind),
            "hervorhebung": meldung_hervorhebung(x.text),
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
    from app.services.einheit_vorschau_service import objektvorschau

    out["objekte"] = objektvorschau(db, ctx, dispatch)
    out["gefahren"] = _gefahren(db, ctx, site)
    from app.services.einheit_karten_service import ziel_sperren

    out.update(ziel_sperren(db, ctx.org_id, site))
    return out


def _gefahren(db: Session, ctx: EinheitKontext, site: IncidentSite) -> list[dict]:
    if site.lat is None or site.lng is None:
        return []
    return _gefahren_an_stelle(site, _gefahren_marker(db, ctx))


def _gefahren_marker(db: Session, ctx: EinheitKontext) -> list[CrossSiteMarker]:
    return db.query(CrossSiteMarker).filter(
        CrossSiteMarker.major_incident_id == ctx.lage.id, CrossSiteMarker.status != "behoben",
    ).order_by(CrossSiteMarker.id).all()


def _gefahren_an_stelle(site: IncidentSite, marker_liste: list[CrossSiteMarker]) -> list[dict]:
    from app.services.einheit_karten_service import hat_zielkoordinaten

    if site.lat is None or site.lng is None or not hat_zielkoordinaten(site):
        return []
    result = []
    for marker in marker_liste:
        if marker.lat is None or marker.lng is None:
            continue
        abstand = math.hypot(
            (marker.lat - site.lat) * 111_320, (marker.lng - site.lng) * 111_320 * math.cos(math.radians(site.lat))
        )
        if abstand <= 300:
            result.append(
                {
                    "id": marker.id, "lat": marker.lat, "lng": marker.lng,
                    "title": marker.title,
                    "type_label": marker.type_label,
                    "status_label": marker.status_label,
                    "abstand_m": round(abstand),
                }
            )
    return result


def _sperren(db: Session, ctx: EinheitKontext, site: IncidentSite) -> list[dict]:
    from app.services.einheit_karten_service import ziel_sperren

    return ziel_sperren(db, ctx.org_id, site)["strassensperren"]


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

    def ergebnis_pruefen() -> None:
        if not isinstance(body.get("ergebnis_client_uuid"), str):
            _fehler(422, "erkundungsergebnis_erforderlich")
        ergebnis = db.query(EinheitAktion).filter(
            EinheitAktion.client_uuid == body.get("ergebnis_client_uuid"),
            EinheitAktion.org_id == ctx.org_id,
            EinheitAktion.einheit_id == ctx.einheit.id,
            EinheitAktion.dispatch_id == dispatch.id,
            EinheitAktion.ergebnis == "ok",
            EinheitAktion.entity_type == "site_log",
            EinheitAktion.aktion == "lagemeldung",
        ).first()
        antwort = json.loads(ergebnis.antwort_json or "{}") if ergebnis else {}
        if (not ergebnis or not antwort.get("erkundungsergebnis") or antwort.get("auftrag_version") != dispatch.version
                or not db.get(SiteLogEntry, ergebnis.entity_id)):
            _fehler(422, "erkundungsergebnis_erforderlich")

    def run() -> dict:
        if body.get("status") == "nicht_durchfuehrbar" and (
            not isinstance(body.get("grund"), str) or not body["grund"].strip()
        ):
            _fehler(422, "grund_erforderlich")
        result = setze_einheit_status(
            db,
            ctx,
            dispatch,
            body.get("status", ""),
            user_id=getattr(getattr(request.state, "user", None), "id", None),
            author_name=_autor(request, ctx),
            grund=body.get("grund"),
            unterbrechen=bool(body.get("unterbrechen")),
            auftrag_version=body.get("auftrag_version"),
            ausgangsstatus=body.get("ausgangsstatus"),
            aktiver_dispatch_id=body.get("aktiver_dispatch_id"),
            erfasst_at=erfasst,
            erkundungsergebnis_pruefen=ergebnis_pruefen,
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
        answer, code = resource_service.status_transaktion(db, lambda: fuehre_aktion_aus(
            db, ctx, client_uuid=body.get("client_uuid"), aktion="status", erfasst_at=erfasst,
            dispatch_id=dispatch.id, ausfuehren=run,
        ))
    except resource_service.StatusTransaktionBelegt:
        return JSONResponse({"code": "status_abgleich_noetig"}, status_code=503)
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
        if answer.get("unterbrochen_dispatch_id"):
            alter = auftrag_laden(db, ctx, answer["unterbrochen_dispatch_id"])
            await _senden(ctx.lage.id, alter.site_id, ctx.einheit.id, alter.id)
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
    if not isinstance(body.get("art", ""), str):
        return JSONResponse({"code": "art_ungueltig"}, status_code=400)
    if not isinstance(felder, dict) or any(v is not None and not isinstance(v, str) for v in felder.values()):
        return JSONResponse({"code": "felder_ungueltig"}, status_code=400)
    if body.get("text") is not None and not isinstance(body["text"], str):
        return JSONResponse({"code": "text_ungueltig"}, status_code=400)
    ergebnis = body.get("erkundungsergebnis") is True
    erfasst, korr = _zeit(body.get("erfasst_at"))

    def run() -> dict:
        text = (body.get("text") or "").strip()
        strukturiert = format_lagemeldung(felder)
        if body.get("schnellmeldung") or ergebnis:
            if ergebnis and body.get("schnellmeldung") != "keine_massnahmen" and sum(
                c.isalnum() for c in text + "".join(v or "" for v in felder.values())
            ) < 3:
                _fehler(422, "erkundungsergebnis_erforderlich")
            try:
                text = format_schnellmeldung(dispatch.auftragsart, body.get("schnellmeldung"),
                                            "\n".join(filter(None, [text, strukturiert])), ergebnis=ergebnis)
            except ValueError as exc:
                _fehler(422, str(exc))
            if body.get("art") != "lagemeldung":
                _fehler(422, "schnellmeldung_ungueltig")
        else:
            text = "\n".join(filter(None, [text, strukturiert]))
        if not text or len(text) > 4000:
            _fehler(400, "text_ungueltig")
        if dispatch.withdrawn_at is not None:
            text += "\n(nach Rückzug eingegangen)"
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
        db.flush()  # Chronik-ID vor der Aktionsantwort sichern (auch bei Notiz/Maßnahme).
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
            "erkundungsergebnis": ergebnis,
            "hervorhebung": meldung_hervorhebung(text),
            "meldung_text": text,
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
        same = _aktion_passt(existing, ctx, dispatch.id, "foto")
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
    db.flush()  # Foto-ID vor der Verknüpfung im bestehenden Aktionsprotokoll sichern.
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


def _material_positionen(body: dict[str, Any]) -> list[dict[str, Any]] | None:
    positionen = body.get("positionen")
    if not isinstance(positionen, list) or not positionen or len(positionen) > 30:
        return None
    for position in positionen:
        if not isinstance(position, dict):
            return None
        bezeichnung = position.get("bezeichnung")
        menge = position.get("menge", 1)
        artikel_id = position.get("artikel_id")
        if not isinstance(bezeichnung, str) or not bezeichnung.strip() or len(bezeichnung) > 200:
            return None
        if isinstance(menge, bool) or not isinstance(menge, int) or not 1 <= menge <= 9999:
            return None
        if artikel_id is not None and (isinstance(artikel_id, bool) or not isinstance(artikel_id, int)):
            return None
    return positionen


@router.post("/api/auftrag/{dispatch_id}/material")
async def material(
    dispatch_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    ctx: EinheitKontext = Depends(einheit_kontext),
    db: Session = Depends(get_db),
):
    _schreibbar(ctx)
    einheit_darf("material", ctx, db)
    _verleih_aktiv(db, ctx)
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"code": "body_ungueltig"}, status_code=400)
    try:
        dispatch = auftrag_laden(db, ctx, dispatch_id)
    except LookupError:
        raise HTTPException(status_code=404)
    positionen = _material_positionen(body)
    if positionen is None:
        return JSONResponse({"code": "positionen_ungueltig"}, status_code=400)
    for feld, maximum in (("name", 200), ("telefon", 50), ("adresse", 300), ("notizen", 2000)):
        value = body.get(feld)
        if value is not None and (not isinstance(value, str) or len(value) > maximum):
            return JSONResponse({"code": f"{feld}_ungueltig"}, status_code=400)
    for feld in ("sms_senden", "pin_senden"):
        if feld in body and not isinstance(body[feld], bool):
            return JSONResponse({"code": f"{feld}_ungueltig"}, status_code=400)
    if body.get("erfasst_at") is not None and not isinstance(body.get("erfasst_at"), str):
        return JSONResponse({"code": "erfasst_at_ungueltig"}, status_code=400)
    client_uuid = body.get("client_uuid")
    if not isinstance(client_uuid, str):
        return JSONResponse({"code": "client_uuid_ungueltig"}, status_code=422)
    erfasst, korr = _zeit(body.get("erfasst_at"))
    ausgefuehrt = False

    def run() -> dict[str, Any]:
        nonlocal ausgefuehrt
        from app.services import verleih_service as svc

        ausleihe = svc.material_an_einsatzstelle(
            db,
            lage=ctx.lage,
            site=dispatch.site,
            positionen=positionen,
            name=body.get("name"),
            telefon=body.get("telefon"),
            adresse=body.get("adresse"),
            notizen=body.get("notizen"),
            einheit=ctx.einheit,
            quelle="einheit",
            user_id=getattr(getattr(request.state, "user", None), "id", None),
            author_name=_autor(request, ctx),
            pin=svc.generate_pin() if body.get("pin_senden") else None,
        )
        ausgefuehrt = True
        dispatch.letzte_rueckmeldung_at = datetime.now(UTC)
        write_audit(
            db,
            "gsl.einheit.material",
            user_id=getattr(getattr(request.state, "user", None), "id", None),
            payload={
                "dispatch_id": dispatch.id, "quelle": ctx.quelle,
                "via": "zugang" if ctx.quelle == "zugang" else None,
                "zugang_id": ctx.zugang.id if ctx.zugang else None,
                "generation": ctx.zugang.generation if ctx.zugang else None,
            },
        )
        answer = {
            "ok": True,
            "entity_type": "verleih_ausleihe",
            "entity_id": ausleihe.id,
            "server_time": _iso_z(datetime.now(UTC)),
            "auftrag_version": dispatch.version,
        }
        if korr:
            answer["hinweis"] = korr
        return answer

    try:
        answer, code = fuehre_aktion_aus(
            db, ctx, client_uuid=client_uuid, aktion="material", erfasst_at=erfasst,
            dispatch_id=dispatch.id, ausfuehren=run,
        )
    except HTTPException as exc:
        return _antwort_exc(exc)
    if code == 200 and answer.get("ok") and ausgefuehrt:
        from app.services import verleih_service as svc
        from app.services.print_dispatcher import autoprint_verleih_background

        if body.get("sms_senden") and body.get("telefon"):
            background_tasks.add_task(svc.sende_material_sms, answer["entity_id"], ctx.org_id, bool(body.get("pin_senden")))
        background_tasks.add_task(autoprint_verleih_background, answer["entity_id"])
        await _senden(ctx.lage.id, dispatch.site_id, ctx.einheit.id, dispatch.id)
        await broadcast_lage(ctx.lage.id, {"type": "verleih:changed", "lage_id": ctx.lage.id})
    return JSONResponse(answer, status_code=code)


@router.post("/api/auftrag/{dispatch_id}/material_foto")
async def material_foto(
    dispatch_id: int,
    request: Request,
    file: UploadFile = File(...),
    client_uuid: str = Form(...),
    erfasst_at: str | None = Form(None),
    material_client_uuid: str = Form(...),
    ctx: EinheitKontext = Depends(einheit_kontext),
    db: Session = Depends(get_db),
):
    _schreibbar(ctx)
    einheit_darf("material", ctx, db)
    _verleih_aktiv(db, ctx)
    try:
        dispatch = auftrag_laden(db, ctx, dispatch_id)
    except LookupError:
        raise HTTPException(status_code=404)
    parsed, korr = _zeit(erfasst_at)
    try:
        UUID(client_uuid)
    except (ValueError, TypeError):
        return JSONResponse({"code": "client_uuid_ungueltig"}, status_code=422)
    existing = db.query(EinheitAktion).filter(EinheitAktion.client_uuid == client_uuid).first()
    if existing:
        same = _aktion_passt(existing, ctx, dispatch.id, "material_foto")
        return JSONResponse(
            json.loads(existing.antwort_json or "{}") if same else {"code": "client_uuid_vergeben"},
            status_code=409 if not same or existing.ergebnis == "konflikt" else 200,
        )
    material_aktion = (
        db.query(EinheitAktion)
        .filter(
            EinheitAktion.client_uuid == material_client_uuid,
            EinheitAktion.aktion == "material",
            EinheitAktion.ergebnis == "ok",
            EinheitAktion.einheit_id == ctx.einheit.id,
        )
        .first()
    )
    ausleihe = db.get(VerleihAusleihe, material_aktion.entity_id) if material_aktion and material_aktion.entity_id else None
    if not ausleihe or ausleihe.site_id != dispatch.site_id or ausleihe.org_id != ctx.org_id:
        return JSONResponse({"code": "material_unbekannt"}, status_code=409)
    from app.services import verleih_service as svc

    foto = await svc.save_verleih_foto(
        file, ausleihe.id, ctx.org_id, getattr(getattr(request.state, "user", None), "id", None), db,
        author_name=_autor(request, ctx),
    )
    dispatch.letzte_rueckmeldung_at = datetime.now(UTC)
    action = EinheitAktion(
        client_uuid=client_uuid, org_id=ctx.org_id, quelle=ctx.quelle,
        device_token_id=ctx.device_token.id if ctx.device_token else None,
        zugang_id=ctx.zugang.id if ctx.zugang else None, einheit_id=ctx.einheit.id,
        dispatch_id=dispatch.id, aktion="material_foto", ergebnis="ok",
        entity_type="verleih_foto", entity_id=foto.id, erfasst_at=parsed, empfangen_at=datetime.now(UTC),
    )
    db.add(action)
    db.flush()
    answer: dict[str, Any] = {
        "ok": True, "aktion_id": action.id, "entity_type": "verleih_foto", "entity_id": foto.id,
        "server_time": _iso_z(datetime.now(UTC)), "auftrag_version": dispatch.version,
    }
    if korr:
        answer["hinweis"] = korr
    action.antwort_json = json.dumps(answer, ensure_ascii=False)
    db.commit()
    await _senden(ctx.lage.id, dispatch.site_id, ctx.einheit.id, dispatch.id)
    await broadcast_lage(ctx.lage.id, {"type": "verleih:changed", "lage_id": ctx.lage.id})
    return answer


@router.post("/api/auftrag/{dispatch_id}/material_zurueck")
async def material_zurueck(
    dispatch_id: int, request: Request, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)
):
    _schreibbar(ctx)
    einheit_darf("material_zurueck", ctx, db)
    _verleih_aktiv(db, ctx)
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"code": "body_ungueltig"}, status_code=400)
    try:
        dispatch = auftrag_laden(db, ctx, dispatch_id)
    except LookupError:
        raise HTTPException(status_code=404)
    ausleihe_id = body.get("ausleihe_id")
    if isinstance(ausleihe_id, bool) or not isinstance(ausleihe_id, int):
        return JSONResponse({"code": "ausleihe_id_ungueltig"}, status_code=400)
    if body.get("erfasst_at") is not None and not isinstance(body.get("erfasst_at"), str):
        return JSONResponse({"code": "erfasst_at_ungueltig"}, status_code=400)
    client_uuid = body.get("client_uuid")
    if not isinstance(client_uuid, str):
        return JSONResponse({"code": "client_uuid_ungueltig"}, status_code=422)
    ausleihe = db.get(VerleihAusleihe, ausleihe_id)
    if not ausleihe:
        raise HTTPException(status_code=404)
    if (
        ausleihe.org_id != ctx.org_id or ausleihe.lage_id != ctx.lage.id
        or ausleihe.site_id != dispatch.site_id or ausleihe.einheit_id != ctx.einheit.id
    ):
        return JSONResponse({"code": "material_fremd"}, status_code=403)
    erfasst, korr = _zeit(body.get("erfasst_at"))

    def run() -> dict[str, Any]:
        from app.services import verleih_service as svc

        result = svc.material_zuruecknehmen(
            db, ausleihe, author_name=_autor(request, ctx),
            user_id=getattr(getattr(request.state, "user", None), "id", None), einheit_id=ctx.einheit.id,
        )
        dispatch.letzte_rueckmeldung_at = datetime.now(UTC)
        answer = {
            "ok": True, "entity_type": "verleih_ausleihe", "entity_id": result.id,
            "server_time": _iso_z(datetime.now(UTC)), "auftrag_version": dispatch.version,
        }
        if korr:
            answer["hinweis"] = korr
        return answer

    try:
        answer, code = fuehre_aktion_aus(
            db, ctx, client_uuid=client_uuid, aktion="material_zurueck", erfasst_at=erfasst,
            dispatch_id=dispatch.id, ausfuehren=run,
        )
    except HTTPException as exc:
        return _antwort_exc(exc)
    if code == 200 and answer.get("ok"):
        await _senden(ctx.lage.id, dispatch.site_id, ctx.einheit.id, dispatch.id)
        await broadcast_lage(ctx.lage.id, {"type": "verleih:changed", "lage_id": ctx.lage.id})
    return JSONResponse(answer, status_code=code)


@router.get("/api/karte")
def karte(ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)):
    from app.services.einheit_karten_service import hat_zielkoordinaten

    rows = (
        db.query(EinheitSiteDispatch)
        .join(IncidentSite)
        .filter(
            EinheitSiteDispatch.einheit_id == ctx.einheit.id,
            IncidentSite.major_incident_id == ctx.lage.id,
            EinheitSiteDispatch.withdrawn_at.is_(None),
            EinheitSiteDispatch.abgezogen_at.is_(None),
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
                "auftragsart": d.auftragsart,
                "priority": d.site.priority.name if d.site.priority else None,
                "reihenfolge": d.reihenfolge,
                "aktuell": d.site_id == ctx.einheit.incident_site_id,
            },
        }
        for d in rows
        if hat_zielkoordinaten(d.site)
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
    if not media or (ctx.quelle == "zugang" and media.einheit_id != ctx.einheit.id):
        raise HTTPException(status_code=404)
    allowed = (
        db.query(EinheitSiteDispatch.id)
        .join(IncidentSite)
        .filter(
            EinheitSiteDispatch.einheit_id == ctx.einheit.id,
            EinheitSiteDispatch.site_id == media.incident_site_id,
            EinheitSiteDispatch.withdrawn_at.is_(None),
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


@router.get("/medien/auftrag/{dispatch_id}/plan/{seite_id}/{format}")
def auftrag_plan(
    dispatch_id: int, seite_id: int, format: Literal["pdf", "bild", "thumb"],
    ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db),
):
    from app.services.einheit_vorschau_service import einsatzobjekte, einsatzseiten
    from app.services.objekt_dokument_service import absolute_pfad

    try:
        dispatch = auftrag_laden(db, ctx, dispatch_id)
    except LookupError:
        raise HTTPException(status_code=404)
    objekte = einsatzobjekte(db, ctx, dispatch)
    seite = einsatzseiten(db, ctx, [objekt.id for objekt in objekte]).filter(ObjektDokumentSeite.id == seite_id).first()
    if not seite:
        raise HTTPException(status_code=404)
    relativ = {"pdf": seite.einzel_pdf_pfad, "bild": seite.bild_pfad, "thumb": seite.thumb_pfad}[format]
    if not relativ or not absolute_pfad(relativ).is_file():
        raise HTTPException(status_code=404)
    pfad = absolute_pfad(relativ)
    if format != "pdf":
        from app.routers.ui_objekt_dokumente import _bild_response
        return _bild_response(pfad, "image/jpeg" if format == "thumb" else "image/png", seite.rotation)
    return FileResponse(pfad, media_type="application/pdf", headers={"Cache-Control": "no-cache"},
                        content_disposition_type="inline")


def _verleih_medium(foto_id: int, thumb: bool, ctx: EinheitKontext, db: Session):
    foto = db.get(VerleihFoto, foto_id)
    if not foto:
        raise HTTPException(status_code=404)
    ausleihe = db.get(VerleihAusleihe, foto.ausleihe_id)
    if (
        not ausleihe or ausleihe.org_id != ctx.org_id or ausleihe.lage_id != ctx.lage.id
        or (ctx.quelle == "zugang" and ausleihe.einheit_id != ctx.einheit.id)
    ):
        raise HTTPException(status_code=404)
    from app.services import verleih_service as svc

    path = svc.foto_thumb_path(foto) if thumb and svc.foto_thumb_path(foto).exists() else svc.foto_path(foto)
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(path), media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.get("/medien/verleih/{foto_id}")
def verleih_medium(foto_id: int, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)):
    return _verleih_medium(foto_id, False, ctx, db)


@router.get("/medien/verleih/thumb/{foto_id}")
def verleih_medium_thumb(foto_id: int, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)):
    return _verleih_medium(foto_id, True, ctx, db)


@router.get("/medien/{media_id}")
def medium(media_id: int, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)):
    return _medium(media_id, False, ctx, db)


@router.get("/medien/thumb/{media_id}")
def medium_thumb(media_id: int, ctx: EinheitKontext = Depends(einheit_kontext), db: Session = Depends(get_db)):
    return _medium(media_id, True, ctx, db)
