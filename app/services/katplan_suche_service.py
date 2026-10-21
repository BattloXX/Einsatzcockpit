"""Gemeinsame, rechtegepruefte Suche fuer UI und lesende Integrationen."""

import json
import math
import re
from html import escape
from typing import Any, cast

from markupsafe import Markup
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.katplan import (
    KatplanDokumentSeite,
    KatplanElement,
    KatplanElementPhase,
    KatplanPhase,
    KatplanPlan,
    KatplanVersion,
    KatplanVersionElement,
)
from app.models.user import User
from app.services.katplan_quelle_service import sichtbare_quellen
from app.services.katplan_service import _HTMLText
from app.services.katplan_typen import ELEMENT_TYPEN, PLANARTEN
from app.services.katplan_zugriff import darf_bearbeiten, darf_lesen, sieht_vertraulich
from app.services.road_closure_geo_service import bbox, distance_point_to_geometry_m, validate_geometry


def _like(wort: str) -> str:
    return "%" + wort.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def sichtbare_plaene(db: Session, user: User, arbeitsstand: bool = False) -> list[dict[str, Any]]:
    """Auch Metadaten stammen in der Freigabesicht aus dem Snapshot."""
    if not darf_lesen(user) or user.org_id is None:
        return []
    arbeit = arbeitsstand and darf_bearbeiten(user)
    query = db.query(KatplanPlan).filter(
        KatplanPlan.org_id == user.org_id, KatplanPlan.status != "archiviert",
    )
    if not arbeit:
        query = query.filter(KatplanPlan.aktuelle_version_id.is_not(None))
    plaene = query.all()
    versionen = {v.id: v for v in db.query(KatplanVersion).filter(
        KatplanVersion.org_id == user.org_id,
        KatplanVersion.id.in_([p.aktuelle_version_id for p in plaene]),
    )}
    result = []
    for plan in plaene:
        daten: dict[str, Any]
        version = versionen.get(plan.aktuelle_version_id or 0)
        if arbeit:
            daten = {"planart": plan.planart, "titel": plan.titel, "gefahrenarten": plan.gefahrenarten,
                     "phasen": [{"id": p.id, "name": p.name} for p in db.query(KatplanPhase).filter_by(
                         org_id=user.org_id, plan_id=plan.id).order_by(KatplanPhase.sort, KatplanPhase.id)]}
        elif version is not None and version.plan_id == plan.id:
            daten = json.loads(version.plan_json)
        else:
            continue
        result.append({"id": plan.id, "plan": plan, "version_id": plan.aktuelle_version_id,
                       "planart": daten.get("planart", "katastrophenplan"),
                       "planart_label": PLANARTEN[daten.get("planart", "katastrophenplan")].label,
                       "titel": daten.get("titel", ""), "gefahrenarten": daten.get("gefahrenarten", []),
                       "phasen": daten.get("phasen", [])})
    return sorted(result, key=lambda p: (p["titel"].lower(), p["id"]))


def ausschnitt(text: str | None, woerter: list[str]) -> str:
    text = " ".join((text or "").split())
    pos = text.lower().find(woerter[0]) if woerter else 0
    start = max(0, pos - 70)
    return ("... " if start else "") + text[start:start + 200] + (" ..." if len(text) > start + 200 else "")


def _klartext(element: KatplanElement | KatplanVersionElement, woerter: list[str]) -> str:
    """Ausschnitt in Originalschreibweise; der kleingeschriebene suchtext ist nur Rueckfall,
    wenn das Suchwort ausschliesslich in Daten, Tags oder Verknuepfungen steht."""
    parser = _HTMLText()
    parser.feed(element.beschreibung_html or "")
    parser.close()
    text = " ".join("".join(parser.teile).split())
    if text and (not woerter or woerter[0] in text.lower()):
        return text
    return element.suchtext or ""


def markiere(text: str, q: str) -> Markup:
    """Nur erzeugte mark-Tags sind HTML; auch Suchbegriffe bleiben Text."""
    woerter = sorted(set(q.lower().split()), key=len, reverse=True)
    if not woerter:
        return Markup(escape(text))
    muster = re.compile("|".join(re.escape(w) for w in woerter), re.IGNORECASE)
    teile = []
    start = 0
    for match in muster.finditer(text):
        teile.extend([escape(text[start:match.start()]), "<mark>", escape(match[0]), "</mark>"])
        start = match.end()
    teile.append(escape(text[start:]))
    return Markup("".join(teile))


def _distanz(element: KatplanElement | KatplanVersionElement, lat: float, lng: float,
             radius: float) -> float | None:
    if element.geometry_json:
        geom = validate_geometry(element.geometry_json)
    elif element.lat is not None and element.lng is not None:
        geom = {"type": "Point", "coordinates": [element.lng, element.lat]}
    else:
        return None
    min_lat, min_lng, max_lat, max_lng = bbox(geom)
    # Konservativer Vorfilter, damit Randtreffer nicht durch Rundung verloren gehen.
    delta_lat = radius / 110000
    delta_lng = min(360, delta_lat / max(0.000001, math.cos(math.radians(min(90, abs(lat) + delta_lat)))))
    if max_lat < lat - delta_lat or min_lat > lat + delta_lat:
        return None
    if abs(lng) + delta_lng < 180 and (max_lng < lng - delta_lng or min_lng > lng + delta_lng):
        return None
    distanz = distance_point_to_geometry_m(lat, lng, geom)
    return distanz if distanz <= radius else None


def suche(db: Session, user: User, *, q: str = "", plan_id: int | None = None,
          gefahr: str = "", typ: str = "", prioritaet: int | None = None, phase: str = "",
          ort: str = "", nur_geprueft: bool = False, lat: float | None = None,
          lng: float | None = None, radius_m: float | None = None, arbeitsstand: bool = False,
          mit_quellen: bool = True, limit: int = 100) -> dict[str, Any]:
    woerter = q.lower().split()
    ort = ort.strip().lower()
    umkreis = any(v is not None for v in (lat, lng, radius_m))
    if umkreis and (lat is None or lng is None or radius_m is None
                    or not all(math.isfinite(v) for v in (lat, lng, radius_m))
                    or not -90 <= lat <= 90 or not -180 <= lng <= 180 or not 0 < radius_m <= 50000):
        raise ValueError("Bitte gültige Koordinaten und einen Radius bis 50 km eingeben.")
    if not woerter and not any((plan_id is not None, gefahr, typ, prioritaet is not None,
                               phase and plan_id is not None, ort, nur_geprueft, umkreis)):
        return {"treffer": [], "gesamt": 0, "abgeschnitten": False}
    arbeit = arbeitsstand and darf_bearbeiten(user)
    treffer: list[dict[str, Any]] = []
    for kopf in sichtbare_plaene(db, user, arbeit):
        plan = kopf["plan"]
        if plan_id is not None and plan.id != plan_id or gefahr and gefahr not in kopf["gefahrenarten"]:
            continue
        model = KatplanElement if arbeit else KatplanVersionElement
        query = db.query(model).filter(model.org_id == user.org_id)
        query = query.filter(KatplanElement.plan_id == plan.id) if arbeit else query.filter(
            KatplanVersionElement.version_id == kopf["version_id"])
        if not sieht_vertraulich(user):
            query = query.filter(model.vertraulich.is_(False))
        for wort in woerter:
            query = query.filter(func.lower(model.suchtext).like(_like(wort), escape="\\"))
        if typ:
            query = query.filter(model.typ == typ)
        if prioritaet is not None:
            query = query.filter(model.prioritaet == prioritaet)
        if ort:
            query = query.filter(or_(*(func.lower(c).like(_like(ort), escape="\\")
                                       for c in (model.ort, model.plz, model.strasse))))
        if nur_geprueft:
            query = query.filter(model.geo_status == "geprueft")
        phase_id = str(phase) if phase and plan_id is not None else ""
        phasen_ids = {z.element_id for z in db.query(KatplanElementPhase).filter_by(
            org_id=user.org_id, phase_id=phase_id)} if phase_id and arbeit else set()
        for element in cast(list[KatplanElement | KatplanVersionElement], query.all()):
            if phase_id:
                ids = [str(p) for p in json.loads(element.phasen_json)] if isinstance(
                    element, KatplanVersionElement) else []
                if (arbeit and element.id not in phasen_ids) or (not arbeit and phase_id not in ids):
                    continue
            distanz = None
            if lat is not None and lng is not None and radius_m is not None:
                distanz = _distanz(element, lat, lng, radius_m)
                if distanz is None:
                    continue
            uid = element.uuid if isinstance(element, KatplanElement) else element.element_uuid
            url = (f"/plaene/{plan.id}/bearbeiten?element={element.id}" if arbeit
                   else f"/plaene/{plan.id}#element-{uid}")
            treffer.append({"art": "element", "element_id": uid, "plan_id": plan.id, "plan_titel": kopf["titel"],
                            "planart_label": kopf["planart_label"],
                            "titel": element.titel, "kennung": element.kennung or "", "typ": element.typ,
                            "typ_label": (ELEMENT_TYPEN[element.typ].label
                                          if element.typ in ELEMENT_TYPEN else element.typ),
                            "prioritaet": element.prioritaet, "vertraulich": element.vertraulich,
                            "ausschnitt": ausschnitt(_klartext(element, woerter), woerter),
                            "url": url, "distanz_m": distanz})
        if not mit_quellen or not woerter or any((ort, umkreis, typ, prioritaet is not None, phase, nur_geprueft)):
            continue
        dokumente = {d.id: d for d in sichtbare_quellen(db, plan, user)}
        seiten = db.query(KatplanDokumentSeite).filter(
            KatplanDokumentSeite.org_id == user.org_id, KatplanDokumentSeite.plan_id == plan.id,
            KatplanDokumentSeite.dokument_id.in_(dokumente),
        )
        for wort in woerter:
            seiten = seiten.filter(func.lower(KatplanDokumentSeite.volltext).like(_like(wort), escape="\\"))
        for seite in seiten:
            dok = dokumente[seite.dokument_id]
            url = f"/plaene/{plan.id}/quellen/{dok.id}?seite={seite.seiten_nr}"
            if not darf_bearbeiten(user):
                url += f"&version_id={kopf['version_id']}"
            treffer.append({"art": "seite", "plan_id": plan.id, "plan_titel": kopf["titel"],
                            "planart_label": kopf["planart_label"],
                            "titel": seite.titel or f"{dok.titel}, Seite {seite.seiten_nr}", "kennung": "",
                            "typ": "", "typ_label": "Dokumentseite", "prioritaet": None, "vertraulich": False,
                            "ausschnitt": ausschnitt(seite.volltext, woerter), "url": url, "distanz_m": None,
                            "dokument_id": dok.id, "dokument_titel": dok.titel, "seiten_nr": seite.seiten_nr})

    def rang(t: dict[str, Any]) -> tuple:
        relevanz = (0 if woerter and any(w in t["titel"].lower() for w in woerter)
                    else 1 if woerter and any(w in t["kennung"].lower() for w in woerter) else 2)
        return (t["distanz_m"] if umkreis else relevanz, t["prioritaet"] or 99,
                t["plan_titel"].lower(), t["titel"].lower(), t["url"])

    treffer.sort(key=rang)
    limit = max(0, limit)
    return {"treffer": treffer[:limit], "gesamt": len(treffer), "abgeschnitten": len(treffer) > limit}
