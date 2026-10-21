"""Dynamische Qualitaetshinweise ausschliesslich fuer den Arbeitsstand."""

import json
import re
from collections import Counter
from collections.abc import Iterator
from datetime import date

from sqlalchemy.orm import Session

from app.models.katplan import KatplanAenderung, KatplanDokument, KatplanElement, KatplanLink, KatplanPlan
from app.services.katplan_freigabe_service import lade_version
from app.services.katplan_link_service import link_ansicht, links_aus_version
from app.services.katplan_quelle_service import _gruppe
from app.services.katplan_ziel_service import ziel_verfuegbar

LABELS = {
    "massnahme_ohne_akteur": "Maßnahme ohne zuständigen Akteur",
    "telefon_im_text": "Telefonnummer im Text – zentralen Kontakt verknüpfen",
    "ohne_geometrie": "ohne Geometrie", "geo_ungeprueft": "Ort ungeprüft",
    "link_ziel_fehlt": "Verknüpfungsziel fehlt", "kontakt_geaendert": "Kontakt seit Freigabe geändert",
    "pruefen_faellig": "Prüfung fällig", "gueltig_abgelaufen": "Gültigkeit abgelaufen",
    "quelle_fehlt": "Maßnahme ohne Quellenangabe",
    "ki_ungeprueft": "KI ungeprüft", "quelle_neuer": "neuere Quelle verfügbar",
}


SYMBOLE = {
    "massnahme_ohne_akteur": "users",
    "telefon_im_text": "phone",
    "ohne_geometrie": "map-pin", "geo_ungeprueft": "help-circle", "link_ziel_fehlt": "link",
    "kontakt_geaendert": "user",
    "pruefen_faellig": "alarm", "gueltig_abgelaufen": "hourglass", "ki_ungeprueft": "robot",
    "quelle_neuer": "file-text", "quelle_fehlt": "file-text",
}


_TELEFON = re.compile(r"(?<![\d.])(?:\+|00)?\d[\d /-]{6,}\d")
_DATUM = re.compile(r"(?<!\d)\d{1,4}[-./]\d{1,2}[-./]\d{1,4}(?!\d)")


def _telefonnummern_im_text(wert: str) -> Iterator[str]:
    ohne_datum = _DATUM.sub(lambda m: "\n" * len(m.group()), wert)
    for treffer in _TELEFON.finditer(ohne_datum):
        nummer = treffer.group()
        if sum(c.isdigit() for c in nummer) >= 8:
            yield nummer


def _hat_telefon(text: str) -> bool:
    """Mindestens acht Ziffern, keine Datumsangabe."""
    return next(_telefonnummern_im_text(text), None) is not None


def string_werte(wert):
    """String-Werte in stabiler Reihenfolge, auch in verschachtelten Typdaten."""
    if isinstance(wert, str):
        yield wert
    elif isinstance(wert, dict):
        for kind in wert.values():
            yield from string_werte(kind)
    elif isinstance(wert, list):
        for kind in wert:
            yield from string_werte(kind)


def telefonnummern_im_element(element) -> list[str]:
    """Originalschreibweisen aus Beschreibung und Typdaten, maximal zehn."""
    from app.services.katplan_mcp_service import text

    def feld(name):
        return element.get(name) if isinstance(element, dict) else getattr(element, name, None)

    nummern: list[str] = []
    for wert in [text(feld("beschreibung_html")) or "", *string_werte(json.loads(feld("daten_json") or "{}"))]:
        for nummer in _telefonnummern_im_text(wert):
            if nummer not in nummern:
                nummern.append(nummer)
                if len(nummern) == 10:
                    return nummern
    return nummern


def freigabe_links(db: Session, plan: KatplanPlan) -> dict[str, list[dict]]:
    """Kontaktlinks je element_uuid aus der aktuellen Freigabe; einmal je Plan laden."""
    if not plan.aktuelle_version_id:
        return {}
    _, _, rows = lade_version(db, plan)
    return {e.element_uuid: [link for link in json.loads(e.links_json) if link["ziel_typ"] == "kontakt"]
            for e in rows}


def element_hinweise(db: Session, plan: KatplanPlan, element: KatplanElement, *, heute: date,
                     version_links: dict[str, list[dict]] | None = None) -> list[dict]:
    if element.org_id != plan.org_id or element.plan_id != plan.id:
        raise ValueError("Element gehört nicht zu diesem Plan.")
    codes = []
    if (element.typ == "massnahme" and plan.planart in ("alarm_einsatzplan", "massnahmenplan")
            and not json.loads(element.akteure_json or "[]")):
        codes.append("massnahme_ohne_akteur")
    if element.typ in ("kontrollstelle", "gefahrenbereich") and not (
        element.geometry_json or (element.lat is not None and element.lng is not None)
    ):
        codes.append("ohne_geometrie")
    if element.geo_status == "ungeprueft":
        codes.append("geo_ungeprueft")
    links = db.query(KatplanLink).filter_by(org_id=plan.org_id, plan_id=plan.id, element_id=element.id).all()
    if any(link_ansicht(db, link, voll=False)["fehlt"] or not ziel_verfuegbar(
        db, plan.org_id, link.ziel_typ) for link in links):
        codes.append("link_ziel_fehlt")
    nummern = telefonnummern_im_element(element)
    if nummern:
        codes.append("telefon_im_text")
    if version_links is None:
        version_links = freigabe_links(db, plan)
    roh = version_links.get(element.uuid)
    if roh and any(a["geaendert_seit_freigabe"] and not a["fehlt"] for a in links_aus_version(
        db, roh, voll=False, user_rechte={"org_id": plan.org_id},
    )):
        codes.append("kontakt_geaendert")
    if element.pruefen_am and element.pruefen_am <= heute:
        codes.append("pruefen_faellig")
    if element.gueltig_bis and element.gueltig_bis < heute:
        codes.append("gueltig_abgelaufen")
    if element.herkunft == "ai_suggested" and not db.query(KatplanAenderung).filter(
        KatplanAenderung.org_id == plan.org_id, KatplanAenderung.plan_id == plan.id,
        KatplanAenderung.element_id == element.id, KatplanAenderung.aktion == "geaendert",
        KatplanAenderung.quelle == "ui", KatplanAenderung.zeit >= element.erstellt_am,
    ).first():
        codes.append("ki_ungeprueft")
    if element.typ == "massnahme" and not element.quelle_dokument_id and db.query(KatplanDokument).filter_by(
        org_id=plan.org_id, plan_id=plan.id, ist_aktuelle_version=True, archiviert=False,
    ).first():
        codes.append("quelle_fehlt")
    if element.quelle_dokument_id:
        quelle = db.query(KatplanDokument).filter_by(
            org_id=plan.org_id, plan_id=plan.id, id=element.quelle_dokument_id,
        ).one_or_none()
        if quelle and any(d.ist_aktuelle_version and not d.archiviert and d.versionsnummer > quelle.versionsnummer
               for d in _gruppe(db, plan, quelle)):
            codes.append("quelle_neuer")
    return [{"code": code, "text": LABELS[code], "farbe": "amber",
             **({"nummern": nummern} if code == "telefon_im_text" else {})} for code in codes]


def plan_zusammenfassung(db: Session, plan: KatplanPlan, heute: date) -> dict[str, int]:
    version_links = freigabe_links(db, plan)
    return dict(Counter(h["code"] for e in db.query(KatplanElement).filter_by(
        org_id=plan.org_id, plan_id=plan.id,
    ) for h in element_hinweise(db, plan, e, heute=heute, version_links=version_links)))
