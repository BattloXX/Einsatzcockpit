"""Statistik-Dashboard."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.core.permissions import has_role
from app.core.templating import templates
from app.core.timezones import local_date_to_utc, now_local, to_org_tz
from app.db import get_db
from app.models.fahrtenbuch import Fahrt, FahrtKategorie, FahrtStatus, Fahrtzweck
from app.models.incident import Incident, IncidentOrg
from app.models.master import VehicleMaster

router = APIRouter()


def _apply_org_scope(q, user, db: Session):
    """Fügt expliziten Org-Filter zu Aggregate-Queries hinzu."""
    if has_role(user, "system_admin"):
        return q
    if not user.org_id:
        return q.filter(False)
    collab_subq = db.query(IncidentOrg.incident_id).filter(IncidentOrg.org_id == user.org_id)
    return q.filter(
        or_(
            Incident.primary_org_id == user.org_id,
            Incident.id.in_(collab_subq),
        )
    )


def _parse_range(value: str, fallback: date) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback


def _stats_context(request: Request, db: Session, user, von: str, bis: str) -> dict:
    from app.models.master import FireDept
    from app.services.stats_service import default_range, get_stats

    org = user.org or db.query(FireDept).filter(FireDept.id == user.org_id).first()
    default_von, default_bis = default_range(org)
    von_date = _parse_range(von, default_von)
    bis_date = _parse_range(bis, default_bis)
    if von_date > bis_date:
        von_date, bis_date = bis_date, von_date
    today = now_local(org).date()
    active_preset = None
    if von_date == date(today.year, 1, 1) and bis_date == today:
        active_preset = "jahr"
    else:
        try:
            previous_year_date = date(today.year - 1, today.month, today.day)
        except ValueError:
            previous_year_date = None
        if von_date == previous_year_date and bis_date == today:
            active_preset = "jahr12"
        else:
            first_of_this_month = date(today.year, today.month, 1)
            last_of_prev_month = first_of_this_month - timedelta(days=1)
            first_of_prev_month = date(last_of_prev_month.year, last_of_prev_month.month, 1)
            if von_date == first_of_prev_month and bis_date == last_of_prev_month:
                active_preset = "monat"
    result = get_stats(db, user.org_id, von_date, bis_date, user=user)
    return {
        "user": user, "org": org, "stats": result,
        "von": von_date, "bis": bis_date,
        "active_preset": active_preset,
        "fb_stats": _fahrtenbuch_stats(user.org_id, db) if user.org_id else None,
    }


@router.get("/statistik", response_class=HTMLResponse)
async def stats(request: Request, db: Session = Depends(get_db), von: str = "", bis: str = ""):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)

    return templates.TemplateResponse(
        request, "stats/dashboard.html", _stats_context(request, db, user, von, bis)
    )


@router.get("/statistik/inhalt", response_class=HTMLResponse)
async def stats_content(request: Request, db: Session = Depends(get_db), von: str = "", bis: str = ""):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    context = _stats_context(request, db, user, von, bis)
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "stats/_dashboard_content.html", context)
    return templates.TemplateResponse(request, "stats/dashboard.html", context)


@router.get("/statistik/export.xlsx")
async def stats_export(request: Request, db: Session = Depends(get_db), von: str = "", bis: str = ""):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    context = _stats_context(request, db, user, von, bis)
    from app.services.excel_export_service import exportiere_einsatzstatistik
    content = exportiere_einsatzstatistik(
        context["stats"], context["von"], context["bis"], context["org"]
    )
    org_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in context["org"].name)
    filename = f"Einsatzstatistik_{org_name}_{context['von']}_{context['bis']}.xlsx"
    return Response(content=content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/statistik/bericht.pdf")
async def stats_pdf(request: Request, db: Session = Depends(get_db), von: str = "", bis: str = ""):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    context = _stats_context(request, db, user, von, bis)
    from app.services.pdf_service import render_statistik_bericht_pdf
    content = render_statistik_bericht_pdf(
        context["stats"], context["org"], context["von"], context["bis"],
        base_url=str(request.base_url),
    )
    return Response(content=content, media_type="application/pdf",
                    headers={"Content-Disposition": "inline; filename=Einsatzstatistik.pdf"})


@router.get("/statistik/fahrtenbuch", response_class=HTMLResponse)
async def stats_fahrtenbuch(
    request: Request,
    db: Session = Depends(get_db),
    von: str = "", bis: str = "",
    fahrzeug_id: int = 0, fahrttyp: str = "",
    zweck_id: int = 0, gruppierung: str = "fahrzeug", matrix_jahr: int = 0,
):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)

    q = (
        db.query(Fahrt)
        .filter(
            Fahrt.org_id == user.org_id,
            Fahrt.status == FahrtStatus.aktiv,
            Fahrt.nicht_statistikrelevant == False,  # noqa: E712
        )
        .execution_options(include_all_tenants=True)
        .options(joinedload(Fahrt.fahrzeug))
    )
    if von:
        dt = local_date_to_utc(von, org=user.org)
        if dt:
            q = q.filter(Fahrt.zeitpunkt >= dt)
    if bis:
        dt = local_date_to_utc(bis, end=True, org=user.org)
        if dt:
            q = q.filter(Fahrt.zeitpunkt <= dt)
    if fahrzeug_id:
        q = q.filter(Fahrt.fahrzeug_id == fahrzeug_id)
    if fahrttyp:
        try:
            q = q.filter(Fahrt.fahrttyp == FahrtKategorie(fahrttyp))
        except ValueError:
            pass
    if zweck_id:
        q = q.filter(Fahrt.zweck_id == zweck_id)

    fahrten = q.all()

    fahrzeuge = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.dept_id == user.org_id, VehicleMaster.active == True)  # noqa: E712
        .execution_options(include_all_tenants=True)
        .order_by(VehicleMaster.display_order)
        .all()
    )
    zwecke = db.query(Fahrtzweck).filter(Fahrtzweck.aktiv == True).order_by(Fahrtzweck.sort).all()  # noqa: E712

    gruppen = _gruppiere_fahrten(fahrten, gruppierung, fahrzeuge)
    from app.services.maschinisten_matrix_service import (
        FARBE_EINSATZ,
        FARBE_STUFE,
        FARBE_UEBUNG,
        berechne_maschinisten_matrix,
    )
    matrix_jahr = matrix_jahr or now_local(user.org).year
    if user.org_id:
        matrix = berechne_maschinisten_matrix(db, user.org_id, matrix_jahr)
        zeitpunkte = (
            db.query(Fahrt.zeitpunkt)
            .filter(Fahrt.org_id == user.org_id)
            .execution_options(include_all_tenants=True)
            .all()
        )
        matrix_jahre = sorted({
            lokal.year for (zeitpunkt,) in zeitpunkte
            if (lokal := to_org_tz(zeitpunkt, user.org)) is not None
        }, reverse=True)
    else:
        matrix = {
            "jahr": matrix_jahr, "spalten": [], "zeilen": [],
            "summen": {"gesamt": {"uebung": 0, "einsatz": 0}},
        }
        matrix_jahre = []
    if matrix_jahr not in matrix_jahre:
        matrix_jahre.insert(0, matrix_jahr)

    return templates.TemplateResponse(request, "stats/fahrtenbuch.html", {
        "user": user,
        "gruppen": gruppen,
        "fahrzeuge": fahrzeuge,
        "zwecke": zwecke,
        "gruppierung": gruppierung,
        "filter": {
            "von": von, "bis": bis, "fahrzeug_id": fahrzeug_id,
            "fahrttyp": fahrttyp, "zweck_id": zweck_id,
        },
        "gesamt_fahrten": len(fahrten),
        "matrix": matrix, "matrix_jahre": matrix_jahre, "matrix_jahr": matrix_jahr,
        "matrix_farben": {
            "uebung": FARBE_UEBUNG, "einsatz": FARBE_EINSATZ, "stufe": FARBE_STUFE,
        },
    })


@router.get("/statistik/fahrtenbuch/maschinisten.xlsx")
async def stats_fahrtenbuch_maschinisten_xlsx(
    request: Request, jahr: int = 0, db: Session = Depends(get_db),
):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if not user.org_id:
        raise HTTPException(status_code=404, detail="Keine Organisation zugeordnet")
    jahr = jahr or now_local(user.org).year
    from app.services.excel_export_service import exportiere_maschinisten_matrix
    from app.services.maschinisten_matrix_service import berechne_maschinisten_matrix
    matrix = berechne_maschinisten_matrix(db, user.org_id, jahr)
    content = exportiere_maschinisten_matrix(matrix, user.org)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="Maschinisten-Matrix_{jahr}.xlsx"'},
    )


def _fahrtenbuch_stats(org_id: int, db: Session) -> dict:
    """Kurzübersicht für das Statistik-Dashboard."""
    basis = (
        db.query(Fahrt)
        .filter(
            Fahrt.org_id == org_id,
            Fahrt.status == FahrtStatus.aktiv,
            Fahrt.nicht_statistikrelevant == False,  # noqa: E712
        )
        .execution_options(include_all_tenants=True)
    )
    total = basis.count()
    einsatz = basis.filter(Fahrt.fahrttyp == FahrtKategorie.einsatz).count()
    uebung = basis.filter(Fahrt.fahrttyp == FahrtKategorie.uebung).count()
    km_sum = db.query(func.sum(Fahrt.km_delta)).filter(
        Fahrt.org_id == org_id, Fahrt.status == FahrtStatus.aktiv,
        Fahrt.nicht_statistikrelevant == False,  # noqa: E712
    ).execution_options(include_all_tenants=True).scalar() or 0
    return {"total": total, "einsatz": einsatz, "uebung": uebung, "km_sum": int(km_sum)}


def _gruppiere_fahrten(fahrten: list, gruppierung: str, fahrzeuge: list) -> list[dict]:
    """Aggregiert Fahrten nach der gewählten Gruppierung."""
    from collections import defaultdict
    from decimal import Decimal

    gruppen: dict[str, dict] = defaultdict(lambda: {
        "label": "", "einsatz": 0, "uebung": 0, "taetigkeit": 0, "sonstige": 0,
        "km_sum": 0, "bh_sum": Decimal("0"),
        "per_fahrzeug": {},
    })

    for f in fahrten:
        entries: list[tuple[str, str]] = []

        if gruppierung == "fahrzeug":
            entries.append((str(f.fahrzeug_id), f.fahrzeug.code if f.fahrzeug else str(f.fahrzeug_id)))
            if (f.maschinist2_name or f.maschinist2_member_id) and f.fahrzeug:
                entries.append(("korb_" + str(f.fahrzeug_id), f.fahrzeug.code + " Korb"))
        elif gruppierung == "maschinist":
            k = str(f.maschinist_member_id or f.maschinist_name)
            entries.append((k, f.maschinist_name or k))
        elif gruppierung == "ausbildner":
            if not f.ausbildner_name and not f.ausbildner_member_id:
                continue
            k = str(f.ausbildner_member_id or f.ausbildner_name)
            entries.append((k, f.ausbildner_name or k))
        elif gruppierung == "gruppenkommandant":
            if not f.gruppenkommandant_name and not f.gruppenkommandant_member_id:
                continue
            k = str(f.gruppenkommandant_member_id or f.gruppenkommandant_name)
            entries.append((k, f.gruppenkommandant_name or k))
        elif gruppierung == "korbmaschinist":
            if not f.maschinist2_name and not f.maschinist2_member_id:
                continue
            k = str(f.maschinist2_member_id or f.maschinist2_name)
            entries.append((k, f.maschinist2_name or k))
        else:
            entries.append(("gesamt", "Gesamt"))

        if f.fahrttyp == FahrtKategorie.einsatz:
            typ = "einsatz"
        elif f.fahrttyp == FahrtKategorie.uebung:
            typ = "uebung"
        elif f.fahrttyp == FahrtKategorie.taetigkeit:
            typ = "taetigkeit"
        else:
            typ = "sonstige"

        for key, label in entries:
            g = gruppen[key]
            g["label"] = label
            g[typ] += 1
            if f.km_delta:
                g["km_sum"] += int(f.km_delta)
            if f.betriebsstunden_delta:
                g["bh_sum"] += Decimal(str(f.betriebsstunden_delta))
            if f.fahrzeug_id and f.fahrzeug:
                fz_key = str(f.fahrzeug_id)
                if fz_key not in g["per_fahrzeug"]:
                    g["per_fahrzeug"][fz_key] = {
                        "label": f.fahrzeug.code, "einsatz": 0, "uebung": 0, "taetigkeit": 0, "sonstige": 0,
                    }
                g["per_fahrzeug"][fz_key][typ] += 1

    result = sorted(gruppen.values(), key=lambda x: -(x["einsatz"] + x["uebung"] + x["taetigkeit"] + x["sonstige"]))
    return result
