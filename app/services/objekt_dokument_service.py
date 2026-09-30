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
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.models.objekt import (
    AUSWAHL_DOKUMENTART,
    DOKUMENT_STATUS_FEHLER,
    DOKUMENT_STATUS_FERTIG,
    DOKUMENT_STATUS_NEU,
    DOKUMENT_STATUS_VERARBEITUNG,
    Objekt,
    ObjektDokument,
    ObjektDokumentSeite,
)
from app.models.user import User
from app.services.objekt_service import lade_auswahl, write_objekt_change
from app.services.storage_service import release_storage, reserve_storage

logger = logging.getLogger("einsatzleiter.objekt_dokument")

# Signatur der injizierbaren Rasterfunktion: (pdf_path, seiten_nr, dpi) -> PNG-Bytes | None
RenderFunc = Callable[[Path, int, int], bytes | None]
# Signatur der injizierbaren OCR-Funktion: (png_bytes) -> erkannter Text ("" wenn nicht verfuegbar)
OcrFunc = Callable[[bytes], str]


class ObjektDokumentFehler(Exception):
    """Fachlicher Fehler der Dokument-Pipeline, ohne HTTP-Abhaengigkeit."""

    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


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


def hole_dokument_gruppe(db: Session, dokument: ObjektDokument) -> list[ObjektDokument]:
    """Alle Versionen desselben logischen Dokuments, nach versionsnummer sortiert."""
    genesis_id = dokument.dokument_gruppe_id or dokument.id
    return (
        db.query(ObjektDokument)
        .filter(
            (ObjektDokument.id == genesis_id) | (ObjektDokument.dokument_gruppe_id == genesis_id)
        )
        .order_by(ObjektDokument.versionsnummer)
        .all()
    )


def naechste_versionsnummer(db: Session, dokument: ObjektDokument) -> int:
    """Naechste freie Versionsnummer innerhalb der Dokumentgruppe."""
    gruppe = hole_dokument_gruppe(db, dokument)
    return max((d.versionsnummer for d in gruppe), default=0) + 1


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
    try:
        return store_dokument_bytes(data, file.filename or "dokument.pdf", objekt, user, db)
    except ObjektDokumentFehler as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def store_dokument_bytes(
    data: bytes,
    dateiname: str,
    objekt: Objekt,
    user: User | None,
    db: Session,
    *,
    ersetzt_dokument_id: int | None = None,
) -> ObjektDokument:
    """Request-freie Variante des PDF-Uploads.

    Der Aufrufer commitet weiterhin selbst. Bei einer Ersatzversion wird die
    produktive Vorgaengerversion bis zu einer expliziten Freigabe nicht veraendert.
    """
    if not data:
        raise ObjektDokumentFehler("Leere Datei")

    max_bytes = _system_int(db, "objekt_pdf_max_bytes", settings.OBJEKT_PDF_MAX_BYTES)
    if len(data) > max_bytes:
        raise ObjektDokumentFehler(f"Datei zu gross (max. {max_bytes // (1024 * 1024)} MB)", 413)

    mime = _detect_mime(data)
    if mime != "application/pdf":
        raise ObjektDokumentFehler("Nur PDF-Dateien erlaubt", 415)

    # Seitenzahl + Validierung via pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        seitenzahl = len(reader.pages)
    except Exception as exc:
        raise ObjektDokumentFehler("PDF konnte nicht gelesen werden", 415) from exc

    max_seiten = _system_int(db, "objekt_pdf_max_seiten", settings.OBJEKT_PDF_MAX_SEITEN)
    if seitenzahl > max_seiten:
        raise ObjektDokumentFehler(f"PDF hat {seitenzahl} Seiten (max. {max_seiten})", 413)

    org_id = objekt.org_id
    if org_id is None:
        raise ObjektDokumentFehler("Objekt ohne Organisation")

    vorgaenger: ObjektDokument | None = None
    if ersetzt_dokument_id is not None:
        vorgaenger = (
            db.query(ObjektDokument)
            .filter(
                ObjektDokument.id == ersetzt_dokument_id,
                ObjektDokument.org_id == org_id,
                ObjektDokument.objekt_id == objekt.id,
            )
            .first()
        )
        if vorgaenger is None:
            raise ObjektDokumentFehler("Zu ersetzendes Dokument wurde fuer dieses Objekt nicht gefunden", 404)

    dokument_uuid = uuid.uuid4().hex
    dest_dir = _dokument_dir(org_id, objekt.id, dokument_uuid)
    original = dest_dir / "original.pdf"
    reserved = False
    try:
        original.write_bytes(data)
        reserve_storage(db, org_id, len(data))
        reserved = True
    except HTTPException as exc:
        raeume_dokument_verzeichnis_auf(dest_dir)
        raise ObjektDokumentFehler(str(exc.detail), exc.status_code) from exc
    except OSError as exc:
        raeume_dokument_verzeichnis_auf(dest_dir)
        raise ObjektDokumentFehler("PDF konnte nicht gespeichert werden") from exc

    sofort_freigeben = objekt.status == "entwurf"
    gruppe_id = None
    versionsnummer = 1
    if vorgaenger is not None:
        gruppe_id = vorgaenger.dokument_gruppe_id or vorgaenger.id
        versionsnummer = naechste_versionsnummer(db, vorgaenger)

    try:
        dokument = ObjektDokument(
            org_id=org_id,
            objekt_id=objekt.id,
            dateiname_original=(dateiname or "dokument.pdf")[:255],
            pfad=f"{org_id}/{objekt.id}/{dokument_uuid}/original.pdf",
            mime="application/pdf",
            groesse_bytes=len(data),
            belegt_bytes=len(data),
            seitenzahl=seitenzahl,
            status=DOKUMENT_STATUS_NEU,
            hochgeladen_von_id=user.id if user else None,
            hochgeladen_am=datetime.now(UTC),
            dokument_gruppe_id=gruppe_id,
            versionsnummer=versionsnummer,
            freigabe_status="freigegeben" if sofort_freigeben else "wartet_freigabe",
            ist_aktuelle_version=sofort_freigeben,
            pflegeauftrag_id=None,
            freigegeben_am=datetime.now(UTC) if sofort_freigeben else None,
            freigegeben_von_id=user.id if sofort_freigeben and user else None,
        )
        db.add(dokument)
        db.flush()
        return dokument
    except Exception:
        # Der Aufrufer bekommt nie eine halb gespeicherte Datei oder eine hängende
        # Quota-Reservierung. Die Session selbst bleibt dabei beim Aufrufer.
        if reserved:
            release_storage(db, org_id, len(data))
        raeume_dokument_verzeichnis_auf(dest_dir)
        raise


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


def _ocr_tesseract(png: bytes) -> str:
    """OCR eines Seitenbilds via Tesseract. "" wenn pytesseract/Binary fehlt (CI/Tests)."""
    try:
        import pytesseract  # type: ignore
        from PIL import Image
    except ImportError:
        logger.warning("pytesseract nicht installiert — OCR uebersprungen")
        return ""
    try:
        img = Image.open(io.BytesIO(png))
        return pytesseract.image_to_string(img, lang=settings.OBJEKT_OCR_LANG)
    except Exception:
        logger.exception("Tesseract-OCR fehlgeschlagen")
        return ""


def _normalisiere_text(text: str) -> str:
    """Whitespace normalisieren + auf die konfigurierte Maximallaenge kappen."""
    zusammen = " ".join((text or "").split())
    return zusammen[: settings.OBJEKT_VOLLTEXT_MAX_CHARS]


def extrahiere_seitentext(
    page: object | None,
    png: bytes | None,
    ocr_func: OcrFunc | None = None,
) -> tuple[str | None, str]:
    """Ermittelt den Volltext einer Seite: erst PDF-Textlayer (pypdf), sonst OCR.

    Gibt (volltext, quelle) zurueck — quelle ∈ pdf/ocr/none. Injizierbare ocr_func
    fuer Tests/CI ohne Tesseract (Default _ocr_tesseract).
    """
    ocr = ocr_func or _ocr_tesseract
    text = ""
    if page is not None:
        try:
            text = _normalisiere_text(page.extract_text() or "")  # type: ignore[attr-defined]
        except Exception:
            text = ""
    if text and len(text) >= settings.OBJEKT_OCR_MIN_CHARS:
        return text, "pdf"
    # Textlayer fehlt/zu kurz → OCR auf dem gerenderten Seitenbild versuchen
    if settings.OBJEKT_OCR_ENABLED and png:
        ocr_text = _normalisiere_text(ocr(png))
        if len(ocr_text) >= settings.OBJEKT_OCR_MIN_CHARS:
            return ocr_text, "ocr"
    if text:
        return text, "pdf"
    return None, "none"


def verarbeite_dokument(
    dokument_id: int,
    render_func: RenderFunc | None = None,
    ocr_func: OcrFunc | None = None,
) -> None:
    """Hintergrund-Verarbeitung: Split (pypdf) + Rasterung (pdf2image) + Thumbs + Volltext.

    Laeuft mit eigener Session (Muster _geocode_incident). Idempotent genug:
    bei Fehlern wird status=fehler gesetzt; vorhandene Seiten-Zeilen des
    Dokuments werden vorab entfernt. Je Seite wird der Volltext (PDF-Textlayer,
    sonst OCR) fuer die Suche indexiert.
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
                    img: Any = Image.open(io.BytesIO(png))
                    img.thumbnail((settings.MEDIA_THUMB_SIZE, settings.MEDIA_THUMB_SIZE * 2))
                    if img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")
                    thumb = dest_dir / f"seite_{i:04d}_thumb.jpg"
                    img.save(thumb, "JPEG", quality=80)
                    neu_belegt += thumb.stat().st_size
                    thumb_pfad_rel = f"{dokument.pfad.rsplit('/', 1)[0]}/seite_{i:04d}_thumb.jpg"
                except Exception:
                    logger.exception("Thumb-Erzeugung fehlgeschlagen (Dokument %d Seite %d)",
                                     dokument.id, i)

            # 3) Volltext fuer die Suche (PDF-Textlayer, sonst OCR auf dem Rendering)
            volltext, text_quelle = extrahiere_seitentext(page, png, ocr_func)

            db.add(ObjektDokumentSeite(
                org_id=org_id,
                objekt_id=dokument.objekt_id,
                dokument_id=dokument.id,
                seiten_nr=i,
                einzel_pdf_pfad=f"{dokument.pfad.rsplit('/', 1)[0]}/seite_{i:04d}.pdf",
                bild_pfad=bild_pfad_rel,
                thumb_pfad=thumb_pfad_rel,
                volltext=volltext,
                text_quelle=text_quelle,
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


def _analyse_seiten_validieren(
    db: Session, dokument: ObjektDokument, seiten: list[dict], user_id: int | None,
) -> list[dict]:
    """Validiert die von einem externen Client fertig gelieferte Seitenanalyse."""
    if len(seiten) != dokument.seitenzahl:
        raise ObjektDokumentFehler(
            f"Seitenanalyse hat {len(seiten)} Seiten, das PDF aber {dokument.seitenzahl}",
        )
    dokumentarten = lade_auswahl(db, dokument.org_id, AUSWAHL_DOKUMENTART)
    validiert: list[dict] = []
    for nummer, daten in enumerate(seiten, start=1):
        if not isinstance(daten, dict):
            raise ObjektDokumentFehler(f"Seitenanalyse fuer Seite {nummer} ist ungueltig")
        dokumentart = str(daten.get("dokumentart") or "").strip()
        if dokumentart and dokumentart not in dokumentarten:
            raise ObjektDokumentFehler(f"Unbekannte Dokumentart auf Seite {nummer}: {dokumentart}")
        stand = daten.get("stand")
        if stand:
            try:
                stand = datetime.strptime(str(stand), "%Y-%m-%d").date()
            except ValueError as exc:
                raise ObjektDokumentFehler(f"Ungueltiger Stand auf Seite {nummer} (YYYY-MM-DD erwartet)") from exc
        validiert.append({
            "volltext": _normalisiere_text(str(daten.get("volltext") or "")) or None,
            "dokumentart": dokumentart or None,
            "titel": str(daten.get("titel") or "").strip()[:200] or None,
            "melderlinien": str(daten.get("melderlinien") or "").strip()[:100] or None,
            "stand": stand,
            "bei_einsatz_drucken": bool(daten.get("bei_einsatz_drucken", False)),
            "user_id": user_id,
        })
    return validiert


def verarbeite_dokument_mit_analyse(
    dokument_id: int,
    seiten: list[dict],
    *,
    db: Session | None = None,
    user_id: int | None = None,
    render_func: RenderFunc | None = None,
) -> ObjektDokument:
    """Bereitet ein bereits analysiertes PDF synchron auf, ohne OCR oder KI.

    Ein uebergebenes ``db`` bleibt im Besitz des Aufrufers und wird nicht committed.
    Ohne Session wird fuer Integrationen eine eigene Session verwendet und committed.
    """
    eigene_session = db is None
    if db is None:
        from app.core.tenant import set_tenant_context
        from app.db import SessionLocal
        db = SessionLocal()
        set_tenant_context(db, None)
    assert db is not None
    render = render_func or _render_page_png_poppler
    verzeichnis: Path | None = None
    try:
        dokument = db.get(ObjektDokument, dokument_id)
        if dokument is None:
            raise ObjektDokumentFehler("Dokument nicht gefunden", 404)
        analysen = _analyse_seiten_validieren(db, dokument, seiten, user_id)
        original = absolute_pfad(dokument.pfad)
        verzeichnis = original.parent
        if not original.exists():
            raise ObjektDokumentFehler("Original-PDF nicht gefunden", 404)
        from PIL import Image
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(str(original))
        if len(reader.pages) != len(analysen):
            raise ObjektDokumentFehler("PDF-Seitenzahl hat sich seit dem Upload geaendert")
        dokument.status = DOKUMENT_STATUS_VERARBEITUNG
        db.flush()
        for alte_seite in db.query(ObjektDokumentSeite).filter(
            ObjektDokumentSeite.dokument_id == dokument.id
        ).all():
            db.delete(alte_seite)
        neu_belegt = 0
        jetzt = datetime.now(UTC)
        for i, (page, analyse) in enumerate(zip(reader.pages, analysen), start=1):
            writer = PdfWriter()
            writer.add_page(page)
            einzel = verzeichnis / f"seite_{i:04d}.pdf"
            with einzel.open("wb") as fh:
                writer.write(fh)
            neu_belegt += einzel.stat().st_size
            bild_pfad_rel: str | None = None
            thumb_pfad_rel: str | None = None
            png = render(original, i, settings.OBJEKT_SEITE_RENDER_DPI)
            if png:
                bild = verzeichnis / f"seite_{i:04d}.png"
                bild.write_bytes(png)
                neu_belegt += len(png)
                bild_pfad_rel = f"{dokument.pfad.rsplit('/', 1)[0]}/seite_{i:04d}.png"
                try:
                    img: Any = Image.open(io.BytesIO(png))
                    img.thumbnail((settings.MEDIA_THUMB_SIZE, settings.MEDIA_THUMB_SIZE * 2))
                    if img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")
                    thumb = verzeichnis / f"seite_{i:04d}_thumb.jpg"
                    img.save(thumb, "JPEG", quality=80)
                    neu_belegt += thumb.stat().st_size
                    thumb_pfad_rel = f"{dokument.pfad.rsplit('/', 1)[0]}/seite_{i:04d}_thumb.jpg"
                except Exception:
                    logger.exception("Thumb-Erzeugung fehlgeschlagen (Dokument %d Seite %d)", dokument.id, i)
            db.add(ObjektDokumentSeite(
                org_id=dokument.org_id, objekt_id=dokument.objekt_id, dokument_id=dokument.id,
                seiten_nr=i, einzel_pdf_pfad=f"{dokument.pfad.rsplit('/', 1)[0]}/seite_{i:04d}.pdf",
                bild_pfad=bild_pfad_rel, thumb_pfad=thumb_pfad_rel,
                volltext=analyse["volltext"], text_quelle="mcp", dokumentart=analyse["dokumentart"],
                titel=analyse["titel"], melderlinien=analyse["melderlinien"], stand=analyse["stand"],
                bei_einsatz_drucken=analyse["bei_einsatz_drucken"],
                klassifiziert_von_id=analyse["user_id"], klassifiziert_am=jetzt,
            ))
        if neu_belegt:
            try:
                if dokument.org_id is None:
                    raise ObjektDokumentFehler("Dokument ohne Organisation")
                reserve_storage(db, dokument.org_id, neu_belegt)
            except HTTPException as exc:
                raise ObjektDokumentFehler(str(exc.detail), exc.status_code) from exc
        dokument.belegt_bytes = dokument.groesse_bytes + neu_belegt
        dokument.status = DOKUMENT_STATUS_FERTIG
        dokument.fehler_text = None
        db.flush()
        if eigene_session:
            db.commit()
        return dokument
    except Exception as exc:
        if isinstance(exc, ObjektDokumentFehler):
            fehler = exc
        else:
            logger.exception("MCP-Dokument-Verarbeitung fehlgeschlagen (Dokument %d)", dokument_id)
            fehler = ObjektDokumentFehler(f"Dokument-Verarbeitung fehlgeschlagen: {exc}")
        try:
            dokument = db.get(ObjektDokument, dokument_id)
            if dokument is not None:
                verzeichnis = delete_dokument(dokument, db)
                db.flush()
                if verzeichnis is not None:
                    raeume_dokument_verzeichnis_auf(verzeichnis)
            if eigene_session:
                db.commit()
        except Exception:
            db.rollback()
        raise fehler
    finally:
        if eigene_session:
            db.close()


def reindex_objekt(objekt_id: int, ocr_func: OcrFunc | None = None) -> int:
    """Fuellt den Volltext bestehender Seiten eines Objekts neu (ohne Neu-Rendering).

    Liest je Seite die verlustfreie Einzelseite (pypdf-Textlayer) und – falls vorhanden –
    das gerenderte PNG (OCR-Fallback). Eigene Session. Gibt die Anzahl aktualisierter
    Seiten zurueck.
    """
    from pypdf import PdfReader

    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal

    db = SessionLocal()
    set_tenant_context(db, None)
    n = 0
    try:
        # Versionshistorie absichtlich mit reindizieren; alte Direktlinks bleiben nutzbar.
        seiten = (
            db.query(ObjektDokumentSeite)
            .filter(ObjektDokumentSeite.objekt_id == objekt_id)
            .execution_options(include_all_tenants=True)
            .all()
        )
        for seite in seiten:
            page = None
            if seite.einzel_pdf_pfad:
                try:
                    pfad = absolute_pfad(seite.einzel_pdf_pfad)
                    if pfad.exists():
                        page = PdfReader(str(pfad)).pages[0]
                except Exception:
                    page = None
            png = None
            if seite.bild_pfad:
                bpfad = absolute_pfad(seite.bild_pfad)
                if bpfad.exists():
                    png = bpfad.read_bytes()
            volltext, quelle = extrahiere_seitentext(page, png, ocr_func)
            seite.volltext = volltext
            seite.text_quelle = quelle
            n += 1
        db.commit()
        return n
    except Exception:
        logger.exception("Reindex fehlgeschlagen (Objekt %d)", objekt_id)
        db.rollback()
        return n
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


def delete_dokument(dokument: ObjektDokument, db: Session) -> Path:
    """Gibt Quota frei und entfernt die DB-Zeilen (Kaskade); loescht das Verzeichnis
    auf der Platte NICHT sofort - siehe raeume_dokument_verzeichnis_auf().

    Der Aufrufer MUSS das zurueckgegebene Verzeichnis erst NACH einem erfolgreichen
    db.commit() aufraeumen (Muster: ui_archive.py::delete_incident). Wuerde stattdessen
    zuerst geloescht, koennte ein spaeter fehlschlagender Commit die DB-Aenderung
    zurueckrollen, waehrend die Dateien bereits unwiderruflich weg sind.
    """
    org_id = dokument.org_id
    verzeichnis = absolute_pfad(dokument.pfad).parent
    if org_id is not None and dokument.belegt_bytes > 0:
        release_storage(db, org_id, dokument.belegt_bytes)
    db.delete(dokument)
    return verzeichnis


def raeume_dokument_verzeichnis_auf(verzeichnis: Path) -> None:
    """Loescht ein Dokument-Verzeichnis von der Platte - nur nach erfolgreichem
    db.commit() aufrufen (siehe delete_dokument())."""
    try:
        if verzeichnis.exists():
            shutil.rmtree(verzeichnis)
    except OSError:
        logger.exception("Dokument-Verzeichnis nicht loeschbar: %s", verzeichnis)


def klassifiziere_seiten(
    db: Session,
    seiten: list[ObjektDokumentSeite],
    daten: dict,
    user_id: int | None,
) -> int:
    """Klassifiziert Seiten ohne Router-/HTTP-Abhaengigkeit; committet nie."""
    if not seiten:
        raise ObjektDokumentFehler("Keine Seiten ausgewaehlt")
    org_id = seiten[0].org_id
    objekt_id = seiten[0].objekt_id
    if any(s.org_id != org_id or s.objekt_id != objekt_id for s in seiten):
        raise ObjektDokumentFehler("Seiten muessen zum selben Objekt gehoeren")
    dokumentart = str(daten.get("dokumentart") or "").strip()
    dokumentarten = lade_auswahl(db, org_id, AUSWAHL_DOKUMENTART)
    if dokumentart and dokumentart not in dokumentarten:
        raise ObjektDokumentFehler("Unbekannte Dokumentart")
    stand_wert = str(daten.get("stand") or "").strip()
    try:
        stand = datetime.strptime(stand_wert, "%Y-%m-%d").date() if stand_wert else None
    except ValueError as exc:
        raise ObjektDokumentFehler("Ungueltiger Stand (YYYY-MM-DD erwartet)") from exc
    titel = str(daten.get("titel") or "").strip()
    melderlinien = str(daten.get("melderlinien") or "").strip()
    jetzt = datetime.now(UTC)
    for seite in seiten:
        if dokumentart:
            seite.dokumentart = dokumentart
        if titel:
            seite.titel = titel[:200]
        if melderlinien:
            seite.melderlinien = melderlinien[:100]
        if stand:
            seite.stand = stand
        seite.bei_einsatz_drucken = bool(daten.get("bei_einsatz_drucken", False))
        seite.klassifiziert_von_id = user_id
        seite.klassifiziert_am = jetzt
    art_label = dokumentarten.get(dokumentart, dokumentart or "unveraendert")
    write_objekt_change(
        db, objekt_id, org_id, "dokumente", "seiten_klassifiziert", before=None,
        after=f"{len(seiten)} Seite(n) → {art_label}", user_id=user_id,
    )
    return len(seiten)


def hole_wartende_dokumente(db: Session, objekt: Objekt) -> list[ObjektDokument]:
    """Nur MCP-artige, nicht an Pflegeauftraege gebundene Freigaben."""
    return (
        db.query(ObjektDokument)
        .filter(
            ObjektDokument.org_id == objekt.org_id,
            ObjektDokument.objekt_id == objekt.id,
            ObjektDokument.freigabe_status == "wartet_freigabe",
            ObjektDokument.pflegeauftrag_id.is_(None),
        )
        .order_by(ObjektDokument.hochgeladen_am, ObjektDokument.id)
        .all()
    )


def gebe_dokument_frei(db: Session, dokument: ObjektDokument, user_id: int) -> None:
    """Aktiviert eine wartende Version; der Aufrufer committet."""
    if dokument.freigabe_status != "wartet_freigabe" or dokument.pflegeauftrag_id is not None:
        raise ObjektDokumentFehler("Dokument wartet nicht auf diese Freigabe")
    if dokument.dokument_gruppe_id is not None:
        genesis_id = dokument.dokument_gruppe_id
        bisherige = (
            db.query(ObjektDokument)
            .filter(
                ObjektDokument.org_id == dokument.org_id,
                ObjektDokument.objekt_id == dokument.objekt_id,
                ((ObjektDokument.id == genesis_id) | (ObjektDokument.dokument_gruppe_id == genesis_id)),
                ObjektDokument.ist_aktuelle_version.is_(True),
            )
            .first()
        )
        if bisherige is not None and bisherige.id != dokument.id:
            bisherige.ist_aktuelle_version = False
            bisherige.freigabe_status = "archiviert"
    dokument.ist_aktuelle_version = True
    dokument.freigabe_status = "freigegeben"
    dokument.freigegeben_am = datetime.now(UTC)
    dokument.freigegeben_von_id = user_id
    write_objekt_change(
        db, dokument.objekt_id, dokument.org_id, "dokumente", "dokument_freigegeben",
        before=None, after=dokument.dateiname_original, user_id=user_id,
    )


def verwirf_wartendes_dokument(
    db: Session,
    dokument: ObjektDokument,
    user_id: int,
    *,
    aufraeumen: bool = True,
) -> None:
    """Verwirft eine wartende Version und gibt deren Speicher sofort frei.

    Die Zeile bleibt als nachvollziehbarer Freigabestatus erhalten; Seiten und
    Dateien werden wie bei ``delete_dokument`` entfernt.
    """
    if dokument.freigabe_status != "wartet_freigabe" or dokument.pflegeauftrag_id is not None:
        raise ObjektDokumentFehler("Dokument wartet nicht auf diese Freigabe")
    verzeichnis = absolute_pfad(dokument.pfad).parent
    if dokument.org_id is not None and dokument.belegt_bytes:
        release_storage(db, dokument.org_id, dokument.belegt_bytes)
    for seite in db.query(ObjektDokumentSeite).filter(
        ObjektDokumentSeite.dokument_id == dokument.id
    ).all():
        db.delete(seite)
    dokument.belegt_bytes = 0
    dokument.freigabe_status = "verworfen"
    dokument.ist_aktuelle_version = False
    dokument.fehler_text = None
    write_objekt_change(
        db, dokument.objekt_id, dokument.org_id, "dokumente", "dokument_verworfen",
        before=None, after=dokument.dateiname_original, user_id=user_id,
    )
    db.flush()
    if aufraeumen:
        raeume_dokument_verzeichnis_auf(verzeichnis)
