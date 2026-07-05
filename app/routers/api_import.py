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

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
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


@router.post("/dokument/{objekt_id}")
async def upload_import_dokument(
    objekt_id: int,
    file: UploadFile = File(...),
    dok_typ_label: str = Form(""),
    favorit: bool = Form(False),
    melderlinie: str = Form(""),
    db: Session = Depends(_get_db_ohne_tenant),
    _: None = Depends(verify_import_key),
):
    """Laedt ein Dokument hoch, zerlegt es (Poppler) und klassifiziert alle Seiten.

    dok_typ_label: Freitext-Label aus dem EUS-Dokumenttyp-Katalog (z. B.
    "Brandschutzplan"); wird per Fuzzy-Match auf DOKUMENTARTEN abgebildet.
    Kein Treffer → Seiten bleiben unklassifiziert (gelb in der Galerie, manuell
    oder per KI-Review nachtragbar) statt eine falsche Art zu raten.
    """
    objekt = db.query(Objekt).filter(Objekt.id == objekt_id).first()
    if objekt is None:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden")

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
    # dem Migrationsscript einen verlaesslichen Endzustand liefert. Poppler-
    # Aufrufe sind blockierend -> in den Threadpool auslagern, damit der
    # Event-Loop waehrenddessen andere Requests bedienen kann.
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

    dokumentart = _match_dokumentart(dok_typ_label)
    if dokumentart or favorit or melderlinie.strip():
        for seite in seiten:
            if dokumentart:
                seite.dokumentart = dokumentart
            if favorit:
                seite.bei_einsatz_drucken = True
            if melderlinie.strip():
                seite.melderlinien = melderlinie.strip()[:100]
        db.commit()

    return {
        "id": dokument.id,
        "objekt_id": objekt_id,
        "status": dokument.status,
        "seitenzahl": dokument.seitenzahl,
        "seiten_erzeugt": len(seiten),
        "dokumentart": dokumentart,
        "fehler_text": dokument.fehler_text,
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
