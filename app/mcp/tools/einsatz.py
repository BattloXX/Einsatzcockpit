"""Privacy-safe, read-only MCP access to visible incidents."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, or_
from sqlalchemy.orm import joinedload, selectinload

from app.core.timezones import local_date_to_utc
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.incident import (
    Incident,
    IncidentColumn,
    IncidentCommLog,
    IncidentLog,
    IncidentOrg,
    IncidentVehicle,
    Message,
    Task,
)
from app.models.master import FireDept, VehicleMaster
from app.models.objekt import (
    OBJEKT_EINSATZ_BESTAETIGT,
    OBJEKT_EINSATZ_VORSCHLAG,
    Objekt,
    ObjektDokument,
    ObjektEinsatz,
)
from app.services.einsatz_mcp_service import (
    _PERSONEN_ENTITY_TYPES,
    EINSATZ_LESE_ROLLEN,
    json_value,
    message_dict,
    objekt_einsatz_payload,
    sichtbarer_einsatz,
    task_dict,
    ui_link,
)
from app.services.objekt_service import objekt_effective_enabled


def _org(context: MCPContext) -> FireDept:
    org = context.db.get(FireDept, context.org_id)
    if org is None:
        raise ValueError("Organisation nicht gefunden.")
    return org


def _visible_query(context: MCPContext):
    collab = context.db.query(IncidentOrg.incident_id).filter(IncidentOrg.org_id == context.org_id)
    return (
        context.db.query(Incident)
        .execution_options(include_all_tenants=True)
        .filter(or_(Incident.primary_org_id == context.org_id, Incident.id.in_(collab)))
    )


@register_tool(
    name="einsaetze_liste",
    description="Listet sichtbare Einsätze ohne Personendaten.",
    required_roles=EINSATZ_LESE_ROLLEN,
)
async def einsaetze_liste(
    context: MCPContext,
    status: str = "aktiv",
    von: str = "",
    bis: str = "",
    suchtext: str = "",
    uebung: bool | None = None,
    limit: int = 25,
) -> dict[str, object]:
    if status not in {"aktiv", "abgeschlossen", "alle"}:
        raise ValueError("Ungültiger Status.")
    if not 1 <= limit <= 100:
        raise ValueError("limit muss zwischen 1 und 100 liegen.")
    org = _org(context)
    query = _visible_query(context).options(
        selectinload(Incident.vehicles),
        selectinload(Incident.tasks).joinedload(Task.column),
        selectinload(Incident.messages).joinedload(Message.column),
    )
    if status == "aktiv":
        query = query.filter(Incident.status == "active")
    elif status == "abgeschlossen":
        query = query.filter(Incident.status != "active")
    if von:
        value = local_date_to_utc(von, org=org)
        if value is None:
            raise ValueError("von muss ein Datum im Format YYYY-MM-DD sein.")
        query = query.filter(Incident.started_at >= value)
    if bis:
        value = local_date_to_utc(bis, end=True, org=org)
        if value is None:
            raise ValueError("bis muss ein Datum im Format YYYY-MM-DD sein.")
        query = query.filter(Incident.started_at <= value)
    if suchtext:
        term = f"%{suchtext.strip()}%"
        query = query.filter(
            or_(
                Incident.address_street.ilike(term),
                Incident.address_city.ilike(term),
                Incident.reason.ilike(term),
                Incident.dibos_diagnose.ilike(term),
                Incident.nummer.cast(String).ilike(term),
            )
        )
    if uebung is not None:
        query = query.filter(Incident.is_exercise == uebung)
    items = []
    for incident in query.order_by(Incident.started_at.desc(), Incident.id.desc()).limit(limit):
        items.append(
            json_value(
                {
                    "id": incident.id,
                    "nummer": incident.nummer,
                    "alarmstufe": incident.alarm_type_code,
                    "stichwort": incident.reason,
                    "adresse": incident.address_street,
                    "ort": incident.address_city,
                    "status": incident.status,
                    "begonnen": incident.started_at,
                    "abgeschlossen": incident.closed_at,
                    "uebung": incident.is_exercise,
                    "rolle": "eigen" if incident.primary_org_id == context.org_id else "kollaborierend",
                    "anzahl_einheiten": sum(row.removed_at is None for row in incident.vehicles),
                    "offene_auftraege": sum(
                        not row.is_done
                        and not row.is_cancelled
                        and (row.column is None or row.column.column_kind != "rescued")
                        for row in incident.tasks
                    ),
                    "offene_meldungen": sum(
                        not row.is_done
                        and not row.is_cancelled
                        and (row.column is None or row.column.column_kind != "rescued")
                        for row in incident.messages
                    ),
                    "ui_link": ui_link(incident.id),
                }
            )
        )
    return {"count": len(items), "items": items}


@register_tool(
    name="einsatz_lesen",
    description="Liest Stammdaten eines sichtbaren Einsatzes ohne Personendaten.",
    required_roles=EINSATZ_LESE_ROLLEN,
)
async def einsatz_lesen(context: MCPContext, incident_id: int) -> dict[str, object]:
    incident = sichtbarer_einsatz(context.db, context.org_id, incident_id)
    incident = (
        context.db.query(Incident)
        .options(
            selectinload(Incident.columns),
            selectinload(Incident.wache_status_entries),
            selectinload(Incident.collaborating_orgs),
            selectinload(Incident.vehicles),
            selectinload(Incident.tasks).joinedload(Task.column),
            selectinload(Incident.messages).joinedload(Message.column),
        )
        .filter(Incident.id == incident.id)
        .one()
    )
    org_ids = [incident.primary_org_id] + [row.org_id for row in incident.collaborating_orgs]
    orgs = {
        row.id: row
        for row in context.db.query(FireDept)
        .execution_options(include_all_tenants=True)
        .filter(FireDept.id.in_([item for item in org_ids if item]))
        .all()
    }
    try:
        hints = json.loads(incident.ai_lage_hints or "[]")
        hints = hints if isinstance(hints, list) else []
    except json.JSONDecodeError:
        hints = []
    return json_value(
        {
            "id": incident.id,
            "nummer": incident.nummer,
            "alarmstufe": incident.alarm_type_code,
            "status": incident.status,
            "begonnen": incident.started_at,
            "uebernommen": incident.taken_over_at,
            "ausgerueckt": incident.departed_at,
            "am_einsatzort": incident.on_scene_at,
            "wieder_einsatzbereit": incident.ready_again_at,
            "abgeschlossen": incident.closed_at,
            "reason": incident.reason,
            "report_text": incident.report_text,
            "dibos": {
                "tycod": incident.dibos_tycod,
                "diagnose": incident.dibos_diagnose,
                "bma_no": incident.dibos_bma_no,
                "event_comment": incident.dibos_event_comment,
            },
            "lis_operation_number": incident.lis_operation_number,
            "lat": incident.lat,
            "lng": incident.lng,
            "adresse": incident.address_street,
            "hausnummer": incident.address_no,
            "ort": incident.address_city,
            "uebung": incident.is_exercise,
            "einsatzleiter_name": incident.incident_leader_name,
            "beteiligte_orgs": [
                {"name": orgs[row.org_id].name if row.org_id in orgs else None, "rolle": row.role}
                for row in incident.collaborating_orgs
            ]
            + (
                []
                if incident.primary_org_id in {row.org_id for row in incident.collaborating_orgs}
                else [
                    {
                        "name": orgs[incident.primary_org_id].name if incident.primary_org_id in orgs else None,
                        "rolle": "leader",
                    }
                ]
            ),
            "abschnitte": [
                {
                    "id": row.id,
                    "titel": row.title,
                    "art": row.column_kind,
                    "abschnittsleiter_name": row.section_leader_name,
                }
                for row in incident.columns
                if row.column_kind != "rescued"
            ],
            "wachenstatus": [
                {
                    "id": row.id,
                    "unid": row.wache_unid,
                    "name": row.wache_name,
                    "status": row.status,
                    "status_text": row.status_text_raw,
                    "zeit": row.status_at,
                }
                for row in incident.wache_status_entries
            ],
            "lagehinweise": hints,
            "zaehler": {
                "einheiten": sum(row.removed_at is None for row in incident.vehicles),
                "offene_auftraege": sum(
                    not row.is_done
                    and not row.is_cancelled
                    and (row.column is None or row.column.column_kind != "rescued")
                    for row in incident.tasks
                ),
                "offene_meldungen": sum(
                    not row.is_done
                    and not row.is_cancelled
                    and (row.column is None or row.column.column_kind != "rescued")
                    for row in incident.messages
                ),
            },
            "ui_link": ui_link(incident.id),
        }
    )


@register_tool(
    name="einsatz_einheiten",
    description="Listet Einheiten eines sichtbaren Einsatzes ohne Mannschaftsnamen.",
    required_roles=EINSATZ_LESE_ROLLEN,
)
async def einsatz_einheiten(
    context: MCPContext, incident_id: int, inklusive_abgezogene: bool = False
) -> dict[str, object]:
    sichtbarer_einsatz(context.db, context.org_id, incident_id)
    rows = (
        context.db.query(IncidentVehicle)
        .options(
            joinedload(IncidentVehicle.vehicle_master).joinedload(VehicleMaster.dept),
            joinedload(IncidentVehicle.column),
            selectinload(IncidentVehicle.assigned_tasks).joinedload(Task.column),
            selectinload(IncidentVehicle.assigned_messages).joinedload(Message.column),
        )
        .filter(IncidentVehicle.incident_id == incident_id)
    )
    if not inklusive_abgezogene:
        rows = rows.filter(IncidentVehicle.removed_at.is_(None))
    units = []
    for row in rows.order_by(IncidentVehicle.display_order, IncidentVehicle.id):
        master = row.master
        units.append(
            json_value(
                {
                    "id": row.id,
                    "fahrzeug": {
                        "name": master.name if master else None,
                        "funkrufname": master.funkrufname if master else None,
                        "rufname": master.code if master else None,
                        "typ": master.type if master else None,
                        "org_kuerzel": master.dept.short_code if master and master.dept else None,
                    },
                    "abschnitt": {"id": row.column.id, "titel": row.column.title},
                    "status": row.unit_status,
                    "hinzugefuegt": row.created_at,
                    "abgezogen": row.removed_at,
                    "km_gefahren": row.km_gefahren,
                    "kommandant_gesetzt": bool(row.commander_member_id or row.commander_name),
                    "anzahl_auftraege": sum(
                        not item.is_done
                        and not item.is_cancelled
                        and (item.column is None or item.column.column_kind != "rescued")
                        for item in row.assigned_tasks
                    ),
                    "anzahl_meldungen": sum(
                        not item.is_done
                        and not item.is_cancelled
                        and (item.column is None or item.column.column_kind != "rescued")
                        for item in row.assigned_messages
                    ),
                }
            )
        )
    incident = (
        context.db.query(Incident)
        .options(selectinload(Incident.wache_status_entries))
        .filter(Incident.id == incident_id)
        .one()
    )
    return {
        "einheiten": units,
        "alarmierte_wachen": json_value(
            [
                {"unid": row.wache_unid, "name": row.wache_name, "status": row.status, "zeit": row.status_at}
                for row in incident.wache_status_entries
            ]
        ),
        "ui_link": ui_link(incident_id),
    }


async def _items(
    context: MCPContext, incident_id: int, status: str, einheit_id: int | None, abschnitt_id: int | None, model: Any
) -> dict[str, object]:
    if status not in {"alle", "offen", "erledigt", "storniert"}:
        raise ValueError("Ungültiger Status.")
    sichtbarer_einsatz(context.db, context.org_id, incident_id)
    media_rel = model.media
    query = (
        context.db.query(model)
        .options(
            selectinload(media_rel),
            joinedload(model.vehicle).joinedload(IncidentVehicle.vehicle_master),
            joinedload(model.column),
            joinedload(Message.objekt_gefahr) if model is Message else joinedload(model.column),
        )
        .filter(model.incident_id == incident_id)
        .filter(or_(model.column_id.is_(None), ~model.column.has(IncidentColumn.column_kind == "rescued")))
    )
    if einheit_id is not None:
        query = query.filter(model.vehicle_id == einheit_id)
    if abschnitt_id is not None:
        query = query.filter(model.column_id == abschnitt_id)
    if status == "offen":
        query = query.filter(model.is_done.is_(False), model.is_cancelled.is_(False))
    elif status == "erledigt":
        query = query.filter(model.is_done.is_(True))
    elif status == "storniert":
        query = query.filter(model.is_cancelled.is_(True))
    serializer = task_dict if model is Task else message_dict
    return {
        "count": query.count(),
        "items": [serializer(row) for row in query.order_by(model.created_at.desc(), model.id.desc())],
        "ui_link": ui_link(incident_id),
    }


@register_tool(
    name="einsatz_auftraege",
    description="Listet Aufträge eines sichtbaren Einsatzes ohne Personendaten.",
    required_roles=EINSATZ_LESE_ROLLEN,
)
async def einsatz_auftraege(
    context: MCPContext,
    incident_id: int,
    status: str = "alle",
    einheit_id: int | None = None,
    abschnitt_id: int | None = None,
) -> dict[str, object]:
    return await _items(context, incident_id, status, einheit_id, abschnitt_id, Task)


@register_tool(
    name="einsatz_meldungen",
    description="Listet Meldungen eines sichtbaren Einsatzes ohne Personendaten.",
    required_roles=EINSATZ_LESE_ROLLEN,
)
async def einsatz_meldungen(
    context: MCPContext,
    incident_id: int,
    status: str = "alle",
    einheit_id: int | None = None,
    abschnitt_id: int | None = None,
) -> dict[str, object]:
    return await _items(context, incident_id, status, einheit_id, abschnitt_id, Message)


def _seit_utc(seit: str) -> datetime | None:
    if not seit:
        return None
    try:
        value = datetime.fromisoformat(seit.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("seit muss ein ISO-Zeitpunkt sein.") from exc
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _verlauf_eintraege(
    context: MCPContext,
    incident_id: int,
    quellen: list[str] | None = None,
    seit: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    erlaubte_quellen = {"tagebuch", "funkjournal"}
    if quellen is None:
        quellen = ["tagebuch", "funkjournal"]
    if not set(quellen) <= erlaubte_quellen:
        raise ValueError("Ungültige Quelle.")
    if not 1 <= limit <= 500:
        raise ValueError("limit muss zwischen 1 und 500 liegen.")
    sichtbarer_einsatz(context.db, context.org_id, incident_id)
    seit_zeit = _seit_utc(seit)
    eintraege: list[dict[str, Any]] = []
    if "tagebuch" in quellen:
        query = context.db.query(IncidentLog).filter(IncidentLog.incident_id == incident_id)
        if seit_zeit is not None:
            query = query.filter(IncidentLog.ts >= seit_zeit)
        for row in query.all():
            if (row.entity_type or "").lower() in _PERSONEN_ENTITY_TYPES:
                continue
            eintraege.append(
                {
                    "quelle": "tagebuch",
                    "zeit": row.ts,
                    "level": row.level,
                    "text": row.text,
                    "bezug": {"typ": row.entity_type, "id": row.entity_id},
                }
            )
    if "funkjournal" in quellen:
        comm_query = context.db.query(IncidentCommLog).filter(IncidentCommLog.incident_id == incident_id)
        if seit_zeit is not None:
            comm_query = comm_query.filter(IncidentCommLog.ts >= seit_zeit)
        eintraege.extend(
            {
                "quelle": "funkjournal",
                "zeit": row.ts,
                "richtung": row.direction,
                "kanal": row.channel,
                "partner": row.partner,
                "text": row.message,
                "anforderung": row.is_request,
                "lagerelevant": row.is_lage_relevant,
                "erledigt": row.handled,
            }
            for row in comm_query.all()
        )
    eintraege.sort(key=lambda row: row["zeit"])
    return eintraege[-limit:]


@register_tool(
    name="einsatz_verlauf",
    description="Liest Einsatz-Tagebuch und Funkjournal; nur lesend und ohne Personendaten.",
    required_roles=EINSATZ_LESE_ROLLEN,
)
async def einsatz_verlauf(
    context: MCPContext,
    incident_id: int,
    quellen: list[str] | None = None,
    seit: str = "",
    limit: int = 200,
) -> dict[str, object]:
    return {
        "eintraege": json_value(_verlauf_eintraege(context, incident_id, quellen, seit, limit)),
        "ui_link": ui_link(incident_id),
    }


def _objekt_optionen():
    return (
        selectinload(ObjektEinsatz.objekt),
        selectinload(ObjektEinsatz.objekt).selectinload(Objekt.bma),
        selectinload(ObjektEinsatz.objekt).selectinload(Objekt.kontakte),
        selectinload(ObjektEinsatz.objekt).selectinload(Objekt.gefahren),
        selectinload(ObjektEinsatz.objekt).selectinload(Objekt.merkmale),
        selectinload(ObjektEinsatz.objekt).selectinload(Objekt.zusatzadressen),
        selectinload(ObjektEinsatz.objekt).selectinload(Objekt.wohnanlage),
    )


def _objekte(context: MCPContext, incident_id: int, inklusive_vorschlaege: bool) -> list[dict[str, Any]]:
    sichtbarer_einsatz(context.db, context.org_id, incident_id)
    if not objekt_effective_enabled(context.org_id, context.db):
        return []
    query = (
        context.db.query(ObjektEinsatz)
        .options(*_objekt_optionen())
        .filter(
            ObjektEinsatz.incident_id == incident_id,
            ObjektEinsatz.status.in_([OBJEKT_EINSATZ_BESTAETIGT, OBJEKT_EINSATZ_VORSCHLAG]),
        )
    )
    if not inklusive_vorschlaege:
        query = query.filter(ObjektEinsatz.status == OBJEKT_EINSATZ_BESTAETIGT)
    verknuepfungen = query.order_by(ObjektEinsatz.erstellt_am, ObjektEinsatz.id).all()
    objekt_ids = [row.objekt_id for row in verknuepfungen]
    dokumente_nach_objekt: dict[int, list[ObjektDokument]] = {}
    if objekt_ids:
        for dokument in (
            context.db.query(ObjektDokument)
            .options(selectinload(ObjektDokument.seiten))
            .filter(
                ObjektDokument.objekt_id.in_(objekt_ids),
                ObjektDokument.ist_aktuelle_version.is_(True),
                ObjektDokument.freigabe_status == "freigegeben",
            )
            .all()
        ):
            dokumente_nach_objekt.setdefault(dokument.objekt_id, []).append(dokument)
    result = []
    for verknuepfung in verknuepfungen:
        objekt = verknuepfung.objekt
        if objekt is None or objekt.entwurf_von_id is not None:
            continue
        payload = objekt_einsatz_payload(context.db, objekt)
        payload["verknuepfung"] = {
            "quelle": verknuepfung.quelle,
            "status": verknuepfung.status,
            "distanz_m": verknuepfung.distanz_m,
            "erstellt": verknuepfung.erstellt_am,
        }
        payload["dokumente"] = [
            {
                "id": dokument.id,
                "dateiname": dokument.dateiname_original,
                "seitenzahl": dokument.seitenzahl,
                "mime": dokument.mime,
                "groesse_bytes": dokument.groesse_bytes,
                "seiten": [
                    {
                        "nr": seite.seiten_nr,
                        "dokumentart": seite.dokumentart,
                        "titel": seite.titel,
                        "bei_einsatz_drucken": seite.bei_einsatz_drucken,
                    }
                    for seite in dokument.seiten
                ],
            }
            for dokument in dokumente_nach_objekt.get(objekt.id, [])
        ]
        result.append(json_value(payload))
    return result


@register_tool(
    name="einsatz_objekte",
    description="Liest verknüpfte Einsatz-Objekte; nur lesend und ohne Personendaten.",
    required_roles=EINSATZ_LESE_ROLLEN,
)
async def einsatz_objekte(
    context: MCPContext, incident_id: int, inklusive_vorschlaege: bool = True
) -> dict[str, object]:
    sichtbarer_einsatz(context.db, context.org_id, incident_id)
    if not objekt_effective_enabled(context.org_id, context.db):
        return {"objekte": [], "hinweis": "Objektmodul nicht aktiv.", "ui_link": ui_link(incident_id)}
    return {"objekte": _objekte(context, incident_id, inklusive_vorschlaege), "ui_link": ui_link(incident_id)}


@register_tool(
    name="einsatz_lagebild",
    description="Liest ein kompaktes Einsatz-Lagebild; nur lesend und ohne Personendaten.",
    required_roles=EINSATZ_LESE_ROLLEN,
)
async def einsatz_lagebild(context: MCPContext, incident_id: int) -> dict[str, object]:
    incident = sichtbarer_einsatz(context.db, context.org_id, incident_id)
    einheiten = await einsatz_einheiten(context, incident_id)
    auftraege = await _items(context, incident_id, "offen", None, None, Task)
    meldungen = await _items(context, incident_id, "offen", None, None, Message)
    objekte = _objekte(context, incident_id, True) if objekt_effective_enabled(context.org_id, context.db) else []
    for item in auftraege["items"] + meldungen["items"]:  # type: ignore[operator]
        item["medien_anzahl"] = len(item.pop("medien"))
    return json_value(
        {
            "einsatz": {
                "id": incident.id,
                "nummer": incident.nummer,
                "alarmstufe": incident.alarm_type_code,
                "status": incident.status,
                "begonnen": incident.started_at,
                "reason": incident.reason,
                "adresse": incident.address_street,
                "ort": incident.address_city,
                "einsatzleiter_name": incident.incident_leader_name,
            },
            "einheiten": einheiten["einheiten"],
            "offene_auftraege": auftraege["items"],
            "offene_meldungen": meldungen["items"],
            "objekte": [
                {
                    "id": objekt["id"],
                    "name": objekt["name"],
                    "adresse": objekt["adresse_zeile"],
                    "verknuepfungsstatus": objekt["verknuepfung"]["status"],
                    "gefahren": [gefahr["name"] for gefahr in objekt["gefahren"]],
                    "bma_vorhanden": objekt["bma"] is not None,
                }
                for objekt in objekte
            ],
            "letzte_eintraege": _verlauf_eintraege(context, incident_id, limit=20),
            "ui_link": ui_link(incident_id),
        }
    )
