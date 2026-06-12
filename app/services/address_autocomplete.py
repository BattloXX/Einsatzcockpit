"""Photon-Adress-Autocomplete (OSM, komoot.io) – über Backend geproxyt.

Nur GET-Anfragen (Lesen), kein API-Key nötig.
Modul-Level TTL-Cache reduziert externe Calls.
Immer mit leerer Liste statt Exception degradieren – Formular darf nie blockieren.
"""
import logging
import time
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger("einsatzleiter.address_autocomplete")

# TTL-Cache: key → (timestamp, [AddressSuggestion, ...])
_cache: dict[str, tuple[float, list]] = {}
_CACHE_MAX = 512


@dataclass
class AddressSuggestion:
    label: str
    street: str | None
    house_number: str | None
    city: str | None
    lat: float | None
    lng: float | None
    source: str  # "photon" | "history"


# ── Cache-Hilfsfunktionen ─────────────────────────────────────────────────────

def _cache_key(field: str, city: str, street: str, q: str) -> str:
    return f"{field}|{city.lower()}|{street.lower()}|{q.lower()}"


def _cache_get(key: str) -> list | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, items = entry
    if time.monotonic() - ts > settings.PHOTON_CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return items


def _cache_put(key: str, items: list) -> None:
    if len(_cache) >= _CACHE_MAX:
        # Älteste Hälfte entfernen
        oldest = sorted(_cache.items(), key=lambda x: x[1][0])[: _CACHE_MAX // 2]
        for k, _ in oldest:
            _cache.pop(k, None)
    _cache[key] = (time.monotonic(), items)


# ── GeoJSON-Parsing ───────────────────────────────────────────────────────────

def _parse_feature(feat: dict, field: str) -> AddressSuggestion | None:
    """Wandelt ein Photon-GeoJSON-Feature in eine AddressSuggestion um."""
    try:
        props = feat.get("properties", {})
        coords = feat.get("geometry", {}).get("coordinates", [])
        lon = float(coords[0]) if len(coords) >= 2 else None
        lat = float(coords[1]) if len(coords) >= 2 else None

        name = props.get("name") or ""
        city_prop = props.get("city") or props.get("district") or ""

        if field == "street":
            # Bei layer=street ist name der Straßenname
            street_name = name
            if not street_name:
                return None
            label = street_name
            if city_prop:
                label += f", {city_prop}"
            return AddressSuggestion(
                label=label, street=street_name, house_number=None,
                city=city_prop or None, lat=lat, lng=lon, source="photon",
            )

        elif field == "house":
            housenumber = props.get("housenumber")
            if not housenumber:
                return None
            street_name = props.get("street") or ""
            label = f"{street_name} {housenumber}".strip() if street_name else housenumber
            if city_prop:
                label += f", {city_prop}"
            return AddressSuggestion(
                label=label, street=street_name or None, house_number=housenumber,
                city=city_prop or None, lat=lat, lng=lon, source="photon",
            )

        elif field == "city":
            if not name:
                return None
            postcode = props.get("postcode", "")
            label = f"{postcode} {name}".strip() if postcode else name
            return AddressSuggestion(
                label=label, street=None, house_number=None,
                city=name, lat=lat, lng=lon, source="photon",
            )

    except Exception as exc:
        logger.debug("Photon-Feature parse error: %s", exc)
    return None


# ── Photon-Abfrage ────────────────────────────────────────────────────────────

async def photon_suggest(
    q: str,
    *,
    field: str,
    city: str | None,
    lat_bias: float | None,
    lon_bias: float | None,
    limit: int = 8,
) -> list[AddressSuggestion]:
    """Sendet eine Anfrage an Photon (komoot). Gibt [] bei Fehler zurück."""
    if field == "street":
        layers = ["street"]
    elif field == "house":
        layers = ["house"]
    elif field == "city":
        layers = ["city", "locality"]
    else:
        return []

    # Etwas mehr Ergebnisse holen, da wir nachfiltern
    fetch_limit = min(limit * 2, 20)
    params: list[tuple[str, str]] = [
        ("q", q),
        ("limit", str(fetch_limit)),
        ("lang", "de"),
    ]
    if lat_bias is not None and lon_bias is not None:
        params += [("lat", str(round(lat_bias, 4))), ("lon", str(round(lon_bias, 4)))]
    for layer in layers:
        params.append(("layer", layer))

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
            timeout=settings.PHOTON_TIMEOUT_SECONDS,
        ) as client:
            resp = await client.get(
                f"{settings.PHOTON_BASE_URL}/api",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Photon-Anfrage fehlgeschlagen: %s", exc)
        return []

    features = data.get("features", [])
    suggestions: list[AddressSuggestion] = []
    seen_labels: set[str] = set()

    # City-Filter: nur Treffer aus der gesuchten Stadt behalten.
    # Photon liefert city, district, locality oder municipality – alle prüfen.
    for feat in features:
        s = _parse_feature(feat, field)
        if s is None:
            continue
        if city and field in ("street", "house"):
            props = feat.get("properties", {})
            feat_city = (
                props.get("city") or
                props.get("municipality") or
                props.get("district") or
                props.get("locality") or ""
            ).lower()
            city_lower = city.lower()
            # Kein Ort im Feature → bei gesetztem City-Filter verwerfen
            if not feat_city:
                continue
            if city_lower not in feat_city and feat_city not in city_lower:
                continue
        if s.label in seen_labels:
            continue
        seen_labels.add(s.label)
        suggestions.append(s)
        if len(suggestions) >= limit:
            break

    return suggestions[:limit]


# ── Historischer Fallback ─────────────────────────────────────────────────────

def _history_fallback(
    db,
    *,
    q: str,
    field: str,
    city: str | None,
    street: str | None,
    org_id: int | None,
    limit: int,
) -> list[AddressSuggestion]:
    """Distinct Adressen aus bisherigen Einsätzen (org-gescoped)."""
    try:
        from sqlalchemy import desc

        from app.models.incident import Incident

        base = db.query(Incident)
        if org_id is not None:
            base = base.filter(Incident.primary_org_id == org_id)

        if field == "street":
            base = base.filter(
                Incident.address_street.isnot(None),
                Incident.address_street.ilike(f"%{q}%"),
            )
            if city:
                base = base.filter(Incident.address_city.ilike(f"%{city}%"))
            rows = (
                base
                .order_by(desc(Incident.started_at))
                .with_entities(
                    Incident.address_street,
                    Incident.address_city,
                    Incident.lat,
                    Incident.lng,
                )
                .limit(limit * 3)
                .all()
            )
            seen: set[str] = set()
            result: list[AddressSuggestion] = []
            for row in rows:
                label = row.address_street
                if row.address_city:
                    label += f", {row.address_city}"
                if label in seen:
                    continue
                seen.add(label)
                result.append(AddressSuggestion(
                    label=label, street=row.address_street,
                    house_number=None, city=row.address_city,
                    lat=row.lat, lng=row.lng, source="history",
                ))
                if len(result) >= limit:
                    break
            return result

        elif field == "house":
            base = base.filter(
                Incident.address_no.isnot(None),
                Incident.address_no.ilike(f"{q}%"),
            )
            if street:
                base = base.filter(Incident.address_street.ilike(f"%{street}%"))
            if city:
                base = base.filter(Incident.address_city.ilike(f"%{city}%"))
            rows = (
                base
                .order_by(desc(Incident.started_at))
                .with_entities(
                    Incident.address_no,
                    Incident.address_street,
                    Incident.address_city,
                    Incident.lat,
                    Incident.lng,
                )
                .limit(limit * 3)
                .all()
            )
            seen = set()
            result = []
            for row in rows:
                parts = [p for p in [row.address_street, row.address_no] if p]
                label = " ".join(parts) if parts else (row.address_no or "")
                if row.address_city:
                    label += f", {row.address_city}"
                if label in seen:
                    continue
                seen.add(label)
                result.append(AddressSuggestion(
                    label=label, street=row.address_street,
                    house_number=row.address_no, city=row.address_city,
                    lat=row.lat, lng=row.lng, source="history",
                ))
                if len(result) >= limit:
                    break
            return result

        elif field == "city":
            base = base.filter(
                Incident.address_city.isnot(None),
                Incident.address_city.ilike(f"%{q}%"),
            )
            rows = (
                base
                .order_by(desc(Incident.started_at))
                .with_entities(Incident.address_city)
                .limit(limit * 3)
                .all()
            )
            seen = set()
            result = []
            for row in rows:
                city_val = row.address_city
                if not city_val or city_val in seen:
                    continue
                seen.add(city_val)
                result.append(AddressSuggestion(
                    label=city_val, street=None, house_number=None,
                    city=city_val, lat=None, lng=None, source="history",
                ))
                if len(result) >= limit:
                    break
            return result

    except Exception as exc:
        logger.warning("History-Fallback Fehler: %s", exc)
    return []


# ── Haupt-Funktion ────────────────────────────────────────────────────────────

async def suggest_addresses(
    db,
    *,
    q: str,
    field: str,
    city: str | None,
    street: str | None,
    org_id: int | None,
    limit: int = 8,
) -> list[AddressSuggestion]:
    """Liefert Adress-Vorschläge. Nutzt Cache, Photon und History-Fallback.

    Wirft keine Exception – gibt im Fehlerfall leere Liste zurück.
    """
    if not q.strip():
        return []

    # Bias-Koordinaten aus Org (für lokale Priorisierung)
    lat_bias: float | None = None
    lon_bias: float | None = None
    if org_id is not None:
        try:
            from app.models.master import FireDept
            org = db.query(FireDept).filter(FireDept.id == org_id).first()
            if org and org.fallback_lat:
                lat_bias = float(org.fallback_lat)
                lon_bias = float(org.fallback_lng)
        except Exception:
            pass

    # Photon-Query aufbauen: Stadt und Straße als Kontext einbauen, damit
    # Photon von vornherein lokal sucht statt global.
    photon_q = q
    if field == "house" and street:
        photon_q = f"{street} {q}"
        if city:
            photon_q += f", {city}"
    elif field == "street" and city:
        photon_q = f"{q}, {city}"

    key = _cache_key(field, city or "", street or "", photon_q)
    cached = _cache_get(key)
    if cached is not None:
        return cached[:limit]

    # Photon aufrufen
    items = await photon_suggest(
        photon_q,
        field=field,
        city=city,
        lat_bias=lat_bias,
        lon_bias=lon_bias,
        limit=limit,
    )

    if not items:
        # Photon leer oder nicht erreichbar → reine Historie
        items = _history_fallback(
            db, q=q, field=field, city=city, street=street,
            org_id=org_id, limit=limit,
        )
    else:
        # Hybrid: bekannte historische Adressen ergänzen (dedupliziert)
        history = _history_fallback(
            db, q=q, field=field, city=city, street=street,
            org_id=org_id, limit=limit,
        )
        seen = {s.label for s in items}
        for h in history:
            if h.label not in seen and len(items) < limit:
                items.append(h)

    _cache_put(key, items)
    return items[:limit]
