"""Deterministische Zuordnung einer Bildkontur zu nummerierten OSM-Ecken."""

from __future__ import annotations

import json
import math

from app.services import objekt_georef_service as geo
from app.services.objekt_karte_service import ObjektKartenFehler

KOLLINEAR_WINKEL_GRAD = 10.0
MAX_HYPOTHESEN = 2000
# Im Pilot kalibrieren.
EINDEUTIG_FAKTOR = 2.0
EINDEUTIG_ABSTAND_M = 1.0


def _ring_vereinfachen(ecken: list[dict]) -> list[dict]:
    """Nahezu gerade Zwischenpunkte entfernen, Originalnummern erhalten."""
    ring = list(ecken)
    if not ring:
        return ring
    faktor = geo.ERDRADIUS_M * math.pi / 180
    cos = math.cos(math.radians(sum(e["lat"] for e in ring) / len(ring)))
    while len(ring) > 3:
        for i, ecke in enumerate(ring):
            davor, danach = ring[i - 1], ring[(i + 1) % len(ring)]
            a = ((ecke["lng"] - davor["lng"]) * faktor * cos, (ecke["lat"] - davor["lat"]) * faktor)
            b = ((danach["lng"] - ecke["lng"]) * faktor * cos, (danach["lat"] - ecke["lat"]) * faktor)
            winkel = math.degrees(math.atan2(abs(a[0] * b[1] - a[1] * b[0]), a[0] * b[0] + a[1] * b[1]))
            if winkel < KOLLINEAR_WINKEL_GRAD:
                ring.pop(i)
                break
        else:
            break
    return ring


def _gegenpruefung(punkte: list[dict], breite: int, hoehe: int, modell: str) -> float | None:
    if len(punkte) - 1 < geo.MIN_PUNKTE[modell]:
        return None
    fehler = []
    faktor = geo.ERDRADIUS_M * math.pi / 180
    for i, punkt in enumerate(punkte):
        ergebnis = geo.transformation_berechnen(punkte[:i] + punkte[i + 1:], breite, hoehe, modell)
        if not ergebnis["parameter_json"]:
            return None
        p = json.loads(ergebnis["parameter_json"])
        try:
            ost, nord = geo._abbilden(p, punkt["x"], punkt["y"])
        except ObjektKartenFehler:
            return None
        ziel_ost = (punkt["lng"] - p["lng0"]) * faktor * math.cos(math.radians(p["lat0"]))
        ziel_nord = (punkt["lat"] - p["lat0"]) * faktor
        fehler.append(math.hypot(ost - ziel_ost, nord - ziel_nord))
    return max(fehler)


def _tragfaehig(hypothese: dict) -> bool:
    return (hypothese["status"] == "gueltig" and hypothese["loo_max_m"] is not None
            and hypothese["loo_max_m"] <= geo.MAX_FEHLER_M)


def _rang(hypothese: dict) -> tuple:
    return (not _tragfaehig(hypothese), hypothese["status"] != "gueltig",
            hypothese["loo_max_m"] if hypothese["loo_max_m"] is not None else math.inf,
            hypothese["rmse_m"] if hypothese["rmse_m"] is not None else math.inf)


def hypothesen_bewerten(
    bild_ecken: list[dict], gebaeude: list[dict], breite: int, hoehe: int, modell: str = "aehnlichkeit",
) -> dict:
    """Alle zyklischen Zuordnungen pruefen; nur eindeutige Ergebnisse als Klasse A."""
    if not isinstance(modell, str) or modell not in geo.MIN_PUNKTE:
        raise ObjektKartenFehler("Unbekanntes Transformationsmodell")
    if not isinstance(bild_ecken, list) or not 4 <= len(bild_ecken) <= 30:
        raise ObjektKartenFehler("4 bis 30 Bildecken als Liste erforderlich")
    for i, punkt in enumerate(bild_ecken):
        if not isinstance(punkt, dict):
            raise ObjektKartenFehler("Bildecke mit x und y erforderlich")
        x, y = geo._zahl(punkt.get("x"), "x", 0, 1), geo._zahl(punkt.get("y"), "y", 0, 1)
        if any(math.hypot(x - p["x"], y - p["y"]) < 0.005 for p in bild_ecken[:i]):
            raise ObjektKartenFehler("Bildecken muessen mindestens 0.005 Abstand haben")
    anzahl = len(bild_ecken)
    ringe = [(g["osm_id"], _ring_vereinfachen(g["ecken"])) for g in gebaeude]
    passend = [(osm_id, ring) for osm_id, ring in ringe if len(ring) == anzahl]
    if len(passend) * anzahl * 2 > MAX_HYPOTHESEN:
        raise ObjektKartenFehler("Zu viele Kandidaten; osm_id angeben oder Radius verkleinern")
    hypothesen = []
    for osm_id, ring in passend:
        for richtung in (1, -1):
            for start in range(anzahl):
                zuordnung = [dict(bild_index=i, x=p["x"], y=p["y"], osm_nummer=e["nummer"],
                                  lat=e["lat"], lng=e["lng"])
                             for i, p in enumerate(bild_ecken)
                             for e in [ring[(start + richtung * i) % anzahl]]]
                punkte = _referenzpunkte(osm_id, zuordnung)
                berechnung = geo.transformation_berechnen(punkte, breite, hoehe, modell)
                hypothesen.append(dict(
                    osm_id=osm_id, zuordnung=zuordnung, status=berechnung["status"],
                    ablehngrund=berechnung["ablehngrund"], rmse_m=berechnung["rmse_m"],
                    max_fehler_m=berechnung["max_fehler_m"],
                    loo_max_m=(_gegenpruefung(punkte, breite, hoehe, modell)
                               if berechnung["status"] == "gueltig" else None),
                ))
    hypothesen.sort(key=_rang)
    tragfaehige = [h for h in hypothesen if _tragfaehig(h)]
    klasse = "C"
    if not passend:
        begruendung = "Keine Gebaeude mit passender Eckenzahl"
    elif anzahl - 1 < geo.MIN_PUNKTE[modell]:
        begruendung = "Zu wenige Ecken fuer Gegenpruefung"
    elif not tragfaehige:
        begruendung = "Keine Hypothese innerhalb der Grenzwerte"
    elif len(tragfaehige) > 1 and tragfaehige[1]["loo_max_m"] < max(
        tragfaehige[0]["loo_max_m"] * EINDEUTIG_FAKTOR,
        tragfaehige[0]["loo_max_m"] + EINDEUTIG_ABSTAND_M,
    ):
        begruendung = "Mehrdeutig: symmetrisches Gebaeude oder gleichartiges Nachbargebaeude"
    else:
        klasse, begruendung = "A", "Eindeutige Zuordnung innerhalb der Grenzwerte inklusive Gegenpruefung"
    return dict(klasse=klasse, begruendung=begruendung, hypothesen=hypothesen[:3],
                punkte=(_referenzpunkte(hypothesen[0]["osm_id"], hypothesen[0]["zuordnung"])
                        if klasse == "A" else None),
                gepruefte_hypothesen=len(hypothesen), eckenzahlen_osm={osm_id: len(ring) for osm_id, ring in ringe})


def _referenzpunkte(osm_id: int, zuordnung: list[dict]) -> list[dict]:
    return [dict(id=e["bild_index"] + 1, x=e["x"], y=e["y"], lat=e["lat"], lng=e["lng"],
                 quelle="osm_gebaeude", beschreibung=f"OSM {osm_id} Ecke {e['osm_nummer']}") for e in zuordnung]
