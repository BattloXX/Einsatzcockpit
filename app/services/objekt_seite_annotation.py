"""Nicht-destruktive Einzeichnungen auf Objektdokument-Seiten."""
from __future__ import annotations

import base64
import binascii
import io
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter, Transformation
from sqlalchemy.orm import Session

from app.config import settings
from app.models.media_annotation import MediaAnnotation, MediaAnnotationVersion
from app.models.objekt import ObjektDokumentSeite
from app.models.user import User
from app.services.annotation_service import annotated_versions, get_annotation, get_or_create
from app.services.objekt_dokument_service import absolute_pfad
from app.services.storage_service import release_storage, reserve_storage


def annotations_dateien(seite: ObjektDokumentSeite) -> tuple[Path, ...]:
    rel = seite.bild_pfad or seite.einzel_pdf_pfad
    if not rel:
        return ()
    bild = absolute_pfad(rel)
    basis = bild.stem + "_annotated"
    return (bild.with_name(basis + ".png"), bild.with_name(basis + "_thumb.jpg"),
            bild.with_name(basis + ".pdf"))


def _datei(db: Session, seite: ObjektDokumentSeite, index: int, original: bool) -> Path | None:
    dateien = annotations_dateien(seite)
    ann = None if original else get_annotation(db, "objekt_seite", seite.id)
    if ann and ann.annotated_file and dateien and dateien[index].is_file():
        return dateien[index]
    rel = (seite.bild_pfad, seite.thumb_pfad, seite.einzel_pdf_pfad)[index]
    return absolute_pfad(rel) if rel else None


def seite_bild_datei(db: Session, seite: ObjektDokumentSeite, original: bool = False) -> Path | None:
    return _datei(db, seite, 0, original)


def seite_thumb_datei(db: Session, seite: ObjektDokumentSeite, original: bool = False) -> Path | None:
    return _datei(db, seite, 1, original)


def seite_pdf_datei(db: Session, seite: ObjektDokumentSeite, original: bool = False) -> Path | None:
    return _datei(db, seite, 2, original)


def annotierte_seiten(db: Session, seiten_ids: list[int]) -> dict[int, int]:
    return annotated_versions(db, "objekt_seite", seiten_ids)


def _bild(data: str, rotation: int) -> Image.Image:
    try:
        raw = base64.b64decode(data.split(",", 1)[1], validate=True)
        with Image.open(io.BytesIO(raw)) as bild:
            if bild.format != "PNG":
                raise ValueError("PNG erwartet")
            # Anzeige dreht im Uhrzeigersinn (-r in PIL), Rueckdrehung daher +r.
            return bild.convert("RGBA").rotate(rotation, expand=True)
    except (ValueError, IndexError, binascii.Error, UnidentifiedImageError, OSError) as exc:
        raise HTTPException(400, "Ungueltiges Annotations-PNG") from exc


def _bytes(bild: Image.Image, fmt: str, **kwargs: int) -> bytes:
    buf = io.BytesIO()
    bild.save(buf, fmt, **kwargs)
    return buf.getvalue()


def _pdf(seite: ObjektDokumentSeite, bild: Image.Image, overlay: Image.Image | None) -> bytes | None:
    if not seite.einzel_pdf_pfad:
        return None
    original = absolute_pfad(seite.einzel_pdf_pfad)
    if not original.is_file():
        return None
    from weasyprint import HTML  # noqa: PLC0415

    page = PdfReader(str(original)).pages[0]
    box = page.mediabox
    breite, hoehe = float(box.width), float(box.height)
    if overlay is not None:
        daten = base64.b64encode(_bytes(overlay, "PNG")).decode("ascii")
        mime = "png"
    else:
        # JPEG-Einbettung durch WeasyPrint vermeidet grosse ASCIIHex-Raster-PDFs.
        daten = base64.b64encode(_bytes(bild.convert("RGB"), "JPEG", quality=92)).decode("ascii")
        mime = "jpeg"
    html = (f'<style>@page {{size: {breite}pt {hoehe}pt; margin: 0}}'
            f'html,body {{margin: 0; width: {breite}pt; height: {hoehe}pt}}'
            'img {display: block; width: 100%; height: 100%}</style>'
            f'<img src="data:image/{mime};base64,{daten}">')
    raster = HTML(string=html).write_pdf()
    stempel_writer = PdfWriter(clone_from=io.BytesIO(raster))
    stempel = stempel_writer.pages[0]
    stempel.add_transformation(Transformation().translate(float(box.left), float(box.bottom)))
    stempel.mediabox = box
    writer = PdfWriter()
    if overlay is not None:
        ziel = writer.add_page(page)
        ziel.merge_page(stempel)
    else:
        # Flaches PNG enthaelt bereits den Seitenhintergrund.
        writer.add_page(stempel)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def speichere_objekt_seite(
    db: Session, user: User, seite: ObjektDokumentSeite, annotation_json: str,
    png: str | None, overlay_png: str | None = None,
) -> MediaAnnotation:
    rotation = seite.rotation or 0
    try:
        stage = json.loads(annotation_json)
    except ValueError:
        stage = annotation_json
    if isinstance(stage, dict) and "rotation" in stage and "stage" in stage:
        stage = stage["stage"]
    inhalt = json.dumps({"rotation": rotation, "stage": stage}, ensure_ascii=False)
    dateien = annotations_dateien(seite)
    ausgaben: tuple[bytes | None, ...] | None = None
    if png:
        bild = _bild(png, rotation)
        overlay = _bild(overlay_png, rotation) if overlay_png else None
        if overlay is not None and overlay.size != bild.size:
            raise HTTPException(400, "Overlay und Seitenbild muessen gleich gross sein")
        thumb = bild.convert("RGB")
        thumb.thumbnail((settings.MEDIA_THUMB_SIZE, settings.MEDIA_THUMB_SIZE * 2))
        ausgaben = (_bytes(bild, "PNG"), _bytes(thumb, "JPEG", quality=80), _pdf(seite, bild, overlay))
        if not dateien:
            raise HTTPException(400, "Seite hat kein Bild")
        alt = sum(p.stat().st_size for p in dateien if p.is_file())
        delta = sum(len(d) for d in ausgaben if d is not None) - alt
        if seite.org_id is not None and delta > 0:
            reserve_storage(db, seite.org_id, delta)
        for pfad, daten in zip(dateien, ausgaben, strict=True):
            if daten is None:
                pfad.unlink(missing_ok=True)
            else:
                pfad.parent.mkdir(parents=True, exist_ok=True)
                pfad.write_bytes(daten)
        if seite.org_id is not None and delta < 0:
            release_storage(db, seite.org_id, -delta)
    ann = get_or_create(db, "objekt_seite", seite.id, seite.org_id)
    if ann.annotation_json:
        db.add(MediaAnnotationVersion(annotation_id=ann.id, annotation_json=ann.annotation_json,
                                      created_by=user.id))
    ann.annotation_json = inhalt
    ann.org_id = seite.org_id
    ann.annotated_at = datetime.now(UTC).replace(tzinfo=None)
    ann.annotated_by = user.id
    if ausgaben:
        ann.annotated_file = dateien[0].name
    db.flush()
    return ann


def entferne_objekt_seite_annotation(db: Session, seite: ObjektDokumentSeite) -> None:
    ann = get_annotation(db, "objekt_seite", seite.id)
    if ann is None:
        return
    dateien = annotations_dateien(seite)
    belegt = sum(p.stat().st_size for p in dateien if p.is_file())
    if seite.org_id is not None:
        release_storage(db, seite.org_id, belegt)
    for pfad in dateien:
        pfad.unlink(missing_ok=True)
    for version in db.query(MediaAnnotationVersion).filter_by(annotation_id=ann.id).all():
        db.delete(version)
    db.delete(ann)


def ersetze_seiten(
    db: Session, alte_seiten: list[ObjektDokumentSeite], neue_seiten: list[ObjektDokumentSeite],
) -> None:
    """Neue IDs vor dem Loeschen vergeben; Verlauf und Locks bleiben am Annotationssatz."""
    db.flush()
    nach_nummer = {s.seiten_nr: s for s in neue_seiten}
    for alt in alte_seiten:
        ann = get_annotation(db, "objekt_seite", alt.id)
        neu = nach_nummer.get(alt.seiten_nr)
        if neu is not None:
            neu.rotation = alt.rotation
        if ann is not None:
            if neu is None:
                entferne_objekt_seite_annotation(db, alt)
            else:
                ann.media_id = neu.id
        db.delete(alt)
    db.flush()
