"""Rechtegepruefte Datenaufbereitung fuer lesende Katplan-Integrationen."""

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.permissions import can_view_kontakt_directory, has_role
from app.core.timezones import now_local
from app.models.katplan import KatplanDokument, KatplanElement, KatplanPlan, KatplanVersionElement
from app.models.user import User
from app.services.katplan_freigabe_service import erstelle_snapshot, lade_version, pruefintervall
from app.services.katplan_link_service import links_aus_version
from app.services.katplan_quelle_service import parse_seiten
from app.services.katplan_service import _HTMLText
from app.services.katplan_typen import ELEMENT_TYPEN, PLANARTEN, bereinige_daten
from app.services.katplan_ziel_service import ziel_verfuegbar
from app.services.katplan_zugriff import darf_bearbeiten, darf_lesen, sieht_vertraulich

# Gleiche Objekt-Leserollen wie in ui_objekt; keine Router-Abhaengigkeit.
_OBJEKT_LESE_ROLLEN = (
    "readonly", "recorder", "breathing_supervisor", "incident_leader",
    "fahrtenbuch_admin", "objekt_verwalter",
)


def plan_laden(db: Session, user: User, plan_id: int) -> KatplanPlan:
    plan = db.query(KatplanPlan).filter_by(org_id=user.org_id, id=plan_id).one_or_none()
    if not darf_lesen(user) or plan is None or (not darf_bearbeiten(user) and (
        not plan.aktuelle_version_id or plan.status == "archiviert"
    )):
        raise LookupError("Nicht gefunden")
    return plan


def pruefe_fassung(user: User, fassung: str) -> bool:
    if fassung not in ("freigegeben", "arbeitsstand"):
        raise ValueError("Fassung muss freigegeben oder arbeitsstand sein.")
    if fassung == "arbeitsstand" and not darf_bearbeiten(user):
        raise PermissionError("Keine Berechtigung für den Arbeitsstand.")
    return fassung == "arbeitsstand"


def text(html: str | None) -> str | None:
    html = re.sub(r"(?i)<li\b[^>]*>", "\n- ", html or "")
    html = re.sub(r"(?i)(</(?:p|li|h[1-6]|ul|ol)\s*>|<br\s*/?>)", r"\1\n", html)
    parser = _HTMLText()
    parser.feed(html)
    parser.close()
    return "\n".join(s for line in "".join(parser.teile).splitlines() if (s := " ".join(line.split()))) or None


def link_ansichten(db: Session, user: User, links: list[dict]) -> list[dict]:
    voll = can_view_kontakt_directory(user)
    rechte = {"org_id": user.org_id, "kontakt": voll,
              "objekt": ziel_verfuegbar(db, user.org_id, "objekt") and has_role(user, *_OBJEKT_LESE_ROLLEN)}
    return [{**ansicht, "hinweis": roh.get("hinweis") or None} for ansicht, roh in zip(
        links_aus_version(db, links, voll=voll, user_rechte=rechte), links, strict=True)]


def anhang_ansichten(user: User, anhaenge: list[dict], version_id: int | None) -> list[dict]:
    result = []
    for a in anhaenge:
        if a.get("vertraulich") and not sieht_vertraulich(user):
            continue
        item = {**{k: a.get(k) for k in (
            "uuid", "art", "beschriftung", "dateiname", "mime", "url", "vertraulich", "seiten")},
            "download_url": f"/plaene/anhang/{a['uuid']}" + (
                f"?version_id={version_id}" if version_id is not None else ""),
            "download_hinweis": "Geschützte Anhang-URL; Planquellen über katplan_dokument_auszug(download=True)."}
        if a["art"] == "link":
            item["download_url"] = a.get("url")
        item["warnings"] = [f"{k}: nicht vorhanden." for k, v in item.items() if v is None]
        result.append(item)
    return result


def _rohdaten(db: Session, plan: KatplanPlan, arbeit: bool, version_id: int | None = None
              ) -> tuple[dict, list[dict], int | None]:
    if arbeit:
        if version_id is not None:
            raise ValueError("version_id ist nur für freigegebene Fassungen zulässig.")
        kopf, elemente = erstelle_snapshot(db, plan, flush=False)
        versionen = dict(db.query(KatplanElement.uuid, KatplanElement.version).filter_by(
            org_id=plan.org_id, plan_id=plan.id).all())
        for e in elemente:
            e["version"] = versionen[e["element_uuid"]]
        return kopf, elemente, None
    try:
        version, kopf, rows = lade_version(db, plan, version_id)
    except ValueError as exc:
        raise LookupError("Nicht gefunden") from exc
    return kopf, [{c.name: getattr(e, c.name) for c in KatplanVersionElement.__table__.columns}
                  for e in rows], version.id


def _relationen(db: Session, user: User, roh: list[dict], arbeit: bool) -> list[dict]:
    result = []
    for r in roh:
        # Ziele werden ausschliesslich aus einer fuer den Aufrufer sichtbaren Fassung gelesen.
        uid = r["zu_element_uuid"]
        if arbeit:
            e = db.query(KatplanElement).filter_by(org_id=user.org_id, uuid=uid).one_or_none()
            if e is None or (e.vertraulich and not sieht_vertraulich(user)):
                continue
            p = db.query(KatplanPlan).filter_by(org_id=user.org_id, id=e.plan_id).one_or_none()
            if p is None or p.status == "archiviert":
                continue
            ziel = {"kennung": e.kennung, "title": e.titel}
        else:
            ziel = None
            for p in db.query(KatplanPlan).filter(
                KatplanPlan.org_id == user.org_id, KatplanPlan.status != "archiviert",
                KatplanPlan.aktuelle_version_id.is_not(None),
            ):
                _, _, elemente = lade_version(db, p)
                treffer = next((e for e in elemente if e.element_uuid == uid and (
                    not e.vertraulich or sieht_vertraulich(user))), None)
                if treffer:
                    ziel = {"kennung": treffer.kennung, "title": treffer.titel}
                    break
            if ziel is None:
                continue
        assert ziel is not None
        result.append({"element_id": uid, "art": r["art"], "hinweis": r.get("hinweis") or None, **ziel})
    return result


def _adresstext(e: dict) -> str | None:
    """"Straße Hausnummer, PLZ Ort" ohne leere Teile."""
    zeile1 = " ".join(str(e[k]) for k in ("strasse", "hausnummer") if e.get(k))
    zeile2 = " ".join(str(e[k]) for k in ("plz", "ort") if e.get(k))
    return ", ".join(t for t in (zeile1, zeile2) if t) or None


def element_dict(db: Session, user: User, plan: KatplanPlan, e: dict, kopf: dict,
                 version_id: int | None) -> dict[str, Any]:
    warnings: list[str] = []
    geometry = json.loads(e["geometry_json"]) if e.get("geometry_json") else None
    if geometry is None and e.get("lat") is not None and e.get("lng") is not None:
        geometry = {"type": "Point", "coordinates": [e["lng"], e["lat"]]}
    abschnitte = {a["id"]: a for a in kopf.get("abschnitte", [])}
    pfad: list[str] = []
    aid = e.get("abschnitt_id")
    gesehen = set()
    while aid in abschnitte and aid not in gesehen:
        gesehen.add(aid)
        a = abschnitte[aid]
        pfad.insert(0, a["titel"])
        aid = a["parent_id"]
    quellen = []
    if e.get("quelle_dokument_id"):
        q = next((q for q in kopf.get("quellen", []) if q["id"] == e["quelle_dokument_id"]), None)
        if q is None:
            # Die sichtbare Elementfassung selbst erteilt die Quellreferenz,
            # auch wenn die Quelle nicht mehr zur aktuellen Arbeitsgruppe gehoert.
            d = db.query(KatplanDokument).filter_by(
                org_id=plan.org_id, plan_id=plan.id, id=e["quelle_dokument_id"],
            ).one_or_none()
            q = {"titel": d.titel, "versionsnummer": d.versionsnummer} if d else {}
        dokument = db.query(KatplanDokument).filter_by(
            org_id=plan.org_id, plan_id=plan.id, id=e["quelle_dokument_id"],
        ).one_or_none()
        seiten_liste = []
        if dokument:
            try:
                nummern = parse_seiten(e.get("quelle_seiten") or "", dokument.seitenzahl, limit=30)
            except ValueError:
                nummern = []
                warnings.append("Ungültiger Seitenbereich der Quellreferenz.")
            suffix = f"?version_id={version_id}" if version_id is not None else ""
            for nr in nummern:
                basis = f"/plaene/quelle/{dokument.uuid}/seite/{nr}"
                seiten_liste.append({"seiten_nr": nr, "thumb_url": basis + "/thumb" + suffix,
                                     "seiten_url": basis + "/bild" + suffix, "text_url": basis + "/text" + suffix})
        quellen.append({"dokument_id": e["quelle_dokument_id"], "titel": q.get("titel"),
                        "versionsnummer": q.get("versionsnummer"), "seiten": e.get("quelle_seiten") or None,
                        "seiten_liste": seiten_liste})
    datum = e.get("pruefen_am")
    ueberfaellig = bool(datum and str(datum) < now_local(user.org).date().isoformat())
    result: dict[str, Any] = {
        "plan_id": plan.id, "plan_version_id": version_id, "element_id": e["element_uuid"],
        "element_version_id": e.get("id") if version_id else None,
        "version": e.get("version") if version_id is None else None,
        "type": e["typ"], "type_label": ELEMENT_TYPEN[e["typ"]].label,
        "kennung": e.get("kennung") or None, "title": e["titel"], "body": text(e.get("beschreibung_html")),
        "daten": bereinige_daten(e["typ"], json.loads(e.get("daten_json") or "{}")),
        "priority": e.get("prioritaet"), "tags": json.loads(e.get("tags_json") or "[]"),
        "phases": [p["name"] for p in kopf.get("phasen", []) if p["id"] in json.loads(e["phasen_json"])],
        "chapter": " > ".join(pfad) or None,
        "address": _adresstext(e),
        "address_parts": {k: e.get(k) or "" for k in ("strasse", "hausnummer", "plz", "ort")},
        "geometry": geometry, "geo_status": e.get("geo_status"),
        "linked_entities": link_ansichten(db, user, json.loads(e["links_json"])),
        "relations": _relationen(db, user, json.loads(e["relationen_json"]), version_id is None),
        "attachments": anhang_ansichten(user, json.loads(e["anhaenge_json"]), version_id),
        "source_references": quellen,
        "review_status": {"herkunft": e.get("herkunft"), "geo_status": e.get("geo_status"),
                          "pruefen_am_ueberschritten": ueberfaellig},
        "vertraulich": e["vertraulich"],
        "gueltig_bis": str(e["gueltig_bis"]) if e.get("gueltig_bis") else None,
        "pruefen_am": str(datum) if datum else None,
    }
    warnings.extend(f"{key}: nicht vorhanden." for key, value in result.items() if value is None
                    and key not in {"version", "element_version_id", "plan_version_id"})
    if ueberfaellig:
        warnings.append("Prüftermin überschritten.")
    if e.get("herkunft") == "ai_suggested":
        warnings.append("KI-Vorschlag: fachlich prüfen.")
    for q in quellen:
        warnings.extend(f"source_references.{key}: nicht vorhanden." for key, value in q.items() if value is None)
    result["warnings"] = warnings
    return result


def fassung_laden(db: Session, user: User, plan: KatplanPlan, fassung: str,
                  version_id: int | None = None) -> tuple[dict, list[dict], int | None, list[str]]:
    if plan.org_id != user.org_id or not darf_lesen(user):
        raise LookupError("Nicht gefunden")
    arbeit = pruefe_fassung(user, fassung)
    kopf, roh, vid = _rohdaten(db, plan, arbeit, version_id)
    kopf.update(pruefdaten(db, plan))
    kopf["wiedervorlage_am"] = kopf["naechste_pruefung"]
    elemente = [element_dict(db, user, plan, e, kopf, vid) for e in roh
                if not e["vertraulich"] or sieht_vertraulich(user)]
    warnings = [f"{key}: nicht vorhanden." for key in (
        "geltungsbereich", "verantwortliche_stelle", "wiedervorlage_am") if not kopf.get(key)]
    if not text(kopf.get("beschreibung_html")):
        warnings.append("body: nicht vorhanden.")
    return kopf, elemente, vid, warnings


def pruefdaten(db: Session, plan: KatplanPlan) -> dict:
    person = db.query(User).filter_by(org_id=plan.org_id, id=plan.pruef_verantwortlich_user_id).first()
    return {"zuletzt_geprueft_am": plan.zuletzt_geprueft_am.isoformat() if plan.zuletzt_geprueft_am else None,
            "naechste_pruefung": plan.wiedervorlage_am.isoformat() if plan.wiedervorlage_am else None,
            "pruefintervall_monate": pruefintervall(plan),
            "pruef_verantwortlich": person.display_name if person else None}


def kopf_ansicht(kopf: dict) -> dict:
    """Gleicher Plankopf fuer Lese- und Schreibantworten, ohne Transaktionsabschluss."""
    planart = kopf.get("planart", "katastrophenplan")
    return {"planart": planart, "planart_label": PLANARTEN[planart].label,
            **{k: kopf.get(k) for k in ("uuid", "titel", "gefahrenarten", "geltungsbereich",
                                       "verantwortliche_stelle", "verantwortlich_kontakt_id", "wiedervorlage_am",
                                       "zuletzt_geprueft_am", "naechste_pruefung",
                                       "pruefintervall_monate", "pruef_verantwortlich")},
            "body": text(kopf.get("beschreibung_html"))}
