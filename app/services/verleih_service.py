"""Geräteverleih – Geschäftslogik."""
from __future__ import annotations

import io
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session, selectinload

from app.models.master import OrgSettings
from app.models.verleih import (
    ArtikelVerfuegbarkeit,
    VerleihArtikel,
    VerleihAusleihe,
    VerleihFoto,
    VerleihGeraetetyp,
    VerleihPosition,
    VerleihStatus,
    VerleihStueckliste,
)

_VERLEIH_FOTO_DIR = "app_storage/verleih_fotos"
logger = logging.getLogger("einsatzleiter.verleih")


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


def get_geraetetypen_aktiv(db: Session) -> list[VerleihGeraetetyp]:
    return db.query(VerleihGeraetetyp).filter_by(aktiv=True).order_by(VerleihGeraetetyp.name).all()


def get_artikel_by_geraetetyp(db: Session, geraetetyp_id: int) -> list[VerleihArtikel]:
    """Aktive, verfuegbare Artikel eines Geraetetyps – fuer Auswahl beim Ausleihen."""
    return (
        db.query(VerleihArtikel)
        .filter_by(aktiv=True, geraetetyp_id=geraetetyp_id)
        .order_by(VerleihArtikel.artikel_nr, VerleihArtikel.bezeichnung)
        .all()
    )


def artikel_nr_eindeutig(db: Session, org_id: int, artikel_nr: str, exclude_id: int | None = None) -> bool:
    """Prueft ob die Artikelnr innerhalb der Org noch nicht vergeben ist."""
    q = db.query(VerleihArtikel).filter_by(aktiv=True, org_id=org_id, artikel_nr=artikel_nr)
    if exclude_id:
        q = q.filter(VerleihArtikel.id != exclude_id)
    return q.first() is None


def _mark_artikel_ausgeliehen(db: Session, artikel_id: int | None) -> None:
    if not artikel_id:
        return
    a = db.get(VerleihArtikel, artikel_id)
    if a and not a.ist_mengenartikel:
        a.verfuegbarkeit = ArtikelVerfuegbarkeit.ausgeliehen


def _mark_artikel_verfuegbar(db: Session, artikel_id: int | None) -> None:
    if not artikel_id:
        return
    a = db.get(VerleihArtikel, artikel_id)
    if a and not a.ist_mengenartikel:
        a.verfuegbarkeit = ArtikelVerfuegbarkeit.verfuegbar


def toggle_artikel_verfuegbarkeit(db: Session, artikel_id: int) -> VerleihArtikel:
    a = db.get(VerleihArtikel, artikel_id)
    if not a:
        raise ValueError("Artikel nicht gefunden")
    if a.ist_mengenartikel:
        raise ValueError("Status nur fuer eindeutige Artikel")
    if a.verfuegbarkeit == ArtikelVerfuegbarkeit.ausgeliehen:
        a.verfuegbarkeit = ArtikelVerfuegbarkeit.verfuegbar
    else:
        a.verfuegbarkeit = ArtikelVerfuegbarkeit.ausgeliehen
    db.commit()
    db.refresh(a)
    return a


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
    quelle: str = "verleih",
    einheit_id: int | None = None,
    created_by_name: str | None = None,
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
        quelle=quelle,
        einheit_id=einheit_id,
        created_by_name=created_by_name,
    )
    if erinnerung_stunden:
        ausleihe.erinnerung_geplant_at = datetime.now(UTC) + timedelta(hours=erinnerung_stunden)

    db.add(ausleihe)
    db.flush()

    for p in positionen:
        aid = p.get("artikel_id")
        pos = VerleihPosition(
            ausleihe_id=ausleihe.id,
            org_id=org_id,
            artikel_id=aid,
            bezeichnung=p["bezeichnung"],
            artikel_nr=p.get("artikel_nr"),
            menge=int(p.get("menge", 1)),
        )
        db.add(pos)
        _mark_artikel_ausgeliehen(db, aid)

    db.commit()
    db.refresh(ausleihe)
    return ausleihe


def _fetch_ausleihe(db: Session, ausleihe_id: int) -> VerleihAusleihe:
    return db.query(VerleihAusleihe).options(  # type: ignore[return-value]
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
    _mark_artikel_verfuegbar(db, pos.artikel_id)
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
        aid = p.get("artikel_id")
        pos = VerleihPosition(
            ausleihe_id=ausleihe_id,
            org_id=org_id,
            artikel_id=aid,
            bezeichnung=p["bezeichnung"],
            artikel_nr=p.get("artikel_nr") or None,
            menge=int(p.get("menge", 1)),
        )
        db.add(pos)
        _mark_artikel_ausgeliehen(db, aid)
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
            _mark_artikel_verfuegbar(db, pos.artikel_id)
    if ausleihe.status != VerleihStatus.zurueckgegeben:
        ausleihe.status = VerleihStatus.zurueckgegeben
        ausleihe.zurueckgegeben_at = now
    db.commit()
    db.refresh(ausleihe)
    return ausleihe


def _positionen_text(positionen: list[VerleihPosition]) -> str:
    return ", ".join(
        f"{p.menge}× {p.bezeichnung}" if p.menge != 1 else p.bezeichnung
        for p in positionen
    )


def material_an_einsatzstelle(
    db: Session,
    *,
    lage,
    site,
    positionen: list[dict],
    name: str | None,
    telefon: str | None,
    adresse: str | None,
    notizen: str | None,
    einheit,
    quelle: str,
    user_id: int | None,
    author_name: str | None,
    pin: str | None = None,
) -> VerleihAusleihe:
    """Hinterlaesst Material an einer Einsatzstelle samt Chronikeintrag."""
    if site.major_incident_id != lage.id or site.org_id != lage.org_id:
        raise ValueError("Einsatzstelle gehoert nicht zur Lage")

    name = (name or "").strip() or f"Vor Ort – {site.bezeichnung}"[:200]
    adresse = (adresse or "").strip()
    if not adresse:
        strasse = " ".join(part for part in (site.strasse, site.hausnr) if part)
        adresse = ", ".join(part for part in (strasse, site.ort) if part)

    katalog_erlaubt = einheit is None or einheit.is_from_org
    bereinigte_positionen = []
    for position in positionen:
        bezeichnung = str(position.get("bezeichnung") or "").strip()
        if not bezeichnung:
            continue
        artikel_id = position.get("artikel_id") if katalog_erlaubt else None
        if artikel_id:
            artikel = db.get(VerleihArtikel, artikel_id)
            if artikel is None or artikel.org_id != lage.org_id:
                artikel_id = None
        try:
            menge = int(position.get("menge", 1))
        except (TypeError, ValueError):
            menge = 1
        bereinigte_positionen.append({
            "bezeichnung": bezeichnung,
            "artikel_nr": position.get("artikel_nr") or None,
            "artikel_id": artikel_id,
            "menge": max(1, min(9999, menge)),
        })
    if not bereinigte_positionen:
        raise ValueError("Mindestens eine Position erforderlich")

    ausleihe = create_ausleihe(
        db=db,
        lage_id=lage.id,
        org_id=lage.org_id,
        name=name,
        adresse=adresse or None,
        telefon=telefon,
        site_id=site.id,
        positionen=bereinigte_positionen,
        user_id=user_id,
        pin=pin,
        notizen=notizen,
        quelle=quelle,
        einheit_id=getattr(einheit, "id", None),
        created_by_name=author_name,
    )
    from app.models.major_incident import SiteLogEntry

    db.add(SiteLogEntry(
        incident_site_id=site.id,
        kind="massnahmen",
        text=f"Material belassen: {_positionen_text(ausleihe.positionen)} (Empfänger: {name})",
        einheit_id=getattr(einheit, "id", None),
        author_name=author_name,
        user_id=user_id,
    ))
    db.commit()
    db.refresh(ausleihe)
    return ausleihe


def material_zuruecknehmen(
    db: Session,
    ausleihe: VerleihAusleihe,
    *,
    author_name: str | None,
    user_id: int | None,
    einheit_id: int | None,
) -> VerleihAusleihe:
    """Nimmt alles Material zurueck und dokumentiert dies an der Einsatzstelle."""
    ausleihe = return_all(db, ausleihe.id)
    if ausleihe.site_id:
        from app.models.major_incident import SiteLogEntry

        db.add(SiteLogEntry(
            incident_site_id=ausleihe.site_id,
            kind="massnahmen",
            text=f"Material zurückgenommen: {_positionen_text(ausleihe.positionen)}",
            einheit_id=einheit_id,
            author_name=author_name,
            user_id=user_id,
        ))
        db.commit()
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
    site = ausleihe.site
    stelle = site.bezeichnung if site is not None else ""
    return (template.replace("{artikel}", artikel).replace("{org}", org_name)
            .replace("{name}", ausleihe.name).replace("{stelle}", stelle))


async def sende_material_sms(ausleihe_id: int, org_id: int, pin_senden: bool) -> bool:
    """Versendet die Ausleih-SMS best effort und protokolliert das Ergebnis."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.major_incident import SiteLogEntry
    from app.services.sms_service import send_sms

    db = SessionLocal()
    set_tenant_context(db, None)
    site_id: int | None = None
    try:
        ausleihe = db.get(VerleihAusleihe, ausleihe_id)
        if ausleihe is None or ausleihe.org_id != org_id or not ausleihe.telefon:
            return False
        site_id = ausleihe.site_id
        set_tenant_context(db, org_id)
        text = get_sms_ausleih_text(db, org_id, ausleihe)
        if pin_senden and ausleihe.pin:
            text += f" PIN: {ausleihe.pin}"
        erfolgreich = bool(await send_sms(org_id, ausleihe.telefon, text))
        if erfolgreich:
            ausleihe.sms_ausleih_gesendet = True
        if ausleihe.site_id:
            db.add(SiteLogEntry(
                incident_site_id=ausleihe.site_id,
                kind="massnahmen",
                text="SMS an Empfänger gesendet" if erfolgreich else "SMS an Empfänger fehlgeschlagen",
            ))
        db.commit()
        return erfolgreich
    except Exception:
        db.rollback()
        logger.exception("Material-SMS fehlgeschlagen: Ausleihe %s", ausleihe_id)
        if site_id:
            try:
                db.add(SiteLogEntry(
                    incident_site_id=site_id,
                    kind="massnahmen",
                    text="SMS an Empfänger fehlgeschlagen",
                ))
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("SMS-Fehler konnte nicht in der Chronik gespeichert werden")
        return False
    finally:
        db.close()


async def save_verleih_foto(
    file,
    ausleihe_id: int,
    org_id: int | None,
    user_id: int | None,
    db: Session,
    author_name: str | None = None,
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
    img = ImageOps.exif_transpose(img)  # type: ignore[assignment]
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")  # type: ignore[assignment]
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
        author_name=author_name,
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
