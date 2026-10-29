"""Georeferenz einer Vorversion ueber identische Planmerkmale uebernehmen."""

from __future__ import annotations

import json
import math

from sqlalchemy.orm import Session

from app.models.objekt import Objekt, ObjektDokumentGeoref
from app.services import objekt_georef_service as geo
from app.services.objekt_karte_service import ObjektKartenFehler
from app.services.objekt_karte_vorschlag_service import _dokument

# Im Pilot kalibrieren; Fehler in Einheiten der neuen Seitenhoehe.
UEBERNAHME_MAX_RESIDUUM = 0.01
UEBERNAHME_MAX_LOO = 0.02


def _ausgleich(paare: list[tuple[float, float, float, float]]) -> list[float]:
    design: list[list[float]] = []
    ziel: list[float] = []
    for u, v, x, y in paare:
        design.extend(([u, -v, 1, 0], [v, u, 0, 1]))
        ziel.extend((x, y))
    return geo._least_squares(design, ziel)


def _abbilden(matrix: list[float], u: float, v: float) -> tuple[float, float]:
    a, b, c, d = matrix
    return a * u - b * v + c, b * u + a * v + d


def paare_pruefen(punktpaare: list[dict], von_groesse: tuple[int, int],
                  nach_groesse: tuple[int, int]) -> dict:
    """Reine Berechnung in isotropen Koordinaten mit eigener Seitengeometrie."""
    if not isinstance(punktpaare, list) or not 4 <= len(punktpaare) <= 30:
        raise ObjektKartenFehler("4 bis 30 Punktpaare als Liste erforderlich")
    von_verhaeltnis = von_groesse[0] / von_groesse[1]
    nach_verhaeltnis = nach_groesse[0] / nach_groesse[1]
    paare = []
    for paar in punktpaare:
        if not isinstance(paar, dict):
            raise ObjektKartenFehler("Punktpaar mit von_x, von_y, nach_x, nach_y erforderlich")
        vx, vy, nx, ny = [geo._zahl(paar.get(k), k, 0, 1)
                          for k in ("von_x", "von_y", "nach_x", "nach_y")]
        paare.append(((vx - 0.5) * von_verhaeltnis, 0.5 - vy,
                      (nx - 0.5) * nach_verhaeltnis, 0.5 - ny))
    residuen: list[dict] = [dict(paar=i + 1, fehler=None, loo=None) for i in range(len(paare))]
    matrix = None
    try:
        matrix = _ausgleich(paare)
        for i, (u, v, x, y) in enumerate(paare):
            rx, ry = _abbilden(matrix, u, v)
            residuen[i]["fehler"] = math.hypot(rx - x, ry - y)
            loo = _ausgleich(paare[:i] + paare[i + 1:])
            lx, ly = _abbilden(loo, u, v)
            residuen[i]["loo"] = math.hypot(lx - x, ly - y)
        # Ein orientierungserhaltender Fit kann kleine Spiegelungen verdecken.
        design = [[u, v, 1] for u, v, _, _ in paare]
        x_fit = geo._least_squares(design, [p[2] for p in paare])
        y_fit = geo._least_squares(design, [p[3] for p in paare])
        if x_fit[0] * y_fit[1] - x_fit[1] * y_fit[0] <= 0:
            raise ObjektKartenFehler("Spiegelung oder entartete Planmerkmale")
        if any(r["fehler"] > UEBERNAHME_MAX_RESIDUUM or r["loo"] > UEBERNAHME_MAX_LOO for r in residuen):
            raise ObjektKartenFehler("Paarfehler oder Leave-one-out ausserhalb der Grenzwerte")
    except ObjektKartenFehler as exc:
        return dict(klasse="C", begruendung=str(exc), paar_residuen=residuen, matrix=None)
    return dict(klasse="A", begruendung="Paarpruefung innerhalb der Grenzwerte", paar_residuen=residuen, matrix=matrix)


def uebernehmen(db: Session, objekt: Objekt, von_dokument_id: int, von_seite: int,
                nach_dokument_id: int, nach_seite: int, punktpaare: list[dict],
                expected_rotation: int, user_id: int, speichern: bool = False) -> dict:
    """Prueft Quellen und Referenzen; der Aufrufer schliesst die Transaktion ab."""
    von = _dokument(db, objekt, von_dokument_id)
    nach = _dokument(db, objekt, nach_dokument_id)
    if (von.dokument_gruppe_id or von.id) != (nach.dokument_gruppe_id or nach.id):
        raise ObjektKartenFehler("Dokumente muessen zur selben Dokumentgruppe gehoeren")
    if not nach.ist_aktuelle_version:
        raise ObjektKartenFehler("Zieldokument muss die aktuelle Version sein")
    if (von_dokument_id, von_seite) == (nach_dokument_id, nach_seite):
        raise ObjektKartenFehler("Quelle und Ziel muessen verschiedene Dokumentseiten sein")
    alte_seite = geo._seite(db, objekt, von.id, von_seite)
    neue_seite = geo._seite(db, objekt, nach.id, nach_seite)
    if type(expected_rotation) is not int or expected_rotation != neue_seite.rotation:
        raise ObjektKartenFehler("Dokumentseite wurde inzwischen gedreht; Seite neu ansehen")
    alte_groesse, neue_groesse = geo._bildgroesse(alte_seite), geo._bildgroesse(neue_seite)
    # markiere_veraltete setzt Vorversionen dauerhaft auf "veraltet"; deshalb auch diese
    # zulassen, die Referenzpunkte aber unten erneut gegen die Grenzwerte rechnen.
    quelle = (db.query(ObjektDokumentGeoref)
              .filter_by(org_id=objekt.org_id, objekt_id=objekt.id, dokument_id=von.id, seite=von_seite)
              .order_by(ObjektDokumentGeoref.revision.desc()).first())
    if quelle is None or quelle.status not in {"gueltig", "veraltet"} or not quelle.parameter_json:
        raise ObjektKartenFehler("Keine gespeicherte gueltige Quellgeoreferenz vorhanden")
    parameter = json.loads(quelle.parameter_json or "{}")
    if (parameter.get("rotation") != alte_seite.rotation
            or parameter.get("bildgroesse") != list(alte_groesse)):
        raise ObjektKartenFehler("Rotation oder Bildgroesse der Quellseite stimmt nicht mit der Georeferenz ueberein")
    nachrechnung = geo.transformation_berechnen(
        json.loads(quelle.referenzpunkte_json or "[]"), alte_groesse[0], alte_groesse[1], quelle.modell
    )
    if nachrechnung["status"] != "gueltig":
        raise ObjektKartenFehler("Quellgeoreferenz erfuellt die Grenzwerte nicht: " + str(nachrechnung["ablehngrund"]))
    ergebnis = paare_pruefen(punktpaare, alte_groesse, neue_groesse)
    matrix = ergebnis.pop("matrix")
    ergebnis.update(uebernommene_punkte=0, verworfene_punkte=0, georeferenz=None, gespeichert=False)
    if ergebnis["klasse"] == "C":
        return ergebnis
    neue_punkte: list[dict] = []
    for punkt in json.loads(quelle.referenzpunkte_json or "[]"):
        u, v = _abbilden(matrix, (punkt["x"] - 0.5) * alte_groesse[0] / alte_groesse[1], 0.5 - punkt["y"])
        x, y = 0.5 + u * neue_groesse[1] / neue_groesse[0], 0.5 - v
        if not (0 <= x <= 1 and 0 <= y <= 1):
            ergebnis["verworfene_punkte"] += 1
            continue
        neue_punkte.append(dict(
            id=len(neue_punkte) + 1, x=x, y=y, lat=punkt["lat"], lng=punkt["lng"], quelle=punkt["quelle"],
            beschreibung=punkt["beschreibung"] + (
                f" (uebernommen aus Dokument {von.id} Seite {von_seite} Rev {quelle.revision})"
            ),
        ))
    ergebnis["uebernommene_punkte"] = len(neue_punkte)
    # Vorpruefung verhindert auch bei speichern=true eine abgelehnte neue Revision.
    pruefung = geo.referenzpunkte(db, objekt, nach.id, nach_seite, "setzen", neue_punkte, quelle.modell,
                                 user_id, pruefen=True, expected_rotation=expected_rotation)
    ergebnis["georeferenz"] = pruefung["georeferenz"]
    if pruefung["georeferenz"]["status"] != "gueltig":
        ergebnis.update(klasse="C", begruendung=pruefung["georeferenz"]["ablehngrund"])
        return ergebnis
    if speichern:
        antwort = geo.referenzpunkte(db, objekt, nach.id, nach_seite, "setzen", neue_punkte, quelle.modell,
                                    user_id, pruefen=False, nur_gueltig=True, expected_rotation=expected_rotation)
        ergebnis.update(georeferenz=antwort["georeferenz"], gespeichert=True)
    ergebnis["begruendung"] = "Paarpruefung bestanden und uebernommene Georeferenz gueltig"
    return ergebnis
