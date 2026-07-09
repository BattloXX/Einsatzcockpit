"""Statistik-Dashboard."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.core.permissions import has_role
from app.core.templating import templates
from app.core.timezones import local_date_to_utc
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


@router.get("/statistik", response_class=HTMLResponse)
async def stats(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)

    base = lambda q: _apply_org_scope(q, user, db)  # noqa: E731

    # Total incidents (excluding exercises)
    total = base(
        db.query(func.count(Incident.id)).filter(Incident.is_exercise == False)  # noqa: E712
    ).scalar()
    total_exercises = base(
        db.query(func.count(Incident.id)).filter(Incident.is_exercise == True)  # noqa: E712
    ).scalar()

    # Per alarm type
    by_alarm = base(
        db.query(Incident.alarm_type_code, func.count(Incident.id))
        .filter(Incident.is_exercise == False)  # noqa: E712
        .group_by(Incident.alarm_type_code)
    ).all()

    # Per month (last 12 months)
    by_month = base(
        db.query(
            func.date_format(Incident.started_at, "%Y-%m").label("month"),
            func.count(Incident.id).label("count"),
        )
        .filter(Incident.is_exercise == False)  # noqa: E712
        .group_by("month")
        .order_by("month")
        .limit(12)
    ).all()

    fb_stats = _fahrtenbuch_stats(user.org_id, db) if user.org_id else None

    return templates.TemplateResponse(request, "stats/dashboard.html", {
        "user": user,
        "total": total, "total_exercises": total_exercises,
        "by_alarm": by_alarm, "by_month": by_month,
        "fb_stats": fb_stats,
    })


@router.get("/statistik/fahrtenbuch", response_class=HTMLResponse)
async def stats_fahrtenbuch(
    request: Request,
    db: Session = Depends(get_db),
    von: str = "", bis: str = "",
    fahrzeug_id: int = 0, fahrttyp: str = "",
    zweck_id: int = 0, gruppierung: str = "fahrzeug",
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
        dt = local_date_to_utc(von)
        if dt:
            q = q.filter(Fahrt.zeitpunkt >= dt)
    if bis:
        dt = local_date_to_utc(bis, end=True)
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
    })


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
                    g["per_fahrzeug"][fz_key] = {"label": f.fahrzeug.code, "einsatz": 0, "uebung": 0, "taetigkeit": 0, "sonstige": 0}
                g["per_fahrzeug"][fz_key][typ] += 1

    result = sorted(gruppen.values(), key=lambda x: -(x["einsatz"] + x["uebung"] + x["taetigkeit"] + x["sonstige"]))
    return result
