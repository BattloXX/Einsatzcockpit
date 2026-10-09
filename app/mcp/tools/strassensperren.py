"""Lesewerkzeuge fuer sichtbare Strassensperren und Einsatz-Anfahrten."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.audit import write_audit
from app.core.permissions import STRASSENSPERREN_LESE_ROLLEN
from app.core.tenant import set_tenant_context
from app.core.timezones import format_local_iso, local_date_to_utc, local_input_to_utc, org_tz
from app.db import SessionLocal
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.mcp.tools.objekt_dokumente import _decode_inhalt
from app.models.incident import Incident, IncidentOrg
from app.models.master import FireDept
from app.models.road_closure import (
    CLOSURE_STATUS,
    DIRECTIONS,
    GEOMETRY_QUALITY,
    GEOMETRY_STATUS,
    PRIORITIES,
    RESTRICTION_TYPES,
    RoadClosure,
    RoadClosureDocument,
    RoadClosureNotification,
    RoadClosureShare,
    RoadClosureTeamsConfig,
)
from app.models.user import User
from app.services import (
    einsatz_routing,
    road_closure_geo_service,
    road_closure_incident_service,
    road_closure_notify_service,
    road_closure_section_service,
    road_closure_service,
    road_closure_stats_service,
    road_closure_token_service,
)
from app.services.einsatz_routing import RoutingError
from app.services.mcp_download_service import erstelle_download_token
from app.services.mcp_upload_service import effektives_limit, erstelle_upload, lade_upload_fuer_uebergabe
from app.services.road_closure_document_service import entwurf_aus_text, extract_text, store_document
from app.services.road_closure_flags import strassensperren_effective_enabled


def _org(context: MCPContext) -> FireDept | None:
    return context.db.get(FireDept, context.org_id)


def _iso(value: datetime | None, org: FireDept | None) -> str | None:
    return format_local_iso(value, org) or None


def _related_dict(related, org: FireDept | None) -> dict[str, object]:
    closure = related.closure
    return {
        "id": closure.id,
        "title": closure.title,
        "street": closure.street,
        "from_text": closure.from_text,
        "to_text": closure.to_text,
        "valid_from": _iso(closure.valid_from, org),
        "valid_until": _iso(closure.valid_until, org),
        "reference_number": closure.reference_number,
        "beziehung": related.beziehung,
        "gruende": related.gruende,
        "vorgeschlagene_aenderung": {
            field: _iso(value, org) if isinstance(value, datetime) else value
            for field, value in related.vorgeschlagene_aenderung.items()
        },
    }


def _closure_dict(closure: RoadClosure, org_id: int, *, voll: bool = False, db: Any = None) -> dict[str, object]:
    org = db.get(FireDept, org_id) if db is not None else None
    own = closure.org_id == org_id
    result: dict[str, object] = {
        "id": closure.id,
        "title": closure.title,
        "street": closure.street,
        "city": closure.city,
        "reference_number": closure.reference_number,
        "exceptions": closure.exceptions,
        "authority": closure.authority,
        "reason": closure.reason,
        "from_text": closure.from_text,
        "to_text": closure.to_text,
        "restriction_type": closure.restriction_type,
        "restriction_label": closure.restriction_label,
        "priority": closure.priority,
        "status": road_closure_service.compute_status(closure),
        "status_label": CLOSURE_STATUS.get(road_closure_service.compute_status(closure), ""),
        "valid_from": _iso(closure.valid_from, org),
        "valid_until": _iso(closure.valid_until, org),
        "geometry_status": closure.geometry_status,
        "geometry_quality": closure.geometry_quality,
        "superseded_by_id": closure.superseded_by_id,
        "eigene": own,
        "besitzer_org": None
        if own or db is None
        else (db.get(FireDept, closure.org_id).name if db.get(FireDept, closure.org_id) else None),
    }
    if voll:
        creator = db.get(User, closure.created_by_user_id) if db is not None and closure.created_by_user_id else None
        result.update(
            {
                "description": closure.description,
                "direction": closure.direction,
                "max_weight_t": closure.max_weight_t,
                "max_height_m": closure.max_height_m,
                "max_width_m": closure.max_width_m,
                "max_length_m": closure.max_length_m,
                "source": closure.source,
                "source_url": closure.source_url,
                "geometry": json.loads(closure.geometry_geojson) if closure.geometry_geojson else None,
                "freigegeben_fuer": (
                    [
                        row.name
                        for row in db.query(FireDept)
                        .join(RoadClosureShare, RoadClosureShare.org_id == FireDept.id)
                        .filter(RoadClosureShare.road_closure_id == closure.id)
                        .all()
                    ]
                    if own and db is not None
                    else []
                ),
                "erstellt_von": getattr(creator, "display_name", None) or getattr(creator, "name", None),
                "erstellt_am": _iso(closure.created_at, org),
                "aktualisiert_am": _iso(closure.updated_at, org),
                "version": closure.version,
                "cancelled_at": _iso(closure.cancelled_at, org),
                "cancel_reason": closure.cancel_reason,
                "teams_melden": closure.teams_melden,
                "ui_link": f"/strassensperren/{closure.id}",
            }
        )
    return result


def _limit(limit: int, maximum: int = 200) -> None:
    if not 1 <= limit <= maximum:
        raise ValueError(f"limit muss zwischen 1 und {maximum} liegen.")


def _date(value: str, *, end: bool, org: FireDept | None) -> datetime | None:
    if not value:
        return None
    parsed = local_date_to_utc(value, end=end, org=org) if "T" not in value else local_input_to_utc(value, org)
    if parsed is None:
        raise ValueError("Datum muss YYYY-MM-DD oder ein ISO-Datum mit Uhrzeit sein.")
    return parsed


def _datetime_input(value: str, org: FireDept | None, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} ist erforderlich.")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field} muss ein ISO-8601-Zeitpunkt sein.") from exc
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    converted = local_input_to_utc(value, org)
    if converted is None:
        raise ValueError(f"{field} muss ein ISO-8601-Zeitpunkt sein.")
    return converted


def _geometry(value: dict | str | None) -> dict | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("geometry_geojson muss gültiges GeoJSON sein.") from exc
    if not isinstance(value, dict):
        raise ValueError("geometry_geojson muss ein GeoJSON-Objekt oder JSON-String sein.")
    return value


def _writable_closure(context: MCPContext, road_closure_id: int) -> RoadClosure:
    try:
        return road_closure_service.get_closure_for_org(context.db, context.org_id, road_closure_id, writable=True)
    except ValueError as exc:
        raise ValueError("Straßensperre nicht gefunden oder nicht änderbar.") from exc


def _duplicate_dict(closure: RoadClosure, org_id: int, db: Any) -> dict[str, object]:
    result = _closure_dict(closure, org_id, db=db)
    return {key: result[key] for key in ("id", "title", "street", "from_text", "to_text", "valid_from", "valid_until")}


def _summary(items: list[dict[str, object]]) -> str:
    if not items:
        return "Keine passenden Straßensperren gefunden."
    names = "; ".join(str(item["title"]) for item in items[:3])
    return f"{len(items)} Straßensperren: {names}."


@register_tool(
    name="strassensperren_liste",
    description="Listet sichtbare Straßensperren (road_closures_list).",
    required_roles=STRASSENSPERREN_LESE_ROLLEN,
    module_check=strassensperren_effective_enabled,
)
async def strassensperren_liste(
    context: MCPContext,
    status: str = "current",
    von: str = "",
    bis: str = "",
    strasse: str = "",
    restriction_type: str = "",
    nur_eigene: bool = False,
    limit: int = 50,
    geometrie: str = "",
    zeitraum: str = "",
    bereich: str = "alle",
    lat: float | None = None,
    lng: float | None = None,
    radius_m: float | None = None,
) -> dict[str, object]:
    if status not in {"current", "active", "planned", "expired", "cancelled", "all"}:
        raise ValueError("status muss current, active, planned, expired, cancelled oder all sein.")
    if restriction_type:
        restriction_type = road_closure_service.normalize_restriction_type(restriction_type)
    _limit(limit)
    org = _org(context)
    if zeitraum not in {"", "heute", "7tage", "30tage"}:
        raise ValueError("zeitraum muss heute, 7tage oder 30tage sein.")
    if bereich not in {"alle", "eigene", "nachbarn"}:
        raise ValueError("bereich muss alle, eigene oder nachbarn sein.")
    if (lat is None) != (lng is None) or (lat is None) != (radius_m is None):
        raise ValueError("lat, lng und radius_m müssen gemeinsam angegeben werden.")
    if radius_m is not None and not 50 <= radius_m <= 20000:
        raise ValueError("radius_m muss zwischen 50 und 20000 liegen.")
    if zeitraum:
        local_now = datetime.now(UTC).astimezone(org_tz(org))
        start = local_now.date()
        days = {"heute": 0, "7tage": 6, "30tage": 29}[zeitraum]
        von_value = local_date_to_utc(start.isoformat(), org=org)
        bis_value = local_date_to_utc((start + timedelta(days=days)).isoformat(), end=True, org=org)
    else:
        von_value, bis_value = _date(von, end=False, org=org), _date(bis, end=True, org=org)
    rows = road_closure_service.list_closures(
        context.db,
        context.org_id,
        status=None if status == "all" else status,
        von=von_value,
        bis=bis_value,
        text=strasse or None,
        restriction_type=restriction_type or None,
        scope="own" if nur_eigene or bereich == "eigene" else ("shared" if bereich == "nachbarn" else "all"),
        limit=limit,
        geometrie=geometrie,
    )
    if lat is not None and lng is not None and radius_m is not None:
        rows = [
            row for row in rows if row.geometry_geojson and road_closure_geo_service.distance_point_to_geometry_m(
                lat, lng, json.loads(row.geometry_geojson)
            ) <= radius_m
        ]
    items = [_closure_dict(row, context.org_id, db=context.db) for row in rows]
    return {"count": len(items), "items": items, "zusammenfassung": _summary(items)}


@register_tool(
    name="strassensperre_lesen",
    description="Liest eine sichtbare Straßensperre (road_closures_get).",
    required_roles=STRASSENSPERREN_LESE_ROLLEN,
    module_check=strassensperren_effective_enabled,
)
async def strassensperre_lesen(context: MCPContext, road_closure_id: int) -> dict[str, object]:
    closure = road_closure_service.get_closure_for_org(context.db, context.org_id, road_closure_id, writable=False)
    result = _closure_dict(closure, context.org_id, voll=True, db=context.db)
    if closure.org_id == context.org_id:
        active_token = road_closure_token_service.active_detail_token(context.db, closure)
        result["freigabelink"] = {
            "aktiv": active_token is not None,
            "gueltig_bis": _iso(active_token.expires_at, _org(context)) if active_token else None,
        }
        notifications = (
            context.db.query(RoadClosureNotification)
            .filter(RoadClosureNotification.road_closure_id == closure.id)
            .order_by(RoadClosureNotification.created_at.desc())
            .limit(5)
            .all()
        )
        result["teams_benachrichtigungen"] = [
            {"ereignis": row.ereignis, "status": row.status, "versuche": row.attempt_count,
             "zeit": _iso(row.created_at, _org(context)), "fehler": row.last_error}
            for row in notifications
        ]
    # Sichtbarkeit (eigen oder freigegeben) ist über get_closure_for_org geprüft; Dokumente gehören der Besitzer-Org.
    documents = (
        context.db.query(RoadClosureDocument)
        .execution_options(include_all_tenants=True)
        .filter(RoadClosureDocument.road_closure_id == closure.id)
        .order_by(RoadClosureDocument.created_at.desc())
        .all()
    )
    base_url = settings.effective_public_base_url.rstrip("/")
    document_items = []
    for document in documents:
        token, expires = erstelle_download_token(
            context.org_id, context.user.id, document.id, None, art="strassensperre"
        )
        document_items.append(
            {
                "id": document.id,
                "dateiname": document.filename,
                "seiten": document.page_count,
                "groesse_bytes": document.size_bytes,
                "hochgeladen_am": _iso(document.created_at, _org(context)),
                "download_url": f"{base_url}/api/mcp/downloads/{token}",
                "gueltig_bis": expires.isoformat() + "Z",
            }
        )
    return result | {"dokumente": document_items, "zusammenfassung": _summary([result])}


def _document_sync(
    org_id: int,
    user_id: int,
    closure_id: int | None,
    filename: str,
    data: bytes | None,
    upload_id: str | None,
    purpose: str,
) -> dict[str, object]:
    """PDF lesen und optional anhängen; pypdf läuft in einem Worker mit eigener Session."""
    db = SessionLocal()
    set_tenant_context(db, org_id)
    upload_path: Path | None = None
    try:
        closure = None
        if closure_id is not None:
            closure = road_closure_service.get_closure_for_org(db, org_id, closure_id, writable=True)
        if upload_id:
            upload = lade_upload_fuer_uebergabe(db, org_id, user_id, upload_id, zweck=purpose)
            if closure is not None and upload.road_closure_id != closure.id:
                raise ValueError("Upload gehört nicht zu dieser Straßensperre.")
            upload_path = Path(settings.OBJEKT_MEDIA_DIR) / str(upload.pfad)
            data = upload_path.read_bytes()
            upload.uebergeben_am = datetime.now(UTC).replace(tzinfo=None)
        if data is None:
            raise ValueError("Keine PDF-Daten erhalten.")
        if closure is not None:
            document = store_document(db, closure, data, filename, user_id, "mcp")
            pages, text = document.page_count or 0, document.extracted_text or ""
        else:
            if not data.startswith(b"%PDF"):
                raise ValueError("Nur PDF-Dateien erlaubt.")
            pages, text = extract_text(data)
            document = None
        db.commit()
        if upload_path is not None:
            upload_path.unlink(missing_ok=True)
        response: dict[str, object] = {
            "seiten": pages,
            "textauszug": text[:4000],
            "entwurf": entwurf_aus_text(text),
        }
        if document is not None:
            response["dokument_id"] = document.id
        return response
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@register_tool(
    name="strassensperre_dokument_upload_vorbereiten",
    description=(
        "Bereitet einen kurzlebigen PDF-Upload für eine Verordnung vor. Mit road_closure_id zum Anhängen "
        "(danach strassensperre_dokument_uebergeben), ohne für einen Entwurf (strassensperre_entwurf_aus_pdf)."
    ),
    required_roles=("objekt_verwalter",),
    module_check=strassensperren_effective_enabled,
)
async def strassensperre_dokument_upload_vorbereiten(
    context: MCPContext,
    road_closure_id: int | None = None,
    dateiname: str = "verordnung.pdf",
    groesse_bytes: int | None = None,
) -> dict[str, object]:
    if road_closure_id is not None:
        _writable_closure(context, road_closure_id)
    purpose = "strassensperre" if road_closure_id is not None else "entwurf"
    limit = effektives_limit(context.db)
    row, token = erstelle_upload(
        context.db,
        context.user,
        context.org_id,
        None,
        dateiname,
        groesse_bytes,
        road_closure_id=road_closure_id,
        zweck=purpose,
    )
    write_audit(
        context.db,
        "road_closure.mcp_upload_vorbereitet",
        org_id=context.org_id,
        user_id=context.user.id,
        entity_type="road_closure",
        entity_id=road_closure_id,
        payload={"upload_id": row.upload_id, "zweck": purpose},
    )
    context.db.commit()
    url = f"{settings.effective_public_base_url.rstrip('/')}/api/mcp/uploads/{row.upload_id}"
    return {
        "upload_id": row.upload_id,
        "upload_url": url,
        "upload_token": token,
        "gueltig_bis": row.expires_at.isoformat() + "Z",
        "max_bytes": limit,
        "curl_beispiel": f'curl -X POST -H "Authorization: Bearer {token}" -F "datei=@<pfad>" {url}',
    }


@register_tool(
    name="strassensperre_dokument_uebergeben",
    description=(
        "Hängt eine PDF-Verordnung an eine eigene Straßensperre an. Genau eines von inhalt_base64 oder upload_id. "
        "Liefert Textauszug und einen ungeprüften Felder-Entwurf."
    ),
    required_roles=("objekt_verwalter",),
    module_check=strassensperren_effective_enabled,
)
async def strassensperre_dokument_uebergeben(
    context: MCPContext,
    road_closure_id: int,
    dateiname: str,
    inhalt_base64: str | None = None,
    upload_id: str | None = None,
) -> dict[str, object]:
    if (inhalt_base64 is None) == (upload_id is None):
        raise ValueError("Genau eines von inhalt_base64 oder upload_id ist erforderlich.")
    data = _decode_inhalt(inhalt_base64) if inhalt_base64 is not None else None
    result = await asyncio.to_thread(
        _document_sync, context.org_id, context.user.id, road_closure_id, dateiname, data, upload_id, "strassensperre"
    )
    return result | {"zusammenfassung": "Verordnung wurde an die Straßensperre angehängt."}


@register_tool(
    name="strassensperre_entwurf_aus_pdf",
    description=(
        "Liest eine PDF-Verordnung, ohne etwas zu speichern, und liefert Textauszug, Felder-Entwurf, "
        "Geometrievorschlag und ähnliche bestehende Sperren. Danach Felder prüfen, strassensperre_anlegen "
        "und strassensperre_dokument_uebergeben aufrufen."
    ),
    required_roles=("objekt_verwalter",),
    module_check=strassensperren_effective_enabled,
)
async def strassensperre_entwurf_aus_pdf(
    context: MCPContext,
    inhalt_base64: str | None = None,
    upload_id: str | None = None,
    city: str = "",
) -> dict[str, object]:
    if (inhalt_base64 is None) == (upload_id is None):
        raise ValueError("Genau eines von inhalt_base64 oder upload_id ist erforderlich.")
    data = _decode_inhalt(inhalt_base64) if inhalt_base64 is not None else None
    result = await asyncio.to_thread(
        _document_sync, context.org_id, context.user.id, None, "verordnung.pdf", data, upload_id, "entwurf"
    )
    draft = result["entwurf"]
    assert isinstance(draft, dict)
    streets = [str(item) for item in draft.get("strassen") or []]
    org = _org(context)
    geometry = None
    related: list[dict[str, object]] = []
    if streets:
        try:
            section = await road_closure_section_service.resolve_section(
                context.db, org, streets[0], "", "", city or getattr(org, "city", None)
            )
            geometry = section.to_dict()
        except Exception:
            geometry = None
        try:
            starts = _datetime_input(str(draft["valid_from"]), org, "valid_from") if draft.get("valid_from") else None
            ends = _datetime_input(str(draft["valid_until"]), org, "valid_until") if draft.get("valid_until") else None
            matches = road_closure_service.find_related(
                context.db,
                context.org_id,
                street=streets[0],
                reference_number=str(draft.get("reference_number") or ""),
                from_text=None,
                to_text=None,
                valid_from=starts or datetime.now(UTC).replace(tzinfo=None),
                valid_until=ends,
                geometry=None,
            )
            related = [_related_dict(item, org) for item in matches]
        except Exception:
            related = []
    return result | {
        "geometrie_vorschlag": geometry,
        "verwandte_sperren": related,
        "hinweise": [draft.get("hinweis")],
        "zusammenfassung": (
            "Entwurf erstellt. Felder prüfen, dann strassensperre_anlegen und anschließend "
            "strassensperre_dokument_uebergeben verwenden."
        ),
    }


@register_tool(
    name="strassensperren_kataloge",
    description="Liefert erlaubte Werte und deutschsprachige Bezeichnungen für Straßensperren.",
    required_roles=STRASSENSPERREN_LESE_ROLLEN,
    module_check=strassensperren_effective_enabled,
)
async def strassensperren_kataloge(context: MCPContext) -> dict[str, object]:
    def items(values: dict[str, str], aliases: dict[str, str] | None = None) -> list[dict[str, object]]:
        return [
            {
                "wert": key,
                "label": label,
                "aliase": sorted(alias for alias, target in (aliases or {}).items() if target == key),
            }
            for key, label in values.items()
        ]

    return {
        "restriction_types": items(RESTRICTION_TYPES, road_closure_service.RESTRICTION_TYPE_ALIASES),
        "prioritaeten": items(PRIORITIES, road_closure_service.PRIORITY_ALIASES),
        "richtungen": items(DIRECTIONS, road_closure_service.DIRECTION_ALIASES),
        "geometry_status": items(GEOMETRY_STATUS),
        "geometry_quality": items(GEOMETRY_QUALITY),
        "status": items(CLOSURE_STATUS),
        "editierbare_felder": sorted(road_closure_service.EDITABLE_FIELDS | {"visible_for_org_ids"}),
        "zusammenfassung": (
            "Kataloge für Einschränkungstypen, Prioritäten, Richtungen, Geometrie und editierbare Felder."
        ),
    }


@register_tool(
    name="strassensperre_anlegen",
    description=(
        "Legt eine Straßensperre der eigenen Organisation an (road_closures_create). "
        "Eine harte Löschung per MCP ist nicht möglich."
    ),
    required_roles=("objekt_verwalter",),
    module_check=strassensperren_effective_enabled,
)
async def strassensperre_anlegen(
    context: MCPContext,
    title: str,
    valid_from: str,
    restriction_type: str,
    street: str = "",
    from_text: str = "",
    to_text: str = "",
    valid_until: str = "",
    description: str = "",
    direction: str = "",
    priority: str = "normal",
    max_weight_t: float | None = None,
    max_height_m: float | None = None,
    max_width_m: float | None = None,
    max_length_m: float | None = None,
    source: str = "",
    source_url: str = "",
    city: str = "",
    reference_number: str = "",
    exceptions: str = "",
    authority: str = "",
    reason: str = "",
    teams_melden: bool | None = None,
    geometry_geojson: dict | str | None = None,
    visible_for_org_ids: list[int] | None = None,
    duplikat_bestaetigt: bool = False,
    als_neu_bestaetigt: bool = False,
    ersetzt_road_closure_id: int | None = None,
) -> dict[str, object]:
    try:
        org = _org(context)
        geometry = _geometry(geometry_geojson)
        starts = _datetime_input(valid_from, org, "valid_from")
        ends = _datetime_input(valid_until, org, "valid_until") if valid_until else None
        if not street.strip() and geometry is None:
            raise ValueError("Mindestens Straße oder Geometrie ist erforderlich.")
        related = road_closure_service.find_related(
            context.db,
            context.org_id,
            street=street,
            reference_number=reference_number,
            from_text=from_text,
            to_text=to_text,
            valid_from=starts,
            valid_until=ends,
            geometry=geometry,
            title=title,
            description=description,
        )
        if related and not (als_neu_bestaetigt or duplikat_bestaetigt) and ersetzt_road_closure_id is None:
            candidates = [_related_dict(item, org) for item in related]
            first = related[0].closure
            relation = related[0].beziehung.replace("aenderung", "Änderung").replace("verlaengerung", "Verlängerung")
            return {
                "status": "possible_update",
                "existing_road_closure_id": first.id,
                "kandidaten": candidates,
                "zusammenfassung": (
                    f"Wahrscheinlich {relation} von #{first.id} ({first.title}) - mit "
                    f"strassensperre_aktualisieren(road_closure_id={first.id}, felder=...) übernehmen, mit "
                    f"ersetzt_road_closure_id={first.id} als neue Verordnung anlegen oder mit "
                    "als_neu_bestaetigt=true trotzdem neu anlegen."
                ),
            }
        old = _writable_closure(context, ersetzt_road_closure_id) if ersetzt_road_closure_id is not None else None
        hints: list[str] = []
        geometry_status = "ok" if geometry is not None else "missing"
        geometry_quality: str | None = "manuell" if geometry is not None else None
        geometry_meta: str | None = None
        section = None
        if geometry is None and street.strip():
            section = await road_closure_section_service.resolve_section(
                context.db, org, street, from_text, to_text, city.strip() or getattr(org, "city", None)
            )
            geometry, geometry_status, geometry_quality = section.geometry, section.geometry_status, section.quality
            geometry_meta = road_closure_section_service.section_meta(section, "osm")
            hints.extend(section.hinweise)
        data = {
            "title": title,
            "street": street,
            "from_text": from_text,
            "to_text": to_text,
            "valid_from": starts,
            "valid_until": ends,
            "restriction_type": restriction_type,
            "description": description,
            "city": city,
            "reference_number": reference_number,
            "exceptions": exceptions,
            "authority": authority,
            "reason": reason,
            "direction": direction or None,
            "priority": priority,
            "max_weight_t": max_weight_t,
            "max_height_m": max_height_m,
            "max_width_m": max_width_m,
            "max_length_m": max_length_m,
            "source": source,
            "source_url": source_url,
            "geometry_geojson": geometry,
            "geometry_status": geometry_status,
            "geometry_quality": geometry_quality,
            "geometry_meta_json": geometry_meta,
        }
        if teams_melden is not None:
            data["teams_melden"] = teams_melden
        closure = road_closure_service.create_closure(
            context.db,
            context.org_id,
            context.user.id,
            data,
            source="mcp",
            mcp_tool="strassensperre_anlegen",
        )
        if visible_for_org_ids is not None:
            road_closure_service.set_shares(
                context.db,
                closure,
                visible_for_org_ids,
                context.user.id,
                source="mcp",
                mcp_tool="strassensperre_anlegen",
            )
        if old is not None:
            road_closure_service.supersede_closure(
                context.db,
                old,
                closure,
                context.user.id,
                "mcp",
                mcp_tool="strassensperre_anlegen",
            )
        if closure.restriction_type == "closed" and geometry_status != "ok":
            hints.append(
                "Wird als Warnung angezeigt, aber erst nach strassensperre_geometrie_bestaetigen (oder Prüfung in "
                "der Web-Oberfläche) bei der Umfahrung berücksichtigt."
            )
        context.db.commit()
        result = _closure_dict(closure, context.org_id, voll=True, db=context.db)
        try:
            validation = (
                road_closure_section_service.validation_from_section(section, street)
                if section is not None
                else await road_closure_section_service.validate_address(
                    context.db, org, street, from_text, to_text, city.strip() or getattr(org, "city", None)
                )
            )
        except Exception:
            validation = {"status": "unbekannt"}
        response: dict[str, object] = {
            "status": "created",
            "strassensperre": result,
            "hinweise": hints,
            "geometrie": {
                "qualitaet": closure.geometry_quality,
                "geometry_status": closure.geometry_status,
                "osm_strassenname": section.osm_name if section else None,
                "laenge_m": section.length_m if section else None,
                "endpunkte": section.endpoints if section else [],
                "mehrdeutigkeiten": section.mehrdeutigkeiten if section else [],
                "wird_umfahren": closure.restriction_type == "closed" and closure.geometry_status == "ok",
            },
            "adressvalidierung": validation,
            "zusammenfassung": f"Straßensperre {closure.title} wurde angelegt.",
        }
        if old is not None:
            response["ersetzt"] = old.id
        return response
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="strassensperre_aktualisieren",
    description=(
        "Aktualisiert angegebene Felder einer eigenen Straßensperre (road_closures_update). geometry_geojson setzt den "
        "Geometriestatus bewusst auf ok. Eine harte Löschung per MCP ist nicht möglich."
    ),
    required_roles=("objekt_verwalter",),
    module_check=strassensperren_effective_enabled,
)
async def strassensperre_aktualisieren(
    context: MCPContext, road_closure_id: int, felder: dict, version: int | None = None
) -> dict[str, object]:
    try:
        allowed = road_closure_service.EDITABLE_FIELDS - {
            "geometry_status", "geometry_quality", "geometry_meta_json"
        } | {"visible_for_org_ids"}
        unknown = set(felder) - allowed
        if unknown:
            raise ValueError("Unbekannte Felder: " + ", ".join(sorted(unknown)) + ".")
        closure = _writable_closure(context, road_closure_id)
        if version is not None and version != closure.version:
            raise ValueError(
                f"Die Sperre wurde inzwischen geändert. Aktuelle Version: {closure.version}. Bitte laden Sie sie neu."
            )
        org = _org(context)
        changes = dict(felder)
        shares = changes.pop("visible_for_org_ids", None)
        for field in ("valid_from", "valid_until"):
            if field in changes:
                value = changes[field]
                changes[field] = None if field == "valid_until" and not value else _datetime_input(value, org, field)
        if "geometry_geojson" in changes:
            changes["geometry_geojson"] = _geometry(changes["geometry_geojson"])
            changes["geometry_status"] = "ok" if changes["geometry_geojson"] is not None else "missing"
            changes["geometry_quality"] = "manuell" if changes["geometry_geojson"] is not None else None
        changed = road_closure_service.update_closure(
            context.db,
            closure,
            context.user.id,
            changes,
            expected_version=version,
            source="mcp",
            mcp_tool="strassensperre_aktualisieren",
        )
        if shares is not None:
            if not isinstance(shares, list) or not all(isinstance(item, int) for item in shares):
                raise ValueError("visible_for_org_ids muss eine Liste von Organisations-IDs sein.")
            added, removed = road_closure_service.set_shares(
                context.db,
                closure,
                shares,
                context.user.id,
                source="mcp",
                mcp_tool="strassensperre_aktualisieren",
            )
            if added or removed:
                changed.append({"feld": "visible_for_org_ids", "vorher": removed, "nachher": added})
        context.db.commit()
        result: dict[str, object] = {
            "strassensperre": _closure_dict(closure, context.org_id, voll=True, db=context.db),
            "geaenderte_felder": changed,
            "zusammenfassung": f"Straßensperre {closure.title} wurde aktualisiert.",
        }
        changed_fields = {item["feld"] for item in changed}
        if {"street", "from_text", "to_text", "city"} & changed_fields:
            try:
                result["adressvalidierung"] = await road_closure_section_service.validate_address(
                    context.db,
                    org,
                    closure.street or "",
                    closure.from_text or "",
                    closure.to_text or "",
                    closure.city or getattr(org, "city", None),
                )
            except Exception:
                result["adressvalidierung"] = {"status": "unbekannt"}
        return result
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="strassensperre_geometrie_ermitteln",
    description="Ermittelt einen OSM-Straßenabschnitt; hoch kann anschließend bestätigt werden.",
    required_roles=("objekt_verwalter",), module_check=strassensperren_effective_enabled,
)
async def strassensperre_geometrie_ermitteln(
    context: MCPContext, road_closure_id: int | None = None, street: str = "", von: str = "", bis: str = "",
    city: str = "", uebernehmen: bool = False,
) -> dict[str, object]:
    try:
        closure = _writable_closure(context, road_closure_id) if road_closure_id is not None else None
        if uebernehmen and closure is None:
            raise ValueError("uebernehmen erfordert road_closure_id.")
        street = street or (closure.street if closure else "") or ""
        von = von or (closure.from_text if closure else "") or ""
        bis = bis or (closure.to_text if closure else "") or ""
        city = city or (closure.city if closure else "") or ""
        if not street.strip():
            raise ValueError("street ist erforderlich.")
        result = await road_closure_section_service.resolve_section(context.db, _org(context), street, von, bis, city)
        if uebernehmen:
            assert closure is not None
            if result.geometry is None:
                raise ValueError("Keine Geometrie ermittelt.")
            road_closure_service.update_closure(context.db, closure, context.user.id, {
                "geometry_geojson": result.geometry, "geometry_status": result.geometry_status,
                "geometry_quality": result.quality,
                "geometry_meta_json": road_closure_section_service.section_meta(result, "osm"),
            }, source="mcp", mcp_tool="strassensperre_geometrie_ermitteln")
            context.db.commit()
        return {"geometry_geojson": result.geometry, "qualitaet": result.quality,
                "geometry_status_vorschlag": result.geometry_status, "osm_strassenname": result.osm_name,
                "laenge_m": result.length_m, "endpunkte": result.endpoints,
                "mehrdeutigkeiten": result.mehrdeutigkeiten, "hinweise": result.hinweise,
                "zusammenfassung": "Abschnitt ermittelt." if result.geometry else "Kein Abschnitt ermittelt."}
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="strassensperre_geometrie_bestaetigen",
    description="Bestätigt eine ermittelte Geometrie für die Umfahrungsberechnung.",
    required_roles=("objekt_verwalter",), module_check=strassensperren_effective_enabled,
)
async def strassensperre_geometrie_bestaetigen(
    context: MCPContext, road_closure_id: int, geometry_geojson: dict | str | None = None, version: int | None = None,
) -> dict[str, object]:
    try:
        closure = _writable_closure(context, road_closure_id)
        road_closure_service.confirm_geometry(
            context.db, closure, context.user.id, _geometry(geometry_geojson), version,
            source="mcp", mcp_tool="strassensperre_geometrie_bestaetigen"
        )
        context.db.commit()
        return {"strassensperre": _closure_dict(closure, context.org_id, voll=True, db=context.db),
                "zusammenfassung": "Geometrie bestätigt."}
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="strassensperre_deaktivieren",
    description="Deaktiviert eine eigene Straßensperre. Eine harte Löschung per MCP ist nicht möglich.",
    required_roles=("objekt_verwalter",),
    module_check=strassensperren_effective_enabled,
)
async def strassensperre_deaktivieren(
    context: MCPContext, road_closure_id: int, grund: str
) -> dict[str, object]:
    try:
        if not grund.strip():
            raise ValueError("grund ist erforderlich.")
        closure = _writable_closure(context, road_closure_id)
        road_closure_service.deactivate_closure(
            context.db, closure, context.user.id, grund.strip(), source="mcp", mcp_tool="strassensperre_deaktivieren"
        )
        context.db.commit()
        return {
            "strassensperre": _closure_dict(closure, context.org_id, voll=True, db=context.db),
            "hinweis": "Mit strassensperre_reaktivieren rückgängig machbar.",
            "zusammenfassung": f"Straßensperre {closure.title} wurde deaktiviert.",
        }
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="strassensperre_reaktivieren",
    description="Reaktiviert eine eigene deaktivierte Straßensperre. Eine harte Löschung per MCP ist nicht möglich.",
    required_roles=("objekt_verwalter",),
    module_check=strassensperren_effective_enabled,
)
async def strassensperre_reaktivieren(context: MCPContext, road_closure_id: int) -> dict[str, object]:
    try:
        closure = _writable_closure(context, road_closure_id)
        road_closure_service.reactivate_closure(
            context.db, closure, context.user.id, source="mcp", mcp_tool="strassensperre_reaktivieren"
        )
        context.db.commit()
        return {
            "strassensperre": _closure_dict(closure, context.org_id, voll=True, db=context.db),
            "zusammenfassung": f"Straßensperre {closure.title} wurde reaktiviert.",
        }
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="strassensperre_beenden",
    description="Beendet eine eigene Straßensperre vorzeitig (road_closures_close).",
    required_roles=("objekt_verwalter",),
    module_check=strassensperren_effective_enabled,
)
async def strassensperre_beenden(
    context: MCPContext, road_closure_id: int, grund: str, ende: str = ""
) -> dict[str, object]:
    try:
        if not grund.strip():
            raise ValueError("grund ist erforderlich.")
        closure = _writable_closure(context, road_closure_id)
        if closure.cancelled_at is not None:
            raise ValueError("Eine deaktivierte Straßensperre kann nicht beendet werden.")
        finish = _date(ende, end=True, org=_org(context)) if ende else datetime.now(UTC).replace(tzinfo=None)
        assert finish is not None
        if finish < closure.valid_from:
            raise ValueError("ende darf nicht vor valid_from liegen.")
        if closure.valid_until is not None and finish > closure.valid_until:
            raise ValueError("Zum Verlängern strassensperre_aktualisieren verwenden.")
        road_closure_service.update_closure(
            context.db, closure, context.user.id, {"valid_until": finish}, expected_version=closure.version,
            source="mcp", mcp_tool="strassensperre_beenden",
        )
        context.db.commit()
        return {
            "strassensperre": _closure_dict(closure, context.org_id, voll=True, db=context.db),
            "grund": grund.strip(),
            "hinweis": "Zum Aufheben ohne Zeitraum strassensperre_deaktivieren verwenden.",
            "zusammenfassung": f"Straßensperre {closure.title} endet vorzeitig.",
        }
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="strassensperre_teams_senden",
    description="Stellt eine manuelle Teams-Meldung in die Warteschlange (road_closures_publish).",
    required_roles=("objekt_verwalter",),
    module_check=strassensperren_effective_enabled,
)
async def strassensperre_teams_senden(context: MCPContext, road_closure_id: int) -> dict[str, object]:
    try:
        closure = _writable_closure(context, road_closure_id)
        config = (
            context.db.query(RoadClosureTeamsConfig)
            .filter(RoadClosureTeamsConfig.org_id == context.org_id)
            .first()
        )
        if not config or not config.enabled or not config.webhook_url_enc:
            raise ValueError("Teams-Meldungen sind für diese Organisation nicht eingerichtet.")
        notification = road_closure_notify_service.enqueue(
            context.db, closure, "manuell", manuell=True, source="mcp", user_id=context.user.id
        )
        if notification is None:
            raise ValueError("Teams-Meldungen sind für diese Organisation nicht eingerichtet.")
        context.db.commit()
        return {
            "benachrichtigung_id": notification.id,
            "status": notification.status,
            "ereignis": notification.ereignis,
            "hinweis": (
                "Versand erfolgt asynchron über die Teams-Warteschlange "
                "(Status mit strassensperre_lesen prüfen)."
            ),
        }
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="strassensperre_freigabelink",
    description="Verwaltet den öffentlichen Freigabelink einer eigenen Sperre (road_closures_share_link).",
    required_roles=("objekt_verwalter",),
    module_check=strassensperren_effective_enabled,
)
async def strassensperre_freigabelink(
    context: MCPContext, road_closure_id: int, aktion: str = "abrufen", gueltig_bis: str = ""
) -> dict[str, object]:
    try:
        if aktion not in {"abrufen", "erzeugen", "widerrufen"}:
            raise ValueError("aktion muss abrufen, erzeugen oder widerrufen sein.")
        closure = _writable_closure(context, road_closure_id)
        if aktion == "erzeugen" and closure.cancelled_at is not None:
            raise ValueError("Für eine deaktivierte Straßensperre kann kein Freigabelink erzeugt werden.")
        expiry = local_date_to_utc(gueltig_bis, end=True, org=_org(context)) if gueltig_bis else None
        if gueltig_bis and expiry is None:
            raise ValueError("gueltig_bis muss YYYY-MM-DD sein.")
        token = road_closure_token_service.active_detail_token(context.db, closure)
        raw: str | None = None
        if aktion == "erzeugen":
            if expiry:
                if token:
                    road_closure_token_service.revoke_token(context.db, token, context.user.id)
                assert closure.org_id is not None
                token, raw = road_closure_token_service.create_token(
                    context.db, closure.org_id, "detail", context.user.id, road_closure_id=closure.id, expires_at=expiry
                )
            else:
                token, raw = road_closure_token_service.get_or_create_detail_token(context.db, closure, context.user.id)
        elif aktion == "widerrufen":
            if token is None:
                raise ValueError("Kein aktiver Freigabelink vorhanden.")
            road_closure_token_service.revoke_token(context.db, token, context.user.id)
            token = None
        elif token:
            raw = road_closure_token_service.token_plain(token)
        context.db.commit()
        return {
            "link": road_closure_token_service.public_url(raw, "detail") if raw else None,
            "gueltig_bis": _iso(token.expires_at, _org(context)) if token else None,
            "erstellt": _iso(token.created_at, _org(context)) if token else None,
            "zuletzt_genutzt": _iso(token.last_used_at, _org(context)) if token else None,
            "zusammenfassung": "Freigabelink verwaltet.",
        }
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="strassensperren_kennzahlen",
    description="Liefert Kennzahlen sichtbarer Straßensperren (road_closures_stats).",
    required_roles=STRASSENSPERREN_LESE_ROLLEN,
    module_check=strassensperren_effective_enabled,
)
async def strassensperren_kennzahlen(context: MCPContext, bereich: str = "alle") -> dict[str, object]:
    if bereich not in {"alle", "oeffentlich"}:
        raise ValueError("bereich muss alle oder oeffentlich sein.")
    metrics = road_closure_stats_service.kennzahlen(
        context.db, _org(context), scope="all" if bereich == "alle" else "public"
    )
    return metrics | {
        "zusammenfassung": (
            f"{metrics['aktiv']} aktiv, {metrics['geplant']} geplant, davon "
            f"{metrics['vollsperren_aktiv']} Vollsperren aktiv."
        )
    }


@register_tool(
    name="strassensperren_suchen",
    description="Sucht sichtbare Straßensperren unscharf.",
    required_roles=STRASSENSPERREN_LESE_ROLLEN,
    module_check=strassensperren_effective_enabled,
)
async def strassensperren_suchen(
    context: MCPContext, suchtext: str, status: str = "all", von: str = "", bis: str = "", limit: int = 20
) -> dict[str, object]:
    if status not in {"current", "active", "planned", "expired", "cancelled", "all"}:
        raise ValueError("Ungültiger Status.")
    _limit(limit)
    words = [road_closure_service._normal(word) for word in re.findall(r"[\wäß]+", suchtext.lower()) if len(word) >= 3]
    if not words:
        raise ValueError("suchtext muss mindestens ein Wort mit drei Zeichen enthalten.")
    org = _org(context)
    rows = road_closure_service.list_closures(
        context.db,
        context.org_id,
        status=None if status == "all" else status,
        von=_date(von, end=False, org=org),
        bis=_date(bis, end=True, org=org),
    )
    ranked = []
    for row in rows:
        haystack = road_closure_service._normal(
            " ".join(filter(None, [row.title, row.street, row.description, row.from_text, row.to_text]))
        )
        score = sum(word in haystack for word in words)
        if score:
            ranked.append((score, row))
    ranked.sort(key=lambda entry: (-entry[0], entry[1].valid_from))
    items = [_closure_dict(row, context.org_id, db=context.db) | {"score": score} for score, row in ranked[:limit]]
    return {"count": len(items), "items": items, "zusammenfassung": _summary(items)}


@register_tool(
    name="strassensperren_im_gebiet",
    description="Findet sichtbare Sperren nahe eines Punktes.",
    required_roles=STRASSENSPERREN_LESE_ROLLEN,
    module_check=strassensperren_effective_enabled,
)
async def strassensperren_im_gebiet(
    context: MCPContext, lat: float, lng: float, radius_m: int = 2000, status: str = "current"
) -> dict[str, object]:
    if not 50 <= radius_m <= 20000:
        raise ValueError("radius_m muss zwischen 50 und 20000 liegen.")
    if status not in {"current", "active", "planned", "expired", "cancelled", "all"}:
        raise ValueError("Ungültiger Status.")
    rows = road_closure_service.list_closures(context.db, context.org_id, status=None if status == "all" else status)
    found = []
    for row in rows:
        if not row.geometry_geojson:
            continue
        distance = round(
            road_closure_geo_service.distance_point_to_geometry_m(lat, lng, json.loads(row.geometry_geojson))
        )
        if distance <= radius_m:
            found.append((distance, row))
    found.sort(key=lambda entry: entry[0])
    items = [_closure_dict(row, context.org_id, db=context.db) | {"entfernung_m": distance} for distance, row in found]
    return {"count": len(items), "items": items, "zusammenfassung": _summary(items)}


def _incident(context: MCPContext, incident_id: int) -> Incident:
    row = (
        context.db.query(Incident)
        .execution_options(include_all_tenants=True)
        .filter(Incident.id == incident_id)
        .first()
    )
    member = (
        context.db.query(IncidentOrg)
        .filter(IncidentOrg.incident_id == incident_id, IncidentOrg.org_id == context.org_id)
        .first()
    )
    if row is None or (row.primary_org_id != context.org_id and member is None):
        raise ValueError("Einsatz nicht gefunden.")
    return row


def _route_answer(payload: dict[str, Any]) -> dict[str, object]:
    result: dict[str, object] = {
        "routing_status": payload["status"],
        "normal_route": {"distance_m": payload.get("distance_m"), "duration_s": payload.get("duration_s")},
        "closures": payload.get("closures", []),
        "alternative_route": {
            "distance_m": payload.get("alternative_distance_m"),
            "duration_s": payload.get("alternative_duration_s"),
            "streets": payload.get("alternative_streets", []),
        },
        "detour_distance_m": payload.get("detour_distance_m"),
        "detour_duration_s": payload.get("detour_duration_s"),
    }
    result["zusammenfassung"] = (
        "Die normale Anfahrt ist betroffen; eine Umfahrung ist verfügbar."
        if payload["status"] == "affected" and payload.get("alternative_status") == "ok"
        else (
            "Die normale Anfahrt ist betroffen."
            if payload["status"] == "affected"
            else "Keine Straßensperren auf der Anfahrt."
        )
    )
    return result


@register_tool(
    name="einsatz_strassensperren",
    description="Liest gespeicherte Sperren einer Einsatzroute (road_closures_route_check).",
    required_roles=STRASSENSPERREN_LESE_ROLLEN,
    module_check=strassensperren_effective_enabled,
)
async def einsatz_strassensperren(context: MCPContext, incident_id: int) -> dict[str, object]:
    incident = _incident(context, incident_id)
    payload = road_closure_incident_service.route_payload(context.db, incident, context.org_id)
    return {
        "incident_id": incident_id,
        "routing_status": payload["status"],
        "closures": payload.get("closures", []),
        "nearby_count": payload.get("nearby_count", 0),
        "zusammenfassung": "Keine Straßensperren auf der gespeicherten Anfahrt."
        if not payload.get("closures")
        else "Straßensperren auf der gespeicherten Anfahrt vorhanden.",
    }


async def _live(context: MCPContext, start: tuple[float, float], destination: tuple[float, float]) -> dict[str, object]:
    try:
        provider = einsatz_routing.get_provider()
    except RoutingError as error:
        raise ValueError(str(error)) from error
    try:
        payload = await road_closure_incident_service.evaluate_route(
            context.db, context.org_id, start, destination, provider
        )
    except RoutingError as error:
        raise ValueError(str(error)) from error
    return _route_answer(payload)


@register_tool(
    name="einsatz_anfahrtsroute_pruefen",
    description="Prüft eine Einsatz-Anfahrt oder berechnet sie live (road_closures_route_check).",
    required_roles=STRASSENSPERREN_LESE_ROLLEN,
    module_check=strassensperren_effective_enabled,
)
async def einsatz_anfahrtsroute_pruefen(
    context: MCPContext,
    incident_id: int | None = None,
    lat: float | None = None,
    lng: float | None = None,
    vehicle_id: int | None = None,
) -> dict[str, object]:
    if (incident_id is None) == (lat is None or lng is None):
        raise ValueError("Genau incident_id oder lat und lng müssen angegeben werden.")
    if incident_id is not None:
        result = _route_answer(
            road_closure_incident_service.route_payload(context.db, _incident(context, incident_id), context.org_id)
        )
    else:
        start = road_closure_incident_service.routing_start_for_org(context.db, context.org_id)
        if start is None:
            raise ValueError("Kein Routing-Startpunkt konfiguriert.")
        assert lat is not None and lng is not None
        result = await _live(context, start, (lat, lng))
    if vehicle_id is not None:
        result["hinweis"] = "Fahrzeugprofile werden noch nicht berücksichtigt."
    return result


@register_tool(
    name="strassensperren_entlang_route",
    description="Berechnet live Sperren entlang einer freien Route (road_closures_route_check).",
    required_roles=STRASSENSPERREN_LESE_ROLLEN,
    module_check=strassensperren_effective_enabled,
)
async def strassensperren_entlang_route(
    context: MCPContext,
    start_lat: float,
    start_lng: float,
    ziel_lat: float,
    ziel_lng: float,
    vehicle_id: int | None = None,
) -> dict[str, object]:
    result = await _live(context, (start_lat, start_lng), (ziel_lat, ziel_lng))
    if vehicle_id is not None:
        result["hinweis"] = "Fahrzeugprofile werden noch nicht berücksichtigt."
    return result
