"""MCP-Werkzeuge fuer fertig analysierte Objekt-PDFs."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.audit import write_audit
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.objekt import OBJEKT_STATUS_ARCHIVIERT, Objekt, ObjektDokument, ObjektDokumentSeite
from app.models.user import User
from app.services.mcp_download_service import erstelle_download_token, loese_datei
from app.services.mcp_upload_service import (
    MCPUploadFehler,
    effektives_limit,
    erstelle_upload,
    lade_upload_fuer_uebergabe,
)
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


def _datei_hash_und_inhalt(pfad: Path, inline: bool) -> tuple[int, str, bytes | None]:
    """Liest fuer den Hash in Stuecken; Inline-Inhalt nur innerhalb des Limits."""
    groesse = pfad.stat().st_size
    inhalt = bytearray() if inline and groesse <= settings.MCP_DOWNLOAD_INLINE_MAX_BYTES else None
    digest = hashlib.sha256()
    with pfad.open("rb") as datei:
        while chunk := datei.read(1024 * 1024):
            digest.update(chunk)
            if inhalt is not None:
                inhalt.extend(chunk)
    return groesse, digest.hexdigest(), bytes(inhalt) if inhalt is not None else None


def _ki_klassifizierung_starten(objekt_id: int) -> None:
    """Startet den asynchronen KI-Fallback ausserhalb des MCP-Requests."""
    def laufen() -> None:
        from app.services.objekt_ki_service import analysiere_unklassifizierte_seiten

        try:
            asyncio.run(analysiere_unklassifizierte_seiten(objekt_id))
        except Exception:
            # Der KI-Fallback erzeugt nur Vorschlaege; eine fehlgeschlagene
            # Hintergrundanalyse darf die bereits erfolgreiche Uebergabe nicht aendern.
            return

    threading.Thread(target=laufen, name=f"mcp-ki-klassifizierung-{objekt_id}", daemon=True).start()


def _uebergabe_sync(
    org_id: int,
    user_id: int,
    objekt_id: int,
    dateiname: str,
    data: bytes | None,
    upload_id: str | None,
    seiten: list[dict] | None,
    ersetzt: int | None,
) -> dict[str, object]:
    """pypdf/Poppler laufen in einem Worker mit eigener Session und Tenant-Kontext."""
    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        objekt = _basis_objekt(db, org_id, objekt_id)
        user = db.get(User, user_id)
        upload_pfad: Path | None = None
        if upload_id is not None:
            upload = lade_upload_fuer_uebergabe(db, org_id, user_id, upload_id, zweck="objekt")
            assert upload.objekt_id is not None
            if upload.objekt_id != objekt.id:
                raise MCPUploadFehler("Upload gehoert nicht zu diesem Objekt.", 403)
            assert upload.pfad is not None
            upload_pfad = Path(settings.OBJEKT_MEDIA_DIR) / upload.pfad
            try:
                data = upload_pfad.read_bytes()
            except OSError as exc:
                raise MCPUploadFehler("Upload-Datei nicht gefunden.", 404) from exc
        assert data is not None
        dokument = store_dokument_bytes(data, dateiname, objekt, user, db, ersetzt_dokument_id=ersetzt)
        unklassifizierte_seiten: list[int] = []
        verarbeite_dokument_mit_analyse(
            dokument.id, seiten, db=db, user_id=user_id, unklassifizierte_seiten=unklassifizierte_seiten
        )
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
        if upload_id is not None:
            upload.uebergeben_am = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
        if upload_pfad is not None:
            upload_pfad.unlink(missing_ok=True)
        klassifizierung_quelle = "server" if unklassifizierte_seiten else "client"
        if unklassifizierte_seiten:
            from app.services.objekt_ki_service import ki_klassifikation_enabled

            if ki_klassifikation_enabled(org_id, db):
                _ki_klassifizierung_starten(objekt.id)
        return {
            "dokument_id": dokument.id,
            "seitenzahl": dokument.seitenzahl,
            "freigabe_status": dokument.freigabe_status,
            "wartet_freigabe": dokument.freigabe_status == "wartet_freigabe",
            "ui_link": f"{settings.effective_public_base_url.rstrip('/')}/objekte/{objekt.id}?tab=dokumente",
            "hinweis": "Bei Entwurf sofort aktiv."
            if objekt.status == "entwurf"
            else "wartet auf Freigabe im Einsatzcockpit (Objekt > Dokumente)",
            "klassifizierung_quelle": klassifizierung_quelle,
            "unklassifizierte_seiten": unklassifizierte_seiten,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@register_tool(
    name="objekt_dokument_uebergeben",
    description=(
        "Uebergibt ein PDF an ein Objekt. Genau eines von inhalt_base64 oder upload_id angeben. "
        "Fuer grosse Dateien zuerst objekt_dokument_upload_vorbereiten aufrufen, die Datei mit dem dort "
        "gelieferten curl-Beispiel hochladen und danach upload_id uebergeben. seiten ist optional und hat "
        "das Format [{\"nr\":1,\"dokumentart\":\"bma_datenblatt\",\"titel\":null}]; optional pro Seite: "
        "volltext, melderlinien, stand (YYYY-MM-DD), bei_einsatz_drucken. Fehlende Seiten werden serverseitig "
        "zur KI-Vorschlagsklassifizierung vorgemerkt. Korrekturen erfolgen mit "
        "objekt_dokument_seiten_klassifizieren."
    ),
    required_roles=("objekt_verwalter",),
    module_check=objekt_modul_aktiv,
)
async def objekt_dokument_uebergeben(
    context: MCPContext,
    objekt_id: int,
    dateiname: str,
    inhalt_base64: str | None = None,
    upload_id: str | None = None,
    seiten: list[dict] | None = None,
    ersetzt_dokument_id: int | None = None,
) -> dict[str, object]:
    if (inhalt_base64 is None) == (upload_id is None):
        raise ValueError("Genau eines von inhalt_base64 oder upload_id ist erforderlich.")
    data = _decode_inhalt(inhalt_base64) if inhalt_base64 is not None else None
    return await asyncio.to_thread(
        _uebergabe_sync,
        context.org_id,
        context.user.id,
        objekt_id,
        dateiname,
        data,
        upload_id,
        seiten,
        ersetzt_dokument_id,
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


_DOWNLOAD_BESCHREIBUNG = (
    "Liefert einen kurzlebigen Download-Link fuer ein Objektdokument. dokument_id kommt aus "
    "objekt_dokumente_auflisten; optional seite fuer eine Einzelseite. Der Link ist ca. 15 Minuten "
    "gueltig und im Browser klickbar. inline=True nur fuer kleine Dateien verwenden."
)


@register_tool(
    name="objekt_dokument_herunterladen",
    description=_DOWNLOAD_BESCHREIBUNG,
    required_roles=("objekt_verwalter",),
    module_check=objekt_modul_aktiv,
)
async def objekt_dokument_herunterladen(
    context: MCPContext, dokument_id: int, seite: int | None = None, inline: bool = False
) -> dict[str, object]:
    dokument, pfad, dateiname, mime = loese_datei(context.db, context.org_id, dokument_id, seite)
    groesse, sha256, inhalt = await asyncio.to_thread(_datei_hash_und_inhalt, pfad, inline)
    token, gueltig_bis = erstelle_download_token(context.org_id, context.user.id, dokument.id, seite)
    url = f"{settings.effective_public_base_url.rstrip('/')}/api/mcp/downloads/{token}"
    write_audit(
        context.db,
        "objekt.mcp_download_vorbereitet",
        org_id=context.org_id,
        user_id=context.user.id,
        entity_type="objekt_dokument",
        entity_id=dokument.id,
        payload={"objekt_id": dokument.objekt_id, "seite": seite},
    )
    context.db.commit()
    ergebnis: dict[str, object] = {
        "dokument_id": dokument.id,
        "objekt_id": dokument.objekt_id,
        "dateiname": dateiname,
        "versionsnummer": dokument.versionsnummer,
        "ist_aktuelle_version": dokument.ist_aktuelle_version,
        "freigabe_status": dokument.freigabe_status,
        "seitenzahl": dokument.seitenzahl,
        "seite": seite,
        "mime": mime,
        "groesse_bytes": groesse,
        "sha256": sha256,
        "download_url": url,
        "gueltig_bis": gueltig_bis.isoformat() + "Z",
        "curl_beispiel": f'curl -o "{dateiname}" "{url}"',
    }
    if inline:
        if inhalt is not None:
            ergebnis["inhalt_base64"] = base64.b64encode(inhalt).decode("ascii")
        else:
            ergebnis["hinweis"] = (
                f"Datei zu gross fuer inline ({groesse} Bytes, max. {settings.MCP_DOWNLOAD_INLINE_MAX_BYTES}), "
                "bitte download_url verwenden."
            )
    return ergebnis


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
                "groesse_bytes": d.groesse_bytes,
                "mime": d.mime,
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
