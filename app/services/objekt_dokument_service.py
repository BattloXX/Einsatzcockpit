"""Objektverwaltung: PDF-Dokumenten-Pipeline (Upload, Zerlegung, Rasterung).

Ablauf:
1. Upload (Magic-Byte-MIME via filetype, Groessen-/Seitenlimit, Quota-Reserve)
   → ObjektDokument mit status=neu, Original unter
   {OBJEKT_MEDIA_DIR}/{org_id}/{objekt_id}/{uuid}/original.pdf
2. Hintergrund-Verarbeitung (verarbeite_dokument): pypdf-Split in verlustfreie
   Einzelseiten-PDFs + Rasterung via pdf2image/Poppler (Hi-Res PNG + Thumb).
   Rasterung ist in _render_page_png gekapselt und injizierbar (Tests/CI ohne
   Poppler); ohne Poppler bleiben bild_pfad/thumb_pfad NULL (UI-Platzhalter).
3. Sammel-PDF: pypdf-Merge der Einzelseiten (Originalqualitaet).

Quota: Original + alle abgeleiteten Dateien werden via storage_service
reserviert; ObjektDokument.belegt_bytes haelt die Summe fuer die Freigabe
beim Loeschen.
"""
from __future__ import annotations

import io
import logging
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.models.objekt import (
    DOKUMENT_STATUS_FEHLER,
    DOKUMENT_STATUS_FERTIG,
    DOKUMENT_STATUS_NEU,
    DOKUMENT_STATUS_VERARBEITUNG,
    Objekt,
    ObjektDokument,
    ObjektDokumentSeite,
)
from app.models.user import User
from app.services.storage_service import release_storage, reserve_storage

logger = logging.getLogger("einsatzleiter.objekt_dokument")

# Signatur der injizierbaren Rasterfunktion: (pdf_path, seiten_nr, dpi) -> PNG-Bytes | None
RenderFunc = Callable[[Path, int, int], bytes | None]


def _storage_root() -> Path:
    root = Path(settings.OBJEKT_MEDIA_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _dokument_dir(org_id: int, objekt_id: int, dokument_uuid: str) -> Path:
    d = _storage_root() / str(org_id) / str(objekt_id) / dokument_uuid
    d.mkdir(parents=True, exist_ok=True)
    return d


def absolute_pfad(relativ: str) -> Path:
    return _storage_root() / relativ.replace("\\", "/")


def _detect_mime(data: bytes) -> str | None:
    """Magic-Byte-MIME (nie Client-Header) — Muster media_service."""
    try:
        import filetype  # type: ignore
        kind = filetype.guess(data)
        return kind.mime if kind else None
    except ImportError:
        logger.error("filetype-Bibliothek fehlt — MIME-Erkennung deaktiviert")
        return None


def _system_int(db: Session, key: str, default: int) -> int:
    """SystemSettings-Override fuer Limits (objekt_pdf_max_bytes / _max_seiten)."""
    from app.models.master import SystemSettings
    row = db.query(SystemSettings).filter(SystemSettings.key == key).first()
    if row and row.value:
        try:
            return int(row.value)
        except ValueError:
            pass
    return default


async def store_dokument_upload(
    file: UploadFile,
    objekt: Objekt,
    user: User | None,
    db: Session,
) -> ObjektDokument:
    """Speichert ein Original-PDF und legt den ObjektDokument-Datensatz an.

    Wirft HTTPException 415 (kein PDF), 413 (zu gross / zu viele Seiten / Quota).
    Die Zerlegung laeuft anschliessend als Background-Task (verarbeite_dokument).
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Leere Datei")

    max_bytes = _system_int(db, "objekt_pdf_max_bytes", settings.OBJEKT_PDF_MAX_BYTES)
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Datei zu gross (max. {max_bytes // (1024 * 1024)} MB)",
        )

    mime = _detect_mime(data)
    if mime != "application/pdf":
        raise HTTPException(status_code=415, detail="Nur PDF-Dateien erlaubt")

    # Seitenzahl + Validierung via pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        seitenzahl = len(reader.pages)
    except Exception as exc:
        raise HTTPException(status_code=415, detail="PDF konnte nicht gelesen werden") from exc

    max_seiten = _system_int(db, "objekt_pdf_max_seiten", settings.OBJEKT_PDF_MAX_SEITEN)
    if seitenzahl > max_seiten:
        raise HTTPException(
            status_code=413,
            detail=f"PDF hat {seitenzahl} Seiten (max. {max_seiten})",
        )

    org_id = objekt.org_id
    if org_id is None:
        raise HTTPException(status_code=400, detail="Objekt ohne Organisation")

    dokument_uuid = uuid.uuid4().hex
    dest_dir = _dokument_dir(org_id, objekt.id, dokument_uuid)
    original = dest_dir / "original.pdf"
    original.write_bytes(data)

    try:
        reserve_storage(db, org_id, len(data))
    except HTTPException:
        original.unlink(missing_ok=True)
        raise

    dokument = ObjektDokument(
        org_id=org_id,
        objekt_id=objekt.id,
        dateiname_original=(file.filename or "dokument.pdf")[:255],
        pfad=f"{org_id}/{objekt.id}/{dokument_uuid}/original.pdf",
        mime="application/pdf",
        groesse_bytes=len(data),
        belegt_bytes=len(data),
        seitenzahl=seitenzahl,
        status=DOKUMENT_STATUS_NEU,
        hochgeladen_von_id=user.id if user else None,
        hochgeladen_am=datetime.now(UTC),
    )
    db.add(dokument)
    db.flush()
    return dokument


def _render_page_png_poppler(pdf_path: Path, seiten_nr: int, dpi: int) -> bytes | None:
    """Rastert eine PDF-Seite via pdf2image/Poppler. None wenn nicht verfuegbar.

    Entscheidung 2026-07-05: pdf2image + Poppler (Prod = Debian, apt install
    poppler-utils) statt PyMuPDF (AGPL). Kapselung haelt einen Backend-Tausch lokal.
    """
    try:
        from pdf2image import convert_from_path  # type: ignore
    except ImportError:
        logger.warning("pdf2image nicht installiert — Seiten-Rendering uebersprungen")
        return None
    try:
        bilder = convert_from_path(
            str(pdf_path), dpi=dpi, first_page=seiten_nr, last_page=seiten_nr,
        )
    except Exception:
        logger.exception("Poppler-Rendering fehlgeschlagen (%s Seite %d)", pdf_path, seiten_nr)
        return None
    if not bilder:
        return None
    buf = io.BytesIO()
    bilder[0].save(buf, format="PNG")
    return buf.getvalue()


def verarbeite_dokument(
    dokument_id: int,
    render_func: RenderFunc | None = None,
) -> None:
    """Hintergrund-Verarbeitung: Split (pypdf) + Rasterung (pdf2image) + Thumbs.

    Laeuft mit eigener Session (Muster _geocode_incident). Idempotent genug:
    bei Fehlern wird status=fehler gesetzt; vorhandene Seiten-Zeilen des
    Dokuments werden vorab entfernt.
    """
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal

    render = render_func or _render_page_png_poppler

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dokument = db.get(ObjektDokument, dokument_id)
        if dokument is None:
            return
        dokument.status = DOKUMENT_STATUS_VERARBEITUNG
        db.commit()

        original = absolute_pfad(dokument.pfad)
        dest_dir = original.parent
        org_id = dokument.org_id

        # Alte Seiten-Zeilen entfernen (Neuverarbeitung)
        db.query(ObjektDokumentSeite).filter(
            ObjektDokumentSeite.dokument_id == dokument.id
        ).delete()
        db.commit()

        from PIL import Image
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(str(original))
        neu_belegt = 0

        for i, page in enumerate(reader.pages, start=1):
            # 1) Verlustfreie Einzelseite
            writer = PdfWriter()
            writer.add_page(page)
            einzel = dest_dir / f"seite_{i:04d}.pdf"
            with einzel.open("wb") as fh:
                writer.write(fh)
            neu_belegt += einzel.stat().st_size

            # 2) Hi-Res-Rendering + Thumb (optional, wenn Poppler verfuegbar)
            bild_pfad_rel: str | None = None
            thumb_pfad_rel: str | None = None
            png = render(original, i, settings.OBJEKT_SEITE_RENDER_DPI)
            if png:
                bild = dest_dir / f"seite_{i:04d}.png"
                bild.write_bytes(png)
                neu_belegt += len(png)
                bild_pfad_rel = f"{dokument.pfad.rsplit('/', 1)[0]}/seite_{i:04d}.png"

                try:
                    img = Image.open(io.BytesIO(png))
                    img.thumbnail((settings.MEDIA_THUMB_SIZE, settings.MEDIA_THUMB_SIZE * 2))
                    if img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")  # type: ignore[assignment]
                    thumb = dest_dir / f"seite_{i:04d}_thumb.jpg"
                    img.save(thumb, "JPEG", quality=80)
                    neu_belegt += thumb.stat().st_size
                    thumb_pfad_rel = f"{dokument.pfad.rsplit('/', 1)[0]}/seite_{i:04d}_thumb.jpg"
                except Exception:
                    logger.exception("Thumb-Erzeugung fehlgeschlagen (Dokument %d Seite %d)",
                                     dokument.id, i)

            db.add(ObjektDokumentSeite(
                org_id=org_id,
                objekt_id=dokument.objekt_id,
                dokument_id=dokument.id,
                seiten_nr=i,
                einzel_pdf_pfad=f"{dokument.pfad.rsplit('/', 1)[0]}/seite_{i:04d}.pdf",
                bild_pfad=bild_pfad_rel,
                thumb_pfad=thumb_pfad_rel,
            ))

        # Quota fuer abgeleitete Dateien reservieren (Entscheidung: zaehlt zur Org-Quota)
        if org_id is not None and neu_belegt > 0:
            try:
                reserve_storage(db, org_id, neu_belegt)
            except HTTPException:
                logger.warning(
                    "Quota beim Zerlegen ueberschritten (Dokument %d) — Renderings verworfen",
                    dokument.id,
                )
                for pfx in ("seite_",):
                    for f in dest_dir.glob(f"{pfx}*"):
                        f.unlink(missing_ok=True)
                db.rollback()
                dokument = db.get(ObjektDokument, dokument_id)
                if dokument is not None:
                    db.query(ObjektDokumentSeite).filter(
                        ObjektDokumentSeite.dokument_id == dokument.id
                    ).delete()
                    dokument.status = DOKUMENT_STATUS_FEHLER
                    dokument.fehler_text = "Speicher-Kontingent der Organisation erschoepft"
                    db.commit()
                return

        dokument.belegt_bytes = dokument.groesse_bytes + neu_belegt
        dokument.status = DOKUMENT_STATUS_FERTIG
        dokument.fehler_text = None
        db.commit()
    except Exception as exc:
        logger.exception("Dokument-Verarbeitung fehlgeschlagen (Dokument %d)", dokument_id)
        try:
            db.rollback()
            dokument = db.get(ObjektDokument, dokument_id)
            if dokument is not None:
                dokument.status = DOKUMENT_STATUS_FEHLER
                dokument.fehler_text = str(exc)[:500]
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def sammel_pdf(seiten: list[ObjektDokumentSeite]) -> bytes:
    """Fuegt Einzelseiten-PDFs in gegebener Reihenfolge zu einem Sammel-PDF zusammen."""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for seite in seiten:
        if not seite.einzel_pdf_pfad:
            continue
        pfad = absolute_pfad(seite.einzel_pdf_pfad)
        if not pfad.exists():
            continue
        reader = PdfReader(str(pfad))
        for page in reader.pages:
            writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def delete_dokument(dokument: ObjektDokument, db: Session) -> None:
    """Loescht Dokument-Verzeichnis, gibt Quota frei, entfernt DB-Zeilen (Kaskade)."""
    org_id = dokument.org_id
    verzeichnis = absolute_pfad(dokument.pfad).parent
    try:
        if verzeichnis.exists():
            shutil.rmtree(verzeichnis)
    except OSError:
        logger.exception("Dokument-Verzeichnis nicht loeschbar: %s", verzeichnis)
    if org_id is not None and dokument.belegt_bytes > 0:
        release_storage(db, org_id, dokument.belegt_bytes)
    db.delete(dokument)
