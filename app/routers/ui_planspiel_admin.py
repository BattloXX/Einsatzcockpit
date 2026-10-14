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
from app.db import SessionLocal, get_db
from app.models.master import AlarmType, OrgSettings
from app.models.objekt import ObjektKategorie
from app.models.planspiel import (
    PlanspielAdresse,
    PlanspielOrtTypZuordnung,
    PlanspielSzenario,
    PlanspielSzenarioEreignis,
    PlanspielSzenarioVersion,
)
from app.models.user import User
from app.routers.ui_planspiel import require_planspiel_enabled
from app.services.geo_service import validate_geojson_polygon
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
from app.services.planspiel_ort_service import (
    GebietFehlt,
    OsmImportFehler,
    gebiet,
    manuellen_punkt_anlegen,
    osm_importieren,
    pool_aufbauen,
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


def _settings_audit(db: Session, action: str, user: User, payload: dict[str, Any] | None = None) -> None:
    write_audit(
        db,
        f"planspiel.einstellungen.{action}",
        user_id=user.id,
        org_id=_org(user),
        entity_type="planspiel_einstellungen",
        entity_id=_org(user),
        payload=payload or {},
    )


def _settings(db: Session, org_id: int) -> OrgSettings:
    item = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).one_or_none()
    if item is None:
        item = OrgSettings(org_id=org_id)
        db.add(item)
        db.flush()
    return item


def _pool_context(request: Request, db: Session, user: User) -> dict[str, Any]:
    org_id = _org(user)
    filters = {key: value for key, value in request.query_params.items() if value}
    query = db.query(PlanspielAdresse).filter(PlanspielAdresse.org_id == org_id)
    if filters.get("quelle"):
        query = query.filter(PlanspielAdresse.quelle == filters["quelle"])
    if filters.get("qualitaet") in {"A", "B", "C"}:
        query = query.filter(PlanspielAdresse.qualitaet == filters["qualitaet"])
    if filters.get("q"):
        query = query.filter(PlanspielAdresse.strasse.ilike(f"%{filters['q']}%"))
    try:
        seite = max(1, int(filters.get("seite", "1")))
    except ValueError:
        seite = 1
    rows = query.order_by(PlanspielAdresse.strasse, PlanspielAdresse.hausnr).all()
    if filters.get("ort_typ"):
        rows = [row for row in rows if filters["ort_typ"] in (row.ort_typen or [])]
    total = len(rows)
    return {
        "pool_rows": rows[(seite - 1) * 50 : seite * 50],
        "pool_filters": filters,
        "pool_seite": seite,
        "pool_seiten": max(1, (total + 49) // 50),
        "can_edit": has_role(user, "org_admin"),
        "ort_typen": ORT_TYPEN,
    }


def _settings_context(request: Request, db: Session, user: User, **extra: Any) -> dict[str, Any]:
    org_id = _org(user)
    setting = _settings(db, org_id)
    polygon = None
    if setting.planspiel_gebiet_geojson:
        try:
            polygon = json.loads(setting.planspiel_gebiet_geojson)
        except (TypeError, json.JSONDecodeError):
            polygon = None
    center = None
    missing_area = False
    try:
        area = gebiet(db, org_id)
        center = area.zentrum
    except GebietFehlt:
        missing_area = True
    categories = (
        db.query(ObjektKategorie)
        .filter(ObjektKategorie.org_id == org_id)
        .order_by(ObjektKategorie.sort, ObjektKategorie.name)
        .all()
    )
    mappings: dict[int, list[str]] = {}
    for mapping in db.query(PlanspielOrtTypZuordnung).filter(
        PlanspielOrtTypZuordnung.org_id == org_id,
        PlanspielOrtTypZuordnung.quelle == "kategorie",
    ):
        mappings.setdefault(int(mapping.schluessel), []).append(mapping.ort_typ)
    stats: dict[str, int] = {}
    for row in db.query(PlanspielAdresse).filter(PlanspielAdresse.org_id == org_id).all():
        stats[f"quelle:{row.quelle}"] = stats.get(f"quelle:{row.quelle}", 0) + int(row.aktiv)
        stats[f"qualitaet:{row.qualitaet}"] = stats.get(f"qualitaet:{row.qualitaet}", 0) + int(row.aktiv)
    context = {
        "user": user,
        "setting": setting,
        "can_edit": has_role(user, "org_admin"),
        "ort_typen": ORT_TYPEN,
        "gebiet_geojson": polygon,
        "gebiet_zentrum": center,
        "gebiet_fehlt": missing_area,
        "kategorien": categories,
        "zuordnungen": mappings,
        "pool_stats": stats,
    }
    context.update(_pool_context(request, db, user))
    context.update(extra)
    return context


def _settings_error(request: Request, db: Session, user: User, error: Exception):
    return templates.TemplateResponse(
        request,
        "planspiel/admin/einstellungen.html",
        _settings_context(request, db, user, fehler=str(error)),
        status_code=400,
    )


@router.get("", response_class=HTMLResponse)
def einstellungen(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("uebungsleiter", "org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    return templates.TemplateResponse(
        request, "planspiel/admin/einstellungen.html", _settings_context(request, db, user)
    )


@router.get("/adresspool", response_class=HTMLResponse)
def adresspool_liste(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("uebungsleiter", "org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    return templates.TemplateResponse(request, "planspiel/admin/_pool_liste.html", _pool_context(request, db, user))


@router.post("/einstellungen/gebiet")
async def gebiet_speichern(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    form = await request.form()
    try:
        radius = int(str(form.get("radius_m", "")))
        if not 500 <= radius <= 50000:
            raise ValueError("Der Radius muss zwischen 500 und 50000 Metern liegen.")
        raw = str(form.get("gebiet_geojson", "")).strip()
        geometry = None
        if raw:
            geometry = json.loads(raw)
            error = validate_geojson_polygon(geometry)
            if error:
                raise ValueError(error)
        setting = _settings(db, _org(user))
        setting.planspiel_gebiet_geojson = json.dumps(geometry) if geometry else None
        setting.planspiel_gebiet_radius_m = radius
        _settings_audit(db, "gebiet", user, {"polygon": bool(geometry), "radius_m": radius})
        db.commit()
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        db.rollback()
        return _settings_error(request, db, user, exc)
    return RedirectResponse("/admin/settings/planspiele", status_code=303)


@router.post("/einstellungen/grenzen")
async def grenzen_speichern(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    form = await request.form()
    try:
        runs = int(str(form.get("max_laufende_runs", "")))
        stellen = int(str(form.get("max_stellen_je_minute", "")))
        if not 1 <= runs <= 10 or not 1 <= stellen <= 60:
            raise ValueError("Grenzen liegen außerhalb des erlaubten Bereichs.")
        setting = _settings(db, _org(user))
        setting.planspiel_max_laufende_runs = runs
        setting.planspiel_max_stellen_je_minute = stellen
        _settings_audit(db, "grenzen", user, {"runs": runs, "stellen": stellen})
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _settings_error(request, db, user, exc)
    return RedirectResponse("/admin/settings/planspiele", status_code=303)


def _pool_result(request: Request, db: Session, user: User, message: str, status_code: int = 200):
    context = _pool_context(request, db, user)
    context["meldung"] = message
    return templates.TemplateResponse(request, "planspiel/admin/_pool_ergebnis.html", context, status_code=status_code)


@router.post("/adresspool/aufbauen")
def pool_aufbauen_post(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    try:
        result = pool_aufbauen(db, _org(user))
        _settings_audit(db, "adresspool_aufbauen", user)
        db.commit()
        message = (
            f"{result.neu} neu, {result.aktualisiert} aktualisiert, {result.deaktiviert} deaktiviert"
        )
        return _pool_result(request, db, user, message)
    except (GebietFehlt, ValueError) as exc:
        db.rollback()
        return _pool_result(request, db, user, str(exc), 400)


@router.post("/adresspool/osm")
async def pool_osm_post(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    try:
        result = await osm_importieren(SessionLocal, _org(user))
        _settings_audit(db, "adresspool_osm", user)
        db.commit()
        message = f"{result.neu} neu, {result.aktualisiert} aktualisiert, {result.deaktiviert} deaktiviert"
        return _pool_result(request, db, user, message)
    except (OsmImportFehler, GebietFehlt) as exc:
        db.rollback()
        return _pool_result(request, db, user, str(exc), 400)


@router.post("/adresspool/punkt")
async def punkt_anlegen(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    form = await request.form()
    try:
        water = str(form.get("gewaesser", "")).strip()
        merkmale: dict[str, Any] | None = None
        if water:
            merkmale = {"gewaesser": water}
        if form.get("bma"):
            merkmale = (merkmale or {}) | {"bma": True}
        row = manuellen_punkt_anlegen(
            db,
            _org(user),
            lat=float(str(form.get("lat", ""))),
            lng=float(str(form.get("lng", ""))),
            bezeichnung=str(form.get("bezeichnung", "")).strip() or None,
            strasse=str(form.get("strasse", "")).strip(), hausnr=str(form.get("hausnr", "")).strip() or None,
            ort=str(form.get("ort", "")).strip() or None,
            ort_typen=[str(value) for value in form.getlist("ort_typen")],
            merkmale=merkmale,
        )
        if not row.strasse:
            raise ValueError("Straße ist erforderlich.")
        _settings_audit(db, "punkt", user, {"adresse_id": row.id})
        db.commit()
    except (GebietFehlt, ValueError) as exc:
        db.rollback()
        return _settings_error(request, db, user, exc)
    return RedirectResponse("/admin/settings/planspiele", status_code=303)


@router.post("/adresspool/{adresse_id:int}/aktiv")
def pool_aktiv(
    adresse_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    row = (
        db.query(PlanspielAdresse)
        .filter(PlanspielAdresse.id == adresse_id, PlanspielAdresse.org_id == _org(user))
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    row.aktiv = not row.aktiv
    _settings_audit(db, "adresspool_aktiv", user, {"adresse_id": row.id, "aktiv": row.aktiv})
    db.commit()
    return templates.TemplateResponse(request, "planspiel/admin/_pool_zeile.html", {"row": row, "can_edit": True})


@router.post("/einstellungen/ortstypen")
async def ortstypen_speichern(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_planspiel_enabled),
):
    form = await request.form()
    try:
        org_id = _org(user)
        mappings = db.query(PlanspielOrtTypZuordnung).filter(
            PlanspielOrtTypZuordnung.org_id == org_id,
            PlanspielOrtTypZuordnung.quelle == "kategorie",
        )
        for row in mappings.all():
            db.delete(row)
        for category in db.query(ObjektKategorie).filter(ObjektKategorie.org_id == org_id).all():
            for ort_typ in form.getlist(f"kategorie_{category.id}"):
                if ort_typ not in ORT_TYPEN:
                    raise ValueError("Unbekannter Ortstyp")
                db.add(
                    PlanspielOrtTypZuordnung(
                        org_id=org_id,
                        quelle="kategorie",
                        schluessel=str(category.id),
                        ort_typ=ort_typ,
                    )
                )
        _settings_audit(db, "ortstypen", user)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _settings_error(request, db, user, exc)
    return RedirectResponse("/admin/settings/planspiele?meldung=Ortstyp-Zuordnung+gespeichert", status_code=303)
