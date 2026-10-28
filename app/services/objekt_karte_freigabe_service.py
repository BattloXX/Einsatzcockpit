"""Atomare Kartenfreigabe. Caller committet; Fehler rollen die Transaktion zurueck.

Offene Konkurrenten werden ersetzt. Zwei ausgewaehlte Aenderungen am selben
Marker sind dagegen ein Konflikt, damit die Auswahl eindeutig bleibt.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.permissions import has_role
from app.models.objekt import Objekt, ObjektDokument, ObjektDokumentGeoref, ObjektKartenObjekt, ObjektKartenVorschlag
from app.models.user import User
from app.services import objekt_karte_vorschlag_service as vservice
from app.services.objekt_karte_service import (
    EINSATZKRITISCHE_SYMBOLTYPEN,
    ObjektKartenFehler,
    _karten_objekt_dict,
    karten_objekte_lesen,
)
from app.services.objekt_service import write_objekt_change


class FreigabeFehler(ObjektKartenFehler):
    def __init__(self, detail: str, ergebnisse: list[dict], status_code: int = 422):
        super().__init__(detail, status_code)
        self.ergebnisse = ergebnisse


class FreigabeKonflikt(FreigabeFehler):
    def __init__(self, detail: str, ergebnisse: list[dict]):
        super().__init__(detail, ergebnisse, 409)


def _berechtigt(objekt: Objekt, user: User) -> None:
    if user.org_id != objekt.org_id:
        raise ObjektKartenFehler("Objekt nicht gefunden", 404)
    if not has_role(user, "objekt_verwalter"):
        raise ObjektKartenFehler("Objektverwaltung erforderlich", 403)
    if objekt.entwurf_von_id is not None:
        raise ObjektKartenFehler("Freigabe nur am Basisobjekt moeglich", 409)


def _ids(ids: list[int]) -> None:
    if not isinstance(ids, list) or not 1 <= len(ids) <= 30 or any(type(i) is not int or i < 1 for i in ids):
        raise ObjektKartenFehler("1 bis 30 Vorschlags-IDs erforderlich", 422)
    if len(set(ids)) != len(ids):
        raise ObjektKartenFehler("Doppelte Vorschlags-IDs", 422)


def _laden(db: Session, objekt: Objekt, ids: list[int]) -> list[ObjektKartenVorschlag]:
    _ids(ids)
    alle = {v.id: v for v in db.query(ObjektKartenVorschlag).filter_by(
        objekt_id=objekt.id, org_id=objekt.org_id).filter(
            ObjektKartenVorschlag.id.in_(ids)).populate_existing().with_for_update().all()}
    if any(i not in alle for i in ids):
        raise ObjektKartenFehler("Vorschlag nicht gefunden", 404)
    return [alle[i] for i in ids]


def markiere_veraltete(db: Session, objekt: Objekt) -> None:
    """Explizite Mutation; Legacy-Marker ohne gespeicherten Hash bleiben unveraendert."""
    from app.services.objekt_georef_service import georef_pruefen, neu_berechnen

    for g in db.query(ObjektDokumentGeoref).filter_by(org_id=objekt.org_id, objekt_id=objekt.id).all():
        if georef_pruefen(db, objekt, g) == "veraltet":
            g.status = "veraltet"
            neu_berechnen(db, objekt, g)
    eintraege: list[ObjektKartenVorschlag | ObjektKartenObjekt] = list(
        vservice.vorschlaege_auflisten(db, objekt, "offen"))
    eintraege.extend(karten_objekte_lesen(db, objekt))
    for eintrag in eintraege:
        if (not isinstance(eintrag, ObjektKartenVorschlag) or eintrag.georef_id is None) and (
            eintrag.quelle_dokument_hash and vservice._veraltet(db, objekt, eintrag)
        ):
            eintrag.position_status = "veraltet"
    db.flush()


def freigeben(db: Session, objekt: Objekt, vorschlag_ids: list[int], user: User,
              bestaetigte_warnungen: dict, quelle: str = "ui") -> dict:
    """Validiert die ganze Auswahl vor Uebernahme; keine internen Commits."""
    try:
        _berechtigt(objekt, user)
        if quelle not in {"mcp", "ui"}:
            raise ObjektKartenFehler("Unbekannte Quelle", 422)
        if not isinstance(bestaetigte_warnungen, dict) or any(
            not isinstance(codes, list) or any(not isinstance(c, str) for c in codes)
            for codes in bestaetigte_warnungen.values()
        ):
            raise ObjektKartenFehler("Warnungsbestaetigungen muessen Listen von Codes sein", 422)
        # Objekt sperren: auch parallele Freigaben von neuen Vorschlaegen serialisieren.
        db.query(Objekt).filter_by(id=objekt.id, org_id=objekt.org_id).with_for_update().one()
        vorschlaege = _laden(db, objekt, vorschlag_ids)
        kopie = db.query(Objekt).filter_by(entwurf_von_id=objekt.id, org_id=objekt.org_id).first()
        marker = {m.id: m for m in db.query(ObjektKartenObjekt).filter_by(
            objekt_id=objekt.id, org_id=objekt.org_id).populate_existing().with_for_update().all()}
        ergebnisse = []
        for v in vorschlaege:
            fehler = None
            konflikt = False
            daten = vservice.vorschlag_dict(v, db, objekt)
            if kopie:
                fehler, konflikt = "Offene Arbeitskopie: Marker wuerden beim Merge ueberschrieben", True
            elif v.freigabe_status != "offen":
                fehler, konflikt = "Vorschlag ist nicht mehr offen", True
            elif v.operation != "hinzufuegen" and (
                v.basis_marker_id not in marker or marker[v.basis_marker_id].revision != v.expected_revision
            ):
                fehler, konflikt = "Revisionskonflikt: Basismarker fehlt oder wurde geaendert", True
            elif v.basis_marker_id and sum(a.basis_marker_id == v.basis_marker_id for a in vorschlaege) > 1:
                fehler, konflikt = "Mehrere ausgewaehlte Vorschlaege zum selben Marker", True
            elif (daten["position_status"] != "vorgeschlagen" or
                  (daten["lat"] is None and not daten["geometry_json"])):
                fehler = "Vorschlag hat keine freigebbare Position"
            elif v.quelle_dokument_hash and vservice._veraltet(db, objekt, v):
                fehler = "Quelldokument ist veraltet; Position zuerst korrigieren"
            else:
                codes = {w["code"] for w in daten["warnungen"]}
                betroffen = {v.symbol_code}
                if v.basis_marker_id in marker:
                    betroffen.add(marker[v.basis_marker_id].typ)
                if any(c in EINSATZKRITISCHE_SYMBOLTYPEN or c.startswith(("gefahr_", "hydrant_")) for c in betroffen):
                    codes.add("einsatzkritisch_freigabe_erforderlich")
                bestaetigt = bestaetigte_warnungen.get(v.id, bestaetigte_warnungen.get(str(v.id), []))
                fehlend = codes - set(bestaetigt)
                if fehlend:
                    fehler = "Warnungen explizit bestaetigen: " + ", ".join(sorted(fehlend))
            ergebnisse.append({"id": v.id, "status": "fehler" if fehler else "nicht_uebernommen",
                               "ursache": fehler, "konflikt": konflikt})
        if any(e["ursache"] for e in ergebnisse):
            for e in ergebnisse:
                if not e["ursache"]:
                    e["ursache"] = "Gesamte Auswahl wegen anderer Fehler nicht uebernommen"
            typ = FreigabeKonflikt if any(e["konflikt"] for e in ergebnisse) else FreigabeFehler
            raise typ("Freigabe vollstaendig abgewiesen", ergebnisse)
        jetzt = datetime.now(UTC).replace(tzinfo=None)
        antwort = []
        sort = max((a.sort for a in marker.values()), default=0)
        for v in vorschlaege:
            m = marker.get(v.basis_marker_id) if v.basis_marker_id else None
            vorher = _snapshot(m) if m else None
            if v.operation == "entfernen":
                assert m is not None
                nachher = None
                feld = "symbol_geloescht"
            else:
                if v.operation == "hinzufuegen":
                    sort += 1
                    m = ObjektKartenObjekt(org_id=objekt.org_id, objekt_id=objekt.id, revision=1, sort=sort)
                    db.add(m)
                    feld = "symbol_neu"
                else:
                    assert m is not None
                    m.revision += 1
                    feld = "symbol_geaendert"
                m.typ = v.symbol_code
                for name in ("lat", "lng", "geometry_json", "rotation", "label", "quelle_dokument_id",
                             "quelle_dokument_hash", "quelle_seite", "genauigkeit_m"):
                    setattr(m, name, getattr(v, name))
                m.position_status, m.geprueft_von, m.geprueft_am = "bestaetigt", user.id, jetzt
                db.flush()
                nachher = _snapshot(m)
            assert m is not None
            v.freigabe_status, v.geprueft_von, v.geprueft_am = "angenommen", user.id, jetzt
            for a in vservice.vorschlaege_auflisten(db, objekt, "offen"):
                if v.basis_marker_id and a.id != v.id and a.basis_marker_id == v.basis_marker_id:
                    a.freigabe_status = "ersetzt"
            write_objekt_change(db, objekt.id, objekt.org_id, "karte", feld, vorher, nachher,
                                user_id=user.id, quelle=quelle)
            antwort.append({"id": v.id, "status": "angenommen", "marker_id": m.id})
            if v.operation == "entfernen":
                db.delete(m)
        db.flush()
        # PDF rendert die Beziehung; bereits geladene Collections muessen aktuell sein.
        db.expire(objekt, ["karten_objekte"])
        return {"ergebnisse": antwort}
    except ObjektKartenFehler as exc:
        db.rollback()
        if isinstance(exc, FreigabeFehler) or not isinstance(vorschlag_ids, list):
            raise
        ergebnisse = [{"id": i, "status": "fehler", "ursache": str(exc),
                       "konflikt": exc.status_code == 409} for i in vorschlag_ids]
        if exc.status_code == 409:
            raise FreigabeKonflikt(str(exc), ergebnisse) from exc
        raise FreigabeFehler(str(exc), ergebnisse, exc.status_code) from exc
    except Exception:
        db.rollback()
        raise


def _snapshot(m: ObjektKartenObjekt) -> dict:
    return _karten_objekt_dict(m) | {"revision": m.revision, "position_status": m.position_status,
                                    "quelle_dokument_id": m.quelle_dokument_id,
                                    "quelle_dokument_hash": m.quelle_dokument_hash,
                                    "quelle_seite": m.quelle_seite, "genauigkeit_m": m.genauigkeit_m,
                                    "geprueft_von": m.geprueft_von,
                                    "geprueft_am": m.geprueft_am.isoformat() if m.geprueft_am else None}


def ablehnen(db: Session, objekt: Objekt, vorschlag_ids: list[int], grund: str, user: User,
             quelle: str = "ui") -> dict:
    _berechtigt(objekt, user)
    if not isinstance(grund, str) or not grund.strip() or len(grund) > 10000:
        raise ObjektKartenFehler("Ablehngrund erforderlich (maximal 10000 Zeichen)", 422)
    alle = _laden(db, objekt, vorschlag_ids)
    if any(v.freigabe_status != "offen" for v in alle):
        raise ObjektKartenFehler("Vorschlag ist nicht mehr offen", 409)
    for v in alle:
        vorher = vservice.vorschlag_dict(v)
        v.freigabe_status = "abgelehnt"
        v.geprueft_von, v.geprueft_am = user.id, datetime.now(UTC).replace(tzinfo=None)
        write_objekt_change(db, objekt.id, objekt.org_id, "karte", "vorschlag_abgelehnt", vorher,
                            {"vorschlag": vservice.vorschlag_dict(v), "grund": grund.strip()},
                            user_id=user.id, quelle=quelle)
    db.flush()
    return {"vorschlaege": [vservice.vorschlag_dict(v) for v in alle]}


def vorschlag_korrigieren(db: Session, objekt: Objekt, vorschlag_id: int, lat: float, lng: float,
                         rotation: int, label: str | None, user: User, quelle: str = "ui") -> dict:
    _berechtigt(objekt, user)
    v = _laden(db, objekt, [vorschlag_id])[0]
    if v.freigabe_status != "offen":
        raise ObjektKartenFehler("Vorschlag ist nicht mehr offen", 409)
    lat = vservice._zahl(lat, "lat", -90, 90)
    lng = vservice._zahl(lng, "lng", -180, 180)
    dokument_id = v.quelle_dokument_id
    if dokument_id is not None:
        dokument = vservice._dokument(db, objekt, dokument_id)
        gruppe = dokument.dokument_gruppe_id or dokument.id
        aktuell = db.query(ObjektDokument).filter(
            ObjektDokument.objekt_id == objekt.id, ObjektDokument.org_id == objekt.org_id,
            (ObjektDokument.id == gruppe) | (ObjektDokument.dokument_gruppe_id == gruppe),
            ObjektDokument.ist_aktuelle_version.is_(True),
        ).order_by(ObjektDokument.versionsnummer.desc()).first()
        # Manuelle Pruefung bezieht sich auf den aktuellen Quellenstand. Der alte
        # Stand bleibt im Vorschlagsaudit erhalten; Georeferenz wird entfernt.
        dokument_id = aktuell.id if aktuell else None
    daten = vservice.vorschlag_speichern(db, objekt, {
        "quelle_dokument_id": dokument_id,
        "georef_id": None, "bild_x": None, "bild_y": None, "geometry_json": None,
        "lat": lat, "lng": lng, "rotation": rotation, "label": label,
        "position_methode": "manuell_korrigiert", "position_status": "vorgeschlagen", "genauigkeit_m": None,
        "quelle_dokument_hash": None,
    }, user_id=user.id, vorschlag_id=v.id, audit_quelle=quelle)
    v.warnungen_json = json.dumps(daten["warnungen"] + [{"code": "position_manuell"}])
    db.flush()
    return vservice.vorschlag_dict(v, db, objekt)


def review_liste(db: Session, objekt: Objekt, status_filter: str | None = None) -> list[dict]:
    marker = {m.id: _snapshot(m) for m in karten_objekte_lesen(db, objekt)}
    antwort = []
    for v in vservice.vorschlaege_auflisten(db, objekt, status_filter):
        g = db.query(ObjektDokumentGeoref).filter_by(
            id=v.georef_id, objekt_id=objekt.id, org_id=objekt.org_id).first() if v.georef_id else None
        from app.services.objekt_georef_service import georef_dict
        dokument = (db.query(ObjektDokument).filter_by(
            id=v.quelle_dokument_id, objekt_id=objekt.id, org_id=objekt.org_id).first()
            if v.quelle_dokument_id else None)
        seite = next((s for s in dokument.seiten if s.seiten_nr == v.quelle_seite), None) if dokument else None
        antwort.append(vservice.vorschlag_dict(v, db, objekt) | {
            "quelle_dokument_name": dokument.dateiname_original if dokument else None,
            "quelle_seite_id": seite.id if seite else None,
            "basis_marker": marker.get(v.basis_marker_id) if v.basis_marker_id else None,
            "georeferenz": georef_dict(db, objekt, g) if g else None})
    return antwort
