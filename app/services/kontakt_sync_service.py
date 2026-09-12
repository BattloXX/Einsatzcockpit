"""Stable, tenant-bound snapshot and delta contract for offline contacts."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.models.kontakt import Kontakt, KontaktSyncAenderung
from app.models.objekt import ObjektKontakt

SCHEMA_VERSION = 1


def record_change(
    db: Session, org_id: int, entity: str, entity_id: int, operation: str, payload: dict | None = None
) -> None:
    db.add(
        KontaktSyncAenderung(
            org_id=org_id,
            entitaet=entity,
            entitaet_id=entity_id,
            operation=operation,
            payload_json=json.dumps(payload) if payload else None,
        )
    )


def contact_payload(kontakt: Kontakt) -> dict[str, Any]:
    return {
        "id": kontakt.id,
        "typ": kontakt.typ,
        "anzeigename": kontakt.anzeigename,
        "vorname": kontakt.vorname,
        "nachname": kontakt.nachname,
        "funktion": kontakt.funktion,
        "organisation": kontakt.organisation,
        "email": kontakt.email,
        "erreichbarkeit": kontakt.erreichbarkeit,
        "notizen": kontakt.notizen,
        "aktiv": kontakt.aktiv,
        "archiviert": kontakt.archiviert,
        "version": kontakt.version,
        "telefone": [
            {
                "id": p.id,
                "nummer": p.nummer,
                "label": p.label,
                "sort": p.sort,
                "bevorzugt": p.bevorzugt,
                "sms_eignung": p.sms_eignung,
            }
            for p in kontakt.telefone
        ],
    }


def mapping_payload(mapping: ObjektKontakt) -> dict[str, Any]:
    return {
        "id": mapping.id,
        "kontakt_id": mapping.kontakt_id,
        "objekt_id": mapping.objekt_id,
        "rolle": mapping.art,
        "sort": mapping.sort,
        "erreichbarkeit": mapping.erreichbarkeit,
    }


def snapshot(db: Session, org_id: int, after: int, limit: int) -> dict[str, Any]:
    contacts = (
        db.query(Kontakt)
        .options(selectinload(Kontakt.telefone))
        .filter(Kontakt.org_id == org_id, Kontakt.id > after)
        .order_by(Kontakt.id)
        .limit(limit + 1)
        .all()
    )
    has_more = len(contacts) > limit
    contacts = contacts[:limit]
    mappings = (
        db.query(ObjektKontakt).filter(ObjektKontakt.org_id == org_id, ObjektKontakt.kontakt_id.is_not(None)).all()
    )
    max_cursor = (
        db.query(KontaktSyncAenderung.id)
        .filter(KontaktSyncAenderung.org_id == org_id)
        .order_by(KontaktSyncAenderung.id.desc())
        .first()
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "snapshot",
        "cursor": max_cursor[0] if max_cursor else 0,
        "next_page": contacts[-1].id if has_more and contacts else None,
        "contacts": [contact_payload(contact) for contact in contacts],
        "mappings": [mapping_payload(item) for item in mappings] if after == 0 else [],
    }


def delta(db: Session, org_id: int, cursor: int, limit: int) -> dict[str, Any]:
    changes = (
        db.query(KontaktSyncAenderung)
        .filter(KontaktSyncAenderung.org_id == org_id, KontaktSyncAenderung.id > cursor)
        .order_by(KontaktSyncAenderung.id)
        .limit(limit + 1)
        .all()
    )
    has_more = len(changes) > limit
    changes = changes[:limit]
    events = []
    for change in changes:
        payload = json.loads(change.payload_json) if change.payload_json else None
        events.append(
            {
                "cursor": change.id,
                "entity": change.entitaet,
                "id": change.entitaet_id,
                "operation": change.operation,
                "payload": payload,
            }
        )
    next_cursor = changes[-1].id if changes else cursor
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "delta",
        "cursor": next_cursor,
        "has_more": has_more,
        "changes": events,
    }
