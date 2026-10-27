"""Geprüfte Dokumentpakete für lokalen Druck und die vorhandenen ECP-PrintJobs."""

import hashlib
import io
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from PIL import Image
from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from sqlalchemy.orm import Session

from app.config import settings
from app.core.permissions import has_role
from app.models.gateway import DOC_DOKUMENTPAKET, Gateway, Printer, PrintJob
from app.models.user import User
from app.services.dokumentviewer_service import seiten_auswahl
from app.services.gateway_service import gateway_effective_enabled, printer_reachable
from app.services.print_agent_service import hat_agent_routen, route_kandidaten

PAPIER = {"A4": (595.276, 841.89), "A3": (841.89, 1190.551)}
TOKEN_TTL = 900


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="dokumentdruck-v1")


def versiegeln(user: User, paket: dict) -> str:
    return _serializer().dumps({"user": user.id, "org": user.org_id, "paket": paket})


def entsiegeln(user: User, token: str) -> dict:
    if len(token) > 30000:
        raise HTTPException(422, "Druckauswahl ist zu groß.")
    try:
        data = _serializer().loads(token, max_age=TOKEN_TTL)
    except (BadSignature, SignatureExpired) as exc:
        raise HTTPException(410, "Druckauswahl ist abgelaufen. Bitte erneut vorbereiten.") from exc
    if data.get("user") != user.id or data.get("org") != user.org_id:
        raise HTTPException(404, "Druckauswahl nicht gefunden.")
    return data["paket"]


def _quell_request(user: User, url: str) -> Request:
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc or len(url) > 2048:
        raise HTTPException(422, "Nur interne Dokumentreferenzen sind zulässig.")
    parameter = dict(parse_qsl(parsed.query))
    if set(parameter) - {"version_id", "seiten", "auswahl", "auszug", "entwurf", "gliederung", "akteur"}:
        raise HTTPException(422, "Ungültige Dokumentparameter.")
    return Request({"type": "http", "method": "GET", "path": parsed.path,
                    "raw_path": parsed.path.encode(), "query_string": urlencode(parameter).encode(),
                    "headers": [], "scheme": "https", "server": ("localhost", 443),
                    "state": {"user": user}})


def quell_datei(db: Session, user: User, url: str,
                druckzeit: datetime | None = None) -> tuple[bytes, str, str | None]:
    """Keine URL-Abfrage: ausschließlich bekannte Controller samt ihren Leserechten."""
    from app.routers import ui_katplan as kp
    from app.services.katplan_zugriff import require_katplan_lesen

    request = _quell_request(user, url)
    path = request.url.path
    params = request.query_params
    version = params.get("version_id")
    if version is not None and (not version.isdigit() or not 0 < int(version) <= 9223372036854775807):
        raise HTTPException(422, "Ungültige Dokumentversion.")
    vid = int(version) if version else None
    response: Response
    if path.startswith("/plaene/"):
        require_katplan_lesen(request, db)
        if match := re.fullmatch(r"/plaene/anhang/([\w-]+)", path):
            # Stellt auch für Objektreferenzen den freigegebenen Auszug bereit.
            parameter = dict(params)
            parameter["auszug"] = "1"
            request = _quell_request(user, path + "?" + urlencode(parameter))
            response = kp.anhang_datei(match[1], request, vid, db, user)
        elif match := re.fullmatch(r"/plaene/quelle/([\w-]+)/original", path):
            response = kp.quelle_original(match[1], request, vid, db, user)
        elif match := re.fullmatch(r"/plaene/quelle/([\w-]+)/seite/([1-9]\d*)/(pdf|bild)", path):
            response = kp.quelle_seite(match[1], int(match[2]), request, vid, db, user)
        elif match := re.fullmatch(r"/plaene/([1-9]\d*)/pdf", path):
            entwurf = params.get("entwurf", "0")
            if entwurf not in ("0", "1"):
                raise HTTPException(422, "Ungültige Planfassung.")
            response = kp.plan_pdf_antwort(int(match[1]), request, vid, int(entwurf),
                                          params.get("gliederung", "kapitel"), params.get("akteur"), db, user,
                                          druckzeit=druckzeit)
        else:
            raise HTTPException(422, "Diese Dokumentreferenz wird nicht unterstützt.")
    elif match := re.fullmatch(r"/einsatz-dokumente/datei/([1-9]\d*)", path):
        from app.services.dokumentviewer_service import viewer_auswahl
        from app.services.einsatz_dokument_service import dokument, pdf_daten

        einsatzdok = dokument(db, user, int(match[1]))
        response = viewer_auswahl(pdf_daten(db, einsatzdok), params)
    elif match := re.fullmatch(r"/nachschlagewerk-cache/rettungskarten/([1-9]\d*)/original.pdf", path):
        from app.routers import ui_nachschlagewerke as nw
        from app.services.nachschlagewerk_service import nachschlagewerke_effective_enabled

        nw.require_role(*nw._LESE_ROLLEN)(request)
        if not nachschlagewerke_effective_enabled(user.org_id, db):
            raise HTTPException(404, "Nicht gefunden")
        rettungskarte = nw.rettungskarten_pdf(request, int(match[1]), db, user, None)
        response = rettungskarte
        if any(k in params for k in ("seiten", "auswahl")):
            from app.services.dokumentviewer_service import viewer_auswahl

            response = viewer_auswahl(Path(rettungskarte.path), params)
    elif match := re.fullmatch(r"/objekt-medien/(?:dokument/([1-9]\d*)/original|seite/([1-9]\d*)/pdf)", path):
        from app.core.permissions import EINSATZ_LESE_ROLLEN, require_role
        from app.routers import ui_objekt_dokumente as obj
        from app.services.objekt_service import objekt_effective_enabled

        require_role(*EINSATZ_LESE_ROLLEN)(request)
        if not objekt_effective_enabled(user.org_id, db):
            raise HTTPException(404, "Dokument nicht verfügbar.")
        if match[1]:
            pfad, _ = obj.dokument_original_datei(db, int(match[1]), user)
        else:
            seite = obj._seite_fuer_user(db, int(match[2]), user)
            if not seite.einzel_pdf_pfad:
                raise HTTPException(404, "Keine Einzelseite vorhanden.")
            pfad = obj.absolute_pfad(seite.einzel_pdf_pfad)
        if not pfad.exists():
            raise HTTPException(404, "Datei fehlt.")
        response = FileResponse(pfad, media_type="application/pdf")
    else:
        raise HTTPException(422, "Diese Dokumentreferenz wird nicht unterstützt. Bitte das Original öffnen.")
    if isinstance(response, FileResponse):
        try:
            datei = Path(response.path)
            if datei.stat().st_size > 100 * 1024 * 1024:
                raise HTTPException(413, "Dokument ist für diesen Druckauftrag zu groß.")
            data = datei.read_bytes()
        except OSError as exc:
            raise HTTPException(404, "Dokumentdatei nicht verfügbar.") from exc
    else:
        if response.status_code != 200:
            raise HTTPException(422, "Dokument kann nicht gedruckt werden.")
        data = bytes(response.body)
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(413, "Dokument ist für diesen Druckauftrag zu groß.")
    return data, response.media_type or "application/octet-stream", response.headers.get("X-ECP-Document-Fingerprint")


def _bild_pdf(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        # Das Originalbild wird eingebettet; keine vorherige Viewer-Rasterung.
        width, height = image.size
        dpi = image.info.get("dpi", (96, 96))
        dx, dy = (max(36, min(float(dpi[i]), 1200)) for i in range(2))
        size = (width * 72 / dx, height * 72 / dy)
    out = io.BytesIO()
    canvas = Canvas(out, pagesize=size)
    canvas.drawImage(ImageReader(io.BytesIO(data)), 0, 0, width=size[0], height=size[1], mask="auto")
    canvas.showPage()
    canvas.save()
    return out.getvalue()


def papierformat(width: float, height: float) -> str:
    a, b = sorted((width, height))
    return next((name for name, size in PAPIER.items() if abs(a - size[0]) < 4 and abs(b - size[1]) < 4),
                f"{round(a * 25.4 / 72)}×{round(b * 25.4 / 72)} mm")


def zusammenstellen(db: Session, user: User, paket: dict, *, vorbereiten: bool = False) -> tuple[bytes, dict]:
    if not user.active or user.org_id is None:
        raise HTTPException(403, "Druckberechtigung nicht mehr vorhanden.")
    writer = PdfWriter()
    refs = []
    formate = set()
    formatwahl, ausrichtung = paket["format"], paket["orientation"]
    druckzeit = datetime.now(UTC).isoformat() if vorbereiten else paket["created_at"]
    gesamt_bytes = 0
    for referenz in paket["documents"]:
        data, mime, fingerprint = quell_datei(db, user, referenz["url"], datetime.fromisoformat(druckzeit))
        gesamt_bytes += len(data)
        if gesamt_bytes > 150 * 1024 * 1024:
            raise HTTPException(413, "Dokumentauswahl ist zu groß.")
        digest = fingerprint or hashlib.sha256(data).hexdigest()
        if not vorbereiten and digest != referenz.get("sha256"):
            raise HTTPException(409, "Ein Dokument wurde geändert. Bitte Druckauswahl erneut vorbereiten.")
        try:
            if mime.startswith("image/"):
                data = _bild_pdf(data)
            elif mime != "application/pdf":
                raise ValueError("Nur PDFs und Bilder sind druckbar.")
            reader = PdfReader(io.BytesIO(data))
            source_writer = PdfWriter()
            seiten = seiten_auswahl(referenz["pages"], len(reader.pages))
            if len(writer.pages) + len(seiten) > 2000:
                raise ValueError("Höchstens 2000 Seiten pro Druckauftrag.")
            for nr in seiten:
                page = source_writer.add_page(reader.pages[nr - 1])
                if ausrichtung != "original":
                    page.transfer_rotation_to_content()
                    quer = float(page.mediabox.width) > float(page.mediabox.height)
                    if quer != (ausrichtung == "landscape"):
                        page.rotate(90)
                if formatwahl == "original" and not paket.get("station_margin"):
                    result = writer.add_page(page)
                    box = result.mediabox
                    formate.add(papierformat(float(box.width), float(box.height)))
                else:
                    page.transfer_rotation_to_content()
                    width, height = float(page.mediabox.width), float(page.mediabox.height)
                    target = (width, height) if formatwahl == "original" else PAPIER[formatwahl]
                    if formatwahl != "original" and width > height:
                        target = (target[1], target[0])
                    # Inhalt vollständig und mit Rand einpassen, keine Rasterung/kein Abschneiden.
                    margin = 18  # 6,35 mm
                    factor = min((target[0] - 2 * margin) / width, (target[1] - 2 * margin) / height)
                    transform = (Transformation().translate(-float(page.mediabox.left), -float(page.mediabox.bottom))
                                 .scale(factor).translate((target[0] - width * factor) / 2,
                                                         (target[1] - height * factor) / 2))
                    result = writer.add_blank_page(width=target[0], height=target[1])
                    result.merge_transformed_page(page, transform)
                    formate.add(papierformat(*target))
            refs.append({"url": referenz["url"], "pages": referenz["pages"], "sha256": digest})
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, "Dokument oder Seitenauswahl ist ungültig, beschädigt oder geschützt.") from exc
    out = io.BytesIO()
    writer.write(out)
    info = {"documents": refs, "format": formatwahl, "orientation": ausrichtung,
            "page_count": len(writer.pages), "media": sorted(formate), "created_at": druckzeit,
            "station_margin": paket.get("station_margin", 0)}
    return out.getvalue(), info


def station_erlaubt(db: Session, user: User) -> bool:
    return has_role(user, "recorder") and gateway_effective_enabled(user.org_id, db)


def drucker_info(db: Session, printer: Printer) -> dict:
    gateway = db.query(Gateway).filter_by(id=printer.gateway_id, org_id=printer.org_id).first()
    if hat_agent_routen(db, printer.id):
        bereit = bool(route_kandidaten(db, printer))
    else:
        bereit = bool(gateway and not gateway.is_windows_agent and gateway.is_paired
                      and printer_reachable(printer, gateway) is True)
    media = (printer.capabilities or {}).get("media") or ["A4"]
    return {"id": printer.id, "name": printer.name, "ready": bereit,
            "media": [m for m in ("A4", "A3") if any(m.lower() in str(w).lower() for w in media)],
            "detail": "Bereit" if bereit else "Drucker oder Druckdienst offline / keine aktuelle Bereitschaftsmeldung"}


def job_status(job: PrintJob) -> dict:
    labels = {"queued": "Wartet auf Druckdienst", "sent": "An Druckdienst übertragen",
              "printing": "Druckdienst verarbeitet den Auftrag", "done": "Druckdienst hat Abschluss bestätigt",
              "failed": "Druckauftrag fehlgeschlagen", "uncertain": "Druckausgang unklar – bitte am Drucker prüfen",
              "canceled": "Druckauftrag abgebrochen"}
    return {"job_id": job.id, "status": job.status, "label": labels.get(job.status, job.status),
            "error": job.error, "confirmed": job.status == "done", "spool_ref": job.spool_ref, "phase": job.phase}


def render_druckjob(db: Session, job: PrintJob) -> bytes:
    user = db.query(User).filter_by(id=job.created_by_id, org_id=job.org_id, active=True).first()
    if user is None or not station_erlaubt(db, user):
        raise HTTPException(403, "Druckberechtigung nicht mehr vorhanden.")
    options = job.options or {}
    if job.document_type != DOC_DOKUMENTPAKET or not options.get("document_packet"):
        raise HTTPException(422, "Druckauswahl fehlt.")
    return zusammenstellen(db, user, options["document_packet"])[0]
