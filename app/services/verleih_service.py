"""Geräteverleih – Geschäftslogik."""
from __future__ import annotations

import io
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session, selectinload

from app.models.major_incident import IncidentSite
from app.models.master import OrgSettings
from app.models.verleih import (
    VerleihArtikel,
    VerleihAusleihe,
    VerleihFoto,
    VerleihPosition,
    VerleihStatus,
    VerleihStueckliste,
)

_VERLEIH_FOTO_DIR = "app_storage/verleih_fotos"


def _foto_dir(ausleihe_id: int, org_id: int | None = None) -> Path:
    if org_id:
        d = Path(_VERLEIH_FOTO_DIR) / str(org_id) / str(ausleihe_id)
    else:
        d = Path(_VERLEIH_FOTO_DIR) / str(ausleihe_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def foto_path(foto: VerleihFoto) -> Path:
    if foto.org_id:
        return Path(_VERLEIH_FOTO_DIR) / str(foto.org_id) / str(foto.ausleihe_id) / foto.stored_filename
    return Path(_VERLEIH_FOTO_DIR) / str(foto.ausleihe_id) / foto.stored_filename


def foto_thumb_path(foto: VerleihFoto) -> Path:
    fname = foto.stored_filename.replace(".jpg", "_thumb.jpg")
    if foto.org_id:
        return Path(_VERLEIH_FOTO_DIR) / str(foto.org_id) / str(foto.ausleihe_id) / fname
    return Path(_VERLEIH_FOTO_DIR) / str(foto.ausleihe_id) / fname


def get_org_settings(db: Session, org_id: int) -> OrgSettings | None:
    return db.query(OrgSettings).filter_by(org_id=org_id).first()


def get_artikel_aktiv(db: Session) -> list[VerleihArtikel]:
    return db.query(VerleihArtikel).filter_by(aktiv=True).order_by(VerleihArtikel.bezeichnung).all()


def get_stuecklisten_aktiv(db: Session) -> list[VerleihStueckliste]:
    return (
        db.query(VerleihStueckliste)
        .filter_by(aktiv=True)
        .options(selectinload(VerleihStueckliste.positionen))
        .order_by(VerleihStueckliste.bezeichnung)
        .all()
    )


def get_ausleihen_fuer_lage(db: Session, lage_id: int) -> list[VerleihAusleihe]:
    return (
        db.query(VerleihAusleihe)
        .filter_by(lage_id=lage_id)
        .options(
            selectinload(VerleihAusleihe.positionen),
        )
        .order_by(VerleihAusleihe.ausgeliehen_at.desc())
        .all()
    )


def create_ausleihe(
    db: Session,
    lage_id: int,
    org_id: int,
    name: str,
    adresse: str | None,
    telefon: str | None,
    site_id: int | None,
    positionen: list[dict],
    user_id: int | None,
    pin: str | None = None,
    notizen: str | None = None,
) -> VerleihAusleihe:
    org = get_org_settings(db, org_id)
    erinnerung_stunden = org.gsl_verleih_erinnerung_stunden if org else None

    ausleihe = VerleihAusleihe(
        org_id=org_id,
        lage_id=lage_id,
        site_id=site_id,
        name=name,
        adresse=adresse or None,
        telefon=telefon or None,
        pin=pin or None,
        notizen=notizen or None,
        created_by_user_id=user_id,
    )
    if erinnerung_stunden:
        ausleihe.erinnerung_geplant_at = datetime.now(UTC) + timedelta(hours=erinnerung_stunden)

    db.add(ausleihe)
    db.flush()

    for p in positionen:
        pos = VerleihPosition(
            ausleihe_id=ausleihe.id,
            org_id=org_id,
            artikel_id=p.get("artikel_id"),
            bezeichnung=p["bezeichnung"],
            artikel_nr=p.get("artikel_nr"),
            menge=int(p.get("menge", 1)),
        )
        db.add(pos)

    db.commit()
    db.refresh(ausleihe)
    return ausleihe


def _fetch_ausleihe(db: Session, ausleihe_id: int) -> VerleihAusleihe:
    return db.query(VerleihAusleihe).options(
        selectinload(VerleihAusleihe.positionen),
        selectinload(VerleihAusleihe.fotos),
    ).filter_by(id=ausleihe_id).first()


def return_position(db: Session, position_id: int) -> VerleihAusleihe:
    pos = db.get(VerleihPosition, position_id)
    if not pos:
        raise ValueError("Position nicht gefunden")
    ausleihe_id = pos.ausleihe_id
    pos.status = VerleihStatus.zurueckgegeben
    pos.zurueckgegeben_at = datetime.now(UTC)
    db.flush()

    ausleihe = _fetch_ausleihe(db, ausleihe_id)
    alle_zurueck = all(p.status == VerleihStatus.zurueckgegeben for p in ausleihe.positionen)
    if alle_zurueck and ausleihe.status != VerleihStatus.zurueckgegeben:
        ausleihe.status = VerleihStatus.zurueckgegeben
        ausleihe.zurueckgegeben_at = datetime.now(UTC)
    db.commit()
    db.refresh(ausleihe)
    return ausleihe


def add_positionen(
    db: Session,
    ausleihe_id: int,
    org_id: int,
    positionen: list[dict],
) -> VerleihAusleihe:
    for p in positionen:
        pos = VerleihPosition(
            ausleihe_id=ausleihe_id,
            org_id=org_id,
            artikel_id=p.get("artikel_id"),
            bezeichnung=p["bezeichnung"],
            artikel_nr=p.get("artikel_nr") or None,
            menge=int(p.get("menge", 1)),
        )
        db.add(pos)
    ausleihe = _fetch_ausleihe(db, ausleihe_id)
    if ausleihe.status == VerleihStatus.zurueckgegeben:
        ausleihe.status = VerleihStatus.ausgeliehen
        ausleihe.zurueckgegeben_at = None
    db.commit()
    db.refresh(ausleihe)
    return ausleihe


def return_all(db: Session, ausleihe_id: int) -> VerleihAusleihe:
    now = datetime.now(UTC)
    ausleihe = _fetch_ausleihe(db, ausleihe_id)
    if not ausleihe:
        raise ValueError("Ausleihe nicht gefunden")
    for pos in ausleihe.positionen:
        if pos.status == VerleihStatus.ausgeliehen:
            pos.status = VerleihStatus.zurueckgegeben
            pos.zurueckgegeben_at = now
    if ausleihe.status != VerleihStatus.zurueckgegeben:
        ausleihe.status = VerleihStatus.zurueckgegeben
        ausleihe.zurueckgegeben_at = now
    db.commit()
    db.refresh(ausleihe)
    return ausleihe


def generate_pin() -> str:
    return str(secrets.randbelow(1_000_000)).zfill(6)


def get_sms_ausleih_text(db: Session, org_id: int, ausleihe: VerleihAusleihe) -> str:
    org = get_org_settings(db, org_id)
    artikel = ausleihe.artikel_bezeichnungen or "Material"
    org_name = "Feuerwehr"
    try:
        from app.models.master import FireDept
        dept = db.get(FireDept, org_id)
        if dept:
            org_name = dept.name
    except Exception:
        pass
    template = (org.gsl_verleih_sms_ausleih_text if org else None) or (
        "Das Material ({artikel}) wurde an Sie ausgeliehen. "
        "Bitte bringen Sie es schnellstmoeglich zurueck. - {org}"
    )
    return template.replace("{artikel}", artikel).replace("{org}", org_name).replace("{name}", ausleihe.name)


async def save_verleih_foto(
    file,
    ausleihe_id: int,
    org_id: int | None,
    user_id: int | None,
    db: Session,
) -> VerleihFoto:
    from app.config import settings
    data = await file.read()
    if not data:
        from fastapi import HTTPException
        raise HTTPException(400, "Leere Datei")
    max_bytes = settings.MAX_UPLOAD_BYTES_IMAGE
    if len(data) > max_bytes:
        from fastapi import HTTPException
        raise HTTPException(413, f"Datei zu groß (max {max_bytes // (1024*1024)} MB)")
    try:
        from PIL import Image, ImageOps
    except ImportError:
        from fastapi import HTTPException
        raise HTTPException(500, "Pillow nicht verfügbar")

    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail(
        (settings.MEDIA_IMAGE_MAX_WIDTH, settings.MEDIA_IMAGE_MAX_HEIGHT),
        Image.Resampling.LANCZOS,
    )
    dest = _foto_dir(ausleihe_id, org_id)
    uid = uuid.uuid4().hex
    main_fn = f"{uid}.jpg"
    thumb_fn = f"{uid}_thumb.jpg"
    img.save(dest / main_fn, "JPEG", quality=85, optimize=True)
    thumb = img.copy()
    thumb.thumbnail((settings.MEDIA_THUMB_SIZE, settings.MEDIA_THUMB_SIZE), Image.Resampling.LANCZOS)
    thumb.save(dest / thumb_fn, "JPEG", quality=80, optimize=True)
    stored_bytes = (dest / main_fn).stat().st_size

    foto = VerleihFoto(
        ausleihe_id=ausleihe_id,
        org_id=org_id,
        stored_filename=main_fn,
        original_filename=(file.filename or "foto.jpg")[:255],
        bytes=stored_bytes,
        uploaded_by=user_id,
    )
    db.add(foto)
    db.commit()
    db.refresh(foto)
    return foto


def get_sms_erinnerung_text(db: Session, org_id: int, ausleihe: VerleihAusleihe) -> str:
    org = get_org_settings(db, org_id)
    artikel = ausleihe.artikel_bezeichnungen or "Material"
    org_name = "Feuerwehr"
    try:
        from app.models.master import FireDept
        dept = db.get(FireDept, org_id)
        if dept:
            org_name = dept.name
    except Exception:
        pass
    template = (org.gsl_verleih_sms_erinnerung_text if org else None) or (
        "Erinnerung: Bitte bringen Sie das ausgeliehene Material ({artikel}) "
        "umgehend zurueck. - {org}"
    )
    return template.replace("{artikel}", artikel).replace("{org}", org_name).replace("{name}", ausleihe.name)
