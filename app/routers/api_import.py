"""TEMPORAERER Import-Endpunkt fuer die einmalige EUS-Datenmigration.

Nimmt Dokument-Uploads fuer bereits per scripts/eus_migration.py angelegte
Objekte entgegen und schleust sie durch die bestehende PDF-Pipeline
(app/services/objekt_dokument_service.py): Split in Einzelseiten, Poppler-
Rendering, danach Klassifizierung (Dokumentart/Melderlinie/Einsatzdruck) auf
alle resultierenden Seiten des jeweiligen EUS-Dokuments uebertragen — EUS
klassifiziert pro Dokument, Einsatzcockpit pro Seite.

Auth: einfacher Shared-Secret-Header (X-Import-Key gegen settings.IMPORT_API_KEY),
kein Session-/Rollen-Login noetig, damit das lokale Migrationsscript ohne
Benutzerkonto arbeiten kann. Bewusst KEIN Tenant-Kontext (Fail-Closed-Bypass
noetig, siehe set_tenant_context(db, None)) — der Endpunkt scopet stattdessen
manuell ueber die uebergebene objekt_id + deren org_id.

WICHTIG: Nur fuer die Dauer der Migration aktiv. Danach:
  - diese Datei loeschen
  - den app.include_router(api_import.router)-Eintrag in app/main.py entfernen
  - IMPORT_API_KEY aus der .env entfernen
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.objekt import DOKUMENTARTEN, Objekt, ObjektDokument, ObjektDokumentSeite
from app.services.objekt_dokument_service import store_dokument_upload, verarbeite_dokument

logger = logging.getLogger("einsatzleiter.api_import")

router = APIRouter(prefix="/api/import", tags=["import"])


def verify_import_key(x_import_key: str = Header(...)) -> None:
    """Fail-closed: ohne konfigurierten Key ist der Endpunkt fuer niemanden nutzbar."""
    if not settings.IMPORT_API_KEY or x_import_key != settings.IMPORT_API_KEY:
        raise HTTPException(status_code=403, detail="Ungueltiger Import-Key")


def _get_db_ohne_tenant():
    """Eigene Session-Dependency (nicht app.db.get_db): dieser Endpunkt hat keine
    Benutzer-/Org-Session, daher wird kein Tenant per _resolve_current_org
    aufgeloest. set_tenant_context(db, None) = unfiltered (Fail-Closed-Bypass);
    der Endpunkt scopet stattdessen manuell ueber die uebergebene objekt_id.

    Import von SessionLocal innerhalb der Funktion (nicht Modul-Top) — gleiches
    Muster wie objekt_dokument_service.verarbeite_dokument, damit Tests
    app.db.SessionLocal monkeypatchen koennen."""
    from app.db import SessionLocal
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        yield db
    finally:
        db.close()


# EUS DokumentTyp (Integer) -> feste EC-Dokumentart-Codes (DOKUMENTARTEN).
# EC kennt keine Arten "Grundriss"/"Feuerwehrplan"/"Foto"/"Sonstiges":
#   Grundriss -> lageplan (gleiche Heuristik wie _DOKUMENTART_STICHWORTE),
#   Feuerwehrplan -> brandschutzplan (naechste EC-Art),
#   Laufkarte -> bma_melderplan, Foto/Sonstiges -> None (unklassifiziert).
_DOK_TYP_MAPPING: dict[int, str] = {
    1: "lageplan",
    2: "lageplan",
    3: "brandschutzplan",
    4: "brandschutzplan",
    5: "bma_melderplan",
    200: "lageplan",
}


def _parse_stand(wert: str) -> date | None:
    """EUS-Stand ("YYYY-MM-DD HH:MM:SS" oder ISO) -> date; None bei leer/ungueltig."""
    if not wert or not wert.strip():
        return None
    try:
        return datetime.fromisoformat(wert.strip()).date()
    except ValueError:
        return None


@router.post("/dokument/{objekt_id}")
async def upload_import_dokument(
    objekt_id: int,
    file: UploadFile = File(...),
    laufende_nr: int = 0,
    dok_typ: int = 0,
    dok_unter_typ: str = "",
    dok_typ_label: str = "",
    favorit: bool = False,
    melderlinie: str = "",
    stand: str = "",
    bemerkung: str = "",
    schluessel_safe: bool = False,
    schluessel_box: bool = False,
    dl: bool = False,
    bmzfbf: bool = False,
    sammelplatz: bool = False,
    druck_format: int = 0,
    db: Session = Depends(_get_db_ohne_tenant),
    _: None = Depends(verify_import_key),
):
    """Laedt ein Dokument hoch, zerlegt es (Poppler) und klassifiziert alle Seiten.

    Alle Klassifizierungshinweise kommen als Query-Parameter (das Migrations-
    script sendet params=, nicht form data). Dokumentart: zuerst der EUS-
    DokumentTyp-Integer (_DOK_TYP_MAPPING), sonst Fuzzy-Match auf dok_typ_label.
    Kein Treffer → Seiten bleiben unklassifiziert (gelb in der Galerie, manuell
    oder per KI-Review nachtragbar) statt eine falsche Art zu raten.

    bei_einsatz_drucken = favorit ODER dl ODER bmzfbf ODER sammelplatz.
    bemerkung -> Seitentitel. Ohne EC-Gegenstueck (bewusst ignoriert):
    laufende_nr (Sortierung ergibt sich aus Upload-Reihenfolge), dok_unter_typ
    (GUID ohne Mapping-Tabelle), schluessel_safe/schluessel_box (liegen in EC am
    Objekt/BMA-Block, nicht am Dokument), druck_format (EC rendert selbst).

    Idempotent: gleicher dateiname_original am selben Objekt → bestehendes
    Dokument zurueckgeben statt Duplikat anzulegen.
    """
    objekt = db.query(Objekt).filter(Objekt.id == objekt_id).first()
    if objekt is None:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden")

    vorhanden = (
        db.query(ObjektDokument)
        .filter(
            ObjektDokument.objekt_id == objekt.id,
            ObjektDokument.dateiname_original == (file.filename or ""),
        )
        .first()
    )
    if vorhanden is not None:
        return {
            "id": vorhanden.id,
            "objekt_id": objekt_id,
            "status": vorhanden.status,
            "seitenzahl": vorhanden.seitenzahl,
            "duplikat": True,
        }

    try:
        dokument = await store_dokument_upload(file, objekt, user=None, db=db)
        db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Import-Upload fehlgeschlagen (Objekt %d, Datei %s)",
                         objekt_id, file.filename)
        raise HTTPException(status_code=500, detail=f"Upload fehlgeschlagen: {exc}") from exc

    # Synchron verarbeiten (nicht als BackgroundTask), damit die HTTP-Antwort
    # dem Migrationsscript einen verlaesslichen Endzustand liefert. Es gibt
    # KEINEN Hintergrund-Job, der auf status='neu' lauscht — ohne diesen Aufruf
    # bliebe das Dokument unverarbeitet. Poppler-Aufrufe sind blockierend ->
    # in den Threadpool auslagern, damit der Event-Loop waehrenddessen andere
    # Requests bedienen kann.
    await run_in_threadpool(verarbeite_dokument, dokument.id)

    db.expire_all()
    dokument_neu = db.get(ObjektDokument, dokument.id)
    assert dokument_neu is not None
    dokument = dokument_neu
    seiten = (
        db.query(ObjektDokumentSeite)
        .filter(ObjektDokumentSeite.dokument_id == dokument.id)
        .all()
    )

    dokumentart = _DOK_TYP_MAPPING.get(dok_typ) or _match_dokumentart(dok_typ_label)
    einsatzdruck = favorit or dl or bmzfbf or sammelplatz
    stand_datum = _parse_stand(stand)
    titel = bemerkung.strip()[:200] or None
    if dokumentart or einsatzdruck or melderlinie.strip() or stand_datum or titel:
        for seite in seiten:
            if dokumentart:
                seite.dokumentart = dokumentart
            if einsatzdruck:
                seite.bei_einsatz_drucken = True
            if melderlinie.strip():
                seite.melderlinien = melderlinie.strip()[:100]
            if stand_datum:
                seite.stand = stand_datum
            if titel:
                seite.titel = titel
        db.commit()

    return {
        "id": dokument.id,
        "objekt_id": objekt_id,
        "status": dokument.status,
        "seitenzahl": dokument.seitenzahl,
        "seiten_erzeugt": len(seiten),
        "dokumentart": dokumentart,
        "fehler_text": dokument.fehler_text,
        "duplikat": False,
    }


# Heuristische Zuordnung EUS-Freitext-Label -> feste EC-Dokumentart-Codes.
# Bewusst konservativ: kein Treffer -> None (unklassifiziert), statt zu raten.
_DOKUMENTART_STICHWORTE: dict[str, tuple[str, ...]] = {
    "bma_melderplan": ("melderplan", "laufkarte"),
    "gefahrgutdatenblatt": ("gefahrgut", "sicherheitsdatenblatt"),
    "bma_datenblatt": ("bma datenblatt", "bma-datenblatt", "datenblatt"),
    "brandschutzplan": ("brandschutzplan", "bsp"),
    "lageplan": ("lageplan", "grundriss", "übersichtsplan", "uebersichtsplan"),
    "objektinformation": ("objektinformation", "objektinfo", "information"),
}


def _match_dokumentart(label: str) -> str | None:
    if not label or not label.strip():
        return None
    norm = label.strip().lower()
    for code, stichworte in _DOKUMENTART_STICHWORTE.items():
        if any(w in norm for w in stichworte):
            return code if code in DOKUMENTARTEN else None
    return None
