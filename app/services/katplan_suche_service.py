"""Gemeinsame, rechtegepruefte Suche fuer UI und lesende Integrationen."""

import json
import math
import re
from html import escape
from typing import Any, cast

from markupsafe import Markup
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.katplan import (
    KatplanDokument,
    KatplanDokumentSeite,
    KatplanElement,
    KatplanElementPhase,
    KatplanPhase,
    KatplanPlan,
    KatplanVersion,
    KatplanVersionElement,
)
from app.models.master import OrgSettings
from app.models.user import User
from app.services.einsatzwissen_kategorien import metadaten
from app.services.katplan_quelle_service import quellen_ids_aus_version, sichtbare_quellen
from app.services.katplan_service import _HTMLText
from app.services.katplan_typen import ELEMENT_TYPEN, PLANARTEN
from app.services.katplan_zugriff import darf_bearbeiten, darf_lesen, sieht_vertraulich
from app.services.road_closure_geo_service import bbox, distance_point_to_geometry_m, validate_geometry
from app.services.suche.synonyme import expandiere, normalisiere


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
                     "kategorie": plan.kategorie, "unterkategorie": plan.unterkategorie,
                     "kurzbeschreibung": plan.kurzbeschreibung,
                     "schlagwoerter": json.loads(plan.schlagwoerter_json or "[]"),
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
                       "phasen": daten.get("phasen", []), **metadaten(daten)})
    return sorted(result, key=lambda p: (p["titel"].lower(), p["id"]))


def _suchmuster(woerter: list[str]) -> re.Pattern[str]:
    alternativen = []
    for wort in sorted(expandiere(woerter), key=len, reverse=True):
        if not wort:
            continue
        # Normalisierte Schreibweise und Umlaute treffen denselben Originaltext.
        muster = re.escape(normalisiere(wort))
        for ersatz, zeichen in (("ae", "ä"), ("oe", "ö"), ("ue", "ü"), ("ss", "ß")):
            muster = muster.replace(ersatz, f"(?:{ersatz}|{zeichen})")
        anfang = r"\b" if wort[0].isalnum() else ""
        ende = r"\w*" if wort[-1].isalnum() else ""
        alternativen.append(anfang + muster + ende)
    return re.compile("|".join(alternativen) if alternativen else r"(?!)", re.IGNORECASE)


def ausschnitt(text: str | None, woerter: list[str], laenge: int = 200) -> str:
    """Fenster mit den meisten verschiedenen Suchwoertern samt Synonymen."""
    text = " ".join((text or "").split())
    funde = sorted((m.start(), m.end(), i) for i, wort in enumerate(dict.fromkeys(woerter))
                   for m in _suchmuster([wort]).finditer(text))
    start = 0
    bester_rang = (0, 0)
    links = 0
    anzahl: dict[int, int] = {}
    for rechts, (_pos, ende, gruppe) in enumerate(funde):
        anzahl[gruppe] = anzahl.get(gruppe, 0) + 1
        while links < rechts and ende - funde[links][0] > laenge:
            alt = funde[links][2]
            anzahl[alt] -= 1
            if not anzahl[alt]:
                del anzahl[alt]
            links += 1
        rang = (len(anzahl), rechts - links + 1)
        if rang > bester_rang:
            bester_rang = rang
            start = max(0, funde[links][0] - min(70, max(0, laenge - (ende - funde[links][0]))))
    return ("... " if start else "") + text[start:start + laenge] + (" ..." if len(text) > start + laenge else "")


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
    muster = _suchmuster(q.split())
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


def zentrale_suche_aktiv(db: Session, user: User) -> bool:
    if not settings.SEARCH_ENABLED or user.org_id is None:
        return False
    org = db.query(OrgSettings).filter_by(org_id=user.org_id).one_or_none()
    return bool(org and org.suche_aktiv)


def _ergebnis(treffer: list[dict[str, Any]], limit: int, q: str) -> dict[str, Any]:
    """Facetten vor Begrenzung zaehlen; Dokumentseiten nur in der Anzeige buendeln."""
    facetten: dict[str, dict[Any, int]] = {"typ": {}, "phase": {}, "plan": {}}
    for t in treffer:
        for art, werte in (("typ", [t["typ"]] if t["typ"] else []),
                           ("plan", [t["plan_id"]]), ("phase", t.get("phase_ids", []))):
            for wert in werte:
                facetten[art][wert] = facetten[art].get(wert, 0) + 1
        t["ausschnitt_html"] = markiere(t["ausschnitt"], q)
    sichtbar = treffer[:max(0, limit)]
    gruppen: dict[int, dict[str, Any]] = {}
    dokumente: dict[tuple[int, int], dict[str, Any]] = {}
    for t in sichtbar:
        gruppe = gruppen.setdefault(t["plan_id"], {
            "plan_id": t["plan_id"], "plan_titel": t["plan_titel"], "elemente": [], "seiten": [],
        })
        if t["art"] == "element":
            gruppe["elemente"].append(t)
        else:
            key = (t["plan_id"], t["dokument_id"])
            if key not in dokumente:
                dokumente[key] = {**t, "seiten": []}
                gruppe["seiten"].append(dokumente[key])
            dokumente[key]["seiten"].append(t)
    for dokument in dokumente.values():
        dokument["seiten"].sort(key=lambda t: t["seiten_nr"])
        dokument["titel"] = dokument["dokument_titel"] + ", Seiten " + ", ".join(
            str(t["seiten_nr"]) for t in dokument["seiten"])
        dokument["url"] = dokument["seiten"][0]["url"]
    return {"treffer": sichtbar, "gesamt": len(treffer), "abgeschnitten": len(treffer) > max(0, limit),
            "facetten": facetten, "gruppen": list(gruppen.values())}


def suche(db: Session, user: User, *, q: str = "", plan_id: int | None = None,
          gefahr: str = "", typ: str = "", prioritaet: int | None = None, phase: str = "",
          ort: str = "", nur_geprueft: bool = False, lat: float | None = None,
          lng: float | None = None, radius_m: float | None = None, arbeitsstand: bool = False,
          mit_quellen: bool = True, limit: int = 100,
          nur_freigegebene_quellen: bool = False) -> dict[str, Any]:
    woerter = q.lower().split()
    ort = ort.strip().lower()
    umkreis = any(v is not None for v in (lat, lng, radius_m))
    if umkreis and (lat is None or lng is None or radius_m is None
                    or not all(math.isfinite(v) for v in (lat, lng, radius_m))
                    or not -90 <= lat <= 90 or not -180 <= lng <= 180 or not 0 < radius_m <= 50000):
        raise ValueError("Bitte gültige Koordinaten und einen Radius bis 50 km eingeben.")
    if not woerter and not any((plan_id is not None, gefahr, typ, prioritaet is not None,
                               phase and plan_id is not None, ort, nur_geprueft, umkreis)):
        return _ergebnis([], limit, q)
    arbeit = arbeitsstand and darf_bearbeiten(user)
    # Lokale Imports: die Engine verwendet ihrerseits ausschnitt() aus diesem Modul.
    from app.services.suche import abfrage
    from app.services.suche.vertrag import PlanSuchAnfrage

    engine = None
    if (woerter and not arbeitsstand and not any((ort, umkreis, nur_geprueft))
            and zentrale_suche_aktiv(db, user)):
        filter_werte = {k: v for k, v in {
            "typ": typ or None, "prioritaet": prioritaet,
            "phase_id": phase if phase and plan_id is not None else None,
        }.items() if v is not None}
        engine = abfrage.suche(db, user, PlanSuchAnfrage(
            q=q, plan_id=plan_id, typen=["katplan_element", "katplan_seite"],
            filter=filter_werte, limit=100,
        ))
    engine_map = {(t.quelle, t.quell_id): t for t in engine.treffer} if engine is not None else {}
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
        if engine is not None:
            uuids = [key.partition(":")[2] for quelle, key in engine_map
                     if quelle == "katplan_element" and key.partition(":")[0] == str(plan.id)]
            query = query.filter(KatplanVersionElement.element_uuid.in_(uuids))
        for wort in ([] if engine is not None else woerter):
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
            ids = (json.loads(element.phasen_json) if isinstance(element, KatplanVersionElement)
                   else [str(z.phase_id) for z in db.query(KatplanElementPhase).filter_by(
                       org_id=user.org_id, element_id=element.id)])
            engine_treffer = engine_map.get(("katplan_element", f"{plan.id}:{uid}"))
            treffer.append({"art": "element", "element_id": uid, "plan_id": plan.id, "plan_titel": kopf["titel"],
                            "planart_label": kopf["planart_label"],
                            "titel": element.titel, "kennung": element.kennung or "", "typ": element.typ,
                            "typ_label": (ELEMENT_TYPEN[element.typ].label
                                          if element.typ in ELEMENT_TYPEN else element.typ),
                            "prioritaet": element.prioritaet, "vertraulich": element.vertraulich,
                            "phase_ids": [str(i) for i in ids],
                            "phase_label": ", ".join(p["name"] for p in kopf["phasen"]
                                                     if str(p["id"]) in {str(i) for i in ids}),
                            "ausschnitt": (engine_treffer.auszug if engine_treffer else
                                           ausschnitt(_klartext(element, woerter), woerter)),
                            "url": url, "distanz_m": distanz})
        if not mit_quellen or not woerter or any((ort, umkreis, typ, prioritaet is not None, phase, nur_geprueft)):
            continue
        dokumente = {d.id: d for d in sichtbare_quellen(db, plan, user)}
        if not arbeit and nur_freigegebene_quellen:
            freigegebene_ids = quellen_ids_aus_version(db, plan, kopf["version_id"], user)
            dokumente = {d.id: d for d in db.query(KatplanDokument).filter(
                KatplanDokument.org_id == user.org_id, KatplanDokument.plan_id == plan.id,
                KatplanDokument.id.in_(freigegebene_ids))}
        seiten = db.query(KatplanDokumentSeite).filter(
            KatplanDokumentSeite.org_id == user.org_id, KatplanDokumentSeite.plan_id == plan.id,
            KatplanDokumentSeite.dokument_id.in_(dokumente),
        )
        if engine is not None:
            seiten = seiten.filter(KatplanDokumentSeite.id.in_(
                [key for quelle, key in engine_map if quelle == "katplan_seite"]))
        for wort in ([] if engine is not None else woerter):
            seiten = seiten.filter(func.lower(KatplanDokumentSeite.volltext).like(_like(wort), escape="\\"))
        for seite in seiten:
            dok = dokumente[seite.dokument_id]
            url = f"/plaene/{plan.id}/quellen/{dok.id}?seite={seite.seiten_nr}"
            if not darf_bearbeiten(user) or (not arbeit and nur_freigegebene_quellen):
                url += f"&version_id={kopf['version_id']}"
            engine_seite = engine_map.get(("katplan_seite", str(seite.id)))
            treffer.append({"art": "seite", "plan_id": plan.id, "plan_titel": kopf["titel"],
                            "planart_label": kopf["planart_label"],
                            "titel": seite.titel or f"{dok.titel}, Seite {seite.seiten_nr}", "kennung": "",
                            "typ": "", "typ_label": "Dokumentseite", "prioritaet": None, "vertraulich": False,
                            "phase_ids": [], "phase_label": "",
                            "ausschnitt": (engine_seite.auszug if engine_seite else
                                           ausschnitt(seite.volltext, woerter)),
                            "url": url, "distanz_m": None,
                            "dokument_id": dok.id, "dokument_titel": dok.titel, "seiten_nr": seite.seiten_nr})

    def rang(t: dict[str, Any]) -> tuple:
        relevanz = (0 if woerter and any(w in t["titel"].lower() for w in woerter)
                    else 1 if woerter and any(w in t["kennung"].lower() for w in woerter) else 2)
        return (t["distanz_m"] if umkreis else relevanz, t["prioritaet"] or 99,
                t["plan_titel"].lower(), t["titel"].lower(), t["url"])

    if engine is not None:
        raenge = {t.url: i for i, t in enumerate(engine.treffer)}
        treffer.sort(key=lambda t: raenge.get(t["url"], 100))
    else:
        treffer.sort(key=rang)
    return _ergebnis(treffer, limit, q)
