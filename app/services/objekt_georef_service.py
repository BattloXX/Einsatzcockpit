"""Plan-Georeferenzierung in lokalen Metern, ohne geschaetzte Geokoordinaten.

Normierte Koordinaten beziehen sich auf das angezeigte, gedrehte Rendering.
Y wird fuer die Mathematik nach oben orientiert; regulaere Abbildungen haben
positive Determinante. QR Least Squares vermeidet schwere Zusatzabhaengigkeiten.
Konsistente Rotation/Skalierung ist ohne unabhaengige Massstabsangabe nicht
als falsch erkennbar; Restfehler pruefen die Konsistenz der Referenzpunkte.
"""

from __future__ import annotations

import json
import math
from typing import Any

from PIL import Image
from sqlalchemy import and_
from sqlalchemy.orm import Query, Session
from sqlalchemy.orm.attributes import get_history

from app.models.objekt import Objekt, ObjektDokumentGeoref, ObjektDokumentSeite
from app.services.objekt_karte_service import ObjektKartenFehler

ERDRADIUS_M = 6371000.0
MAX_RMSE_M = 2.0
MAX_FEHLER_M = 4.0
MIN_HUELLFLAECHE = 0.02
MIN_PLAN_AUSDEHNUNG_M = 1.0
MAX_PLAN_AUSDEHNUNG_M = 10000.0
MAX_VERZERRUNG = 10.0
PIXELUNSICHERHEIT_NORMIERT = 0.005
MAX_POSITIONSUNSICHERHEIT_M = 4.0
QUELLEN = {"osm_gebaeude", "orthofoto_klick", "dokument_koordinate", "manuell"}
MIN_PUNKTE = {"aehnlichkeit": 3, "affin": 4, "homographie": 6}


def _zahl(wert: Any, name: str, unten: float, oben: float) -> float:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not math.isfinite(wert):
        raise ObjektKartenFehler(f"{name}: endliche Zahl erforderlich")
    if not unten <= wert <= oben:
        raise ObjektKartenFehler(f"{name}: ausserhalb {unten}..{oben}")
    return float(wert)


def _flaeche(punkte: list[tuple[float, float]]) -> float:
    def kreuz(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    sortiert = sorted(set(punkte))
    teile = []
    for folge in (sortiert, sortiert[::-1]):
        h: list[tuple[float, float]] = []
        for p in folge:
            while len(h) >= 2 and kreuz(h[-2], h[-1], p) <= 0:
                h.pop()
            h.append(p)
        teile.extend(h[:-1])
    if len(teile) < 3:
        return 0
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(teile, teile[1:] + teile[:1], strict=True))) / 2


def _least_squares(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(a[0])
    q: list[list[float]] = []
    r = [[0.0] * n for _ in range(n)]
    for j in range(n):
        v = [zeile[j] for zeile in a]
        for i in range(j):
            r[i][j] = sum(x * y for x, y in zip(q[i], v, strict=True))
            v = [x - r[i][j] * y for x, y in zip(v, q[i], strict=True)]
        r[j][j] = math.sqrt(sum(x * x for x in v))
        if r[j][j] < 1e-9:
            raise ObjektKartenFehler("Kollineare oder numerisch schlecht verteilte Referenzpunkte")
        q.append([x / r[j][j] for x in v])
    c = [sum(x * y for x, y in zip(spalte, b, strict=True)) for spalte in q]
    loesung = [0.0] * n
    for i in range(n - 1, -1, -1):
        loesung[i] = (c[i] - sum(r[i][j] * loesung[j] for j in range(i + 1, n))) / r[i][i]
    return loesung


def _abbilden(p: dict, x: float, y: float) -> tuple[float, float]:
    u, v = (x - 0.5) * p["seitenverhaeltnis"], 0.5 - y
    a, b, c, d, e, f, g, h = p["matrix"]
    nenner = g * u + h * v + 1
    if nenner <= 1e-6:
        raise ObjektKartenFehler("Homographie mit Polstelle im Plan")
    return (a * u + b * v + c) / nenner, (d * u + e * v + f) / nenner


def _massstab(p: dict, x: float, y: float) -> tuple[float, float, float]:
    # Analytische Jacobi-Matrix bezogen auf isotrope Bildkoordinaten.
    u, v = (x - 0.5) * p["seitenverhaeltnis"], 0.5 - y
    a, b, c, d, e, f, g, h = p["matrix"]
    w = g * u + h * v + 1
    if w <= 1e-6:
        raise ObjektKartenFehler("Homographie mit Polstelle im Plan")
    j1, j2 = (a * w - g * (a * u + b * v + c)) / w**2, (b * w - h * (a * u + b * v + c)) / w**2
    j3, j4 = (d * w - g * (d * u + e * v + f)) / w**2, (e * w - h * (d * u + e * v + f)) / w**2
    det = j1 * j4 - j2 * j3
    spur = j1 * j1 + j2 * j2 + j3 * j3 + j4 * j4
    gross = math.sqrt(max(0, (spur + math.sqrt(max(0, spur**2 - 4 * det**2))) / 2))
    klein = abs(det) / gross if gross else 0
    return det, gross, klein


def transformation_berechnen(punkte: list[dict], breite: int, hoehe: int, modell: str = "aehnlichkeit") -> dict:
    """Ergebnis inklusive Ablehngrund; auch abgelehnte Punkte bleiben speicherbar."""
    if not isinstance(modell, str) or modell not in MIN_PUNKTE:
        raise ObjektKartenFehler("Unbekanntes Transformationsmodell")
    if not isinstance(punkte, list) or len(punkte) > 100:
        raise ObjektKartenFehler("Maximal 100 Referenzpunkte als Liste")
    ids = set()
    for punkt in punkte:
        if not isinstance(punkt, dict) or not isinstance(punkt.get("id"), (str, int)):
            raise ObjektKartenFehler("Referenzpunkt mit eindeutiger id erforderlich")
        if str(punkt["id"]) in ids:
            raise ObjektKartenFehler("Doppelte Referenzpunkt-ID")
        ids.add(str(punkt["id"]))
        for name, unten, oben in (("x", 0, 1), ("y", 0, 1), ("lat", -85, 85), ("lng", -180, 180)):
            _zahl(punkt.get(name), name, unten, oben)
        if (
            not isinstance(punkt.get("quelle"), str)
            or punkt["quelle"] not in QUELLEN
            or not isinstance(punkt.get("beschreibung"), str)
        ):
            raise ObjektKartenFehler("Referenzquelle und Beschreibung erforderlich")
    ergebnis: dict[str, Any] = dict(
        status="abgelehnt", ablehngrund=None, parameter_json=None, rmse_m=None, max_fehler_m=None, residuen_json="[]"
    )
    try:
        if len(punkte) < MIN_PUNKTE[modell]:
            raise ObjektKartenFehler(f"Mindestens {MIN_PUNKTE[modell]} gut verteilte Referenzpunkte erforderlich")
        if breite <= 0 or hoehe <= 0:
            raise ObjektKartenFehler("Gueltige Renderinggroesse erforderlich")
        if _flaeche([(p["x"], p["y"]) for p in punkte]) < MIN_HUELLFLAECHE:
            raise ObjektKartenFehler("Kollineare oder schlecht verteilte Punkte: Bildflaeche zu klein")
        lat0 = sum(p["lat"] for p in punkte) / len(punkte)
        lng0 = sum(p["lng"] for p in punkte) / len(punkte)
        faktor = ERDRADIUS_M * math.pi / 180
        cos = math.cos(math.radians(lat0))
        p = {"lat0": lat0, "lng0": lng0, "seitenverhaeltnis": breite / hoehe}
        a: list[list[float]] = []
        b: list[float] = []
        ziel = []
        for punkt in punkte:
            u, v = (punkt["x"] - 0.5) * breite / hoehe, 0.5 - punkt["y"]
            ost, nord = (punkt["lng"] - lng0) * faktor * cos, (punkt["lat"] - lat0) * faktor
            ziel.append((ost, nord))
            if modell == "aehnlichkeit":
                a.extend(([u, -v, 1, 0], [v, u, 0, 1]))
            elif modell == "affin":
                a.extend(([u, v, 1, 0, 0, 0], [0, 0, 0, u, v, 1]))
            else:
                a.extend(([u, v, 1, 0, 0, 0, -ost * u, -ost * v], [0, 0, 0, u, v, 1, -nord * u, -nord * v]))
            b.extend((ost, nord))
        m = _least_squares(a, b)
        p["matrix"] = (
            [m[0], -m[1], m[2], m[1], m[0], m[3], 0, 0]
            if modell == "aehnlichkeit"
            else m + ([0, 0] if modell == "affin" else [])
        )
        residuen = []
        for punkt, (ost, nord) in zip(punkte, ziel, strict=True):
            o, n = _abbilden(p, punkt["x"], punkt["y"])
            residuen.append(
                {"id": punkt["id"], "ost_m": o - ost, "nord_m": n - nord, "fehler_m": math.hypot(o - ost, n - nord)}
            )
        rmse = math.sqrt(sum(r["fehler_m"] ** 2 for r in residuen) / len(residuen))
        maximum = max(r["fehler_m"] for r in residuen)
        ergebnis.update(
            parameter_json=json.dumps(p), rmse_m=rmse, max_fehler_m=maximum, residuen_json=json.dumps(residuen)
        )
        if modell == "aehnlichkeit":
            # Ein orientierungserhaltender Fit allein kann kleine Spiegelungen
            # mit tolerierbaren Restfehlern verdecken: Orientierung separat testen.
            design = [[(pt["x"] - 0.5) * breite / hoehe, 0.5 - pt["y"], 1] for pt in punkte]
            ost_fit = _least_squares(design, [z[0] for z in ziel])
            nord_fit = _least_squares(design, [z[1] for z in ziel])
            if ost_fit[0] * nord_fit[1] - ost_fit[1] * nord_fit[0] <= 0:
                raise ObjektKartenFehler("Spiegelung der Referenzpunkte")
        for x in (0, 0.5, 1):
            for y in (0, 0.5, 1):
                det, gross, klein = _massstab(p, x, y)
                if det <= 0:
                    raise ObjektKartenFehler("Spiegelung der Referenzpunkte")
                if klein <= 0 or gross / klein > MAX_VERZERRUNG:
                    raise ObjektKartenFehler("Unplausible Verzerrung")
                diagonal = math.hypot(breite / hoehe, 1)
                if klein * diagonal < MIN_PLAN_AUSDEHNUNG_M or gross * diagonal > MAX_PLAN_AUSDEHNUNG_M:
                    raise ObjektKartenFehler("Unplausible Skalierung des Plans")
        if rmse > MAX_RMSE_M or maximum > MAX_FEHLER_M:
            raise ObjektKartenFehler(f"Restfehler zu gross: RMSE {rmse:.2f} m, Maximum {maximum:.2f} m")
        ergebnis["status"] = "gueltig"
    except ObjektKartenFehler as exc:
        ergebnis["ablehngrund"] = str(exc)
    return ergebnis


def bild_zu_geo(georef: ObjektDokumentGeoref, x: float, y: float) -> tuple[float, float, float]:
    """Nur zuvor gegen Dokument/Rendering gepruefte, gueltige Referenz verwenden."""
    x, y = _zahl(x, "bild_x", 0, 1), _zahl(y, "bild_y", 0, 1)
    if georef.status != "gueltig" or not georef.parameter_json:
        raise ObjektKartenFehler("Georeferenz ist nicht gueltig")
    p = json.loads(georef.parameter_json)
    ost, nord = _abbilden(p, x, y)
    _, gross, _ = _massstab(p, x, y)
    faktor = ERDRADIUS_M * math.pi / 180
    genauigkeit = (georef.rmse_m or 0) + PIXELUNSICHERHEIT_NORMIERT * gross * max(1, p["seitenverhaeltnis"])
    return p["lat0"] + nord / faktor, p["lng0"] + ost / (faktor * math.cos(math.radians(p["lat0"]))), genauigkeit


def _seite(db: Session, objekt: Objekt, dokument_id: int, seite: int) -> ObjektDokumentSeite:
    s = (
        db.query(ObjektDokumentSeite)
        .filter_by(org_id=objekt.org_id, objekt_id=objekt.id, dokument_id=dokument_id, seiten_nr=seite)
        .first()
    )
    if s is None:
        raise ObjektKartenFehler("Dokumentseite nicht gefunden")
    return s


def _bildgroesse(seite: ObjektDokumentSeite) -> tuple[int, int]:
    from pathlib import Path

    from app.config import settings

    root = Path(settings.OBJEKT_MEDIA_DIR).resolve()
    pfad = (root / (seite.bild_pfad or "")).resolve()
    if not seite.bild_pfad or not pfad.is_relative_to(root):
        raise ObjektKartenFehler("Plan-Rendering fehlt")
    try:
        with Image.open(pfad) as bild:
            breite, hoehe = bild.size
    except OSError, ValueError:
        raise ObjektKartenFehler("Plan-Rendering nicht lesbar") from None
    return (hoehe, breite) if seite.rotation % 180 else (breite, hoehe)


def georef_pruefen(db: Session, objekt: Objekt, g: ObjektDokumentGeoref) -> str:
    """Veraltet nur melden, ohne Schreibwirkung fuer lesende MCP-Tools."""
    from app.services.objekt_karte_vorschlag_service import _dokument, _hash

    dokument = _dokument(db, objekt, g.dokument_id)
    if not dokument.ist_aktuelle_version or _hash(dokument) != g.dokument_hash:
        return "veraltet"
    s = _seite(db, objekt, g.dokument_id, g.seite)
    try:
        breite, hoehe = _bildgroesse(s)
    except ObjektKartenFehler:
        return "veraltet"
    p = json.loads(g.parameter_json or "{}")
    if p and (p.get("rotation") != s.rotation or p.get("bildgroesse") != [breite, hoehe]):
        return "veraltet"
    return g.status


def georef_dict(db: Session, objekt: Objekt, g: ObjektDokumentGeoref) -> dict:
    daten = {c.name: getattr(g, c.name) for c in g.__table__.columns}
    for name in ("referenzpunkte_json", "parameter_json", "residuen_json"):
        daten[name.removesuffix("_json")] = json.loads(daten.pop(name) or "null")
    for name in ("erstellt_am", "geprueft_am"):
        daten[name] = daten[name].isoformat() if daten[name] else None
    daten["status"] = georef_pruefen(db, objekt, g)
    return daten


def seiten_sperrabfrage(db: Session, objekt: Objekt, dokument_id: int, seite: int) -> Query:
    """Objekt-Mutex mit aktuellem Seitenstand im selben Locking Read.

    Ein normaler SELECT nach dem Mutex liest unter REPEATABLE READ weiterhin
    den alten Snapshot. Skalare Spalten umgehen zusaetzlich den ORM-Cache.
    Keine Subqueries: deren Lesesicht waere wiederum der alte Snapshot.
    """
    return db.query(
        Objekt.id, ObjektDokumentSeite.rotation,
        ObjektDokumentGeoref.id.label("georef_id"), ObjektDokumentGeoref.revision,
        ObjektDokumentGeoref.status,
    ).outerjoin(ObjektDokumentSeite, and_(
        ObjektDokumentSeite.objekt_id == Objekt.id,
        ObjektDokumentSeite.org_id == Objekt.org_id,
        ObjektDokumentSeite.dokument_id == dokument_id,
        ObjektDokumentSeite.seiten_nr == seite,
    )).outerjoin(ObjektDokumentGeoref, and_(
        ObjektDokumentGeoref.objekt_id == Objekt.id,
        ObjektDokumentGeoref.org_id == Objekt.org_id,
        ObjektDokumentGeoref.dokument_id == dokument_id,
        ObjektDokumentGeoref.seite == seite,
    )).filter(Objekt.id == objekt.id, Objekt.org_id == objekt.org_id)


def seitenstand_pruefen(stand: list, seite: ObjektDokumentSeite, g: ObjektDokumentGeoref | None) -> None:
    """Bei altem Snapshot abbrechen; weder impliziter Commit noch Retry."""
    aktuell = max(stand, key=lambda zeile: zeile.revision or 0)
    # Eigene, noch nicht geflushte Drehung gegen ihren geladenen Basiswert pruefen.
    rotation = get_history(seite, "rotation")
    basis_rotation = rotation.deleted[0] if rotation.deleted else seite.rotation
    if aktuell.rotation != basis_rotation:
        raise ObjektKartenFehler("Dokumentseite wurde inzwischen gedreht; Editor neu oeffnen", 409)
    if (aktuell.georef_id, aktuell.status) != (g.id if g else None, g.status if g else None):
        raise ObjektKartenFehler("Georeferenz wurde inzwischen geaendert; Ansicht neu laden", 409)


def referenzpunkte(
    db: Session,
    objekt: Objekt,
    dokument_id: int,
    seite: int,
    aktion: str,
    punkte: list[dict] | None,
    modell: str | None,
    user_id: int,
    *, pruefen: bool = False, nur_gueltig: bool = False, expected_revision: int | None = None,
    expected_rotation: int | None = None,
) -> dict:
    from app.services.objekt_karte_vorschlag_service import _dokument, _hash

    if type(seite) is not int or seite < 1:
        raise ObjektKartenFehler("Positive Seitennummer erforderlich")
    stand = None
    if aktion != "lesen" and not pruefen:
        stand = seiten_sperrabfrage(db, objekt, dokument_id, seite).with_for_update().all()
    dokument = _dokument(db, objekt, dokument_id)
    s = _seite(db, objekt, dokument_id, seite)
    g = (
        db.query(ObjektDokumentGeoref)
        .filter_by(org_id=objekt.org_id, objekt_id=objekt.id, dokument_id=dokument_id, seite=seite)
        .order_by(ObjektDokumentGeoref.revision.desc())
        .first()
    )
    if stand is not None:
        seitenstand_pruefen(stand, s, g)
    breite, hoehe = _bildgroesse(s)
    meta = {"bildgroesse": [breite, hoehe], "rotation": s.rotation,
            "seitenbild_url": f"/objekt-medien/seite/{s.id}/bild?rotation={s.rotation}",
            "schwellen": {"rmse_m": MAX_RMSE_M, "max_fehler_m": MAX_FEHLER_M}}
    if aktion == "lesen":
        return {"georeferenz": georef_dict(db, objekt, g) if g else None, **meta}
    if expected_revision is not None and expected_revision != (g.revision if g else 0):
        raise ObjektKartenFehler("Georeferenz wurde inzwischen geaendert", 409)
    if expected_rotation is not None and expected_rotation != s.rotation:
        raise ObjektKartenFehler("Dokumentseite wurde inzwischen gedreht; Editor neu oeffnen", 409)
    vorher = g
    if aktion == "verwerfen":
        if g is None:
            raise ObjektKartenFehler("Georeferenz nicht gefunden")
        if pruefen:
            daten = georef_dict(db, objekt, g)
            daten.update(status="abgelehnt", ablehngrund="Manuell verworfen")
            return {"georeferenz": daten, **meta}
        g.status, g.ablehngrund = "abgelehnt", "Manuell verworfen"
    elif aktion == "setzen":
        hashwert = _hash(dokument)
        if not hashwert or not dokument.ist_aktuelle_version:
            raise ObjektKartenFehler("Aktuelles Quelldokument nicht lesbar")
        modell = modell or "aehnlichkeit"
        ergebnis = transformation_berechnen(punkte or [], breite, hoehe, modell)
        if ergebnis["parameter_json"]:
            p = json.loads(ergebnis["parameter_json"])
            p.update(rotation=s.rotation, bildgroesse=[breite, hoehe])
            ergebnis["parameter_json"] = json.dumps(p)
        if pruefen:
            return {"georeferenz": {"status": ergebnis["status"], "ablehngrund": ergebnis["ablehngrund"],
                    "rmse_m": ergebnis["rmse_m"], "max_fehler_m": ergebnis["max_fehler_m"],
                    "residuen": json.loads(ergebnis["residuen_json"])}, **meta}
        if nur_gueltig and ergebnis["status"] != "gueltig":
            raise ObjektKartenFehler(ergebnis["ablehngrund"], 422)
        revision = g.revision + 1 if g else 1
        if g and g.status == "gueltig":
            g.status = "veraltet"
        g = ObjektDokumentGeoref(
            org_id=objekt.org_id,
            objekt_id=objekt.id,
            dokument_id=dokument_id,
            seite=seite,
            dokument_hash=hashwert,
            referenzpunkte_json=json.dumps(punkte or []),
            modell=modell,
            erstellt_von=user_id,
            revision=revision,
            **ergebnis,
        )
        db.add(g)
    else:
        raise ObjektKartenFehler("Unbekannte Aktion")
    db.flush()
    if vorher and vorher.id != g.id:
        # Nach Drehung sind auch die Bildkoordinaten der Vorschlaege unbrauchbar.
        parameter = json.loads(vorher.parameter_json or "{}")
        kompatibel = (parameter.get("rotation") == s.rotation
                      and parameter.get("bildgroesse") == [breite, hoehe]
                      and vorher.dokument_hash == g.dokument_hash)
        neu_berechnen(db, objekt, vorher, ersatz=g if kompatibel and g.status == "gueltig" else None)
    neu_berechnen(db, objekt, g)
    return {"georeferenz": georef_dict(db, objekt, g), **meta}


def neu_berechnen(db: Session, objekt: Objekt, g: ObjektDokumentGeoref,
                  *, ersatz: ObjektDokumentGeoref | None = None) -> None:
    """Offene Planpositionen aktualisieren; ungueltige Positionen als Altwerte behalten."""
    from app.models.objekt import ObjektKartenVorschlag

    ziel = ersatz or g
    gueltig = georef_pruefen(db, objekt, ziel) == "gueltig"
    for v in db.query(ObjektKartenVorschlag).filter_by(
        org_id=objekt.org_id, objekt_id=objekt.id, georef_id=g.id, freigabe_status="offen"
    ).all():
        if v.position_methode == "manuell_korrigiert":
            continue
        warnungen = [w for w in json.loads(v.warnungen_json or "[]") if not w["code"].startswith("georef_")]
        if gueltig and v.bild_x is not None and v.bild_y is not None:
            v.georef_id = ziel.id
            v.lat, v.lng, v.genauigkeit_m = bild_zu_geo(ziel, v.bild_x, v.bild_y)
            v.position_status = "vorgeschlagen"
            if v.genauigkeit_m > MAX_POSITIONSUNSICHERHEIT_M:
                v.position_status = "pruefung_erforderlich"
                warnungen.append({"code": "georef_ungenau"})
        else:
            v.position_status = "pruefung_erforderlich"
            warnungen.append({"code": "georef_ungueltig"})
        v.warnungen_json = json.dumps(warnungen)
    db.flush()


def seite_veralten(db: Session, objekt: Objekt, seite: ObjektDokumentSeite) -> None:
    for g in db.query(ObjektDokumentGeoref).filter_by(
        org_id=objekt.org_id, objekt_id=objekt.id, dokument_id=seite.dokument_id, seite=seite.seiten_nr
    ).all():
        g.status, g.ablehngrund = "veraltet", "Dokumentseite wurde gedreht; Bildkoordinaten neu erfassen"
        neu_berechnen(db, objekt, g)


def web_georef(db: Session, objekt: Objekt, daten: Any, user_id: int, aktion: str = "setzen") -> dict:
    """Strikte Web-Eingabe; MCP darf weiterhin abgelehnte Saetze dokumentieren."""
    if not isinstance(daten, dict):
        raise ObjektKartenFehler("JSON-Objekt erforderlich", 422)
    for name in ("dokument_id", "seite"):
        if type(daten.get(name)) is not int or daten[name] < 1:
            raise ObjektKartenFehler(f"{name}: positive Ganzzahl erforderlich", 422)
    if aktion == "setzen" and not isinstance(daten.get("punkte"), list):
        raise ObjektKartenFehler("punkte: Liste erforderlich", 422)
    if type(daten.get("pruefen", False)) is not bool:
        raise ObjektKartenFehler("pruefen: Wahrheitswert erforderlich", 422)
    if "expected_revision" in daten and (type(daten["expected_revision"]) is not int
                                        or daten["expected_revision"] < 0):
        raise ObjektKartenFehler("expected_revision: Ganzzahl erforderlich", 422)
    if "expected_rotation" in daten and (type(daten["expected_rotation"]) is not int
                                        or daten["expected_rotation"] not in {0, 90, 180, 270}):
        raise ObjektKartenFehler("expected_rotation: ungueltige Drehung", 422)
    return referenzpunkte(db, objekt, daten["dokument_id"], daten["seite"], aktion,
                          daten.get("punkte"), daten.get("modell"), user_id,
                          pruefen=daten.get("pruefen", False), nur_gueltig=True,
                          expected_revision=daten.get("expected_revision"),
                          expected_rotation=daten.get("expected_rotation"))
