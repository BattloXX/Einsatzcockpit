"""Versionierte PDF-Planquellen mit tenantgebundener Seitenverarbeitung."""

import hashlib
import json
import logging
import re
import shutil
from datetime import date
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.katplan import (
    KatplanAnhang,
    KatplanDokument,
    KatplanDokumentSeite,
    KatplanElement,
    KatplanPlan,
    KatplanVersion,
    KatplanVersionElement,
    _utcnow,
)
from app.models.master import OrgSettings, SystemSettings
from app.models.user import User
from app.services import pdf_seiten_pipeline as pipeline
from app.services.katplan_service import markiere_ueberarbeitung, protokolliere
from app.services.katplan_zugriff import darf_bearbeiten, sieht_vertraulich
from app.services.media_service import _detect_mime
from app.services.storage_service import release_storage, reserve_storage

logger = logging.getLogger(__name__)


def erkenne_ueberschriften(pfad: Path, seiten) -> dict[int, str]:
    """PDF-Lesezeichen bevorzugen, sonst ausdrücklich erkennbare Textüberschriften."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    titel: dict[int, str] = {}
    try:
        reader = PdfReader(pfad)

        def lesen(knoten):
            for eintrag in knoten:
                if isinstance(eintrag, list):
                    lesen(eintrag)
                else:
                    nr = reader.get_destination_page_number(eintrag)
                    if nr is not None and 0 <= nr < len(reader.pages):
                        titel.setdefault(nr + 1, str(eintrag.title)[:200])

        lesen(reader.outline)
    except (OSError, ValueError, KeyError, TypeError, PdfReadError, RecursionError):
        logger.info("PDF-Lesezeichen konnten nicht ausgewertet werden")
    for seite in seiten:
        for zeile in (seite.volltext or "").splitlines()[:12]:
            zeile = zeile.strip()
            if 4 <= len(zeile) <= 160 and (re.match(r"^\d+(?:\.\d+)*[.)]?\s+\S", zeile)
                                         or zeile.isupper() and any(c.isalpha() for c in zeile)):
                titel.setdefault(seite.seiten_nr, zeile)
                break
    return titel


def absolute_pfad(relativ: str) -> Path:
    root = Path(settings.MEDIA_STORAGE_DIR).resolve()
    pfad = (root / relativ).resolve()
    if not pfad.is_relative_to(root):
        raise ValueError("Ungültiger Dateipfad.")
    return pfad


def _limit(db: Session, key: str, default: int) -> int:
    row = db.query(SystemSettings).filter_by(key=key).first()
    try:
        return int(row.value) if row and row.value else default
    except ValueError:
        return default


def _dokument(db: Session, plan: KatplanPlan, dokument_id: int) -> KatplanDokument:
    dokument = db.query(KatplanDokument).filter_by(
        org_id=plan.org_id, plan_id=plan.id, id=dokument_id,
    ).one_or_none()
    if dokument is None:
        raise ValueError("Quelle gehört nicht zu diesem Plan.")
    return dokument


def _gruppe(db: Session, plan: KatplanPlan, dokument: KatplanDokument) -> list[KatplanDokument]:
    genesis = dokument.dokument_gruppe_id or dokument.id
    return db.query(KatplanDokument).filter(
        KatplanDokument.org_id == plan.org_id, KatplanDokument.plan_id == plan.id,
        (KatplanDokument.id == genesis) | (KatplanDokument.dokument_gruppe_id == genesis),
    ).order_by(KatplanDokument.versionsnummer).all()


def _bindung(plan: KatplanPlan, user: User) -> None:
    if plan.org_id is None or user.org_id != plan.org_id or plan.status == "archiviert":
        raise ValueError("Dieser Plan kann nicht bearbeitet werden.")


def _geaendert(db: Session, plan: KatplanPlan, user: User, dokument: KatplanDokument, aktion: str) -> None:
    protokolliere(db, plan, "quelle", aktion, user, feld=dokument.uuid, neu=dokument.titel)
    markiere_ueberarbeitung(db, plan, user)
    plan.version += 1
    plan.aktualisiert_am = _utcnow()
    plan.aktualisiert_von_id = user.id


def quelle_hochladen(db: Session, plan: KatplanPlan, user: User, data: bytes, dateiname: str, *,
                     titel: str, herausgeber: str = "", stand: date | None = None,
                     ersetzt_dokument_id: int | None = None) -> KatplanDokument:
    _bindung(plan, user)
    db.info["katplan_quelle_verarbeiten"] = False
    titel = titel.strip()
    if not titel or len(titel) > 200 or len(herausgeber) > 200:
        raise ValueError("Titel ist erforderlich; Titel und Herausgeber dürfen höchstens 200 Zeichen enthalten.")
    if stand is not None and not isinstance(stand, date):
        raise ValueError("Ungültiger Stand.")
    try:
        seitenzahl = pipeline.pruefe_pdf(
            data, max_bytes=_limit(db, "objekt_pdf_max_bytes", settings.OBJEKT_PDF_MAX_BYTES),
            max_seiten=_limit(db, "objekt_pdf_max_seiten", settings.OBJEKT_PDF_MAX_SEITEN),
            detect_mime=_detect_mime,
        )
    except pipeline.PdfFehler as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    digest = hashlib.sha256(data).hexdigest()
    db.query(OrgSettings).filter_by(org_id=plan.org_id).with_for_update().first()
    if ersetzt_dokument_id is None:
        bestand = db.query(KatplanDokument).filter_by(
            org_id=plan.org_id, plan_id=plan.id, datei_hash=digest, archiviert=False,
            ist_aktuelle_version=True,
        ).with_for_update().first()
        if bestand is not None and absolute_pfad(bestand.pfad).is_file():
            if (bestand.titel, bestand.herausgeber, bestand.stand) != (titel, herausgeber, stand):
                raise ValueError("Dieses PDF ist bereits als Quelle vorhanden. Vorhandene Quelle zuordnen "
                                 "oder ausdrücklich eine neue Quellversion anlegen.")
            return bestand
    vorher = _dokument(db, plan, ersetzt_dokument_id) if ersetzt_dokument_id is not None else None
    gruppe = _gruppe(db, plan, vorher) if vorher else []
    uid = str(uuid4())
    relativ = f"{plan.org_id}/katplan/{plan.uuid}/quellen/{uid}/original.pdf"
    original = absolute_pfad(relativ)
    assert plan.org_id is not None
    reserve_storage(db, plan.org_id, len(data))
    try:
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(data)
        dokument = KatplanDokument(
            org_id=plan.org_id, plan_id=plan.id, uuid=uid, titel=titel,
            dateiname_original=Path(dateiname.replace("\\", "/")).name[:255] or "original.pdf",
            pfad=relativ, groesse_bytes=len(data), belegt_bytes=len(data), seitenzahl=seitenzahl,
            datei_hash=digest,
            herausgeber=herausgeber, stand=stand, hochgeladen_von_id=user.id,
            dokument_gruppe_id=(vorher.dokument_gruppe_id or vorher.id) if vorher else None,
            versionsnummer=max((d.versionsnummer for d in gruppe), default=0) + 1,
            ist_aktuelle_version=True,
        )
        db.add(dokument)
        db.flush()
    except Exception:
        shutil.rmtree(original.parent, ignore_errors=True)
        release_storage(db, plan.org_id, len(data))
        raise
    for alt in gruppe:
        alt.ist_aktuelle_version = False
    _geaendert(db, plan, user, dokument, "neue Quellversion" if vorher else "hochgeladen")
    db.info["katplan_quelle_verarbeiten"] = True
    return dokument


def verarbeite_quelle(dokument_id: int, render_func: pipeline.RenderFunc | None = None,
                      ocr_func: pipeline.OcrFunc | None = None) -> None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal

    db = SessionLocal()
    dest_dir: Path | None = None
    beansprucht = False
    try:
        for versuch in range(2):
            set_tenant_context(db, None)
            try:
                dokument = db.query(KatplanDokument).filter_by(id=dokument_id).with_for_update().first()
                if dokument is None or dokument.org_id is None or dokument.status == "verarbeitung":
                    return
                org_id, plan_id = dokument.org_id, dokument.plan_id
                pfad = dokument.pfad
                original = absolute_pfad(pfad)
                dest_dir = original.parent
                dokument.status = "verarbeitung"
                dokument.fehler_text = None
                for seite in db.query(KatplanDokumentSeite).filter_by(
                    org_id=org_id, dokument_id=dokument_id,
                ).all():
                    db.delete(seite)
                if org_id is not None:
                    release_storage(db, org_id, max(0, dokument.belegt_bytes - dokument.groesse_bytes))
                dokument.belegt_bytes = dokument.groesse_bytes
                db.commit()
                beansprucht = True
                break
            except OperationalError as exc:
                db.rollback()
                if versuch or not (exc.orig is not None and exc.orig.args and exc.orig.args[0] == 1020):
                    raise
                logger.warning("Lesekonflikt beim Start von Dokument %d; neue Transaktion", dokument_id)
                db.close()
                db = SessionLocal()
        # Keine ORM-Zugriffe nach commit(): expire_on_commit wuerde hier bereits
        # vor dem minutenlangen Rendern einen neuen REPEATABLE-READ-Snapshot oeffnen.
        db.close()
        assert dest_dir is not None
        pipeline.entferne_seitendateien(dest_dir)
        ergebnisse = pipeline.zerlege_pdf(
            original, dest_dir, render_func=render_func or pipeline.render_seite_png_poppler,
            ocr_func=ocr_func or pipeline.ocr_tesseract,
            dpi=settings.OBJEKT_SEITE_RENDER_DPI, thumb_groesse=settings.MEDIA_THUMB_SIZE,
            log_kontext=f"Planquelle {dokument_id}",
        )
        neu_belegt = sum(e.bytes_belegt for e in ergebnisse)
        ueberschriften = erkenne_ueberschriften(original, ergebnisse)
        for versuch in range(2):
            db = SessionLocal()
            set_tenant_context(db, org_id)
            try:
                dokument = db.query(KatplanDokument).filter_by(
                    id=dokument_id, org_id=org_id,
                ).with_for_update().first()
                if dokument is None:
                    pipeline.entferne_seitendateien(dest_dir)
                    return
                if org_id is not None:
                    reserve_storage(db, org_id, neu_belegt)
                basis = pfad.rsplit("/", 1)[0]
                for e in ergebnisse:
                    db.add(KatplanDokumentSeite(
                        org_id=org_id, plan_id=plan_id, dokument_id=dokument_id,
                        seiten_nr=e.seiten_nr, einzel_pdf_pfad=f"{basis}/{e.einzel_pdf_name}",
                        bild_pfad=f"{basis}/{e.bild_name}" if e.bild_name else None,
                        thumb_pfad=f"{basis}/{e.thumb_name}" if e.thumb_name else None,
                        volltext=e.volltext, text_quelle=e.text_quelle, titel=ueberschriften.get(e.seiten_nr),
                    ))
                dokument.belegt_bytes = dokument.groesse_bytes + neu_belegt
                dokument.status = "fertig"
                dokument.fehler_text = None
                db.commit()
                return
            except OperationalError as exc:
                db.rollback()
                if versuch or not (exc.orig is not None and exc.orig.args and exc.orig.args[0] == 1020):
                    raise
                logger.warning("Lesekonflikt beim Abschluss von Dokument %d; neue Transaktion", dokument_id)
            finally:
                db.close()
    except Exception as exc:
        logger.exception("Planquelle %d konnte nicht verarbeitet werden", dokument_id)
        db.rollback()
        db.close()
        if beansprucht and dest_dir is not None:
            pipeline.entferne_seitendateien(dest_dir)
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            dokument = db.query(KatplanDokument).filter_by(id=dokument_id).with_for_update().first()
            erwarteter_status = "verarbeitung" if beansprucht else "neu"
            if dokument is not None and dokument.status == erwarteter_status:
                dokument.status = "fehler"
                dokument.fehler_text = str(exc.detail if isinstance(exc, HTTPException) else exc)[:500]
                db.commit()
        except Exception:
            db.rollback()
            logger.exception("Fehlerstatus konnte nicht gespeichert werden (Dokument %d)", dokument_id)
    finally:
        db.close()


def quellen_ids_aus_version(db: Session, plan: KatplanPlan, version_id: int,
                           user: User | None = None) -> set[int]:
    version = db.query(KatplanVersion).filter_by(
        org_id=plan.org_id, plan_id=plan.id, id=version_id,
    ).one_or_none()
    if version is None:
        return set()
    ids = {q["id"] for q in json.loads(version.plan_json).get("quellen", [])}
    for e in db.query(KatplanVersionElement).filter_by(org_id=plan.org_id, version_id=version.id):
        if e.quelle_dokument_id and (user is None or not e.vertraulich or sieht_vertraulich(user)):
            ids.add(e.quelle_dokument_id)
    return ids


def quelle_entfernen(db: Session, plan: KatplanPlan, user: User, dokument: KatplanDokument) -> None:
    _bindung(plan, user)
    db.query(OrgSettings).filter_by(org_id=plan.org_id).with_for_update().first()
    _dokument(db, plan, dokument.id)
    gruppe_ids = {d.id for d in _gruppe(db, plan, dokument)}
    referenziert = db.query(KatplanElement).filter(
        KatplanElement.org_id == plan.org_id, KatplanElement.plan_id == plan.id,
        KatplanElement.quelle_dokument_id.in_(gruppe_ids),
    ).first() is not None
    referenziert = referenziert or any(
        gruppe_ids & quellen_ids_aus_version(db, plan, v.id)
        for v in db.query(KatplanVersion).filter_by(org_id=plan.org_id, plan_id=plan.id)
    )
    referenziert = referenziert or db.query(KatplanAnhang.id).filter_by(
        org_id=plan.org_id, pfad=dokument.pfad,
    ).first() is not None
    _geaendert(db, plan, user, dokument, "entfernt")
    if referenziert:
        dokument.archiviert = True
    else:
        # Fehlendes Verzeichnis (z. B. abgebrochener Upload) darf das Entfernen nicht blockieren.
        shutil.rmtree(absolute_pfad(dokument.pfad).parent, ignore_errors=True)
        assert plan.org_id is not None
        release_storage(db, plan.org_id, dokument.belegt_bytes)
        for seite in db.query(KatplanDokumentSeite).filter_by(
            org_id=plan.org_id, plan_id=plan.id, dokument_id=dokument.id,
        ).all():
            db.delete(seite)
        # Auch bei SQLite ohne FK-Pragma dieselbe SET NULL-Semantik sicherstellen.
        rest = [d for d in _gruppe(db, plan, dokument) if d.id != dokument.id]
        if rest and dokument.dokument_gruppe_id is None:
            genesis = rest[0]
            for andere in rest:
                andere.dokument_gruppe_id = None if andere is genesis else genesis.id
        db.delete(dokument)
    db.flush()


def quelle_der_seite(db: Session, plan: KatplanPlan, dokument_id: int,
                     seiten_nr: int) -> KatplanDokumentSeite | None:
    return db.query(KatplanDokumentSeite).filter_by(
        org_id=plan.org_id, plan_id=plan.id, dokument_id=dokument_id, seiten_nr=seiten_nr,
    ).one_or_none()


def sichtbare_quellen(db: Session, plan: KatplanPlan, user: User) -> list[KatplanDokument]:
    if user.org_id != plan.org_id:
        return []
    query = db.query(KatplanDokument).filter_by(org_id=plan.org_id, plan_id=plan.id)
    if darf_bearbeiten(user):
        aktuelle = query.filter_by(archiviert=False, ist_aktuelle_version=True).all()
        ids = {d.id for aktuell in aktuelle for d in _gruppe(db, plan, aktuell)}
    elif plan.aktuelle_version_id and plan.status != "archiviert":
        ids = quellen_ids_aus_version(db, plan, plan.aktuelle_version_id, user)
    else:
        return []
    return query.filter(KatplanDokument.id.in_(ids)).order_by(
        KatplanDokument.dokument_gruppe_id, KatplanDokument.versionsnummer, KatplanDokument.id,
    ).all()


def validiere_element_quelle(db: Session, plan: KatplanPlan, dokument_id: int | None,
                            seiten: str) -> None:
    if dokument_id is None:
        if seiten:
            raise ValueError("Seiten benötigen eine Quelle.")
        return
    dokument = _dokument(db, plan, dokument_id)
    if seiten:
        parse_seiten(seiten, dokument.seitenzahl, limit=0)


def parse_seiten(seiten: str, seitenzahl: int | None = None, *, limit: int | None = None) -> list[int]:
    """Seitenangaben wie 3-4,7 pruefen, ohne grosse Bereiche zu expandieren."""
    if not isinstance(seiten, str) or len(seiten) > 30:
        raise ValueError("Ungültiger Seitenbereich.")
    if not seiten:
        return []
    nummern: list[int] = []
    for teil in seiten.split(","):
        match = re.fullmatch(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?", teil)
        if not match:
            raise ValueError("Ungültiger Seitenbereich.")
        start, ende = int(match[1]), int(match[2] or match[1])
        if start > ende or (seitenzahl is not None and ende > seitenzahl):
            raise ValueError("Seitenbereich liegt außerhalb des Dokuments.")
        # Auch nach Erreichen des Ausgabelimits alle restlichen Bereiche validieren.
        if limit is not None and len(nummern) >= limit:
            continue
        for nr in range(start, ende + 1):
            if nr not in nummern:
                nummern.append(nr)
                if limit is not None and len(nummern) >= limit:
                    break
    return nummern


def quellen_seiten(dokument: KatplanDokument, seiten: str, *,
                   version_id: int | None = None, limit: int = 30) -> list[dict]:
    """Validierte Seitenreferenzen mit einheitlichen, versionsgebundenen URLs."""
    nummern = parse_seiten(seiten, dokument.seitenzahl, limit=limit)
    suffix = f"?version_id={version_id}" if version_id is not None else ""
    return [{"nr": nr, "seiten_nr": nr,
             "thumb_url": f"/plaene/quelle/{dokument.uuid}/seite/{nr}/thumb{suffix}",
             "seiten_url": f"/plaene/quelle/{dokument.uuid}/seite/{nr}/bild{suffix}",
             "text_url": f"/plaene/quelle/{dokument.uuid}/seite/{nr}/text{suffix}"}
            for nr in nummern]


def original_url(dokument: KatplanDokument, nr: int, version_id: int | None = None) -> str:
    suffix = f"?version_id={version_id}" if version_id is not None else ""
    return f"/plaene/quelle/{dokument.uuid}/original{suffix}#page={nr}"


def vergleiche_quellen(db: Session, plan: KatplanPlan, dokument_id: int,
                      mit: int | None = None) -> dict:
    """Vergleicht gleiche Seitennummern; aendert keine Referenzen."""
    import difflib
    import textwrap

    neu = _dokument(db, plan, dokument_id)
    gruppe = _gruppe(db, plan, neu)
    alt = _dokument(db, plan, mit) if mit is not None else next(
        (d for d in reversed(gruppe) if d.versionsnummer < neu.versionsnummer), None)
    if alt is None or alt.versionsnummer >= neu.versionsnummer or alt.id not in {d.id for d in gruppe}:
        raise ValueError("Keine passende Vorgängerversion.")
    seiten: list[dict] = []
    maps = [{s.seiten_nr: s for s in db.query(KatplanDokumentSeite).filter_by(
        org_id=plan.org_id, plan_id=plan.id, dokument_id=d.id,
    )} for d in (alt, neu)]
    for nr in sorted(maps[0].keys() | maps[1].keys()):
        vorher, nachher = [pipeline.normalisiere_text(m[nr].volltext or "") if nr in m else "" for m in maps]
        status = ("neu" if nr not in maps[0] else "entfallen" if nr not in maps[1]
                  else "unverändert" if vorher == nachher else "geändert")
        diff = list(difflib.unified_diff(textwrap.wrap(vorher, 100), textwrap.wrap(nachher, 100),
                                        fromfile="alt", tofile="neu", lineterm="")) if status != "unverändert" else []
        seiten.append({"nr": nr, "status": status, "quote": difflib.SequenceMatcher(
            None, vorher, nachher).ratio(), "diff": diff[:60], "gekuerzt": len(diff) > 60})
    betroffen = {s["nr"] for s in seiten if s["status"] in ("geändert", "entfallen")}
    elemente = []
    for e in db.query(KatplanElement).filter(
        KatplanElement.org_id == plan.org_id, KatplanElement.plan_id == plan.id,
        KatplanElement.quelle_dokument_id.in_([d.id for d in gruppe]),
    ).order_by(KatplanElement.sort, KatplanElement.id):
        try:
            referenzen = parse_seiten(e.quelle_seiten or "", max(d.seitenzahl for d in gruppe))
            if betroffen.intersection(referenzen):
                elemente.append(e)
        except ValueError:
            continue
    return {"alt": alt, "neu": neu, "seiten": seiten, "elemente": elemente}


def stelle_quelle_um(db: Session, plan: KatplanPlan, user: User, dokument_id: int,
                     element: KatplanElement) -> None:
    _bindung(plan, user)
    if not darf_bearbeiten(user) or element.org_id != plan.org_id or element.plan_id != plan.id:
        raise ValueError("Element gehört nicht zu diesem Plan.")
    neu = _dokument(db, plan, dokument_id)
    if element.quelle_dokument_id not in {d.id for d in _gruppe(db, plan, neu)}:
        raise ValueError("Quelle gehört nicht zur selben Gruppe.")
    alt = element.quelle_dokument_id
    element.quelle_dokument_id = neu.id
    element.version += 1
    element.aktualisiert_am = _utcnow()
    element.aktualisiert_von_id = user.id
    protokolliere(db, plan, "element", "quelle_umgestellt", user, element_id=element.id,
                  feld="quelle_dokument_id", alt=str(alt), neu=str(neu.id))
    _geaendert(db, plan, user, neu, "Referenz umgestellt")
