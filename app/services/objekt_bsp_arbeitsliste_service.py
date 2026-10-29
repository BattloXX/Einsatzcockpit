"""Lesende, organisationsgebundene Arbeitsliste fuer aktuelle Planseiten."""

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.objekt import (
    DOKUMENT_STATUS_FERTIG,
    Objekt,
    ObjektDokument,
    ObjektDokumentGeoref,
    ObjektDokumentSeite,
    ObjektKartenObjekt,
    ObjektKartenVorschlag,
)
from app.services import objekt_georef_service, objekt_karte_vorschlag_service

ABLAUF = [
    "objekt_dokument_seite_ansehen: rotation merken, ausschnitt fuer Details.",
    "georef_uebernehmen oder georef_zuordnen erst ohne speichern; "
    "Klasse A speichern, Klasse C: editor_link an Menschen.",
    "objekt_karte_symbolkatalog (aliase), objekt_karte_vorschlaege mit "
    "georef_id, bild_x/bild_y, bild_richtung, batch_id.",
    "objekt_karte_pruefen und objekt_karte_vorschau.",
    "Freigabe nur nach ausdruecklicher Bestaetigung des Menschen. Koordinaten nie erfinden.",
]
SCHRITTE = {
    "georef_uebernehmen": "objekt_karte_georef_uebernehmen zuerst ohne speichern pruefen.",
    "georef_zuordnen": ("objekt_karte_georef_zuordnen zuerst ohne speichern pruefen; "
                        "alternativ editor_link an Menschen."),
    "vorschlaege_pruefen": "objekt_karte_vorschau aufrufen; Freigabe durch Menschen.",
    "symbole_erfassen": "objekt_karte_symbolkatalog abgleichen und objekt_karte_vorschlaege mit georef_id erfassen.",
    "aktuell": "Marker aus dieser Planversion vorhanden.",
}


def arbeitsliste_lesen(db: Session, org_id: int, objekt_id: int | None = None,
                      nur_offen: bool = True, limit: int = 50) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit muss zwischen 1 und 200 liegen")
    kandidaten = db.query(Objekt, ObjektDokument, ObjektDokumentSeite).join(
        ObjektDokument, ObjektDokument.objekt_id == Objekt.id,
    ).join(ObjektDokumentSeite, ObjektDokumentSeite.dokument_id == ObjektDokument.id).filter(
        Objekt.org_id == org_id, Objekt.entwurf_von_id.is_(None),
        ObjektDokument.org_id == org_id, ObjektDokument.ist_aktuelle_version.is_(True),
        ObjektDokument.status == DOKUMENT_STATUS_FERTIG,
        ObjektDokumentSeite.org_id == org_id, ObjektDokumentSeite.objekt_id == Objekt.id,
        ObjektDokumentSeite.dokumentart.in_({"lageplan", "brandschutzplan"}),
    )
    if objekt_id is not None:
        kandidaten = kandidaten.filter(Objekt.id == objekt_id)
    seiten = kandidaten.all()
    if not seiten:
        return {"eintraege": [], "anzahl_gesamt": 0, "abgeschnitten": False, "ablauf": list(ABLAUF)}
    objekt_ids = {o.id for o, _, _ in seiten}
    dokument_ids = {d.id for _, d, _ in seiten}
    dokumente = db.query(ObjektDokument).filter(
        ObjektDokument.org_id == org_id, ObjektDokument.objekt_id.in_(objekt_ids),
    ).all()
    dokumente.sort(key=lambda d: (d.versionsnummer, d.id), reverse=True)
    georefs = db.query(ObjektDokumentGeoref).filter(
        ObjektDokumentGeoref.org_id == org_id, ObjektDokumentGeoref.objekt_id.in_(objekt_ids),
    ).order_by(ObjektDokumentGeoref.revision.desc()).all()
    aktuell: dict[tuple[int, int], ObjektDokumentGeoref] = {}
    for georef in georefs:
        aktuell.setdefault((georef.dokument_id, georef.seite), georef)
    v = ObjektKartenVorschlag
    offene = {(oid, did, nr): anzahl for oid, did, nr, anzahl in db.query(
        v.objekt_id, v.quelle_dokument_id, v.quelle_seite, func.count(v.id),
    ).filter(v.org_id == org_id, v.objekt_id.in_(objekt_ids), v.quelle_dokument_id.in_(dokument_ids),
             v.freigabe_status == "offen").group_by(v.objekt_id, v.quelle_dokument_id, v.quelle_seite).all()}
    m = ObjektKartenObjekt
    marker = {(oid, hashwert): anzahl for oid, hashwert, anzahl in db.query(
        m.objekt_id, m.quelle_dokument_hash, func.count(m.id),
    ).filter(m.org_id == org_id, m.objekt_id.in_(objekt_ids), m.quelle_dokument_hash.is_not(None)).group_by(
        m.objekt_id, m.quelle_dokument_hash,
    ).all()}
    hashes = {}
    for _, dokument, _ in seiten:
        if dokument.id not in hashes:
            hashes[dokument.id] = objekt_karte_vorschlag_service._hash(dokument)
    eintraege: list[dict[str, Any]] = []
    for objekt, dokument, seite in seiten:
        g = aktuell.get((dokument.id, seite.seiten_nr))
        status = objekt_georef_service.georef_pruefen(
            db, objekt, g, dokument=dokument, seite=seite, dokument_hash=hashes[dokument.id],
        ) if g else None
        vorversion = None
        for alt in dokumente:
            if (alt.objekt_id != objekt.id or (alt.dokument_gruppe_id or alt.id) !=
                    (dokument.dokument_gruppe_id or dokument.id) or alt.versionsnummer >= dokument.versionsnummer):
                continue
            quelle = aktuell.get((alt.id, seite.seiten_nr))
            if quelle and quelle.status in {"gueltig", "veraltet"}:
                vorversion = {"dokument_id": alt.id, "seite": seite.seiten_nr,
                              "georef_id": quelle.id, "status": quelle.status}
                break
        anzahl_offen = offene.get((objekt.id, dokument.id, seite.seiten_nr), 0)
        anzahl_marker = marker.get((objekt.id, g.dokument_hash if g else hashes[dokument.id]), 0)
        if status != "gueltig":
            schritt = "georef_uebernehmen" if vorversion else "georef_zuordnen"
        elif anzahl_offen:
            schritt = "vorschlaege_pruefen"
        elif not anzahl_marker:
            schritt = "symbole_erfassen"
        else:
            schritt = "aktuell"
        if nur_offen and schritt == "aktuell":
            continue
        eintraege.append({
            "objekt_id": objekt.id, "objekt_name": objekt.name, "dokument_id": dokument.id,
            "dokument_versionsnummer": dokument.versionsnummer, "seite": seite.seiten_nr,
            "dokumentart": seite.dokumentart, "titel": seite.titel, "rotation": seite.rotation,
            "georef": {"id": g.id, "revision": g.revision, "status": status, "rmse_m": g.rmse_m} if g else None,
            "offene_vorschlaege": anzahl_offen, "marker_aus_planversion": anzahl_marker,
            "vorversion": vorversion, "naechster_schritt": schritt, "hinweis": SCHRITTE[schritt],
        })
    reihenfolge = {schritt: rang for rang, schritt in enumerate(SCHRITTE)}
    eintraege.sort(key=lambda e: (reihenfolge[e["naechster_schritt"]], e["objekt_id"], e["dokument_id"], e["seite"]))
    return {"eintraege": eintraege[:limit], "anzahl_gesamt": len(eintraege),
            "abgeschnitten": len(eintraege) > limit, "ablauf": list(ABLAUF)}
