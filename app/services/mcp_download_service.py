"""Kurzlebige, signierte Downloads fuer MCP-Objektdokumente."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.mcp.context import MCPPermissionError, load_live_context
from app.models.objekt import ObjektDokument, ObjektDokumentSeite
from app.services.objekt_dokument_service import absolute_pfad
from app.services.objekt_service import objekt_effective_enabled

_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="mcp-download")


class MCPDownloadFehler(ValueError):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def erstelle_download_token(
    org_id: int, user_id: int, dokument_id: int, seite: int | None
) -> tuple[str, datetime]:
    gueltig_bis = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=settings.MCP_DOWNLOAD_TOKEN_MINUTEN)
    token = _signer.dumps({"o": org_id, "u": user_id, "d": dokument_id, "s": seite})
    return token, gueltig_bis


def loese_datei(
    db: Session, org_id: int, dokument_id: int, seite: int | None
) -> tuple[ObjektDokument, Path, str, str]:
    dokument = (
        db.query(ObjektDokument)
        .filter(ObjektDokument.id == dokument_id, ObjektDokument.org_id == org_id)
        .first()
    )
    if dokument is None:
        raise MCPDownloadFehler("Dokument nicht gefunden.", 404)

    if seite is None:
        pfad = absolute_pfad(dokument.pfad)
        dateiname = dokument.dateiname_original
        mime = dokument.mime or "application/pdf"
    else:
        dokument_seite = (
            db.query(ObjektDokumentSeite)
            .filter(
                ObjektDokumentSeite.dokument_id == dokument.id,
                ObjektDokumentSeite.seiten_nr == seite,
            )
            .first()
        )
        if dokument_seite is None or not dokument_seite.einzel_pdf_pfad:
            raise MCPDownloadFehler("Keine Einzelseite vorhanden.", 404)
        pfad = absolute_pfad(dokument_seite.einzel_pdf_pfad)
        dateiname = f"{Path(dokument.dateiname_original).stem}_seite_{seite:04d}.pdf"
        mime = "application/pdf"

    if not pfad.is_file():
        raise MCPDownloadFehler("Datei fehlt.", 404)
    return dokument, pfad, dateiname, mime


def lade_download(db: Session, token: str) -> tuple[Path, str, str]:
    try:
        daten = _signer.loads(token, max_age=settings.MCP_DOWNLOAD_TOKEN_MINUTEN * 60)
    except SignatureExpired as exc:
        raise MCPDownloadFehler("Download-Link ist abgelaufen.", 410) from exc
    except BadSignature as exc:
        raise MCPDownloadFehler("Download nicht gefunden.", 404) from exc

    try:
        org_id = int(daten["o"])
        user_id = int(daten["u"])
        dokument_id = int(daten["d"])
        seite = daten["s"]
        if seite is not None:
            seite = int(seite)
    except (KeyError, TypeError, ValueError) as exc:
        raise MCPDownloadFehler("Download nicht gefunden.", 404) from exc

    try:
        load_live_context(db, user_id, org_id, ("objekt_verwalter",))
    except MCPPermissionError as exc:
        raise MCPDownloadFehler(str(exc), 403) from exc
    if not objekt_effective_enabled(org_id, db):
        raise MCPDownloadFehler("Das Objektmodul ist fuer diese Organisation nicht aktiviert.", 403)

    _, pfad, dateiname, mime = loese_datei(db, org_id, dokument_id, seite)
    return pfad, dateiname, mime
