"""Adresspool, Gebiet und deterministische Ortswahl fuer Planspiele."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.core.tenant import set_tenant_context
from app.data.planspiel_katalog import ORT_TYPEN
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings
from app.models.objekt import Objekt, ObjektBMA, ObjektZusatzadresse
from app.models.planspiel import PlanspielAdresse, PlanspielOrtTypZuordnung, PlanspielSzenario, PlanspielSzenarioVersion
from app.services.geo_service import validate_geojson_polygon
from app.services.hoehen_service import haversine_m
from app.services.overpass_endpunkte import endpunkte

logger = logging.getLogger(__name__)


class GebietFehlt(ValueError):
    pass


class OsmImportFehler(RuntimeError):
    pass


class OrtNichtGefunden(ValueError):
    pass


@dataclass(frozen=True)
class Gebiet:
    polygon: Any | None
    zentrum: tuple[float, float] | None
    radius_m: int


@dataclass(frozen=True)
class PoolErgebnis:
    neu: int = 0
    aktualisiert: int = 0
    deaktiviert: int = 0


@dataclass(frozen=True)
class OrtWahl:
    adresse_id: int
    objekt_id: int | None
    bezeichnung: str | None
    strasse: str
    hausnr: str | None
    plz: str | None
    ort: str | None
    lat: float
    lng: float
    qualitaet: str
    quelle: str
    adresse: str


_BUILDING_TYPEN = {
    "house": {"wohngebaeude"}, "detached": {"wohngebaeude"}, "semidetached_house": {"wohngebaeude"},
    "residential": {"wohngebaeude"}, "terrace": {"wohngebaeude"},
    "apartments": {"mehrfamilienhaus", "wohngebaeude", "wohngebaeude_gross"},
    "commercial": {"gewerbe"}, "retail": {"gewerbe"}, "office": {"gewerbe"}, "supermarket": {"gewerbe"},
    "industrial": {"industrie", "gewerbe"}, "warehouse": {"industrie", "gewerbe"}, "factory": {"industrie", "gewerbe"},
    "farm": {"landwirtschaft"}, "barn": {"landwirtschaft"}, "farm_auxiliary": {"landwirtschaft"},
    "stable": {"landwirtschaft"}, "cowshed": {"landwirtschaft"},
    "garage": {"garage"}, "garages": {"garage"}, "carport": {"garage"},
    "school": {"sonderobjekt", "gebaeude_gross"}, "hospital": {"sonderobjekt", "gebaeude_gross"},
    "church": {"sonderobjekt", "gebaeude_gross"}, "public": {"sonderobjekt", "gebaeude_gross"},
    "civic": {"sonderobjekt", "gebaeude_gross"}, "kindergarten": {"sonderobjekt", "gebaeude_gross"},
    "hotel": {"sonderobjekt", "gebaeude_gross"}, "sports_hall": {"sonderobjekt", "gebaeude_gross"},
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def gebiet(db: Session, org_id: int) -> Gebiet:
    org = db.query(FireDept).filter(FireDept.id == org_id).one_or_none()
    conf = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).one_or_none()
    raw = conf.planspiel_gebiet_geojson if conf else None
    if raw:
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if validate_geojson_polygon(data) is None:
                from shapely.geometry import shape
                polygon = shape(data)
                return Gebiet(polygon, None, int((conf.planspiel_gebiet_radius_m if conf else None) or 5000))
        except (ValueError, TypeError, ImportError):
            pass
    zentrum = None
    if conf and conf.routing_start_lat is not None and conf.routing_start_lng is not None:
        zentrum = (conf.routing_start_lat, conf.routing_start_lng)
    elif org and org.fallback_lat is not None and org.fallback_lng is not None:
        zentrum = (org.fallback_lat, org.fallback_lng)
    if zentrum is None:
        raise GebietFehlt("Planspiel-Gebiet ist nicht festgelegt (Polygon oder Koordinaten der Organisation fehlen).")
    return Gebiet(None, zentrum, int((conf.planspiel_gebiet_radius_m if conf else None) or 5000))


def im_gebiet(wert: Gebiet, lat: float, lng: float) -> bool:
    if wert.polygon is not None:
        from shapely.geometry import Point
        return bool(wert.polygon.covers(Point(lng, lat)))
    return wert.zentrum is not None and haversine_m(*wert.zentrum, lat, lng) <= wert.radius_m


def _mapping(db: Session, org_id: int, quelle: str, schluessel: str, basis: set[str] | None = None) -> list[str]:
    typen = set(basis or {"gebaeude"})
    for row in db.query(PlanspielOrtTypZuordnung).filter_by(org_id=org_id, quelle=quelle, schluessel=schluessel):
        typen.add(row.ort_typ)
    typen.update({"gebaeude", "beliebig", "beliebig_geeignet"})
    return sorted(typen)


def _objekt_typen(db: Session, org_id: int, objekt: Objekt) -> list[str]:
    return _mapping(db, org_id, "kategorie", str(objekt.kategorie_id), set()) if objekt.kategorie_id else _mapping(
        db, org_id, "kategorie", "", set()
    )


def _upsert(db: Session, org_id: int, values: dict[str, Any], result: PoolErgebnis) -> PoolErgebnis:
    row = db.query(PlanspielAdresse).filter_by(
        org_id=org_id, quelle=values["quelle"], quell_id=values["quell_id"]
    ).one_or_none()
    if row is None:
        db.add(PlanspielAdresse(org_id=org_id, **values))
        return PoolErgebnis(result.neu + 1, result.aktualisiert, result.deaktiviert)
    for key, value in values.items():
        setattr(row, key, value)
    return PoolErgebnis(result.neu, result.aktualisiert + 1, result.deaktiviert)


def pool_aufbauen(db: Session, org_id: int) -> PoolErgebnis:
    area, result, seen = gebiet(db, org_id), PoolErgebnis(), set()
    objekte = db.query(Objekt).filter_by(org_id=org_id, status="freigegeben").all()
    for objekt in objekte:
        if objekt.lat is None or objekt.lng is None or not im_gebiet(area, objekt.lat, objekt.lng):
            continue
        bma = db.query(ObjektBMA).filter_by(org_id=org_id, objekt_id=objekt.id).one_or_none()
        merkmale = {"bma": True} if bma and (bma.bma_nummer or bma.rfl_nummer) else None
        typen = _objekt_typen(db, org_id, objekt)
        if merkmale:
            typen = sorted(set(typen) | {"objekt_bma"})
        key = ("objekt", str(objekt.id))
        seen.add(key)
        result = _upsert(db, org_id, {"quelle": key[0], "quell_id": key[1], "objekt_id": objekt.id,
            "bezeichnung": objekt.name, "strasse": objekt.strasse or objekt.name, "hausnr": objekt.hausnummer,
            "plz": objekt.plz, "ort": objekt.ort, "lat": objekt.lat, "lng": objekt.lng, "qualitaet": "A",
            "ort_typen": typen, "merkmale": merkmale, "aktiv": True}, result)
    for extra in db.query(ObjektZusatzadresse).filter_by(org_id=org_id).all():
        obj = next((x for x in objekte if x.id == extra.objekt_id), None)
        if not obj or extra.lat is None or extra.lng is None or not im_gebiet(area, extra.lat, extra.lng):
            continue
        key = ("zusatzadresse", str(extra.id))
        seen.add(key)
        result = _upsert(db, org_id, {"quelle": key[0], "quell_id": key[1], "objekt_id": obj.id,
            "bezeichnung": extra.bezeichnung, "strasse": extra.strasse or obj.strasse or obj.name,
            "hausnr": extra.hausnummer, "plz": extra.plz, "ort": extra.ort, "lat": extra.lat, "lng": extra.lng,
            "qualitaet": "A", "ort_typen": _objekt_typen(db, org_id, obj), "merkmale": None, "aktiv": True}, result)
    dedupe: set[tuple[str, str]] = set()
    for incident in db.query(Incident).filter_by(primary_org_id=org_id, is_exercise=False).all():
        if not incident.address_street or not incident.address_no or incident.lat is None or incident.lng is None:
            continue
        key = (incident.address_street.lower(), incident.address_no.lower())
        if key in dedupe or not im_gebiet(area, incident.lat, incident.lng):
            continue
        dedupe.add(key)
        source = ("einsatzhistorie", f"{key[0]}|{key[1]}")
        seen.add(source)
        result = _upsert(db, org_id, {"quelle": source[0], "quell_id": source[1], "objekt_id": None,
            "bezeichnung": None, "strasse": incident.address_street, "hausnr": incident.address_no, "plz": None,
            "ort": incident.address_city, "lat": incident.lat, "lng": incident.lng, "qualitaet": "B",
            "ort_typen": ["gebaeude", "wohngebaeude", "beliebig", "beliebig_geeignet"], "merkmale": None,
            "aktiv": True}, result)
    stale = db.query(PlanspielAdresse).filter(
        PlanspielAdresse.org_id == org_id,
        PlanspielAdresse.quelle.in_(("objekt", "zusatzadresse", "einsatzhistorie")),
        PlanspielAdresse.aktiv.is_(True),
    )
    for row in stale:
        if (row.quelle, row.quell_id) not in seen:
            row.aktiv = False
            result = PoolErgebnis(result.neu, result.aktualisiert, result.deaktiviert + 1)
    db.flush()
    ortschaften_ergaenzen(db, org_id)
    db.flush()
    return result


def ortschaften_ergaenzen(db: Session, org_id: int) -> int:
    """Ergaenzt fehlende Ortsnamen vom naechsten bekannten Pool-Eintrag.

    Das grobe Koordinatengitter begrenzt die Distanzberechnungen auch bei
    groesseren Pools auf die unmittelbare Nachbarschaft.
    """
    rows = db.query(PlanspielAdresse).filter_by(org_id=org_id, aktiv=True).all()
    bekannte = [row for row in rows if row.ort and row.ort.strip()]
    fehlende = [row for row in rows if not row.ort or not row.ort.strip()]
    if not bekannte or not fehlende:
        return 0
    bucket_size = 0.02
    buckets: dict[tuple[int, int], list[PlanspielAdresse]] = {}
    for row in bekannte:
        key = (int(row.lat / bucket_size), int(row.lng / bucket_size))
        buckets.setdefault(key, []).append(row)
    ergaenzt = 0
    for row in fehlende:
        x, y = int(row.lat / bucket_size), int(row.lng / bucket_size)
        nearby = [candidate for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                  for candidate in buckets.get((x + dx, y + dy), [])]
        if not nearby:
            continue
        nearest = min(nearby, key=lambda candidate: haversine_m(row.lat, row.lng, candidate.lat, candidate.lng))
        if haversine_m(row.lat, row.lng, nearest.lat, nearest.lng) <= 1500:
            row.ort = nearest.ort
            ergaenzt += 1
    return ergaenzt


def ortschaften(db: Session, org_id: int) -> list[tuple[str, int]]:
    rows = db.query(PlanspielAdresse).filter_by(org_id=org_id, aktiv=True).all()
    grouped: dict[str, list[str]] = {}
    for row in rows:
        if row.ort and row.ort.strip():
            name = row.ort.strip()
            grouped.setdefault(name.casefold(), []).append(name)
    result = []
    for names in grouped.values():
        spelling = sorted(set(names), key=lambda name: (-names.count(name), name.casefold(), name))[0]
        result.append((spelling, len(names)))
    return sorted(result, key=lambda item: item[0].casefold())


def standard_ortschaften(db: Session, org_id: int) -> list[str] | None:
    org = db.query(FireDept).filter(FireDept.id == org_id).one_or_none()
    city = (org.city or "").strip() if org else ""
    if not city:
        return None
    available = {name.casefold() for name, _count in ortschaften(db, org_id)}
    return [city] if city.casefold() in available else None


def _overpass_query(area: Gebiet) -> str:
    if area.zentrum:
        selector = f"(around:{area.radius_m},{area.zentrum[0]},{area.zentrum[1]})"
    elif area.polygon is not None:
        bounds = area.polygon.bounds
        selector = f"({bounds[1]},{bounds[0]},{bounds[3]},{bounds[2]})"
    else:
        raise GebietFehlt("Planspiel-Gebiet ist nicht festgelegt (Polygon oder Koordinaten der Organisation fehlen).")
    timeout = int(settings.PLANSPIEL_OVERPASS_TIMEOUT_SECONDS)
    highway = "^(primary|secondary|tertiary|residential|unclassified|living_street)$"
    teile = (
        f'nwr["addr:housenumber"]["addr:street"]{selector};',
        f'way["highway"~"{highway}"]["name"]{selector};',
        f'way["waterway"~"^(river|stream)$"]{selector};',
        f'way["railway"="rail"]{selector};',
    )
    return f"[out:json][timeout:{timeout}];(" + "".join(teile) + ");out center tags;"


# Wartezeiten vor dem 2. und 3. Versuch, wenn Overpass ausgelastet ist
_OSM_WARTEN_S: tuple[float, ...] = (5.0, 15.0)
_OSM_WIEDERHOLEN = {429, 502, 503, 504}


async def _overpass_abrufen(query: str) -> list[dict[str, Any]]:
    urls = endpunkte(settings.STRASSEN_OVERPASS_URL)
    timeout = settings.PLANSPIEL_OVERPASS_TIMEOUT_SECONDS + 15
    async with httpx.AsyncClient(headers={"User-Agent": settings.HYDRANT_USER_AGENT}, timeout=timeout) as client:
        for versuch in range(len(_OSM_WARTEN_S) + 1):
            try:
                response = await client.post(urls[versuch % len(urls)], data={"data": query})
                if response.status_code in _OSM_WIEDERHOLEN and versuch < len(_OSM_WARTEN_S):
                    logger.info("Overpass HTTP %s, Versuch %s", response.status_code, versuch + 1)
                    await asyncio.sleep(_OSM_WARTEN_S[versuch])
                    continue
                response.raise_for_status()
                return list(response.json().get("elements", []))
            except httpx.TimeoutException as exc:
                if versuch < len(_OSM_WARTEN_S):
                    logger.info("Overpass Timeout, Versuch %s", versuch + 1)
                    await asyncio.sleep(_OSM_WARTEN_S[versuch])
                    continue
                raise OsmImportFehler(
                    "OSM-Import fehlgeschlagen: Overpass hat nicht rechtzeitig geantwortet. "
                    "Bitte später erneut versuchen."
                ) from exc
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("Overpass-Abruf fehlgeschlagen: %s", exc)
                raise OsmImportFehler(
                    "OSM-Import fehlgeschlagen: Overpass ist derzeit ausgelastet oder nicht erreichbar. "
                    "Bitte in einigen Minuten erneut versuchen."
                ) from exc
    raise OsmImportFehler("OSM-Import fehlgeschlagen: Overpass ist derzeit ausgelastet.")


async def osm_importieren(db_factory: Any, org_id: int) -> PoolErgebnis:
    def get_area() -> Gebiet:
        db = db_factory()
        set_tenant_context(db, org_id)
        try:
            return gebiet(db, org_id)
        finally:
            db.close()
    area = await asyncio.to_thread(get_area)
    elements = await _overpass_abrufen(_overpass_query(area))
    if len(elements) > 20000:
        elements = elements[:20000]
    def store() -> PoolErgebnis:
        db = db_factory()
        set_tenant_context(db, org_id)
        result = PoolErgebnis()
        try:
            vorhanden = {
                (row.quelle, row.quell_id): row
                for row in db.query(PlanspielAdresse).filter(
                    PlanspielAdresse.org_id == org_id, PlanspielAdresse.quelle.in_(("osm", "strassenpunkt"))
                )
            }
            for item in elements:
                tags, center = item.get("tags") or {}, item.get("center") or item
                if "lat" not in center or "lon" not in center:
                    continue
                lat, lng = float(center["lat"]), float(center["lon"])
                if not im_gebiet(area, lat, lng):
                    continue
                typ, ident = str(item.get("type", "")), str(item.get("id", ""))
                highway = tags.get("highway")
                common = {
                    "objekt_id": None, "bezeichnung": tags.get("name"), "plz": tags.get("addr:postcode"),
                    "ort": tags.get("addr:city"), "lat": lat, "lng": lng, "aktiv": True,
                }
                if tags.get("addr:housenumber") and tags.get("addr:street"):
                    base = _BUILDING_TYPEN.get(tags.get("building", ""), {"gebaeude"})
                    types = _mapping(db, org_id, "osm", str(tags.get("building", "")), base)
                    if tags.get("amenity") == "parking" or tags.get("building") == "parking":
                        types = sorted(set(types) | {"parkplatz"})
                    values = common | {"quelle": "osm", "quell_id": f"{typ}/{ident}",
                        "strasse": tags["addr:street"], "hausnr": tags["addr:housenumber"], "qualitaet": "B",
                        "ort_typen": types, "merkmale": None}
                elif highway:
                    strassen_typen = {"strasse", "verkehrsflaeche", "beliebig_geeignet"}
                    if highway in {"primary", "secondary", "tertiary"}:
                        strassen_typen.add("strasse_geeignet")
                    values = common | {"quelle": "strassenpunkt", "quell_id": f"{typ}/{ident}",
                        "strasse": tags["name"], "hausnr": None, "plz": None, "ort": None, "qualitaet": "C",
                        "ort_typen": sorted(strassen_typen), "merkmale": None}
                elif tags.get("waterway"):
                    values = common | {"quelle": "strassenpunkt", "quell_id": f"{typ}/{ident}",
                        "strasse": tags.get("name") or "Gewässer", "hausnr": None, "plz": None, "ort": None,
                        "qualitaet": "C", "ort_typen": ["gewaessernaehe", "gewaesser"],
                        "merkmale": {"gewaesser": tags.get("name") or tags["waterway"]}}
                elif tags.get("railway") == "rail":
                    values = common | {"quelle": "strassenpunkt", "quell_id": f"{typ}/{ident}",
                        "strasse": tags.get("name") or "Gleisbereich", "hausnr": None, "plz": None,
                        "ort": None, "qualitaet": "C", "ort_typen": ["gleisbereich"], "merkmale": None}
                else:
                    continue
                row = vorhanden.get((values["quelle"], values["quell_id"]))
                if row is None:
                    row = PlanspielAdresse(org_id=org_id, **values)
                    db.add(row)
                    vorhanden[(values["quelle"], values["quell_id"])] = row
                    result = PoolErgebnis(result.neu + 1, result.aktualisiert, result.deaktiviert)
                else:
                    for key, value in values.items():
                        setattr(row, key, value)
                    result = PoolErgebnis(result.neu, result.aktualisiert + 1, result.deaktiviert)
            db.flush()
            ortschaften_ergaenzen(db, org_id)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    return await asyncio.to_thread(store)


def manuellen_punkt_anlegen(
    db: Session, org_id: int, *, lat: float, lng: float, bezeichnung: str | None, strasse: str,
    hausnr: str | None, ort: str | None, ort_typen: list[str], merkmale: dict[str, Any] | None,
) -> PlanspielAdresse:
    if not set(ort_typen).issubset(ORT_TYPEN):
        raise ValueError("Unbekannter Ortstyp")
    if not im_gebiet(gebiet(db, org_id), lat, lng):
        raise ValueError("Punkt liegt außerhalb des Planspiel-Gebiets")
    row = PlanspielAdresse(
        org_id=org_id, quelle="manuell", quell_id=str(uuid.uuid4()), bezeichnung=bezeichnung,
        strasse=strasse, hausnr=hausnr, plz=None, ort=ort, lat=lat, lng=lng, qualitaet="C",
        ort_typen=ort_typen, merkmale=merkmale, aktiv=True,
    )
    db.add(row)
    db.flush()
    return row


def _candidates(
    db: Session, org_id: int, inhalt: dict[str, Any], ausschliessen_ids: set[int] | frozenset[int] = frozenset(), *,
    ortschaften: set[str] | list[str] | None = None,
) -> list[PlanspielAdresse]:
    rows = db.query(PlanspielAdresse).filter_by(org_id=org_id, aktiv=True).all()
    wanted = set(inhalt.get("ort_typen") or [])
    quality = inhalt.get("min_adressqualitaet")
    filter_orte = {name.strip().casefold() for name in (ortschaften or []) if name.strip()}
    return [r for r in rows if r.id not in ausschliessen_ids and wanted.intersection(r.ort_typen or [])
            and (not filter_orte or (r.ort or "").strip().casefold() in filter_orte)
            and (not inhalt.get("requires_existing_object") or r.objekt_id is not None)
            and (not inhalt.get("requires_verified_bma") or (r.merkmale or {}).get("bma") is True)
            and (not quality or r.qualitaet <= quality)
            and all((r.merkmale or {}).get(k) == v for k, v in (inhalt.get("geografie_bedingung") or {}).items())]


def hat_kandidaten(db: Session, org_id: int, version_inhalt: dict[str, Any], *,
                   ortschaften: set[str] | list[str] | None = None) -> bool:
    return bool(_candidates(db, org_id, version_inhalt, ortschaften=ortschaften))


def ort_waehlen(
    db: Session, org_id: int, version_inhalt: dict[str, Any], rng: random.Random, *,
    ausschliessen_ids: set[int] | frozenset[int] = frozenset(), sperrfrist_h: int = 24, jetzt: datetime | None = None,
    ortschaften: set[str] | list[str] | None = None,
) -> OrtWahl:
    rows = _candidates(db, org_id, version_inhalt, ausschliessen_ids, ortschaften=ortschaften)
    now = jetzt or _now()
    fresh = [r for r in rows if r.zuletzt_verwendet_at is None
             or r.zuletzt_verwendet_at <= now - timedelta(hours=sperrfrist_h)]
    rows = fresh or rows
    if not rows:
        orts_filter = [name.strip() for name in (ortschaften or []) if name.strip()]
        if orts_filter:
            raise OrtNichtGefunden(f"Kein Ort in {', '.join(orts_filter)} im Adresspool")
        if version_inhalt.get("requires_verified_bma"):
            raise OrtNichtGefunden("Kein Objekt mit registrierter BMA im Gebiet")
        wanted = ", ".join(version_inhalt.get("ort_typen") or [])
        raise OrtNichtGefunden(f"Kein Ort mit Ortstyp {wanted} im Adresspool")
    rows.sort(key=lambda r: r.id)
    weights = [{"A": 3, "B": 2, "C": 1}.get(r.qualitaet, 1) for r in rows]
    row = rng.choices(rows, weights=weights, k=1)[0]
    place = " ".join(x for x in [row.plz, row.ort] if x)
    address = " ".join(x for x in [row.strasse, row.hausnr] if x)
    return OrtWahl(
        row.id, row.objekt_id, row.bezeichnung, row.strasse, row.hausnr, row.plz, row.ort,
        row.lat, row.lng, row.qualitaet, row.quelle, ", ".join(x for x in [address, place] if x),
    )


def als_verwendet_markieren(db: Session, adresse_id: int, jetzt: datetime | None = None) -> None:
    row = db.get(PlanspielAdresse, adresse_id)
    if row:
        row.zuletzt_verwendet_at = jetzt or _now()


def szenario_zufall(
    db: Session, org_id: int, rng: random.Random, *, art: Literal["einzel", "gsl"],
    kategorie: str | None = None, schwierigkeit: str | None = None,
    ortschaften: set[str] | list[str] | None = None, gsl_modus: str | None = None,
) -> tuple[PlanspielSzenario, dict[str, Any]]:
    choices: list[tuple[PlanspielSzenario, PlanspielSzenarioVersion, dict[str, Any]]] = []
    query = db.query(PlanspielSzenario).filter_by(org_id=org_id, aktiv=True, zufall_erlaubt=True)
    for scenario in query:
        if scenario.archiviert_at is not None or scenario.aktive_version_id is None:
            continue
        version = db.get(PlanspielSzenarioVersion, scenario.aktive_version_id)
        if not version or (art == "gsl") != (version.kategorie == "gsl"):
            continue
        if kategorie and version.kategorie != kategorie:
            continue
        if schwierigkeit and version.schwierigkeit != schwierigkeit:
            continue
        if gsl_modus and getattr(version, "gsl_modus", None) != gsl_modus:
            continue
        fields = ("ort_typen", "geografie_bedingung", "requires_existing_object",
                  "requires_verified_bma", "min_adressqualitaet")
        content = {key: getattr(version, key) for key in fields}
        if art == "gsl" or hat_kandidaten(db, org_id, content, ortschaften=ortschaften):
            choices.append((scenario, version, content))
    if not choices:
        raise OrtNichtGefunden("Kein passendes Szenario mit einem Ort im Adresspool")
    choices.sort(key=lambda item: item[0].code)
    selected = rng.choices(choices, weights=[x[0].gewicht for x in choices], k=1)[0]
    from app.services.planspiel_katalog_service import version_als_dict

    return selected[0], version_als_dict(selected[1], db)
