"""Fahrtenbuch – Erfassungsformular (Web-Login + Token/QR)."""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.templating import templates
from app.db import get_db
from app.models.fahrtenbuch import FahrtErfassungsweg, FahrtKategorie, Fahrtzweck, Zielort
from app.models.incident import Incident
from app.models.master import Member, MemberQualification, OrgSettings, Qualification, VehicleMaster
from app.services.fahrtenbuch_service import erstelle_fahrt, pruefe_doppelfahrt, pruefe_zaehler
from app.services.schaden_service import melde_schaden

router = APIRouter()
logger = logging.getLogger("einsatzleiter.fahrtenbuch")

# slowapi Rate-Limiter (optional, fällt ohne Decorator graceful zurück)
try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    _limiter = Limiter(key_func=get_remote_address)
except ImportError:
    _limiter = None


def _resolve_org_by_token(token: str, db: Session):
    """Liefert OrgSettings zur Token-Org oder None."""
    return db.query(OrgSettings).filter(OrgSettings.fahrtenbuch_token == token).execution_options(include_all_tenants=True).first()


def _resolve_vehicle_by_qr(qr_token: str, db: Session):
    return db.query(VehicleMaster).filter(VehicleMaster.qr_token == qr_token).execution_options(include_all_tenants=True).first()


def _current_user(request: Request):
    return getattr(request.state, "user", None)


# ── Web-Login ──────────────────────────────────────────────────────────────────

@router.get("/fahrtenbuch/neu", response_class=HTMLResponse)
async def fahrtenbuch_neu(
    request: Request,
    fahrzeug: int | None = None,
    db: Session = Depends(get_db),
):
    user = _current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return await _render_erfassung(request, db, user=user, preset_fahrzeug_id=fahrzeug)


@router.post("/fahrtenbuch", response_class=HTMLResponse)
async def fahrtenbuch_speichern(
    request: Request,
    db: Session = Depends(get_db),
):
    user = _current_user(request)
    # Token-basierter Zugriff: org_id aus Session-State
    token_org: OrgSettings | None = getattr(request.state, "fahrtenbuch_org", None)
    if not user and not token_org:
        raise HTTPException(status_code=401, detail="Nicht authentifiziert")

    org_id = user.org_id if user else token_org.org_id

    form = await request.form()
    daten = _form_zu_daten(form, org_id=org_id, user=user, token_org=token_org)

    try:
        fahrt = erstelle_fahrt(daten, db)
        if fahrt.schaden_vorhanden:
            base_url = str(request.base_url).rstrip("/")
            await melde_schaden(fahrt, db, base_url=base_url)
        db.commit()
    except HTTPException as exc:
        db.rollback()
        # Warnung-Flags: Formular erneut anzeigen mit Fehlermeldung
        fahrzeug_id = int(form.get("fahrzeug_id") or 0)
        fahrzeug = db.query(VehicleMaster).filter(VehicleMaster.id == fahrzeug_id).execution_options(include_all_tenants=True).first() if fahrzeug_id else None
        return await _render_erfassung(
            request, db, user=user, preset_fahrzeug_id=fahrzeug_id,
            fehler=exc.detail, form_daten=dict(form),
            token_org=token_org,
        )

    return templates.TemplateResponse(request, "fahrtenbuch/erfolg.html", {
        "user": user,
        "fahrt": fahrt,
        "token_org": token_org,
    })


# ── Token/QR ──────────────────────────────────────────────────────────────────

@router.get("/f/{token}", response_class=HTMLResponse)
async def token_formular(request: Request, token: str, db: Session = Depends(get_db)):
    org_settings = _resolve_org_by_token(token, db)
    if not org_settings:
        raise HTTPException(status_code=404, detail="Token ungültig")
    request.state.fahrtenbuch_org = org_settings
    return await _render_erfassung(request, db, token_org=org_settings)


@router.get("/f/{token}/v/{qr_token}", response_class=HTMLResponse)
async def token_qr_formular(request: Request, token: str, qr_token: str, db: Session = Depends(get_db)):
    org_settings = _resolve_org_by_token(token, db)
    if not org_settings:
        raise HTTPException(status_code=404, detail="Token ungültig")
    fahrzeug = _resolve_vehicle_by_qr(qr_token, db)
    if not fahrzeug or fahrzeug.dept_id != org_settings.org_id:
        raise HTTPException(status_code=404, detail="Fahrzeug-QR ungültig")
    request.state.fahrtenbuch_org = org_settings
    return await _render_erfassung(
        request, db, token_org=org_settings, preset_fahrzeug_id=fahrzeug.id
    )


# ── HTMX-Partials ─────────────────────────────────────────────────────────────

@router.get("/fahrtenbuch/hx/maschinist", response_class=HTMLResponse)
async def hx_maschinist_autocomplete(
    request: Request, db: Session = Depends(get_db)
):
    # HTMX sendet den Feldnamen als Parameter-Key (maschinist_name, ausbildner_name …)
    qp = request.query_params
    q = qp.get("q") or qp.get("maschinist_name") or qp.get("ausbildner_name") or ""
    user = _current_user(request)
    token_org: OrgSettings | None = getattr(request.state, "fahrtenbuch_org", None)
    org_id = user.org_id if user else (token_org.org_id if token_org else None)
    if not org_id or len(q) < 2:
        return HTMLResponse("")

    members = (
        db.query(Member)
        .filter(Member.active == True, Member.lastname.ilike(f"%{q}%") | Member.firstname.ilike(f"%{q}%"))  # noqa: E712
        .order_by(Member.lastname, Member.firstname)
        .limit(10)
        .all()
    )
    html = "<ul class='autocomplete-list'>"
    for m in members:
        html += (
            f"<li class='autocomplete-item' "
            f"data-member-id='{m.id}' "
            f"data-name='{m.full_name}'>"
            f"{m.full_name}</li>"
        )
    html += "</ul>"
    return HTMLResponse(html)


@router.get("/fahrtenbuch/hx/fahrzeug-felder", response_class=HTMLResponse)
async def hx_fahrzeug_felder(
    request: Request, fahrzeug_id: int = 0, db: Session = Depends(get_db)
):
    fahrzeug = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.id == fahrzeug_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fahrzeug:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "fahrtenbuch/_fahrzeug_felder.html", {
        "fahrzeug": fahrzeug,
    })


@router.get("/fahrtenbuch/hx/zweck-felder", response_class=HTMLResponse)
async def hx_zweck_felder(
    request: Request, zweck_id: int = 0, db: Session = Depends(get_db)
):
    user = _current_user(request)
    token_org: OrgSettings | None = getattr(request.state, "fahrtenbuch_org", None)
    org_id = user.org_id if user else (token_org.org_id if token_org else None)

    zweck = db.query(Fahrtzweck).filter(Fahrtzweck.id == zweck_id).first() if zweck_id else None

    incidents = []
    if zweck and zweck.kategorie == FahrtKategorie.einsatz and org_id:
        grenze = datetime.now(UTC) - timedelta(days=2)
        incidents = (
            db.query(Incident)
            .filter(Incident.primary_org_id == org_id, Incident.started_at >= grenze)
            .execution_options(include_all_tenants=True)
            .order_by(Incident.started_at.desc())
            .limit(20)
            .all()
        )

    gk_members = []
    if zweck and zweck.verlangt_gruppenkommandant and org_id:
        gk_members = (
            db.query(Member)
            .join(MemberQualification, MemberQualification.member_id == Member.id)
            .join(Qualification, Qualification.id == MemberQualification.qualification_id)
            .filter(
                Member.active == True,  # noqa: E712
                Member.org_id == org_id,
                Qualification.is_gruppenkommandant == True,  # noqa: E712
            )
            .execution_options(include_all_tenants=True)
            .order_by(Member.lastname, Member.firstname)
            .distinct()
            .all()
        )

    return templates.TemplateResponse(request, "fahrtenbuch/_zweck_felder.html", {
        "zweck": zweck,
        "incidents": incidents,
        "gk_members": gk_members,
    })


@router.post("/fahrtenbuch/hx/zaehler-check", response_class=HTMLResponse)
async def hx_zaehler_check(
    request: Request,
    fahrzeug_id: int = Form(0),
    art: str = Form("km"),
    wert: str = Form(""),
    db: Session = Depends(get_db),
):
    if not wert.strip():
        return HTMLResponse("")
    fahrzeug = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.id == fahrzeug_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fahrzeug:
        return HTMLResponse("")
    try:
        from decimal import Decimal
        val = Decimal(wert) if art != "km" else int(wert)
        erg = pruefe_zaehler(fahrzeug, art, val)
    except HTTPException as exc:
        return templates.TemplateResponse(request, "fahrtenbuch/_zaehler_check.html", {
            "fehler": exc.detail, "art": art,
        })
    except Exception:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "fahrtenbuch/_zaehler_check.html", {
        "erg": erg, "art": art,
    })


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

async def _render_erfassung(
    request: Request,
    db: Session,
    *,
    user=None,
    token_org: OrgSettings | None = None,
    preset_fahrzeug_id: int | None = None,
    fehler: str | None = None,
    form_daten: dict | None = None,
) -> HTMLResponse:
    org_id = user.org_id if user else (token_org.org_id if token_org else None)
    if not org_id:
        raise HTTPException(status_code=400, detail="Keine Org")

    fahrzeuge = (
        db.query(VehicleMaster)
        .filter(
            VehicleMaster.dept_id == org_id,
            VehicleMaster.active == True,  # noqa: E712
            VehicleMaster.deleted == False,  # noqa: E712
            VehicleMaster.is_adhoc == False,  # noqa: E712
            VehicleMaster.is_external == False,  # noqa: E712
        )
        .execution_options(include_all_tenants=True)
        .order_by(VehicleMaster.display_order)
        .all()
    )
    zwecke = (
        db.query(Fahrtzweck)
        .filter(Fahrtzweck.aktiv == True)  # noqa: E712
        .order_by(Fahrtzweck.sort)
        .all()
    )
    zielorte = (
        db.query(Zielort)
        .filter(Zielort.aktiv == True)  # noqa: E712
        .order_by(Zielort.sort)
        .all()
    )

    preset_fahrzeug = None
    doppelfahrt_warnung = False
    if preset_fahrzeug_id:
        preset_fahrzeug = next((fz for fz in fahrzeuge if fz.id == preset_fahrzeug_id), None)
        if preset_fahrzeug:
            doppelfahrt_warnung = pruefe_doppelfahrt(preset_fahrzeug, db)

    return templates.TemplateResponse(request, "fahrtenbuch/neu.html", {
        "user": user,
        "token_org": token_org,
        "fahrzeuge": fahrzeuge,
        "zwecke": zwecke,
        "zielorte": zielorte,
        "preset_fahrzeug": preset_fahrzeug,
        "doppelfahrt_warnung": doppelfahrt_warnung,
        "fehler": fehler,
        "form_daten": form_daten or {},
        "now": datetime.now(UTC),
    })


def _form_zu_daten(form, *, org_id: int, user=None, token_org: OrgSettings | None = None) -> dict:
    def _int(key: str) -> int | None:
        v = form.get(key, "").strip()
        return int(v) if v else None

    def _dec(key: str):
        from decimal import Decimal
        v = form.get(key, "").strip()
        return Decimal(v) if v else None

    def _bool(key: str) -> bool:
        return form.get(key) in ("on", "true", "1", "yes")

    def _str(key: str) -> str | None:
        v = form.get(key, "").strip()
        return v or None

    zeitpunkt_raw = _str("zeitpunkt")
    zeitpunkt = None
    if zeitpunkt_raw:
        try:
            zeitpunkt = datetime.fromisoformat(zeitpunkt_raw).replace(tzinfo=UTC)
        except ValueError:
            zeitpunkt = None

    token_label = None
    if token_org and not user:
        fahrzeug_id = _int("fahrzeug_id")
        if fahrzeug_id:
            token_label = f"Token {token_org.org_id}"
        else:
            token_label = "Org-Token"

    return {
        "org_id": org_id,
        "zeitpunkt": zeitpunkt,
        "fahrzeug_id": _int("fahrzeug_id"),
        "maschinist_member_id": _int("maschinist_member_id"),
        "maschinist_name": _str("maschinist_name") or "",
        "maschinist2_member_id": _int("maschinist2_member_id"),
        "maschinist2_name": _str("maschinist2_name"),
        "km_stand_neu": _int("km_stand_neu"),
        "km_warnung_bestaetigt": _bool("km_warnung_bestaetigt"),
        "betriebsstunden_neu": _dec("betriebsstunden_neu"),
        "bh_warnung_bestaetigt": _bool("bh_warnung_bestaetigt"),
        "seilwinde_bh_neu": _dec("seilwinde_bh_neu"),
        "seilwinde_warnung_bestaetigt": _bool("seilwinde_warnung_bestaetigt"),
        "seilwinde_bediener_member_id": _int("seilwinde_bediener_member_id"),
        "seilwinde_bediener_name": _str("seilwinde_bediener_name"),
        "zielort_id": _int("zielort_id"),
        "zielort_freitext": _str("zielort_freitext"),
        "zweck_id": _int("zweck_id"),
        "incident_id": _int("incident_id"),
        "ausbildner_member_id": _int("ausbildner_member_id"),
        "ausbildner_name": _str("ausbildner_name"),
        "gruppenkommandant_member_id": _int("gruppenkommandant_member_id"),
        "gruppenkommandant_name": _str("gruppenkommandant_name"),
        "schaden_vorhanden": _bool("schaden_vorhanden"),
        "schaden_betriebsfaehig": _bool("schaden_betriebsfaehig") if _bool("schaden_vorhanden") else None,
        "schaden_beschreibung": _str("schaden_beschreibung"),
        "bemerkung": _str("bemerkung"),
        "nicht_statistikrelevant": _bool("nicht_statistikrelevant"),
        "doppelfahrt_bestaetigt": _bool("doppelfahrt_bestaetigt"),
        "erfasst_von_user_id": user.id if user else None,
        "erfasst_via": FahrtErfassungsweg.web if user else FahrtErfassungsweg.token,
        "token_label": token_label,
    }
