"""MCP-Werkzeuge fuer fertig analysierte Objekt-PDFs."""

from __future__ import annotations

import asyncio
import base64
import binascii
from typing import Any

from app.config import settings
from app.core.audit import write_audit
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.objekt import OBJEKT_STATUS_ARCHIVIERT, Objekt, ObjektDokument, ObjektDokumentSeite
from app.models.user import User
from app.services.mcp_upload_service import effektives_limit, erstelle_upload
from app.services.objekt_dokument_service import (
    klassifiziere_seiten,
    store_dokument_bytes,
    verarbeite_dokument_mit_analyse,
)
from app.services.objekt_service import objekt_effective_enabled, write_objekt_change


def objekt_modul_aktiv(org_id: int, db: object) -> bool:
    return objekt_effective_enabled(org_id, db)  # type: ignore[arg-type]


def _basis_objekt(db: Any, org_id: int, objekt_id: int) -> Objekt:
    objekt = db.query(Objekt).filter(Objekt.id == objekt_id, Objekt.org_id == org_id).first()
    if objekt is None:
        raise ValueError("Objekt nicht gefunden.")
    if objekt.entwurf_von_id:
        objekt = db.query(Objekt).filter(Objekt.id == objekt.entwurf_von_id, Objekt.org_id == org_id).first()
        if objekt is None:
            raise ValueError("Basisobjekt nicht gefunden.")
    if objekt.status == OBJEKT_STATUS_ARCHIVIERT:
        raise ValueError("Archivierte Objekte koennen keine Dokumente erhalten.")
    return objekt


def _decode_inhalt(inhalt_base64: str) -> bytes:
    if not isinstance(inhalt_base64, str):
        raise ValueError("inhalt_base64 muss ein Base64-Text sein.")
    # Base64 ist maximal 4/3 so gross wie das Ergebnis. Die Vorabpruefung
    # verhindert, dass ein unbeschraenkt grosser Request dekodiert wird.
    maximum = ((settings.MCP_MAX_UPLOAD_BYTES + 2) // 3) * 4
    if len(inhalt_base64) > maximum:
        raise ValueError(f"Datei zu gross (MCP-Limit {settings.MCP_MAX_UPLOAD_BYTES} Bytes).")
    try:
        data = base64.b64decode(inhalt_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("inhalt_base64 ist ungueltig.") from exc
    if len(data) > settings.MCP_MAX_UPLOAD_BYTES:
        raise ValueError(f"Datei zu gross (MCP-Limit {settings.MCP_MAX_UPLOAD_BYTES} Bytes).")
    return data


def _uebergabe_sync(
    org_id: int, user_id: int, objekt_id: int, dateiname: str, data: bytes, seiten: list[dict], ersetzt: int | None
) -> dict[str, object]:
    """pypdf/Poppler laufen in einem Worker mit eigener Session und Tenant-Kontext."""
    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        objekt = _basis_objekt(db, org_id, objekt_id)
        user = db.get(User, user_id)
        dokument = store_dokument_bytes(data, dateiname, objekt, user, db, ersetzt_dokument_id=ersetzt)
        verarbeite_dokument_mit_analyse(dokument.id, seiten, db=db, user_id=user_id)
        write_objekt_change(
            db,
            objekt.id,
            org_id,
            "dokumente",
            "mcp_uebergeben",
            None,
            {"dokument_id": dokument.id, "seiten": dokument.seitenzahl},
            user_id=user_id,
            quelle="mcp",
        )
        write_audit(
            db,
            "objekt.mcp_dokument_uebergeben",
            org_id=org_id,
            user_id=user_id,
            entity_type="objekt_dokument",
            entity_id=dokument.id,
            payload={"objekt_id": objekt.id},
        )
        db.commit()
        return {
            "dokument_id": dokument.id,
            "seitenzahl": dokument.seitenzahl,
            "freigabe_status": dokument.freigabe_status,
            "wartet_freigabe": dokument.freigabe_status == "wartet_freigabe",
            "ui_link": f"{settings.effective_public_base_url.rstrip('/')}/objekte/{objekt.id}?tab=dokumente",
            "hinweis": "Bei Entwurf sofort aktiv."
            if objekt.status == "entwurf"
            else "wartet auf Freigabe im Einsatzcockpit (Objekt > Dokumente)",
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@register_tool(
    name="objekt_dokument_uebergeben",
    description="Uebergibt ein fertig analysiertes PDF an ein Objekt.",
    required_roles=("objekt_verwalter",),
    module_check=objekt_modul_aktiv,
)
async def objekt_dokument_uebergeben(
    context: MCPContext,
    objekt_id: int,
    dateiname: str,
    inhalt_base64: str,
    seiten: list[dict],
    ersetzt_dokument_id: int | None = None,
) -> dict[str, object]:
    data = _decode_inhalt(inhalt_base64)
    return await asyncio.to_thread(
        _uebergabe_sync, context.org_id, context.user.id, objekt_id, dateiname, data, seiten, ersetzt_dokument_id
    )


@register_tool(
    name="objekt_dokument_upload_vorbereiten",
    description="Bereitet einen kurzlebigen Bearer-Upload fuer ein Objekt-PDF vor.",
    required_roles=("objekt_verwalter",),
    module_check=objekt_modul_aktiv,
)
async def objekt_dokument_upload_vorbereiten(
    context: MCPContext, objekt_id: int, dateiname: str, groesse_bytes: int | None = None
) -> dict[str, object]:
    objekt = _basis_objekt(context.db, context.org_id, objekt_id)
    limit = effektives_limit(context.db)
    if groesse_bytes is not None and groesse_bytes > limit:
        raise ValueError(f"Datei zu gross (max. {limit} Bytes).")
    row, token = erstelle_upload(
        context.db, context.user, context.org_id, objekt.id, dateiname, groesse_bytes
    )
    write_audit(
        context.db,
        "objekt.mcp_upload_vorbereitet",
        org_id=context.org_id,
        user_id=context.user.id,
        entity_type="objekt",
        entity_id=objekt.id,
        payload={"upload_id": row.upload_id, "dateiname": row.dateiname},
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
    name="objekt_dokumente_auflisten",
    description="Listet Dokumente und Seiten eines Objekts.",
    required_roles=("objekt_verwalter",),
    module_check=objekt_modul_aktiv,
)
async def objekt_dokumente_auflisten(context: MCPContext, objekt_id: int) -> dict[str, object]:
    objekt = _basis_objekt(context.db, context.org_id, objekt_id)
    dokumente = (
        context.db.query(ObjektDokument)
        .filter(ObjektDokument.org_id == context.org_id, ObjektDokument.objekt_id == objekt.id)
        .order_by(ObjektDokument.id)
        .all()
    )
    return {
        "objekt_id": objekt.id,
        "dokumente": [
            {
                "id": d.id,
                "dateiname": d.dateiname_original,
                "versionsnummer": d.versionsnummer,
                "freigabe_status": d.freigabe_status,
                "ist_aktuelle_version": d.ist_aktuelle_version,
                "seitenzahl": d.seitenzahl,
                "seiten": [{"nr": s.seiten_nr, "dokumentart": s.dokumentart, "titel": s.titel} for s in d.seiten],
            }
            for d in dokumente
        ],
    }


@register_tool(
    name="objekt_dokument_seiten_klassifizieren",
    description="Korrigiert die Klassifizierung von Dokumentseiten.",
    required_roles=("objekt_verwalter",),
    module_check=objekt_modul_aktiv,
)
async def objekt_dokument_seiten_klassifizieren(
    context: MCPContext, dokument_id: int, seiten: list[dict]
) -> dict[str, object]:
    dokument = (
        context.db.query(ObjektDokument)
        .filter(ObjektDokument.id == dokument_id, ObjektDokument.org_id == context.org_id)
        .first()
    )
    if dokument is None:
        raise ValueError("Dokument nicht gefunden.")
    _basis_objekt(context.db, context.org_id, dokument.objekt_id)
    if not seiten:
        raise ValueError("Mindestens eine Seite ist erforderlich.")
    bearbeitet = 0
    for eintrag in seiten:
        nr = eintrag.get("seite") if isinstance(eintrag, dict) else None
        if not isinstance(nr, int):
            raise ValueError("Jede Seite braucht eine gueltige Seitennummer.")
        seite = (
            context.db.query(ObjektDokumentSeite)
            .filter(ObjektDokumentSeite.dokument_id == dokument.id, ObjektDokumentSeite.seiten_nr == nr)
            .first()
        )
        if seite is None:
            raise ValueError(f"Seite {nr} nicht gefunden.")
        daten = dict(eintrag)
        daten.pop("seite", None)
        if "volltext" in daten:
            seite.volltext = (
                " ".join(str(daten.pop("volltext") or "").split())[: settings.OBJEKT_VOLLTEXT_MAX_CHARS] or None
            )
            seite.text_quelle = "mcp"
        bearbeitet += klassifiziere_seiten(context.db, [seite], daten, context.user.id)
    write_objekt_change(
        context.db,
        dokument.objekt_id,
        context.org_id,
        "dokumente",
        "mcp_seiten_klassifiziert",
        None,
        {"seiten": bearbeitet},
        user_id=context.user.id,
        quelle="mcp",
    )
    context.db.commit()
    return {"dokument_id": dokument.id, "seiten_klassifiziert": bearbeitet, "freigabe_status": dokument.freigabe_status}
