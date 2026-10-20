"""Kurzlebige, signierte Downloads fuer MCP-Objektdokumente."""
from __future__ import annotations

import mimetypes
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.core.permissions import STRASSENSPERREN_LESE_ROLLEN
from app.mcp.context import MCPPermissionError, load_live_context
from app.mcp.tools.gsl import READ
from app.models.incident import IncidentColumn, Message, MessageMedia, TaskMedia
from app.models.major_incident import (
    CitizenReport,
    CrossMarkerMedia,
    CrossSiteMarker,
    LageJournalEntry,
    LageJournalMedia,
    MajorIncident,
    SiteMedia,
)
from app.models.objekt import Objekt, ObjektDokument, ObjektDokumentSeite, ObjektEinsatz
from app.models.road_closure import RoadClosure, RoadClosureDocument
from app.services import lage_media_service, road_closure_service
from app.services.einsatz_mcp_service import EINSATZ_LESE_ROLLEN, sichtbarer_einsatz
from app.services.media_service import absolute_path
from app.services.mi_feature_service import get_mi_features
from app.services.objekt_dokument_service import absolute_pfad
from app.services.objekt_service import objekt_effective_enabled
from app.services.road_closure_document_service import absolute_path as road_closure_document_path
from app.services.road_closure_flags import strassensperren_effective_enabled

_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="mcp-download")


class MCPDownloadFehler(ValueError):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def erstelle_download_token(
    org_id: int,
    user_id: int,
    dokument_id: int,
    seite: int | None,
    art: str = "objekt",
    incident_id: int | None = None,
    medium_art: str | None = None,
) -> tuple[str, datetime]:
    gueltig_bis = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=settings.MCP_DOWNLOAD_TOKEN_MINUTEN)
    token = _signer.dumps(
        {"o": org_id, "u": user_id, "d": dokument_id, "s": seite, "a": art, "i": incident_id, "m": medium_art}
    )
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
        art = daten.get("a", "objekt")
        incident_id = daten.get("i")
        medium_art = daten.get("m")
        if seite is not None:
            seite = int(seite)
    except (KeyError, TypeError, ValueError) as exc:
        raise MCPDownloadFehler("Download nicht gefunden.", 404) from exc

    if art == "katplan_quelle":
        from app.core.permissions import KATPLAN_LESE_ROLLEN
        from app.models.katplan import KatplanDokument
        from app.services.katplan_flags import katplan_effective_enabled
        from app.services.katplan_mcp_service import plan_laden
        from app.services.katplan_quelle_service import absolute_pfad as quelle_pfad
        from app.services.katplan_quelle_service import quelle_der_seite, sichtbare_quellen

        try:
            context = load_live_context(db, user_id, org_id, KATPLAN_LESE_ROLLEN)
        except MCPPermissionError as exc:
            raise MCPDownloadFehler(str(exc), 403) from exc
        if not katplan_effective_enabled(org_id, db):
            raise MCPDownloadFehler("Katastrophenplan-Modul nicht aktiviert.", 403)
        quelle = db.query(KatplanDokument).filter_by(org_id=org_id, id=dokument_id).one_or_none()
        if quelle is None:
            raise MCPDownloadFehler("Dokument nicht gefunden.", 404)
        try:
            plan = plan_laden(db, context.user, quelle.plan_id)
        except LookupError as exc:
            raise MCPDownloadFehler("Dokument nicht gefunden.", 404) from exc
        if quelle.id not in {d.id for d in sichtbare_quellen(db, plan, context.user)}:
            raise MCPDownloadFehler("Dokument nicht gefunden.", 404)
        relativ = quelle.pfad
        name = quelle.dateiname_original
        if seite is not None:
            quellseite = quelle_der_seite(db, plan, quelle.id, seite)
            if quellseite is None or not quellseite.einzel_pdf_pfad:
                raise MCPDownloadFehler("Seite nicht gefunden.", 404)
            relativ = quellseite.einzel_pdf_pfad
            name = f"{Path(name).stem}_seite_{seite:04d}.pdf"
        pfad = quelle_pfad(relativ)
        if not pfad.is_file():
            raise MCPDownloadFehler("Datei fehlt.", 404)
        return pfad, name, "application/pdf"
    if art == "strassensperre":
        try:
            load_live_context(db, user_id, org_id, STRASSENSPERREN_LESE_ROLLEN)
        except MCPPermissionError as exc:
            raise MCPDownloadFehler(str(exc), 403) from exc
        if not strassensperren_effective_enabled(org_id, db):
            raise MCPDownloadFehler("Das Strassensperrenmodul ist fuer diese Organisation nicht aktiviert.", 403)
        document = db.query(RoadClosureDocument).execution_options(include_all_tenants=True).filter(
            RoadClosureDocument.id == dokument_id
        ).first()
        if document is None or road_closure_service.visible_closures_q(db, org_id).filter(
            RoadClosure.id == document.road_closure_id
        ).first() is None:
            raise MCPDownloadFehler("Dokument nicht gefunden.", 404)
        path = road_closure_document_path(document)
        if not path.is_file():
            raise MCPDownloadFehler("Datei fehlt.", 404)
        return path, document.filename, "application/pdf"
    if art == "einsatz_objekt":
        try:
            load_live_context(db, user_id, org_id, EINSATZ_LESE_ROLLEN)
        except MCPPermissionError as exc:
            raise MCPDownloadFehler(str(exc), 403) from exc
        if not objekt_effective_enabled(org_id, db):
            raise MCPDownloadFehler("Das Objektmodul ist fuer diese Organisation nicht aktiviert.", 403)
        try:
            incident = sichtbarer_einsatz(db, org_id, int(incident_id))
        except (TypeError, ValueError) as exc:
            raise MCPDownloadFehler("Dokument nicht gefunden.", 404) from exc
        dokument, pfad, dateiname, mime = loese_datei(db, org_id, dokument_id, seite)
        verknuepfung = (
            db.query(ObjektEinsatz)
            .join(Objekt, Objekt.id == ObjektEinsatz.objekt_id)
            .filter(
                ObjektEinsatz.incident_id == incident.id,
                ObjektEinsatz.objekt_id == dokument.objekt_id,
                Objekt.entwurf_von_id.is_(None),
            )
            .first()
        )
        if (
            verknuepfung is None
            or not dokument.ist_aktuelle_version
            or dokument.freigabe_status != "freigegeben"
        ):
            raise MCPDownloadFehler("Dokument nicht gefunden.", 404)
        return pfad, dateiname, mime
    if art == "einsatz_medium":
        try:
            load_live_context(db, user_id, org_id, EINSATZ_LESE_ROLLEN)
        except MCPPermissionError as exc:
            raise MCPDownloadFehler(str(exc), 403) from exc
        try:
            incident = sichtbarer_einsatz(db, org_id, int(incident_id))
        except (TypeError, ValueError) as exc:
            raise MCPDownloadFehler("Medium nicht gefunden.", 404) from exc
        medium: TaskMedia | MessageMedia | None
        if medium_art == "auftrag":
            medium = db.query(TaskMedia).filter(TaskMedia.id == dokument_id).first()
            column_id = medium.task.column_id if medium and medium.task else None
        elif medium_art == "meldung":
            medium = db.query(MessageMedia).filter(MessageMedia.id == dokument_id).first()
            message = db.get(Message, medium.message_id) if medium else None
            column_id = message.column_id if message else None
        else:
            raise MCPDownloadFehler("Medium nicht gefunden.", 404)
        if medium is None or medium.incident_id != incident.id:
            raise MCPDownloadFehler("Medium nicht gefunden.", 404)
        column = db.get(IncidentColumn, column_id) if column_id is not None else None
        if column is not None and column.column_kind == "rescued":
            raise MCPDownloadFehler("Medium nicht gefunden.", 404)
        path = absolute_path(cast(TaskMedia, medium))
        if not path.is_file():
            raise MCPDownloadFehler("Datei fehlt.", 404)
        mime = "application/octet-stream" if medium.kind == "datei" else medium.mime_type
        return path, medium.original_filename, mime
    if art == "gsl_medium":
        try:
            load_live_context(db, user_id, org_id, READ)
            lage = db.get(MajorIncident, int(incident_id))
        except (MCPPermissionError, TypeError, ValueError) as exc:
            raise MCPDownloadFehler(str(exc), 403) from exc
        if lage is None or lage.org_id != org_id:
            raise MCPDownloadFehler("Medium nicht gefunden.", 404)
        features = get_mi_features(db, org_id)
        gsl_medium: Any
        if medium_art == "stelle":
            gsl_medium = db.get(SiteMedia, dokument_id)
            ok = gsl_medium is not None and gsl_medium.incident_site_id in {s.id for s in lage.sites}
            path = lage_media_service.site_media_path(gsl_medium) if ok else Path()
        elif medium_art == "journal":
            gsl_medium = db.get(LageJournalMedia, dokument_id)
            entry = db.get(LageJournalEntry, gsl_medium.journal_entry_id) if gsl_medium else None
            ok = entry is not None and entry.major_incident_id == lage.id
            path = lage_media_service.journal_media_path(gsl_medium) if ok else Path()
        elif medium_art == "uebergreifend":
            gsl_medium = db.get(CrossMarkerMedia, dokument_id)
            marker = db.get(CrossSiteMarker, gsl_medium.marker_id) if gsl_medium else None
            if not features["uebergreifend"]:
                raise MCPDownloadFehler("Das Modul für übergreifende Meldungen ist nicht aktiviert.", 403)
            ok = marker is not None and marker.major_incident_id == lage.id
            path = lage_media_service.cross_media_path(gsl_medium) if ok else Path()
        elif medium_art == "meldung":
            report = db.get(CitizenReport, dokument_id)
            if not features["meldungen"]:
                raise MCPDownloadFehler("Das Meldungsmodul ist nicht aktiviert.", 403)
            if report is None or report.major_incident_id != lage.id or not report.photo_filename:
                raise MCPDownloadFehler("Medium nicht gefunden.", 404)
            path = Path("app_storage/citizen_media") / report.photo_filename
            if not path.is_file():
                raise MCPDownloadFehler("Datei fehlt.", 404)
            return path, report.photo_filename, "image/jpeg"
        else:
            raise MCPDownloadFehler("Medium nicht gefunden.", 404)
        if not ok or not path.is_file():
            raise MCPDownloadFehler("Medium nicht gefunden.", 404)
        gsl_mime = mimetypes.guess_type(path.name)[0] or mimetypes.guess_type(gsl_medium.original_filename)[0]
        return path, gsl_medium.original_filename, gsl_mime or "application/octet-stream"
    if art != "objekt":
        raise MCPDownloadFehler("Download nicht gefunden.", 404)
    try:
        load_live_context(db, user_id, org_id, ("objekt_verwalter",))
    except MCPPermissionError as exc:
        raise MCPDownloadFehler(str(exc), 403) from exc
    if not objekt_effective_enabled(org_id, db):
        raise MCPDownloadFehler("Das Objektmodul ist fuer diese Organisation nicht aktiviert.", 403)

    _, pfad, dateiname, mime = loese_datei(db, org_id, dokument_id, seite)
    return pfad, dateiname, mime
