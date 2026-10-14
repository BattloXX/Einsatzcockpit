"""Administration des versionierten Planspiel-Szenariokatalogs."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import has_role, require_role
from app.core.templating import templates
from app.data.lfv_stichworte_v03 import QUELLE, REVISION, STICHWORTE
from app.data.planspiel_katalog import BAUSTEINE, MELDENDE, ORT_TYPEN, SYSTEM_KATALOG_VERSION
from app.db import get_db
from app.models.master import AlarmType
from app.models.planspiel import PlanspielSzenario, PlanspielSzenarioEreignis, PlanspielSzenarioVersion
from app.models.user import User
from app.routers.ui_planspiel import require_planspiel_enabled
from app.services.planspiel_katalog_service import (
    aktiv_setzen,
    aktive_version,
    auf_standard_zuruecksetzen,
    duplizieren,
    entfernen,
    export_json,
    import_json,
    katalog_sicherstellen,
    neue_version,
    org_stichwort_code,
    rendern,
    systemdiff,
    version_als_dict,
)

router = APIRouter(prefix="/admin/settings/planspiele", tags=["planspiele-admin"])
_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _org(user: User) -> int:
    if user.org_id is None:
        raise HTTPException(status_code=400, detail="Keine Organisation ausgewaehlt")
    return user.org_id


def _scenario(db: Session, user: User, scenario_id: int) -> PlanspielSzenario:
    item = (
        db.query(PlanspielSzenario)
        .filter(
            PlanspielSzenario.id == scenario_id,
            PlanspielSzenario.org_id == _org(user),
        )
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return item


def _audit(db: Session, action: str, user: User, item: PlanspielSzenario) -> None:
    write_audit(
        db,
        f"planspiel.szenario.{action}",
        user_id=user.id,
        org_id=_org(user),
        entity_type="planspiel_szenario",
        entity_id=item.id,
        payload={"code": item.code},
    )


def _rows(db: Session, user: User, filters: dict[str, str]):
    query = db.query(PlanspielSzenario).filter(PlanspielSzenario.org_id == _org(user))
    status = filters.get("status", "")
    if filters.get("herkunft") in {"system", "org"}:
        query = query.filter(PlanspielSzenario.herkunft == filters["herkunft"])
    if status == "archiviert":
        query = query.filter(PlanspielSzenario.archiviert_at.is_not(None))
    else:
        query = query.filter(PlanspielSzenario.archiviert_at.is_(None))
        if status == "aktiv":
            query = query.filter(PlanspielSzenario.aktiv.is_(True))
        if status == "inaktiv":
            query = query.filter(PlanspielSzenario.aktiv.is_(False))
    q = filters.get("q", "").casefold()
    result = []
    for item in query.order_by(PlanspielSzenario.code).all():
        version = aktive_version(db, item)
        if version is None:
            continue
        if q and q not in item.code.casefold() and q not in version.titel.casefold():
            continue
        if filters.get("kategorie") and version.kategorie != filters["kategorie"]:
            continue
        if filters.get("stichwort") and version.stichwort_code.casefold() != filters["stichwort"].casefold():
            continue
        if filters.get("schwierigkeit") and version.schwierigkeit != filters["schwierigkeit"]:
            continue
        if filters.get("orttyp") and filters["orttyp"] not in version.ort_typen:
            continue
        result.append((item, version))
    return result


def _list_context(request: Request, db: Session, user: User, **extra: Any) -> dict[str, Any]:
    filters = {key: value for key, value in request.query_params.items() if value}
    context = {
        "user": user,
        "rows": _rows(db, user, filters),
        "filters": filters,
        "can_edit": has_role(user, "org_admin"),
        "ort_typen": ORT_TYPEN,
        "system_version": SYSTEM_KATALOG_VERSION,
    }
    context.update(extra)
    return context


def _seconds(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value)) * Decimal(60)
    except InvalidOperation as exc:
        raise ValueError("Offsets muessen Minuten als Zahl sein.") from exc
    if result < 0 or result != result.to_integral_value():
        raise ValueError("Offsets muessen ganze Sekunden ergeben.")
    return int(result)


def _content(form: Any) -> dict[str, Any]:
    try:
        raw = json.loads(str(form.get("ereignisse_json", "[]")))
    except json.JSONDecodeError as exc:
        raise ValueError("Folgemeldungen sind ungueltig.") from exc
    if not isinstance(raw, list) or len(raw) > 20:
        raise ValueError("Es sind maximal 20 Folgemeldungen erlaubt.")
    events = []
    for index, event in enumerate(raw):
        item = dict(event)
        item["offset_min_s"] = _seconds(item.get("offset_min_s"))
        item["offset_max_s"] = _seconds(item.get("offset_max_s"))
        item["wahrscheinlichkeit"] = float(item.get("wahrscheinlichkeit", 100)) / 100
        # Leere Auswahlfelder ("keine") nicht als Bedingung speichern.
        for feld in ("trigger_param", "voraussetzungen", "abbruch"):
            werte = item.get(feld)
            if isinstance(werte, dict):
                werte = {k: v for k, v in werte.items() if v not in (None, "")}
            item[feld] = werte or None
        item["reihenfolge"] = index
        events.append(item)
    geo = str(form.get("geografie_bedingung", "")).strip()
    if geo and "=" not in geo:
        raise ValueError("Geografie-Bedingung im Format Schluessel=Wert angeben.")
    geo_dict = dict([geo.split("=", 1)]) if geo else None
    return {
        "titel": str(form.get("titel", "")).strip(),
        "beschreibung": str(form.get("beschreibung", "")).strip() or None,
        "kategorie": str(form.get("kategorie", "")),
        "stichwort_code": str(form.get("stichwort_code", "")).strip(),
        "stichwort_quelle": str(form.get("stichwort_quelle", "")).strip() or None,
        "alarmtext_template": str(form.get("alarmtext_template", "")),
        "regie_geheimlage": str(form.get("regie_geheimlage", "")).strip() or None,
        "grundsituation": str(form.get("grundsituation", "")).strip() or None,
        "ort_typen": form.getlist("ort_typen"),
        "geografie_bedingung": geo_dict,
        "requires_existing_object": bool(form.get("requires_existing_object")),
        "requires_verified_bma": bool(form.get("requires_verified_bma")),
        "min_adressqualitaet": form.get("min_adressqualitaet") or None,
        "schwierigkeit": str(form.get("schwierigkeit", "")),
        "schlagworte": str(form.get("schlagworte", "")).strip() or None,
        "lernziele": str(form.get("lernziele", "")).strip() or None,
        "ressourcenhinweis": None,
        "ausschlussregeln": None,
        "gsl_modus": form.get("gsl_modus") or None,
        "gsl_verteilung": None,
        "ereignisse": events,
    }


def _baustein_daten() -> dict[str, dict[str, Any]]:
    return {
        code: {
            "baustein_code": baustein.code,
            "ereignis_typ": baustein.typ,
            "trigger_type": baustein.trigger_type,
            "offset_min_s": str(Decimal(baustein.offset_min_s) / 60) if baustein.offset_min_s is not None else None,
            "offset_max_s": str(Decimal(baustein.offset_max_s) / 60) if baustein.offset_max_s is not None else None,
            "trigger_param": baustein.trigger_param,
            "wahrscheinlichkeit": baustein.wahrscheinlichkeit * 100,
            "max_fires": 1,
            "prioritaet": 0,
            "sichtbarkeit": baustein.sichtbarkeit,
            "ziel": baustein.ziel,
            "payload_template": baustein.text,
        }
        for code, baustein in BAUSTEINE.items()
    }


def _detail_context(request: Request, db: Session, user: User, item: PlanspielSzenario, **extra: Any):
    version = aktive_version(db, item)
    if version is None:
        raise HTTPException(status_code=404, detail="Keine aktive Version")
    content = version_als_dict(version, db)
    form_events = []
    for event in content["ereignisse"]:
        row = dict(event)
        row["offset_min_s"] = str(Decimal(row["offset_min_s"]) / 60) if row["offset_min_s"] is not None else None
        row["offset_max_s"] = str(Decimal(row["offset_max_s"]) / 60) if row["offset_max_s"] is not None else None
        row["wahrscheinlichkeit"] = str(Decimal(str(row["wahrscheinlichkeit"])) * 100)
        form_events.append(row)
    values = {
        "adresse": "Musterstraße 1, Musterort",
        "strasse": "Musterstraße",
        "hausnr": "1",
        "ort": "Musterort",
        "objekt": "Beispielobjekt",
        "meldender": MELDENDE[0],
    }
    for baustein in BAUSTEINE.values():
        for key, candidates in baustein.variablen.items():
            values.setdefault(key, candidates[0])
    versions = (
        db.query(PlanspielSzenarioVersion)
        .filter(
            PlanspielSzenarioVersion.szenario_id == item.id,
            PlanspielSzenarioVersion.org_id == _org(user),
        )
        .order_by(PlanspielSzenarioVersion.version.desc())
        .all()
    )
    events = (
        db.query(PlanspielSzenarioEreignis)
        .filter(
            PlanspielSzenarioEreignis.version_id == version.id,
            PlanspielSzenarioEreignis.org_id == _org(user),
        )
        .order_by(PlanspielSzenarioEreignis.reihenfolge)
        .all()
    )
    context = {
        "user": user,
        "szenario": item,
        "version": version,
        "inhalt": content,
        "form_events": form_events,
        "ereignisse": events,
        "ort_typen": ORT_TYPEN,
        "bausteine": BAUSTEINE,
        "baustein_daten": _baustein_daten(),
        "stichworte": STICHWORTE,
        "alarmtypen": db.query(AlarmType).filter(AlarmType.org_id == _org(user)).all(),
        "versions": versions,
        "systemdiff": systemdiff(item, db),
        "werte": values,
        "rendern": rendern,
        "archiviert": item.archiviert_at is not None,
        "can_edit": has_role(user, "org_admin"),
    }
    context.update(extra)
    return context


@router.get("/szenarien", response_class=HTMLResponse)
def szenarien(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("uebungsleiter", "org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    result = katalog_sicherstellen(db, _org(user), user.id)
    if result.neu or result.aktualisiert:
        db.commit()
    template = (
        "planspiel/admin/_szenario_liste.html"
        if request.headers.get("HX-Request") == "true"
        else "planspiel/admin/szenarien.html"
    )
    return templates.TemplateResponse(request, template, _list_context(request, db, user))


@router.get("/szenarien/neu", response_class=HTMLResponse)
def neu_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    return templates.TemplateResponse(
        request,
        "planspiel/admin/neu.html",
        {
            "user": user,
            "ort_typen": ORT_TYPEN,
            "stichworte": STICHWORTE,
            "bausteine": BAUSTEINE,
            "baustein_daten": _baustein_daten(),
            "form_events": [],
        },
    )


@router.post("/szenarien/neu")
async def neu(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    form = await request.form()
    code = str(form.get("code", "")).strip()
    try:
        if not _CODE_RE.fullmatch(code):
            raise ValueError("Code muss 1-40 Zeichen aus Buchstaben, Zahlen, _ oder - enthalten.")
        if (
            db.query(PlanspielSzenario)
            .filter(PlanspielSzenario.org_id == _org(user), PlanspielSzenario.code == code)
            .first()
        ):
            raise ValueError("Dieser Szenario-Code existiert bereits.")
        item = PlanspielSzenario(
            org_id=_org(user),
            code=code,
            herkunft="org",
            aktiv=bool(form.get("aktiv")),
            zufall_erlaubt=bool(form.get("zufall_erlaubt")),
            gewicht=int(str(form.get("gewicht", 10))),
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(item)
        db.flush()
        neue_version(db, item, _content(form), user.id)
        _audit(db, "anlegen", user, item)
        db.commit()
        return RedirectResponse(f"/admin/settings/planspiele/szenarien/{item.id}", status_code=303)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return templates.TemplateResponse(
            request,
            "planspiel/admin/neu.html",
            {
                "user": user,
                "ort_typen": ORT_TYPEN,
                "stichworte": STICHWORTE,
                "bausteine": BAUSTEINE,
                "baustein_daten": _baustein_daten(),
                "form_events": [],
                "fehler": str(exc),
                "form": form,
            },
            status_code=400,
        )


@router.get("/szenarien/export.json")
def export(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    return JSONResponse(
        export_json(db, _org(user)), headers={"Content-Disposition": 'attachment; filename="planspiel-katalog.json"'}
    )


@router.post("/szenarien/import")
async def import_(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    try:
        raw = await file.read()
        if len(raw) > 1_000_000:
            raise ValueError("Die Datei ist größer als 1 MB.")
        result = import_json(db, _org(user), json.loads(raw), user.id)
        for code in [*result.neu, *result.aktualisiert]:
            item = (
                db.query(PlanspielSzenario)
                .filter(PlanspielSzenario.org_id == _org(user), PlanspielSzenario.code == code)
                .first()
            )
            if item:
                _audit(db, "import", user, item)
        db.commit()
        return templates.TemplateResponse(
            request,
            "planspiel/admin/szenarien.html",
            _list_context(request, db, user, meldung=f"{len(result.neu)} neu, {len(result.aktualisiert)} aktualisiert"),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        db.rollback()
        return templates.TemplateResponse(
            request,
            "planspiel/admin/szenarien.html",
            _list_context(request, db, user, fehler=f"Import fehlgeschlagen: {exc}"),
            status_code=400,
        )


@router.get("/stichworte", response_class=HTMLResponse)
def stichworte(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("uebungsleiter", "org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    rows = []
    for entry in STICHWORTE:
        matches = [row for row in _rows(db, user, {}) if row[1].stichwort_code.casefold() == entry.code]
        rows.append(
            (
                entry,
                org_stichwort_code(db, _org(user), entry.code),
                len(matches),
                sum(item.gewicht for item, _ in matches),
            )
        )
    return templates.TemplateResponse(
        request, "planspiel/admin/stichworte.html", {"user": user, "rows": rows, "quelle": QUELLE, "revision": REVISION}
    )


@router.get("/szenarien/{scenario_id:int}", response_class=HTMLResponse)
def detail(
    scenario_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("uebungsleiter", "org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    return templates.TemplateResponse(
        request, "planspiel/admin/detail.html", _detail_context(request, db, user, _scenario(db, user, scenario_id))
    )


@router.post("/szenarien/{scenario_id:int}")
async def speichern(
    scenario_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    item = _scenario(db, user, scenario_id)
    form = await request.form()
    try:
        neue_version(db, item, _content(form), user.id)
        item.aktiv = bool(form.get("aktiv"))
        item.zufall_erlaubt = bool(form.get("zufall_erlaubt"))
        item.gewicht = int(str(form.get("gewicht", 10)))
        _audit(db, "bearbeiten", user, item)
        db.commit()
        return RedirectResponse(f"/admin/settings/planspiele/szenarien/{item.id}", status_code=303)
    except (TypeError, ValueError) as exc:
        db.rollback()
        return templates.TemplateResponse(
            request,
            "planspiel/admin/detail.html",
            _detail_context(request, db, user, item, fehler=str(exc)),
            status_code=400,
        )


@router.post("/szenarien/{scenario_id:int}/duplizieren")
async def kopieren(
    scenario_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    item = _scenario(db, user, scenario_id)
    form = await request.form()
    try:
        code = str(form.get("code", "")).strip()
        if not _CODE_RE.fullmatch(code):
            raise ValueError("Ungültiger Szenario-Code.")
        copy = duplizieren(db, item, code, user.id)
        _audit(db, "duplizieren", user, copy)
        db.commit()
        return RedirectResponse(f"/admin/settings/planspiele/szenarien/{copy.id}", status_code=303)
    except ValueError as exc:
        db.rollback()
        return templates.TemplateResponse(
            request,
            "planspiel/admin/detail.html",
            _detail_context(request, db, user, item, fehler=str(exc)),
            status_code=400,
        )


@router.post("/szenarien/{scenario_id:int}/aktiv")
async def toggle(
    scenario_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    item = _scenario(db, user, scenario_id)
    form = await request.form()
    aktiv_setzen(db, item, str(form.get("aktiv", "")) == "1")
    _audit(db, "aktiv", user, item)
    db.commit()
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request,
            "planspiel/admin/_szenario_zeile.html",
            {
                "item": item,
                "version": aktive_version(db, item),
                "system_version": SYSTEM_KATALOG_VERSION,
                "user": user,
                "can_edit": True,
            },
        )
    return RedirectResponse("/admin/settings/planspiele/szenarien", status_code=303)


@router.post("/szenarien/{scenario_id:int}/zuruecksetzen")
def reset(
    scenario_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    item = _scenario(db, user, scenario_id)
    try:
        auf_standard_zuruecksetzen(db, item, user.id)
        _audit(db, "zuruecksetzen", user, item)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return templates.TemplateResponse(
            request,
            "planspiel/admin/detail.html",
            _detail_context(request, db, user, item, fehler=str(exc)),
            status_code=400,
        )
    return RedirectResponse(f"/admin/settings/planspiele/szenarien/{item.id}", status_code=303)


@router.post("/szenarien/{scenario_id:int}/entfernen")
def delete(
    scenario_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    item = _scenario(db, user, scenario_id)
    result = entfernen(db, item)
    _audit(db, "entfernen", user, item)
    db.commit()
    return RedirectResponse(f"/admin/settings/planspiele/szenarien?meldung={result}+{item.code}", status_code=303)
