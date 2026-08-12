"""Archiv & PDF-Export.

Org-Scoping:
- Listen und Detailansichten werden nach Org gefiltert; system_admin sieht alles.
- Endpoints können nur eigene oder mitwirkende Org-Einsätze abrufen.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session, selectinload

from app.core.permissions import can_access_incident, has_role, same_org_or_system_admin
from app.core.templating import templates
from app.core.timezones import local_date_to_utc, now_local
from app.db import get_db
from app.models.incident import Incident, IncidentOrg, IncidentVehicle
from app.models.master import VehicleMaster
from app.models.wordpress_report import WordPressReportConfig
from app.services.ai_service import AIServiceError, generate_report_draft
from app.services.ai_service import is_enabled as ai_is_enabled
from app.services.pdf_service import load_fahrten_details, render_incident_pdf

router = APIRouter()
logger = logging.getLogger("einsatzleiter.archive")


def _load_incident_with_orgs(incident_id: int, db: Session) -> Incident | None:
    """Lädt Incident und stellt sicher, dass collaborating_orgs eager geladen ist,
    damit can_access_incident() nicht in ein Lazy-Load-Problem läuft."""
    return (
        db.query(Incident)
        .options(selectinload(Incident.collaborating_orgs))
        .filter(Incident.id == incident_id)
        .first()
    )


def _load_archive_incident(incident_id: int, db: Session) -> Incident | None:
    """Vollständiger Archiv-Loader mit Eager-Loading aller Archiv/PDF-Relationen.

    Vermeidet N+1 beim Rendern der Archiv-Detailseite und des PDF-Exports.
    """
    return (
        db.query(Incident)
        .options(
            selectinload(Incident.collaborating_orgs),
            selectinload(Incident.columns),
            selectinload(Incident.vehicles)
                .joinedload(IncidentVehicle.vehicle_master)
                .joinedload(VehicleMaster.dept),
            selectinload(Incident.tasks),
            selectinload(Incident.messages),
            selectinload(Incident.rescued_persons),
            selectinload(Incident.breathing_troops),
            selectinload(Incident.log_entries),
        )
        .filter(Incident.id == incident_id)
        .first()
    )


def _deny_access(user, incident) -> HTTPException:
    """Erzeugt eine 403 mit diagnostischem Hinweis (welche Orgs verglichen wurden)."""
    collab_ids = [io.org_id for io in (incident.collaborating_orgs or [])]
    logger.info(
        "access denied: user=%s org=%s incident=%s primary_org=%s collaborators=%s",
        user.id, user.org_id, incident.id, incident.primary_org_id, collab_ids,
    )
    msg = (
        f"Kein Zugriff auf diesen Einsatz. Dein Account gehört zu Org "
        f"{user.org_id}, der Einsatz zur Org {incident.primary_org_id}. "
        f"Bitte als Mit-Organisation eintragen lassen (Admin)."
    )
    return HTTPException(403, detail=msg)


def _scoped_incidents_query(db: Session, user):
    """Liefert eine Incident-Query, die nur Einsätze enthält, die der User sehen darf."""
    q = db.query(Incident)
    user_role_codes = {r.code for r in user.roles}
    if "system_admin" in user_role_codes:
        return q
    if user.org_id is None:
        # Kein Org, kein system_admin → keine Einsätze
        return q.filter(Incident.id == None)  # noqa: E711  → leeres Resultset
    collab_ids_subq = db.query(IncidentOrg.incident_id).filter(
        IncidentOrg.org_id == user.org_id
    )
    return q.filter(
        or_(
            Incident.primary_org_id == user.org_id,
            Incident.id.in_(collab_ids_subq),
        )
    )


_AI_ROLES = ("incident_leader", "recorder", "org_admin", "system_admin")
_WP_REPORT_ROLES = ("incident_leader", "recorder", "org_admin", "system_admin")


@router.get("/archiv", response_class=HTMLResponse)
def archive_list(
    request: Request,
    db: Session = Depends(get_db),
    jahr: str = "",
    von: str = "",
    bis: str = "",
    sort: str = "datum_desc",
):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)

    aktuelles_jahr = now_local(user.org).year
    ausgewaehltes_jahr = jahr or str(aktuelles_jahr)
    basis_query = _scoped_incidents_query(db, user)

    jahre = [
        row[0]
        for row in basis_query.with_entities(func.year(Incident.started_at))
        .filter(Incident.started_at.isnot(None))
        .distinct()
        .order_by(func.year(Incident.started_at).desc())
        .all()
        if row[0] is not None
    ]
    jahre_optionen = sorted(set(jahre) | {aktuelles_jahr}, reverse=True)

    incidents_query = basis_query
    if von or bis:
        if von:
            von_utc = local_date_to_utc(von, org=user.org)
            if von_utc:
                incidents_query = incidents_query.filter(Incident.started_at >= von_utc)
        if bis:
            bis_utc = local_date_to_utc(bis, end=True, org=user.org)
            if bis_utc:
                incidents_query = incidents_query.filter(Incident.started_at <= bis_utc)
    elif ausgewaehltes_jahr != "alle":
        jahr_von = local_date_to_utc(f"{ausgewaehltes_jahr}-01-01", org=user.org)
        jahr_bis = local_date_to_utc(
            f"{ausgewaehltes_jahr}-12-31", end=True, org=user.org
        )
        if jahr_von and jahr_bis:
            incidents_query = incidents_query.filter(
                Incident.started_at >= jahr_von,
                Incident.started_at <= jahr_bis,
            )

    duration_expr = func.timestampdiff(
        text("SECOND"), Incident.started_at, Incident.closed_at
    )
    sortierungen = {
        "datum_desc": (Incident.started_at.desc(),),
        "datum_asc": (Incident.started_at.asc(),),
        "dauer_desc": (Incident.closed_at.is_(None), duration_expr.desc()),
        "dauer_asc": (Incident.closed_at.is_(None), duration_expr.asc()),
        "typ_asc": (Incident.alarm_type_code.asc(), Incident.started_at.desc()),
        "ort_asc": (
            Incident.address_city.asc(),
            Incident.address_street.asc(),
            Incident.started_at.desc(),
        ),
    }
    if sort not in sortierungen:
        sort = "datum_desc"
    incidents = incidents_query.order_by(*sortierungen[sort]).all()

    uas_incident_ids: set[int] = set()
    if getattr(request.state, "uas_module_enabled", False) and user.org_id:
        from app.models.uas import UASEinsatz
        uas_incident_ids = {
            row[0]
            for row in db.query(UASEinsatz.incident_id)
            .filter(UASEinsatz.org_id == user.org_id)
            .all()
        }

    return templates.TemplateResponse(request, "archive/list.html", {
        "user": user, "incidents": incidents, "uas_incident_ids": uas_incident_ids,
        "filter": {
            "jahr": ausgewaehltes_jahr, "von": von, "bis": bis, "sort": sort,
        },
        "jahre_optionen": jahre_optionen,
    })


@router.get("/archiv/{incident_id}", response_class=HTMLResponse)
def archive_detail(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = _load_archive_incident(incident_id, db)
    if not incident:
        raise HTTPException(404)
    if not can_access_incident(user, incident):
        raise _deny_access(user, incident)

    uas_einsatz = None
    if getattr(request.state, "uas_module_enabled", False):
        from app.models.uas import UASEinsatz
        uas_einsatz = (
            db.query(UASEinsatz)
            .filter(UASEinsatz.incident_id == incident_id)
            .first()
        )

    can_edit = has_role(user, "incident_leader", "admin", "org_admin", "system_admin", "recorder")
    wp_config = db.query(WordPressReportConfig).filter(
        WordPressReportConfig.org_id == incident.primary_org_id
    ).first()
    wp_report_available = bool(wp_config and wp_config.enabled)

    from app.services.incident_service import combined_verlauf
    verlauf = combined_verlauf(db, incident_id, limit=500)
    fahrten_details = load_fahrten_details(incident_id, db=db)

    return templates.TemplateResponse(request, "archive/detail.html", {
        "user": user, "incident": incident,
        "ai_enabled": ai_is_enabled(),
        "fahrten_details": fahrten_details,
        "uas_einsatz": uas_einsatz,
        "can_edit": can_edit,
        "can_delete": has_role(user, "admin", "org_admin", "system_admin"),
        "wp_report_available": wp_report_available,
        "verlauf": verlauf,
    })


@router.get("/archiv/{incident_id}/pdf")
def download_pdf(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = _load_archive_incident(incident_id, db)
    if not incident:
        raise HTTPException(404)
    if not can_access_incident(user, incident):
        raise _deny_access(user, incident)
    pdf_bytes = render_incident_pdf(incident, base_url=str(request.base_url))
    filename = f"einsatz_{incident.id}_{incident.alarm_type_code}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.post("/archiv/{incident_id}/ki-bericht", response_class=HTMLResponse)
async def generate_ai_report(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if not has_role(user, *_AI_ROLES):
        raise HTTPException(403, detail="Keine Berechtigung")
    if not ai_is_enabled():
        return HTMLResponse('<p class="text-muted">KI-Funktionen sind nicht aktiviert.</p>')

    incident = _load_incident_with_orgs(incident_id, db)
    if not incident:
        raise HTTPException(404)
    if not can_access_incident(user, incident):
        raise _deny_access(user, incident)

    from app.core.audit import write_audit
    from app.services.incident_service import collect_report_context

    try:
        context = collect_report_context(incident_id, db)
        draft = await generate_report_draft(context, org_id=incident.primary_org_id)
    except AIServiceError as exc:
        return HTMLResponse(f'<p style="color:var(--red)">KI-Fehler: {exc}</p>')

    write_audit(db, "ai_report_generated", user_id=user.id, incident_id=incident_id)
    db.commit()

    return templates.TemplateResponse(request, "archive/_ki_bericht.html", {
        "user": user, "incident": incident, "draft": draft,
    })


@router.post("/archiv/{incident_id}/webseiten-bericht", response_class=HTMLResponse)
async def create_wordpress_report(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if not has_role(user, *_WP_REPORT_ROLES):
        raise HTTPException(403, detail="Keine Berechtigung")

    incident = _load_incident_with_orgs(incident_id, db)
    if not incident:
        raise HTTPException(404)
    if not can_access_incident(user, incident):
        raise _deny_access(user, incident)

    from app.core.audit import write_audit
    from app.services.wordpress_report_service import post_incident_report

    result = await post_incident_report(db, incident)
    if result.success and not result.already_existed:
        write_audit(db, "wordpress_report_created", user_id=user.id, incident_id=incident_id)
        db.commit()

    return templates.TemplateResponse(request, "archive/_webseiten_bericht.html", {
        "user": user, "incident": incident, "result": result,
    })


@router.post("/archiv/{incident_id}/ki-bericht/speichern", response_class=HTMLResponse)
async def save_ai_report(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if not has_role(user, *_AI_ROLES):
        raise HTTPException(403, detail="Keine Berechtigung")

    incident = _load_incident_with_orgs(incident_id, db)
    if not incident:
        raise HTTPException(404)
    if not can_access_incident(user, incident):
        raise _deny_access(user, incident)

    form = await request.form()
    draft_text = str(form.get("ki_bericht_entwurf", "")).strip()
    incident.ai_report_draft = draft_text or None
    db.commit()

    return HTMLResponse('<p style="color:var(--green); margin-top:.5rem;">✓ Entwurf gespeichert.</p>')


@router.post("/archiv/{incident_id}/loeschen")
def delete_incident(incident_id: int, request: Request, db: Session = Depends(get_db)):
    """Löscht einen Einsatz endgültig. Org-Admins nur in der eigenen Org."""
    import shutil
    from pathlib import Path

    from app.config import settings
    from app.core.audit import write_audit

    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if not has_role(user, "admin", "org_admin", "system_admin"):
        raise HTTPException(403, detail="Nur Administratoren können Einsätze löschen.")

    # Bewusst tenant-übergreifend laden, damit der nachfolgende explizite Guard
    # zwischen "nicht vorhanden" und "fremde Organisation" unterscheiden kann.
    incident = (
        db.query(Incident)
        .execution_options(include_all_tenants=True)
        .filter(Incident.id == incident_id)
        .first()
    )
    if not incident:
        raise HTTPException(404)
    if not same_org_or_system_admin(user, incident.primary_org_id):
        raise HTTPException(403, detail="Einsätze einer fremden Organisation können nicht gelöscht werden.")

    # UASEinsatz besitzt absichtlich eine RESTRICT-FK. Beim ausdrücklichen
    # Hard-Delete des gesamten Einsatzes gehört die Drohnenmission mit dazu.
    from app.models.uas import UASEinsatz
    for uas_einsatz in db.query(UASEinsatz).filter(UASEinsatz.incident_id == incident_id).all():
        db.delete(uas_einsatz)
    db.flush()

    write_audit(
        db, "admin.incident.deleted",
        user_id=user.id,
        entity_type="incident", entity_id=incident_id,
        payload={"alarm_type": incident.alarm_type_code, "started_at": str(incident.started_at)},
    )
    db.flush()
    db.delete(incident)
    db.commit()

    media_dir = Path(settings.MEDIA_STORAGE_DIR) / str(incident_id)
    if media_dir.exists():
        try:
            shutil.rmtree(media_dir)
        except Exception:
            pass

    return RedirectResponse("/archiv?deleted=1", status_code=303)
